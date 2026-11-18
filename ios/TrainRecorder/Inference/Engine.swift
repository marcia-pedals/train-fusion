import Foundation
import simd

/// The online version of phone-sensor-fusion's model: per second, phone state ->
/// along-track acceleration and yaw (while the phone rests) -> emissions -> one forward
/// step of the HMM over (mode, speed) -> distance -> at each door opening, the station
/// layer. Only the past is used. Not thread-safe: use from one queue.
///
/// Two beliefs are kept, because one measurement (the accelerometer's line spectrum, for
/// the wheel-rotation lines) arrives about 3 s after the second it describes:
///  - the *settled* belief, 3 s behind, which has every measurement of its second;
///  - the *current* belief: the settled one stepped forward through the seconds since,
///    using everything already in for them (all but those lines). It is exactly the belief
///    about now given all that has arrived, and is what gets shown. It is recomputed each
///    second (three extra forward steps), so nothing in it is ever double-counted.
/// Stops, legs and the station layer advance with the settled belief.
///
/// Where the Python pipeline works on a whole recording at once, this has to make do:
///  - the along-track axis, its sign and offset are re-estimated every second from the
///    resting stretch so far, and only trusted once they can be told;
///  - a stop is the settled probability of the door-open chime crossing 1/2;
///  - distance is the expected distance given everything so far (carried forward per
///    state), not the sum of the filtered speeds.
final class InferenceEngine {
    typealias Accel = (a: Double, sign: Double, offset: Double)

    /// The hidden-state belief and what rides along with it.
    private struct Filter {
        var alpha: [Double]
        var dist: [Double]  // expected metres since the last stop, given the state now
        var started = false
        var armed = true  // ready to count the next door-open chime as a stop
        var prevAccel: Accel?  // the acceleration measured in the second this belief is about
        var prevResting = false
    }

    /// A second whose prompt measurements are in but whose accelerometer spectrum isn't.
    private struct Pending {
        let frame: Frame
        var lb: [Double]  // log emissions from everything prompt
        let phone: [Double]
        let phoneState: Int
        let resting: Bool
        let stretch: Int?  // which resting stretch this second belongs to, if any
        let accel: Accel?
        let yaw: Double?
        let heading: Double?  // compass heading of travel
        let toneRel: [Double]  // the motor tone's relative log-likelihood per speed state
    }

    let model: Model
    let features: FeatureExtractor
    private let n: Int, nSt: Int, nV: Int
    private let trans: [(i: Int, j: Int, p: Double, dv: Double)]
    private var settled: Filter
    private var pending: [Pending] = []
    private var phone = [1.0 / 3, 1.0 / 3, 1.0 / 3]
    private var settledSpeed = 0.0
    private var lastPrepared: Pending?
    private var speedRecent: [Double] = []  // the current belief's mean speed over the last 4 s
    private var toneRecent: [Double] = []  // evidence over the last few seconds, for the status display
    private var wheelRecent: [Double?] = []
    private static let evidenceSpan = 5
    /// For checking the port: the latest prompt frame, the latest accelerometer spectrum
    /// (and which second it belongs to), and how the acceleration is being read.
    private(set) var lastFrame: Frame?
    private(set) var lastAccelSpec: (second: Int, spec: [Float])?
    private(set) var lastAccel: Accel?

    // the current stretch of resting phone
    private var restRun = 0
    private var stretchID = 0
    private var sAcc: [SIMD3<Double>] = []
    private var sGyro: [SIMD3<Double>] = []
    // from settled beliefs, so these trail sAcc by the seconds still pending
    private var sDv: [Double] = []  // speed change into each second of the stretch
    private var sDoors: [Bool] = []  // whether the doors were (all but certainly) open or chiming that second
    private static let edge = 2, minStretch = 30
    private static let minAccelSd = 0.1, minCorrelation = 0.25
    private static let minEventAccel = 0.15  // m/s^2: mean braking / pulling-away acceleration that settles the sign

