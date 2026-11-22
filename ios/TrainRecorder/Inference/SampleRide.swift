import Accelerate
import Foundation
import simd

/// A made-up ride from San Francisco 4th & King to Palo Alto, stopping everywhere but
/// Broadway, generated second by second as it is played back, so the real feature
/// extraction and filter run on it as on a recording. For trying the app without a train
/// (and for the store's reviewers).
///
/// The only hard-coded data is the stop list below: how long each leg takes and how long
/// the train stands. Everything else comes from simple models plus noise:
///  - motion: power-limited acceleration, constant braking, a cruise speed fitted to each
///    leg's time; positions are the map's (`model.track`);
///  - audio: a noise floor, rolling rumble that grows with speed, the traction motor's tone
///    (two partials proportional to speed) and the door chimes (their partials);
///  - accelerometer: gravity, the along-track acceleration and some of the centripetal one
///    in a phone lying flat at an angle to the track, wheel-rotation lines and vibration
///    that grow with speed;
///  - gyro: the train's turning (speed x the track's curvature) plus noise and bias;
///  - attitude: the track's heading; barometer: the track's elevation.
/// Same seed, same ride: replaying it from the start gives the same data.
final class SampleRide: ReplaySource {
    /// Which station, how long the train takes to get there from the stop before (s), and
    /// how long it stands there (s; the last one: until the sample ends).
    private static let legs: [(station: String, run: Double, dwell: Double)] = [
        ("22nd Street", 150, 40),
        ("Bayshore", 240, 35),
        ("South San Francisco", 275, 45),
        ("San Bruno", 155, 35),
        ("Millbrae", 190, 55),
        ("Burlingame", 205, 40),
        ("San Mateo", 140, 45),
        ("Hayward Park", 140, 35),
        ("Hillsdale", 115, 45),
        ("Belmont", 165, 35),
        ("San Carlos", 135, 40),
        ("Redwood City", 175, 60),
        ("Menlo Park", 240, 40),
        ("Palo Alto", 135, 25),
    ]
    private static let origin = "San Francisco 4th & King Street"
    private static let originOpen = 6.0  // s into the sample: the doors open at the origin
    private static let originDwell = 50.0
    private static let seed: UInt64 = 0x5EED_CA17

    // a few physical constants of the made-up train and phone
    private static let maxAccel = 0.7  // m/s^2
    private static let power = 12.0  // m^2/s^3: acceleration x speed at most (a diesel's power per mass)
    private static let braking = 0.65  // m/s^2
    private static let maxSpeed = 35.5  // m/s (79 mph)
    private static let phoneYaw = 25.0  // degrees between the phone's x axis and the train's forward
    private static let gridStep = 0.1  // s, of the precomputed motion

    let duration: Double
    /// The stops: station and when (s into the sample) the doors open and close.
    let stops: [(station: String, opened: Double, closed: Double)]

    private let track: Model.Track
    private let s: [Double], v: [Double], a: [Double]  // position (m on the map), speed, acceleration on the grid
    private let openChimes: [Double], closeChimes: [Double]

    // generator state (reset by rewind)
    private var rng = Random(seed: SampleRide.seed)
    private var rumbleFilter = Biquad.bandpass(f0: 40, q: 0.9, fs: 16000)
    private var tonePhase = [0.0, 0.0]
    private var wheelPhase = [0.0, 0.0, 0.0, 0.0]
    private var baroDrift = 0.0
    private let rumbleGain: Double  // output rms of the rumble filter for unit white noise

