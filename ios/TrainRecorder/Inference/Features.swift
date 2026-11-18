import Accelerate
import Foundation
import simd

/// One second of measurements: what phone-sensor-fusion/features/frames.py computes,
/// recomputed here from the live streams. Second n covers recording time [n, n+1).
struct Frame {
    var second = 0
    var openChime: Float = 0, closeChime: Float = 0  // fraction of the second each chime is present
    var toneSpec: [Float] = []  // audio line spectrum, dB over the local floor
    var rumbleDb: Float = 0
    var accelSpec: [Float] = []  // accelerometer line spectrum
    var vibDb: [Float] = [0, 0]
    var gyroRms: Float = 0  // log10 rad/s
    var accMean = SIMD3<Double>()  // g
    var gyroMean = SIMD3<Double>()  // rad/s
    var baroAlt: Double?  // pressure altitude, m
    var attitude: simd_quatd?  // phone axes -> world (x magnetic north, y west, z up)
}

/// Turns the sensor streams into `Frame`s. Feed it samples in time order per stream.
/// A second comes out in two parts: almost everything as soon as the second is over
/// (`makePromptFrame`), and the accelerometer line spectrum about 3 s later
/// (`makeAccelSpec`), because its 5 s window is centred on the second.
/// Not thread-safe: use from one queue.
final class FeatureExtractor {
    private let d: Model.DSP
    private let fs: Int

    // audio, 16 kHz, sample k at recording time k / fs
    private var decimator: Decimator
    private var a16: [Float] = []
    private var a16Base = 0  // index of a16[0] in the whole stream
    private var fed48 = 0  // 48 kHz samples received so far (including padding)
    private var rumble: SOSFilter
    private var rumbleSum: [Double] = []  // per second, sum of filtered^2
    private var rumbleCount = 0  // 16 kHz samples filtered so far
    private let chimeSpec: SegmentSpectrum
    private let toneSpec: SegmentSpectrum
    private var chimeDb: [Float]
    private var toneDb: [Float], toneMed: [Float], tonePool: [Float]

    // IMU, resampled to 100 Hz from t = 0
    private struct Resampler {
        var prevT = -Double.infinity
        var prev = SIMD3<Double>()
        var out: [SIMD3<Double>] = []
        var base = 0
        var count: Int { base + out.count }

        mutating func add(_ t: Double, _ v: SIMD3<Double>) {
            // np.interp onto the grid k / 100: linear between samples, the first value held before it
            while Double(count) * 0.01 <= t {
                let g = Double(count) * 0.01
                out.append(prevT == -.infinity || t == prevT ? v : prev + (v - prev) * ((g - prevT) / (t - prevT)))
            }
            (prevT, prev) = (t, v)
        }

        subscript(i: Int) -> SIMD3<Double> { out[max(i, base) - base] }

        mutating func trim(before i: Int) {
            guard i > base else { return }
            out.removeFirst(min(i - base, out.count))
            base = i
        }
    }

    private var accel = Resampler()
    private var gyro = Resampler()
    private var mag: [Float] = []  // |accel|, aligned with accel.out
    private var firstMag: Float?
    private var vib: [SOSFilter]
    private var vibSum: [[Double]] = [[], []]
    private let accelSpec: SegmentSpectrum
    private var accDb: [Float], accMed: [Float], accSeg: [Float]

    private var baro: [(t: Double, alt: Double)] = []
    private var attitude: [(t: Double, q: simd_quatd)] = []
    private var attitudeFlip: Bool?  // whether iOS's quaternion has to be inverted to be phone -> world
    private var flipVotes = (keep: 0.0, flip: 0.0, n: 0)

    private(set) var nextPrompt = 0  // the next second whose prompt part is due
    private(set) var nextLate = 0  // the next second whose accelerometer spectrum is due

    init(model: Model) {
        d = model.dsp
        fs = d.fs
        decimator = Decimator(taps: d.decimate_taps)
        rumble = SOSFilter(d.rumble_sos)
        vib = d.vib_sos.map(SOSFilter.init)
        chimeSpec = SegmentSpectrum(window: d.chime.window, nfft: d.chime.nwin, fs: Float(d.fs))
        toneSpec = SegmentSpectrum(window: d.tone.window, nfft: d.tone.nfft, fs: Float(d.fs))
        accelSpec = SegmentSpectrum(window: d.accel.window, nfft: d.accel.nfft, fs: 100)
        chimeDb = [Float](repeating: 0, count: d.chime.nwin / 2 + 1)
        toneDb = [Float](repeating: 0, count: d.tone.bins)
        toneMed = toneDb
        tonePool = toneDb
        accDb = [Float](repeating: 0, count: d.accel.bins)
        accMed = accDb
        accSeg = [Float](repeating: 0, count: d.accel.nwin)
    }