    // stops and the station layer (settled)
    private var stops = 0
    private var legs: [Double] = []
    private var stopLog: [Belief.Stop] = []
    private var closeArmed = true
    private let stationLayer: StationLayer

    init(model: Model) {
        self.model = model
        features = FeatureExtractor(model: model)
        (n, nSt, nV) = (model.n, model.n_stopped, model.n_v)
        trans = model.transitions.map { (Int($0[0]), Int($0[1]), $0[2], model.v[Int($0[1])] - model.v[Int($0[0])]) }
        settled = Filter(alpha: model.prior, dist: [Double](repeating: 0, count: model.n))
        stationLayer = StationLayer(model: model)
    }

    /// The belief about every second that has become available since the last call.
    func process() -> [Belief] {
        var out: [Belief] = []
        while let b = processNext() { out.append(b) }
        return out
    }

    /// The current belief about the next second whose prompt measurements are in, if any.
    func processNext() -> Belief? {
        guard features.promptReady else { return nil }
        let frame = features.makePromptFrame()
        lastFrame = frame
        let prepared = prepare(frame)
        lastPrepared = prepared
        pending.append(prepared)
        // settle every second whose accelerometer spectrum has now arrived
        while features.lateReady, !pending.isEmpty {
            let spec = features.makeAccelSpec()
            lastAccelSpec = (pending[0].frame.second, spec)
            settle(pending.removeFirst(), spec)
        }
        // the current belief: the settled one carried through what is still pending
        var now = settled
        // (if the door-open chime is in those seconds, the current belief has already started
        // the next leg at 0 while the stop isn't counted yet: remember the leg it finished)
        var uncounted: Double?
        for p in pending {
            if let leg = advance(&now, p.lb, resting: p.resting, accel: p.accel, countStops: true) { uncounted = leg }
        }
        var b = summarise(now, second: frame.second)
        b.phoneP = phone
        b.accelInUse = pending.last?.accel != nil
        b.accelReading = pending.last?.accel.map { $0.sign * ($0.a - $0.offset) }
        b.stops = stops
        b.legs = legs
        b.stations = stationLayer.top(4)
        // until the stop is counted the station layer is still on the leg just finished
        b.stopPending = uncounted != nil
        b.place = stationLayer.place(metresSinceStop: (uncounted ?? 0) + b.distanceSinceStop)
        b.stopLog = stopLog
        // the best guess, however unsure: whoever shows it looks at the confidence
        let names = stationLayer.stopNames(legs: legs)
        for k in 0..<min(names.count, b.stopLog.count) { b.stopLog[k].station = names[k] }
        speedRecent.append(b.speedMean)
        if speedRecent.count > 4 { speedRecent.removeFirst() }
        if speedRecent.count == 4 { b.acceleration = (speedRecent[3] - speedRecent[0]) / 3 }
        if let p = pending.last ?? lastPrepared {
            toneRecent.append(evidence(p.toneRel, at: b.speedMean))
            if toneRecent.count > Self.evidenceSpan { toneRecent.removeFirst() }
        }
        b.toneEvidence = toneRecent.isEmpty ? nil : toneRecent.reduce(0, +) / Double(toneRecent.count)
        let wheel = wheelRecent.compactMap { $0 }
        b.wheelEvidence = wheelRecent.last.flatMap { $0 } == nil || wheel.isEmpty ? nil : wheel.reduce(0, +) / Double(wheel.count)
        return b
    }