    init(model: Model) {
        let track = model.track
        func at(_ name: String) -> Double { track.station_s[track.stations.firstIndex(of: name) ?? 0] }

        // the motion, leg by leg, on the grid
        var (gs, gv, ga): ([Double], [Double], [Double]) = ([], [], [])
        var t = 0.0
        var sNow = at(Self.origin)
        func stand(until end: Double) {
            while t < end { gs.append(sNow); gv.append(0); ga.append(0); t += Self.gridStep }
        }
        let departure = Self.originOpen + Self.originDwell
        var stops = [(station: Self.origin, opened: Self.originOpen, closed: departure - 9)]
        stand(until: departure)
        for leg in Self.legs {
            let target = at(leg.station)
            for (vv, aa) in Self.drive(target - sNow, in: leg.run) {
                sNow += vv * Self.gridStep
                gs.append(sNow); gv.append(vv); ga.append(aa)
                t += Self.gridStep
            }
            sNow = target
            let leaves = t + leg.dwell
            stops.append((leg.station, t + 2, leaves - 9))
            stand(until: leaves)
        }

        var probe = Biquad.bandpass(f0: 40, q: 0.9, fs: 16000)
        var r = Random(seed: 1)
        var sum = 0.0
        for _ in 0..<32000 { let y = probe.step(r.gaussian()); sum += y * y }

        self.track = track
        (s, v, a) = (gs, gv, ga)
        self.stops = stops
        duration = t.rounded(.down)
        openChimes = stops.map(\.opened)
        closeChimes = stops.dropLast().map(\.closed)  // the sample ends with the doors open
        rumbleGain = (sum / 32000).squareRoot()
    }

    func rewind() {
        rng = Random(seed: Self.seed)
        rumbleFilter = Biquad.bandpass(f0: 40, q: 0.9, fs: 16000)
        tonePhase = [0, 0]
        wheelPhase = [0, 0, 0, 0]
        baroDrift = 0
    }

    /// Speed and acceleration every grid step of a leg of `distance` m that should take
    /// `run` s: the cruise speed is found by bisection (as fast as allowed if it can't be
    /// done in time).
    private static func drive(_ distance: Double, in run: Double) -> [(Double, Double)] {
        func simulate(_ cruise: Double) -> [(Double, Double)] {
            var out: [(Double, Double)] = []
            var (x, vv, aa) = (0.0, 0.0, 0.0)
            while x < distance - 0.05, out.count < 100_000 {
                let left = distance - x
                var want: Double
                if vv * vv / (2 * braking) >= left {
                    want = -vv * vv / (2 * max(left, 0.01))  // stop exactly at the platform
                } else if vv < cruise {
                    want = min(maxAccel, power / max(vv, 0.1))
                } else {
                    want = 0
                }
                // traction comes on and goes off gradually; braking is applied as needed
                aa = want < 0 ? want : aa + (want - aa) * min(gridStep / 1.2, 1)
                vv = max(vv + aa * gridStep, want < 0 ? 0.05 : 0)
                x += vv * gridStep
                out.append((vv, aa))
            }
            out.append((0, 0))
            return out
        }
        var (lo, hi) = (3.0, maxSpeed)
        var best = simulate(hi)
        if Double(best.count) * gridStep > run { return best }
        for _ in 0..<30 {
            let mid = (lo + hi) / 2
            let trial = simulate(mid)
            if Double(trial.count) * gridStep > run { lo = mid } else { hi = mid; best = trial }
        }
        return best
    }

    // MARK: one second of every stream

    func feed(_ second: Int, into f: FeatureExtractor) {
        let t0 = Double(second)
        audio(t0, into: f)
        imu(t0, into: f)
    }

    /// Motion at time t (s into the sample), interpolated on the grid.
    private func motion(_ t: Double) -> (s: Double, v: Double, a: Double) {
        let x = min(max(t / Self.gridStep, 0), Double(s.count - 1))
        let i = min(Int(x), s.count - 2), w = x - Double(i)
        return (s[i] + (s[i + 1] - s[i]) * w, v[i] + (v[i + 1] - v[i]) * w, a[i] + (a[i + 1] - a[i]) * w)
    }

    private func onTrack(_ table: [Double], _ x: Double, _ step: Double) -> Double {
        let p = min(max(x / step, 0), Double(table.count - 1))
        let i = min(Int(p), table.count - 2)
        return table[i] + (table[i + 1] - table[i]) * (p - Double(i))
    }