    // MARK: feeding

    /// 48 kHz mono samples whose first sample was captured at recording time `t` (nil:
    /// straight after the previous buffer). Gaps are filled with silence.
    func addAudio48k(_ x: UnsafeBufferPointer<Float>, at t: Double?) {
        if let t {
            let want = Int((t * 48000).rounded())
            if want - fed48 > 2400 || fed48 == 0 {  // the start offset, or a gap of > 50 ms
                let pad = [Float](repeating: 0, count: max(want - fed48, 0))
                pad.withUnsafeBufferPointer { feed($0) }
            }
        }
        feed(x)
    }

    private func feed(_ x: UnsafeBufferPointer<Float>) {
        guard x.count > 0 else { return }
        fed48 += x.count
        let before = a16.count
        decimator.process(x, into: &a16)
        for i in before..<a16.count {
            let y = rumble.step(Double(a16[i]))
            let sec = rumbleCount / fs
            if sec >= rumbleSum.count { rumbleSum.append(0) }
            rumbleSum[sec] += y * y
            rumbleCount += 1
        }
    }

    func addAccel(_ t: Double, _ v: SIMD3<Double>) {
        let before = accel.out.count
        accel.add(t, v)
        for i in before..<accel.out.count {
            let m = simd_length(accel.out[i])
            if firstMag == nil { firstMag = Float(m) }
            mag.append(Float(m))
            let k = accel.base + i
            for b in 0..<2 {
                let y = vib[b].step(m)
                if k / 100 >= vibSum[b].count { vibSum[b].append(0) }
                vibSum[b][k / 100] += y * y
            }
        }
    }

    func addGyro(_ t: Double, _ v: SIMD3<Double>) { gyro.add(t, v) }

    func addBaro(_ t: Double, kPa: Double) {
        baro.append((t, 44330.0 * (1 - pow(kPa / 101.325, 0.1903))))
    }

    /// iOS's attitude quaternion and gravity (phone axes) from one device-motion sample.
    func addAttitude(_ t: Double, _ q: simd_quatd, gravity: SIMD3<Double>) {
        if attitudeFlip == nil {
            // whichever sense of the quaternion sends gravity straight down is phone -> world
            flipVotes.keep += simd_act(q, gravity).z
            flipVotes.flip += simd_act(q.inverse, gravity).z
            flipVotes.n += 1
            if flipVotes.n >= 100 { attitudeFlip = flipVotes.keep > flipVotes.flip }
        }
        attitude.append((t, q))
    }

    // MARK: producing

    /// Whether the prompt part of second `nextPrompt` can be made from what has arrived.
    var promptReady: Bool {
        let n = nextPrompt
        return a16Base + a16.count >= (n + 1) * fs + d.chime.nwin
            && accel.count >= 100 * (n + 1) && gyro.count >= 100 * (n + 1)
    }

    /// Everything but the accelerometer line spectrum (`accelSpec` is left empty).
    func makePromptFrame() -> Frame {
        let n = nextPrompt
        var f = Frame(second: n)
        a16.withUnsafeBufferPointer { audio in
            chimes(n, audio, &f)
            tone(n, audio, &f)
        }
        f.rumbleDb = Float(10 * log10(rumbleSum[n] / Double(fs) + 1e-16))
        for b in 0..<2 { f.vibDb[b] = Float(10 * log10(vibSum[b][n] / 100 + 1e-16)) }

        var sumSq = 0.0
        for k in (100 * n)..<(100 * n + 100) {
            f.accMean += accel[k]
            f.gyroMean += gyro[k]
            sumSq += simd_length_squared(gyro[k])
        }
        f.accMean /= 100
        f.gyroMean /= 100
        f.gyroRms = Float(0.5 * log10(sumSq / 100 + 1e-12))

        // barometer and attitude at the second's middle (the barometer's next sample may
        // not be in yet: then its latest value stands)
        let centre = Double(n) + 0.5
        if !baro.isEmpty {
            while baro.count >= 2, baro[1].t <= centre { baro.removeFirst() }
            if baro.count >= 2, baro[0].t <= centre {
                let (a, b) = (baro[0], baro[1])
                f.baroAlt = a.alt + (b.alt - a.alt) * (centre - a.t) / (b.t - a.t)
            } else {
                f.baroAlt = baro[0].alt
            }
        }
        if let flip = attitudeFlip {
            while attitude.count >= 2, attitude[0].t < centre { attitude.removeFirst() }
            if let a = attitude.first, abs(a.t - centre) < 0.5 { f.attitude = flip ? a.q.inverse : a.q }
        }

        nextPrompt += 1
        // drop what no later second needs
        let keepAudio = nextPrompt * fs - d.chime.nwin - a16Base
        if keepAudio > fs { a16.removeFirst(keepAudio); a16Base += keepAudio }
        if 100 * nextPrompt - gyro.base > 500 { gyro.trim(before: 100 * nextPrompt) }
        return f
    }