    /// Everything about a second that doesn't wait for the accelerometer spectrum.
    private func prepare(_ f: Frame) -> Pending {
        // phone state (forward filter on the gyro level)
        let ph = model.phone
        let q = ph.switch
        var pred = [0.0, 0.0, 0.0]
        for a in 0..<3 { for b in 0..<3 { pred[b] += phone[a] * (a == b ? 1 - 1.5 * q : q / 2) } }
        var best = -Double.infinity
        var ll = [0.0, 0.0, 0.0]
        for s in 0..<3 {
            let z = (Double(f.gyroRms) - ph.mean[s]) / ph.sd[s]
            ll[s] = -0.5 * z * z - log(ph.sd[s])
            best = max(best, ll[s])
        }
        var sum = 0.0
        for s in 0..<3 { phone[s] = pred[s] * exp(ll[s] - best); sum += phone[s] }
        for s in 0..<3 { phone[s] /= sum }
        let state = phone.indices.max { phone[$0] < phone[$1] }!
        let resting = phone[0] > 0.5

        // motion while resting
        var accel: Accel?
        var yaw: Double?, heading: Double?
        var stretch: Int?
        if resting {
            restRun += 1
            if restRun > Self.edge {
                sAcc.append(f.accMean); sGyro.append(f.gyroMean)
                stretch = stretchID
            }
            if sAcc.count >= Self.minStretch { (accel, yaw, heading) = motion(f) }
        } else if restRun > 0 {
            restRun = 0
            stretchID += 1
            sAcc.removeAll(keepingCapacity: true); sGyro.removeAll(keepingCapacity: true)
            sDv.removeAll(keepingCapacity: true); sDoors.removeAll(keepingCapacity: true)
        }
        lastAccel = accel

        // emissions from the prompt measurements
        var lb = [Double](repeating: 0, count: n)
        for (name, x, st) in [("open", f.openChime, model.open_chime_state), ("close", f.closeChime, model.close_chime_state)] {
            let cat = (Double(x) >= model.chime_edges[0] ? 1 : 0) + (Double(x) >= model.chime_edges[1] ? 1 : 0)
            let c = model.chime[name]!
            for k in 0..<n { lb[k] += c.log_out[cat] }
            lb[st] += c.log_in[cat] - c.log_out[cat]
        }
        let toneRel = addComb(model.tone, f.toneSpec, &lb)
        if let r = model.level["rumble"] ?? nil { addLevel(r, Double(f.rumbleDb), &lb) }
        if state < 2 {
            let suffix = state == 0 ? "resting" : "held"
            for (b, name) in ["vib_low", "vib_high"].enumerated() {
                if let v = model.level["\(name)_\(suffix)"] ?? nil { addLevel(v, Double(f.vibDb[b]), &lb) }
            }
        }
        let travel = heading.flatMap { h in accel.map { (h + ($0.sign > 0 ? 180 : 0)).truncatingRemainder(dividingBy: 360) } }
        return Pending(frame: f, lb: lb, phone: phone, phoneState: state, resting: resting, stretch: stretch,
                       accel: accel, yaw: yaw, heading: travel, toneRel: toneRel)
    }

    /// A second's accelerometer spectrum has arrived: finish its emissions and make it
    /// part of the settled belief; count stops and feed the station layer.
    private func settle(_ p: Pending, _ accelSpec: [Float]) {
        var lb = p.lb
        var wheelRel: [Double]?
        if p.phoneState < 2, let w = model.wheel[p.phoneState == 0 ? "resting" : "held"] ?? nil { wheelRel = addComb(w, accelSpec, &lb) }
        let stopped = advance(&settled, lb, resting: p.resting, accel: p.accel, countStops: true)
        let b = summarise(settled, second: p.frame.second)
        wheelRecent.append(wheelRel.map { evidence($0, at: b.speedMean) })
        if wheelRecent.count > Self.evidenceSpan { wheelRecent.removeFirst() }
        if p.stretch == stretchID {  // (a second of a stretch that has since ended teaches nothing)
            sDv.append(b.speedMean - settledSpeed)
            sDoors.append(b.modeP[2] + b.modeP[3] + b.modeP[4] > 0.9)
        }
        settledSpeed = b.speedMean
        stationLayer.addSecond(v: b.speedMean, dist: stopped ?? b.distanceSinceStop, yaw: p.yaw, heading: p.heading, baro: p.frame.baroAlt)
        if let leg = stopped {
            if stops > 0 { legs.append(leg) }
            stops += 1
            stationLayer.stop()
            stopLog.append(Belief.Stop(opened: p.frame.second))
        }
        // the door-close chime's leading edge, for the log of stops
        let pClose = settled.alpha[model.close_chime_state]
        if closeArmed, pClose > 0.5 {
            closeArmed = false
            if !stopLog.isEmpty, stopLog[stopLog.count - 1].closed == nil { stopLog[stopLog.count - 1].closed = p.frame.second }
        } else if pClose < 0.1 {
            closeArmed = true
        }
    }