    private func audio(_ t0: Double, into f: FeatureExtractor) {
        let fs = 16000
        let floor = 0.01  // rms of the broadband noise floor
        var x = [Float](repeating: 0, count: fs)
        let m0 = motion(t0), m1 = motion(t0 + 1)
        // rolling rumble, dB in 20..70 Hz: quiet standing, louder with speed
        let rumbleDb = -49 + 11 * tanh(max(m0.v, m1.v) / 15)
        let rumbleScale = pow(10, rumbleDb / 20) / rumbleGain
        // the traction motor's whine: stronger while pulling than while braking
        let pull = { (m: (s: Double, v: Double, a: Double)) in m.v < 0.5 ? 0 : min(m.v / 3, 1) * (m.a < -0.1 ? 0.5 : 1) }
        let tone0 = pull(m0), tone1 = pull(m1)
        let ratios = [26.41, 58.12], toneAmp = [0.3 * floor, 0.18 * floor]
        let chime = chimeEnvelope(t0)
        for k in 0..<fs {
            let u = Double(k) / Double(fs)
            let vv = m0.v + (m1.v - m0.v) * u
            var y = floor * rng.gaussian() + rumbleScale * rumbleFilter.step(rng.gaussian())
            let tone = tone0 + (tone1 - tone0) * u
            if tone > 0 {
                for p in 0..<2 {
                    tonePhase[p] += 2 * .pi * ratios[p] * vv / Double(fs)
                    y += tone * toneAmp[p] * sin(tonePhase[p])
                }
            }
            x[k] = Float(y)
        }
        for (partials, start) in [(Self.openPartials, chime.open), (Self.closePartials, chime.close)] {
            guard let start else { continue }
            addChime(&x, partials: partials, from: start, amplitude: 3 * floor)
        }
        x.withUnsafeBufferPointer { f.addAudio16k($0) }
    }

    private static let openPartials = [2203.0, 4398.0, 6602.0]  // Hz, inside the model's bands
    private static let closePartials = [3516.0, 5281.0, 7039.0]
    private static let chimeLength = 2.6  // s

    /// When, relative to the second starting at `t0`, a door chime sounding in it started.
    private func chimeEnvelope(_ t0: Double) -> (open: Double?, close: Double?) {
        func find(_ times: [Double]) -> Double? {
            times.first { $0 < t0 + 1 && $0 + Self.chimeLength > t0 }.map { $0 - t0 }
        }
        return (find(openChimes), find(closeChimes))
    }

    /// Two "dings", the second a little softer, each decaying, with these partials.
    private func addChime(_ x: inout [Float], partials: [Double], from start: Double, amplitude: Double) {
        let fs = Double(x.count)
        for k in 0..<x.count {
            let tc = Double(k) / fs - start  // s into the chime
            guard tc >= 0, tc < Self.chimeLength else { continue }
            let ding = tc < 1.3 ? exp(-tc * 0.5) : 0.8 * exp(-(tc - 1.3) * 0.5)
            var y = 0.0
            for (i, p) in partials.enumerated() { y += sin(2 * .pi * p * tc) * (i == 0 ? 1 : 0.8) }
            x[k] += Float(amplitude * ding * y)
        }
    }

    private func imu(_ t0: Double, into f: FeatureExtractor) {
        let phi = Self.phoneYaw * .pi / 180
        let forward = SIMD3(cos(phi), sin(phi), 0.0)  // the train's forward, in phone axes
        let left = SIMD3(-sin(phi), cos(phi), 0.0)
        let up = SIMD3(0.0, 0.0, 1.0)
        let gyroBias = SIMD3(0.002, -0.001, 0.0015)
        let wheelRatios = [0.2639, 0.3611, 0.7013, 1.0481]  // Hz per m/s
        let wheelAmp = [0.45, 0.25, 0.3, 0.25]  // x the vibration's rms
        for k in 0..<100 {
            let t = t0 + Double(k) / 100
            let m = motion(t)
            let curvature = onTrack(track.curvature, m.s, track.step)  // rad/m, + turning left
            // vertical vibration (g): its 10..45 Hz level in dB grows with speed
            let highDb = -58 + 24 * tanh(m.v / 18)
            let vib = (pow(10, highDb / 10) / 0.7).squareRoot()
            var bump = vib * rng.gaussian()
            if m.v > 1 {
                for i in 0..<4 {
                    wheelPhase[i] += 2 * .pi * wheelRatios[i] * m.v / 100
                    bump += wheelAmp[i] * vib * sin(wheelPhase[i])
                }
            }
            // specific force (m/s^2): the train's acceleration, a third of the centripetal
            // (the rest is taken by the track's cant), and gravity's reaction; iOS reports
            // its negative in g
            let force = m.a * forward + 0.3 * m.v * m.v * curvature * left + 9.81 * up
            var acc = -force / 9.81
            acc += SIMD3(0.002 * rng.gaussian(), 0.002 * rng.gaussian(), bump)
            f.addAccel(t, acc)
            let yaw = m.v * curvature
            f.addGyro(t, yaw * up + gyroBias + SIMD3(rng.gaussian(), rng.gaussian(), rng.gaussian()) * 0.0049)
            if k % 10 == 0 {
                // phone -> world (x magnetic north, y west, z up): the phone's x axis points
                // the train's heading turned by the phone's angle, less the declination
                let heading = onTrack(track.heading_south, m.s, track.step) + Self.phoneYaw - track.declination
                f.addAttitude(t, simd_quatd(angle: -heading * .pi / 180, axis: up), gravity: SIMD3(0, 0, -1))
            }
            if k == 37 {
                baroDrift += 0.02 * rng.gaussian()
                let alt = onTrack(track.elevation, m.s, track.elev_step) + 4 + baroDrift + 0.15 * rng.gaussian()
                f.addBaro(t, kPa: 101.325 * pow(1 - alt / 44330, 1 / 0.1903))
            }
        }
    }

