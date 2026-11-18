import Accelerate
import Foundation

/// Power spectrum of a real signal, |X_k|^2 for k = 0...n/2, n a power of two.
final class PowerFFT {
    let n: Int
    private let log2n: vDSP_Length
    private let setup: FFTSetup
    private var re: [Float]
    private var im: [Float]

    init(n: Int) {
        precondition(n > 0 && n & (n - 1) == 0)
        self.n = n
        log2n = vDSP_Length(n.trailingZeroBitCount)
        setup = vDSP_create_fftsetup(log2n, FFTRadix(kFFTRadix2))!
        re = [Float](repeating: 0, count: n / 2)
        im = [Float](repeating: 0, count: n / 2)
    }

    deinit { vDSP_destroy_fftsetup(setup) }

    /// `x`: n samples (already windowed / zero-padded). `out`: n/2 + 1 values.
    func power(_ x: [Float], into out: inout [Float]) {
        let half = n / 2
        x.withUnsafeBufferPointer { xp in
            re.withUnsafeMutableBufferPointer { rp in
                im.withUnsafeMutableBufferPointer { ip in
                    var split = DSPSplitComplex(realp: rp.baseAddress!, imagp: ip.baseAddress!)
                    xp.baseAddress!.withMemoryRebound(to: DSPComplex.self, capacity: half) {
                        vDSP_ctoz($0, 2, &split, 1, vDSP_Length(half))
                    }
                    vDSP_fft_zrip(setup, &split, 1, log2n, FFTDirection(FFT_FORWARD))
                }
            }
        }
        // vDSP's packed real FFT: everything is 2x the true transform; re[0] is DC, im[0] is Nyquist.
        out[0] = re[0] * re[0] / 4
        out[half] = im[0] * im[0] / 4
        for k in 1..<half { out[k] = (re[k] * re[k] + im[k] * im[k]) / 4 }
    }
}

/// scipy.signal.spectrogram's power spectral density in dB for one segment: mean removed,
/// windowed, zero-padded to nfft; 10 log10(density + 1e-16).
final class SegmentSpectrum {
    private let window: [Float]
    private let fft: PowerFFT
    private let scale: Float
    private var buf: [Float]
    private var pow: [Float]
    let bins: Int

    init(window: [Float], nfft: Int, fs: Float) {
        self.window = window
        fft = PowerFFT(n: nfft)
        var sumSq: Float = 0
        vDSP_svesq(window, 1, &sumSq, vDSP_Length(window.count))
        scale = 1 / (fs * sumSq)
        buf = [Float](repeating: 0, count: nfft)
        pow = [Float](repeating: 0, count: nfft / 2 + 1)
        bins = nfft / 2 + 1
    }

    /// dB of the first `out.count` bins of the segment `x` (window.count samples).
    func db(_ x: UnsafeBufferPointer<Float>, into out: inout [Float]) {
        let m = window.count
        var mean: Float = 0
        vDSP_meanv(x.baseAddress!, 1, &mean, vDSP_Length(m))
        var neg = -mean
        buf.withUnsafeMutableBufferPointer { bp in
            vDSP_vsadd(x.baseAddress!, 1, &neg, bp.baseAddress!, 1, vDSP_Length(m))
            vDSP_vmul(bp.baseAddress!, 1, window, 1, bp.baseAddress!, 1, vDSP_Length(m))
            if bp.count > m { vDSP_vclr(bp.baseAddress! + m, 1, vDSP_Length(bp.count - m)) }
        }
        fft.power(buf, into: &pow)
        let last = bins - 1
        for k in 0..<out.count {
            let s = pow[k] * scale * ((k == 0 || k == last) ? 1 : 2)
            out[k] = 10 * log10f(s + 1e-16)
        }
    }
}

/// A cascade of biquads (scipy's "sos" rows: b0 b1 b2 a0 a1 a2), run sample by sample.
struct SOSFilter {
    private let c: [[Double]]
    private var z1: [Double]
    private var z2: [Double]

    init(_ sos: [[Double]]) {
        c = sos
        z1 = [Double](repeating: 0, count: sos.count)
        z2 = z1
    }

    mutating func step(_ x: Double) -> Double {
        var y = x
        for s in 0..<c.count {
            let b = c[s]
            let out = b[0] * y + z1[s]
            z1[s] = b[1] * y - b[4] * out + z2[s]
            z2[s] = b[2] * y - b[5] * out
            y = out
        }
        return y
    }
}

enum Filters {
    /// Running median over `size` (odd) neighbours, the edges repeating the end values.
    static func median(_ x: [Float], size: Int, into out: inout [Float]) {
        let n = x.count, h = size / 2
        var win = [Float]()
        win.reserveCapacity(size)
        for k in -h...h { win.append(x[min(max(k, 0), n - 1)]) }
        win.sort()
        out[0] = win[h]
        for i in 1..<n {
            let leaving = x[min(max(i - 1 - h, 0), n - 1)], entering = x[min(i + h, n - 1)]
            if leaving != entering {
                win.remove(at: lowerBound(win, leaving))
                win.insert(entering, at: lowerBound(win, entering))
            }
            out[i] = win[h]
        }
    }

    private static func lowerBound(_ a: [Float], _ v: Float) -> Int {
        var lo = 0, hi = a.count
        while lo < hi {
            let mid = (lo + hi) / 2
            if a[mid] < v { lo = mid + 1 } else { hi = mid }
        }
        return lo
    }

    /// Running maximum over `size` (odd) neighbours, the edges mirrored (d c b a | a b c d).
    static func maximum(_ x: [Float], size: Int, into out: inout [Float]) {
        let n = x.count, h = size / 2
        for i in 0..<n {
            var m = -Float.infinity
            for k in (i - h)...(i + h) {
                let j = k < 0 ? -k - 1 : (k >= n ? 2 * n - 1 - k : k)
                if x[j] > m { m = x[j] }
            }
            out[i] = m
        }
    }
}

/// 48 kHz -> 16 kHz: low-pass FIR, keep every third sample; fed in pieces.
struct Decimator {
    private let taps: [Float]
    private var pending: [Float] = []

    init(taps: [Float]) { self.taps = taps }

    mutating func process(_ x: UnsafeBufferPointer<Float>, into out: inout [Float]) {
        pending.append(contentsOf: x)
        let n = (pending.count - taps.count) / 3 + 1
        guard pending.count >= taps.count, n > 0 else { return }
        let start = out.count
        out.append(contentsOf: repeatElement(0, count: n))
        out.withUnsafeMutableBufferPointer { op in
            vDSP_desamp(pending, 3, taps, op.baseAddress! + start, vDSP_Length(n), vDSP_Length(taps.count))
        }
        pending.removeFirst(3 * n)
    }
}