    /// One forward step of the HMM into a second with log emissions `lb`, the transition
    /// weighed by the acceleration measured in the second before. Returns the distance of
    /// the leg just ended if this step is a door opening (and starts the next leg at 0).
    @discardableResult
    private func advance(_ f: inout Filter, _ lb: [Double], resting: Bool, accel: Accel?, countStops: Bool) -> Double? {
        var new = [Double](repeating: 0, count: n)
        var newDist = [Double](repeating: 0, count: n)
        if !f.started {
            new = f.alpha
            f.started = true
        } else {
            let acc = model.accel
            let m = (resting && f.prevResting) ? f.prevAccel : nil
            for t in trans {
                var w = f.alpha[t.i] * t.p
                if w == 0 { continue }
                if let m {
                    let r = (m.a - m.offset) - m.sign * t.dv
                    let o = (m.a - m.offset) / acc.sd_outlier
                    w *= (1 - acc.outlier) * exp(-0.5 * (r / acc.sd) * (r / acc.sd)) / acc.sd
                        + acc.outlier * exp(-0.5 * o * o) / acc.sd_outlier
                }
                new[t.j] += w
                newDist[t.j] += w * f.dist[t.i]
            }
        }
        let top = lb.max()!
        var total = 0.0
        for k in 0..<n {
            if new[k] > 0 { newDist[k] = newDist[k] / new[k] + model.v[k] } else { newDist[k] = 0 }
            new[k] *= exp(lb[k] - top)
            total += new[k]
        }
        if total > 0 { for k in 0..<n { new[k] /= total } } else { new = model.prior }
        f.alpha = new
        f.dist = newDist
        f.prevAccel = accel
        f.prevResting = resting

        let pOpen = new[model.open_chime_state]
        var leg: Double?
        if f.armed, pOpen > 0.5 {
            f.armed = false
            leg = 0
            for k in 0..<n { leg! += new[k] * newDist[k] }
            f.dist = [Double](repeating: 0, count: n)
        } else if pOpen < 0.1 {
            f.armed = true
        }
        return countStops ? leg : nil
    }

    private func summarise(_ f: Filter, second: Int) -> Belief {
        var b = Belief(second: second)
        var m2 = 0.0
        b.modeP = [Double](repeating: 0, count: 6)
        b.speedHist = [Float](repeating: 0, count: Int((model.v[n - 1]).rounded(.up)) + 1)
        for k in 0..<n {
            b.speedMean += f.alpha[k] * model.v[k]
            m2 += f.alpha[k] * model.v[k] * model.v[k]
            b.distanceSinceStop += f.alpha[k] * f.dist[k]
            b.modeP[model.mode[k]] += f.alpha[k]
            if k >= nSt { b.speedHist[Int(model.v[k])] += Float(f.alpha[k]) }
        }
        b.speedSd = (max(m2 - b.speedMean * b.speedMean, 0)).squareRoot()
        b.pStanding = 1 - b.modeP[0]
        return b
    }