    // MARK: the sample's place in History

    /// The sample's meta.json: enough for History (a ride with its stops, as if the model
    /// had heard them) and for replay to know it's the sample.
    func meta(startedUnix: Double) -> [String: Any] {
        [
            "sample": true,
            "app_version": "sample",
            "complete": true,
            "started_uptime_s": 0.0,
            "started_unix_s": startedUnix,
            "ended_uptime_s": duration,
            "inferred": [
                "direction": "southbound",
                "station_confidence": 1.0,
                "stops": stops.map {
                    ["station": $0.station, "doors_opened_uptime_s": $0.opened, "doors_closed_uptime_s": $0.closed] as [String: Any]
                },
            ] as [String: Any],
        ]
    }
}

// MARK: - small helpers

/// xoshiro256** with Gaussian draws (Box-Muller, one cached).
private struct Random {
    private var s: (UInt64, UInt64, UInt64, UInt64)
    private var spare: Double?

    init(seed: UInt64) {
        var z = seed
        func next() -> UInt64 {  // splitmix64
            z &+= 0x9E37_79B9_7F4A_7C15
            var x = z
            x = (x ^ (x >> 30)) &* 0xBF58_476D_1CE4_E5B9
            x = (x ^ (x >> 27)) &* 0x94D0_49BB_1331_11EB
            return x ^ (x >> 31)
        }
        s = (next(), next(), next(), next())
    }

    private mutating func next() -> UInt64 {
        let result = ((s.1 &* 5) << 7 | (s.1 &* 5) >> 57) &* 9
        let t = s.1 << 17
        s.2 ^= s.0; s.3 ^= s.1; s.1 ^= s.2; s.0 ^= s.3
        s.2 ^= t
        s.3 = (s.3 << 45) | (s.3 >> 19)
        return result
    }

    mutating func uniform() -> Double { Double(next() >> 11) * 0x1.0p-53 }

    mutating func gaussian() -> Double {
        if let g = spare { spare = nil; return g }
        let u1 = max(uniform(), 1e-300), u2 = uniform()
        let r = (-2 * log(u1)).squareRoot()
        spare = r * sin(2 * .pi * u2)
        return r * cos(2 * .pi * u2)
    }
}

/// A second-order section (RBJ cookbook).
private struct Biquad {
    let b0, b1, b2, a1, a2: Double
    var z1 = 0.0, z2 = 0.0

    /// Band-pass with 0 dB peak gain.
    static func bandpass(f0: Double, q: Double, fs: Double) -> Biquad {
        let w = 2 * .pi * f0 / fs, alpha = sin(w) / (2 * q), a0 = 1 + alpha
        return Biquad(b0: alpha / a0, b1: 0, b2: -alpha / a0, a1: -2 * cos(w) / a0, a2: (1 - alpha) / a0)
    }

    mutating func step(_ x: Double) -> Double {
        let y = b0 * x + z1
        z1 = b1 * x - a1 * y + z2
        z2 = b2 * x - a2 * y
        return y
    }
}