    /// Whether the accelerometer line spectrum of second `nextLate` can be made.
    var lateReady: Bool {
        nextLate < nextPrompt && accel.count >= 100 * nextLate + d.accel.nwin - 206
    }

    /// The accelerometer line spectrum of second `nextLate`: 5.12 s centred on its middle.
    func makeAccelSpec() -> [Float] {
        let n = nextLate
        let start = 100 * n - 206
        for i in 0..<d.accel.nwin {
            let k = start + i
            accSeg[i] = k < 0 ? (firstMag ?? 1) : mag[max(k, accel.base) - accel.base]
        }
        accSeg.withUnsafeBufferPointer { accelSpec.db($0, into: &accDb) }
        Filters.median(accDb, size: d.accel.median, into: &accMed)
        vDSP_vsub(accMed, 1, accDb, 1, &accDb, 1, vDSP_Length(accDb.count))
        var out = [Float](repeating: 0, count: accDb.count)
        Filters.maximum(accDb, size: d.accel.pool, into: &out)
        nextLate += 1
        let keepImu = 100 * nextLate - 300
        if keepImu - accel.base > 500 {
            mag.removeFirst(keepImu - accel.base)
            accel.trim(before: keepImu)
        }
        return out
    }

    private func chimes(_ n: Int, _ audio: UnsafeBufferPointer<Float>, _ f: inout Frame) {
        // STFT frames whose middle falls in this second
        let c = d.chime
        let lo = max(0, Int((Double(fs * n - c.nwin / 2) / Double(c.hop)).rounded(.up)))
        let hi = (fs * (n + 1) - c.nwin / 2 - 1) / c.hop
        var on = [0, 0], total = 0
        if hi >= lo {
            for i in lo...hi {
                let s = i * c.hop - a16Base
                chimeSpec.db(UnsafeBufferPointer(rebasing: audio[s..<(s + c.nwin)]), into: &chimeDb)
                total += 1
                for (which, partials) in [c.open, c.close].enumerated() {
                    var present = 0
                    for p in partials where prominence(p) > c.db { present += 1 }
                    if present >= c.min_partials { on[which] += 1 }
                }
            }
        }
        f.openChime = Float(on[0]) / Float(max(total, 1))
        f.closeChime = Float(on[1]) / Float(max(total, 1))
    }

    /// dB of the strongest bin at a partial over the median of the bins to either side.
    private func prominence(_ b: Model.Bins) -> Float {
        var peak = -Float.infinity
        for k in b.in where chimeDb[k] > peak { peak = chimeDb[k] }
        var side = b.side.map { chimeDb[$0] }
        side.sort()
        let m = side.count
        return peak - (m % 2 == 1 ? side[m / 2] : (side[m / 2 - 1] + side[m / 2]) / 2)
    }

    private func tone(_ n: Int, _ audio: UnsafeBufferPointer<Float>, _ f: inout Frame) {
        let t = d.tone
        let per = fs / t.hop!
        f.toneSpec = [Float](repeating: 0, count: t.bins)
        for i in (per * n)..<(per * n + per) {
            let s = i * t.hop! - a16Base
            toneSpec.db(UnsafeBufferPointer(rebasing: audio[s..<(s + t.nwin)]), into: &toneDb)
            Filters.median(toneDb, size: t.median, into: &toneMed)
            vDSP_vsub(toneMed, 1, toneDb, 1, &toneDb, 1, vDSP_Length(t.bins))
            Filters.maximum(toneDb, size: t.pool, into: &tonePool)
            vDSP_vadd(f.toneSpec, 1, tonePool, 1, &f.toneSpec, 1, vDSP_Length(t.bins))
        }
        var scale = 1 / Float(per)
        vDSP_vsmul(f.toneSpec, 1, &scale, &f.toneSpec, 1, vDSP_Length(t.bins))
    }
}