    /// Along-track acceleration (with the sign and offset to read it by, once they can be
    /// told), yaw rate about the vertical, and the compass heading of the + axis, from the
    /// resting stretch so far.
    private func motion(_ f: Frame) -> (Accel?, Double?, Double?) {
        let count = Double(sAcc.count)
        let g = sAcc.reduce(SIMD3<Double>(), +) / count
        let up = -simd_normalize(g)
        var cov = simd_double3x3()
        var horiz: [SIMD3<Double>] = []
        horiz.reserveCapacity(sAcc.count)
        for a in sAcc {
            let d = a - g
            let h = d - simd_dot(d, up) * up
            horiz.append(h)
            cov += simd_double3x3(columns: (h * h.x, h * h.y, h * h.z))
        }
        // principal axis: power iteration, then a fixed sign so it doesn't flip from second to second
        var axis = SIMD3<Double>(1, 1, 1)
        for _ in 0..<60 {
            let nxt = cov * axis
            let len = simd_length(nxt)
            if len < 1e-18 { break }
            axis = nxt / len
        }
        axis -= simd_dot(axis, up) * up
        if simd_length(axis) < 1e-9 { return (nil, nil, nil) }
        axis = simd_normalize(axis)
        let big = [abs(axis.x), abs(axis.y), abs(axis.z)].enumerated().max { $0.element < $1.element }!.offset
        if axis[big] < 0 { axis = -axis }

        let gAcc = 9.80665

        // yaw about the vertical, less its median over the stretch (the gyro's bias)
        var yaws = sGyro.map { simd_dot($0, up) }
        let yawNow = yaws[yaws.count - 1]
        yaws.sort()
        let yaw = yawNow - yaws[yaws.count / 2]

        var heading: Double?
        if let q = f.attitude {
            let w = simd_act(q, axis)  // world: x magnetic north, y west
            heading = (atan2(-w.y, w.x) * 180 / .pi + model.track.declination + 360).truncatingRemainder(dividingBy: 360)
        }

        // Which way to read it (sign), and its zero (offset). Two ways, the first much the
        // better when it is available:
        //  (a) from a station stop inside this stretch. Door chimes are certain, so: the
        //      level while the doors are open is zero; the train braked into the stop
        //      (negative) and pulls away from it (positive).
        //  (b) otherwise, from how the acceleration goes with the filter's own speed changes.
        var aLong: Accel?
        let known = min(sDoors.count, horiz.count)
        let a = horiz.map { simd_dot($0, axis) * gAcc }
        let aNow = a[a.count - 1]
        var zeroSum = 0.0, zeroN = 0.0
        for k in 0..<known where sDoors[k] { zeroSum += a[k]; zeroN += 1 }
        if zeroN >= 3 {
            let zero = zeroSum / zeroN
            var score = 0.0
            func mean(_ lo: Int, _ hi: Int) -> Double? {
                let (l, h) = (max(lo, 0), min(hi, a.count))
                return h - l >= 8 ? a[l..<h].reduce(0, +) / Double(h - l) - zero : nil
            }
            var k = 0
            while k < known {
                guard sDoors[k] else { k += 1; continue }
                var end = k
                while end < known, sDoors[end] { end += 1 }
                if let braking = mean(k - 25, k - 3) { score -= braking }
                if let pulling = mean(end + 8, end + 30) { score += pulling }
                k = end
            }
            if abs(score) >= Self.minEventAccel { aLong = (aNow, score > 0 ? 1 : -1, zero) }
        }
        let m = min(sDv.count, horiz.count) - 1
        if aLong == nil, m >= Self.minStretch - 1 {
            var sa = 0.0, sd = 0.0, saa = 0.0, sdd = 0.0, sad = 0.0
            for k in 1...m {
                // sDv[k] is the change INTO second k; the acceleration behind it was measured in second k - 1
                let x = a[k - 1], d = sDv[k]
                sa += x; sd += d; saa += x * x; sdd += d * d; sad += x * d
            }
            let cnt = Double(m)
            let varA = saa / cnt - (sa / cnt) * (sa / cnt)
            let varD = sdd / cnt - (sd / cnt) * (sd / cnt)
            let cov = sad / cnt - sa / cnt * sd / cnt
            // The filter's speed changes are the noisy side of this pair (they are what the
            // acceleration is meant to improve), so judge by correlation, not by the slope,
            // which noise in the regressor shrinks; and take the acceleration at face value
            // (slope 1) for the offset. Needs the train to have accelerated in this stretch.
            if varA > Self.minAccelSd * Self.minAccelSd, varD > 0, abs(cov) / (varA * varD).squareRoot() >= Self.minCorrelation {
                let sign: Double = cov > 0 ? 1 : -1
                aLong = (aNow, sign, sa / cnt - sign * sd / cnt)
            }
        }
        return (aLong, yaw, heading)
    }

    /// Adds log p(line spectrum | speed) - log p(no lines) to the moving states, scaled so
    /// that it says which speed, never whether moving.
    @discardableResult
    private func addComb(_ c: Model.Comb, _ spec: [Float], _ lb: inout [Double]) -> [Double] {
        var ll = [Double](repeating: 0, count: nV)
        var pooled = spec
        for p in c.partials {
            var s = spec
            if p.extra > 0 { Filters.maximum(spec, size: 2 * p.extra + 1, into: &pooled); s = pooled }
            for v in 0..<nV where p.bin[v] >= 0 {
                let z = Double(s[p.bin[v]])
                var zb = 0
                while zb < c.z_edges.count, z >= c.z_edges[zb] { zb += 1 }
                ll[v] += p.table[p.band[v]][zb]
            }
        }
        let top = ll.max()!
        var mean = 0.0
        for v in 0..<nV { mean += exp(ll[v] - top) }
        let norm = top + log(mean / Double(nV))
        for v in 0..<nV { ll[v] -= norm; lb[nSt + v] += ll[v] }
        return ll
    }

    /// How well a line-spectrum likelihood (per speed state) backs speed `v`: its best value within 1 m/s.
    private func evidence(_ rel: [Double], at v: Double) -> Double {
        let k = Int((v / model.dv).rounded()) - 1, r = Int(1 / model.dv)
        var best = -Double.infinity
        for i in max(k - r, 0)...min(max(k + r, 0), nV - 1) { best = max(best, rel[i]) }
        return best
    }

    private func addLevel(_ q: Model.Level, _ x: Double, _ lb: inout [Double]) {
        for k in 0..<n {
            let z = (x - q.mu[k]) / q.sd
            lb[k] += q.weight * log((1 - model.level_outlier) * exp(-0.5 * z * z) + model.level_outlier * model.level_floor)
        }
    }
}

/// Which station: a second, small HMM over (station, direction) that advances at each
/// door opening, weighing every possible leg a -> b by its length, and (where the phone
/// gives them) the yaw, heading and pressure altitude measured along it against the map.
final class StationLayer {
    private let tr: Model.Track
    private let c: [String: Double]
    private let nS: Int
    private var alpha: [[Double]]  // [station][0 south, 1 north]
    private var haveStop = false
    // this leg so far, one entry per second
    private var v: [Double] = [], d: [Double] = [], yaw: [Double?] = [], heading: [Double?] = [], baro: [Double?] = []

    init(model: Model) {
        tr = model.track
        c = model.stations
        nS = tr.stations.count
        alpha = [[Double]](repeating: [0.5 / Double(tr.stations.count), 0.5 / Double(tr.stations.count)], count: tr.stations.count)
    }

    func addSecond(v: Double, dist: Double, yaw: Double?, heading: Double?, baro: Double?) {
        self.v.append(v); d.append(dist); self.yaw.append(yaw); self.heading.append(heading); self.baro.append(baro)
    }

    private func robust(_ r: [Double], _ outlier: Double, _ weight: Double) -> Double {
        weight * r.reduce(0) { $0 + log((1 - outlier) * exp(-0.5 * $1 * $1) + outlier * 0.05) }
    }

    private func interp(_ a: [Double], _ x: Double, _ step: Double) -> Double {
        let u = min(max(x / step, 0), Double(a.count - 1))
        let i = min(Int(u), a.count - 2)
        return a[i] + (a[i + 1] - a[i]) * (u - Double(i))
    }

    private func trackHeading(_ s: Double, _ direction: Double) -> Double {
        interp(tr.heading_south, s, tr.step) + (direction > 0 ? 0 : 180)
    }

    private func wrap(_ x: Double) -> Double { (x + 540).truncatingRemainder(dividingBy: 360) - 180 }

    private func legLogLik(_ sa: Double, _ sb: Double, _ dMeas: Double) -> Double {
        let direction: Double = sb > sa ? 1 : -1
        let scale = abs(sb - sa) / dMeas
        var ll = 0.0
        var ry: [Double] = [], rh: [Double] = [], rb: [Double] = []
        for t in 0..<v.count {
            let pos = sa + direction * d[t] * scale
            if let y = yaw[t], v[t] > c["MIN_YAW_SPEED"]! {
                ry.append((y - direction * v[t] * interp(tr.curvature, pos, tr.step)) / c["YAW_SD"]!)
            }
            if let h = heading[t] { rh.append(wrap(h - trackHeading(pos, direction)) / c["HEADING_SD"]!) }
            if let b = baro[t], v[t] > c["MIN_BARO_SPEED"]! { rb.append(b - interp(tr.elevation, pos, tr.elev_step)) }
        }
        if ry.count >= 5 { ll += robust(ry, c["YAW_OUTLIER"]!, c["YAW_WEIGHT"]!) }
        if rh.count >= 5 { ll += robust(rh, c["HEADING_OUTLIER"]!, c["HEADING_WEIGHT"]!) }
        if rb.count >= 20 {
            let med = rb.sorted()[rb.count / 2]
            ll += robust(rb.map { ($0 - med) / c["BARO_SD"]! }, c["BARO_OUTLIER"]!, c["BARO_WEIGHT"]!)
        }
        return ll
    }

    /// A door opening was heard: fold in the leg that just ended, and start a new one.
    func stop() {
        var new = alpha
        if haveStop, let dMeas = d.last.map({ max($0, 1) }) {
            let sd = hypot(c["DIST_REL"]! * dMeas, c["DIST_ABS"]!)
            var lw = [[Double]](repeating: [Double](repeating: -.infinity, count: nS), count: nS)
            var top = -Double.infinity
            for a in 0..<nS { for b in 0..<nS where a != b {
                let trueD = abs(tr.station_s[b] - tr.station_s[a])
                if abs(trueD - dMeas) > 6 * sd { continue }
                let z = (dMeas - trueD) / sd
                lw[a][b] = -0.5 * z * z - log(sd) + Double(abs(b - a) - 1) * log(c["P_SKIP"]!)
                    + legLogLik(tr.station_s[a], tr.station_s[b], dMeas)
                top = max(top, lw[a][b])
            } }
            var w = [[Double]](repeating: [Double](repeating: 0, count: nS), count: nS)
            var sum = 0.0
            if top > -.infinity {
                for a in 0..<nS { for b in 0..<nS where lw[a][b] > -.infinity { w[a][b] = exp(lw[a][b] - top); sum += w[a][b] } }
            }
            let pu = c["P_UNEXPLAINED"]!, floor = pu / Double(nS * (nS - 1))
            new = [[Double]](repeating: [0, 0], count: nS)
            for a in 0..<nS { for b in 0..<nS where a != b {
                let wab = (1 - pu) * (sum > 0 ? w[a][b] / sum : 0) + floor
                if b > a { new[b][0] += alpha[a][0] * wab } else { new[b][1] += alpha[a][1] * wab }
            } }
        }
        // heading around the stop itself, against the track's heading at each station
        let recent = heading.suffix(15).compactMap { $0 }
        if recent.count >= 5 {
            let sx = recent.reduce(0) { $0 + sin($1 * .pi / 180) }, cx = recent.reduce(0) { $0 + cos($1 * .pi / 180) }
            let here = atan2(sx, cx) * 180 / .pi
            let o = c["HEADING_OUTLIER"]!
            for a in 0..<nS { for dir in 0..<2 {
                let z = wrap(here - trackHeading(tr.station_s[a], dir == 0 ? 1 : -1)) / c["HEADING_SD"]!
                new[a][dir] *= (1 - o) * exp(-0.5 * z * z) + o * 0.05
            } }
        }
        let total = new.reduce(0) { $0 + $1[0] + $1[1] }
        if total > 0 { alpha = new.map { [$0[0] / total, $0[1] / total] } }
        haveStop = true
        v.removeAll(keepingCapacity: true); d.removeAll(keepingCapacity: true); yaw.removeAll(keepingCapacity: true)
        heading.removeAll(keepingCapacity: true); baro.removeAll(keepingCapacity: true)
    }

    /// For the hypothesis (station a, direction dir) and metres since that stop: the
    /// station behind, the next one ahead that hasn't clearly been passed (an express skips
    /// some), and the position between them.
    private func segment(_ a: Int, _ dir: Int, _ d: Double) -> (last: Int, next: Int?, from: Double, to: Double?) {
        let step = dir == 0 ? 1 : -1
        let here = tr.station_s[a] + Double(step) * d
        var nxt = a + step
        while nxt >= 0, nxt < nS, Double(step) * (tr.station_s[nxt] - here) < -300 { nxt += step }
        guard nxt >= 0, nxt < nS else { return (a, nil, abs(here - tr.station_s[a]), nil) }
        return (nxt - step, nxt, abs(here - tr.station_s[nxt - step]), max(abs(tr.station_s[nxt] - here), 0))
    }

    /// The most probable (station, direction), turned into "between here and there".
    func place(metresSinceStop d: Double) -> Belief.Place? {
        guard haveStop else { return nil }
        var ranked: [(a: Int, dir: Int, p: Double)] = []
        for a in 0..<nS { for dir in 0..<2 { ranked.append((a, dir, alpha[a][dir])) } }
        ranked.sort { $0.p > $1.p }
        let best = ranked[0], second = ranked[1]
        guard best.p > 0.2 else { return nil }  // one stop in, with nothing to tell the stations apart
        let seg = segment(best.a, best.dir, d)
        var alternative: String?
        if second.p > 0.15 {
            let alt = segment(second.a, second.dir, d)
            alternative = tr.stations[alt.last] + (alt.next.map { " → " + tr.stations[$0] } ?? "")
        }
        let span = seg.to.map { seg.from + $0 } ?? 1
        return Belief.Place(
            last: tr.stations[seg.last], next: seg.next.map { tr.stations[$0] },
            southbound: best.dir == 0, confidence: best.p,
            progress: seg.next == nil ? 0 : min(max(seg.from / max(span, 1), 0), 1),
            metresFromLast: seg.from, metresToNext: seg.to, alternative: alternative)
    }

    /// The station of every stop so far, working back from the most probable current one
    /// along the legs' lengths. `legs[k]` is the distance from stop k to stop k + 1.
    func stopNames(legs: [Double]) -> [String] {
        guard haveStop else { return [] }
        var best = (a: 0, dir: 0, p: -1.0)
        for a in 0..<nS { for dir in 0..<2 where alpha[a][dir] > best.p { best = (a, dir, alpha[a][dir]) } }
        let step: Double = best.dir == 0 ? 1 : -1
        var names = [tr.stations[best.a]]
        var s = tr.station_s[best.a]
        for leg in legs.reversed() {
            s -= step * leg
            let nearest = (0..<nS).min { abs(tr.station_s[$0] - s) < abs(tr.station_s[$1] - s) }!
            names.append(tr.stations[nearest])
            s = tr.station_s[nearest]  // snap, so errors don't add up along the way
        }
        return names.reversed()
    }

    func top(_ k: Int) -> [Belief.Station] {
        guard haveStop else { return [] }
        var all: [Belief.Station] = []
        for a in 0..<nS { for dir in 0..<2 where alpha[a][dir] > 0.005 {
            all.append(.init(name: tr.stations[a], southbound: dir == 0, p: alpha[a][dir]))
        } }
        return Array(all.sorted { $0.p > $1.p }.prefix(k))
    }
}
