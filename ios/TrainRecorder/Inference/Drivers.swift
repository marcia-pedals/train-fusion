import AVFoundation
import Foundation
import Observation
import simd
import zlib

/// What a screen shows: the latest belief.
@MainActor
@Observable
final class InferenceStore {
    private(set) var belief: Belief?
    var message: String?

    func publish(_ beliefs: [Belief]) {
        if let last = beliefs.last { belief = last }
    }

    func reset() {
        belief = nil
        message = nil
    }
}

enum SharedModel {
    /// Loaded once (a quarter of a megabyte of JSON).
    static let result = Result { try Model.load() }
}

/// Inference alongside a recording in progress. The sensor callbacks only append to
/// buffers under a lock; once a second a timer on a utility queue drains them, runs the
/// model for whatever seconds are complete, and publishes the newest belief.
final class LiveInference: @unchecked Sendable {
    private let store: InferenceStore
    private let t0: Double
    private let queue = DispatchQueue(label: "inference", qos: .utility)
    private let lock = NSLock()
    private var engine: InferenceEngine?
    private var timer: DispatchSourceTimer?

    private var audio: [(t: Double?, x: [Float])] = []
    private var accel: [(Double, SIMD3<Double>)] = []
    private var gyro: [(Double, SIMD3<Double>)] = []
    private var baro: [(Double, Double)] = []
    private var attitude: [(Double, simd_quatd, SIMD3<Double>)] = []
    private var badAudio = false

    @MainActor
    init(store: InferenceStore, startedUptime: Double) {
        self.store = store
        t0 = startedUptime
        store.reset()
        switch SharedModel.result {
        case .success(let model): engine = InferenceEngine(model: model)
        case .failure(let error): store.message = "No model: \(error.localizedDescription)"
        }
        guard engine != nil else { return }
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + 1, repeating: 1, leeway: .milliseconds(200))
        timer.setEventHandler { [weak self] in self?.pump() }
        timer.resume()
        self.timer = timer
    }

    func stop() {
        timer?.cancel()
        timer = nil
    }

    // MARK: from the capture threads

    func audio(_ buffer: AVAudioPCMBuffer, uptime: Double?) {
        guard let data = buffer.floatChannelData, buffer.format.sampleRate == 48000 else {
            lock.lock(); badAudio = true; lock.unlock()
            return
        }
        let x = Array(UnsafeBufferPointer(start: data[0], count: Int(buffer.frameLength)))
        lock.lock()
        audio.append((uptime.map { $0 - t0 }, x))
        lock.unlock()
    }

    func accel(_ t: Double, _ x: Double, _ y: Double, _ z: Double) {
        lock.lock(); accel.append((t - t0, SIMD3(x, y, z))); lock.unlock()
    }

    func gyro(_ t: Double, _ x: Double, _ y: Double, _ z: Double) {
        lock.lock(); gyro.append((t - t0, SIMD3(x, y, z))); lock.unlock()
    }

    func baro(_ t: Double, kPa: Double) {
        lock.lock(); baro.append((t - t0, kPa)); lock.unlock()
    }

    func attitude(_ t: Double, _ q: simd_quatd, gravity: SIMD3<Double>) {
        lock.lock(); attitude.append((t - t0, q, gravity)); lock.unlock()
    }

    // MARK: on the inference queue

    private func pump() {
        guard let engine else { return }
        lock.lock()
        let (a, ac, gy, ba, at, bad) = (audio, accel, gyro, baro, attitude, badAudio)
        audio.removeAll(keepingCapacity: true); accel.removeAll(keepingCapacity: true); gyro.removeAll(keepingCapacity: true)
        baro.removeAll(keepingCapacity: true); attitude.removeAll(keepingCapacity: true)
        lock.unlock()
        let f = engine.features
        for chunk in a { chunk.x.withUnsafeBufferPointer { f.addAudio48k($0, at: chunk.t) } }
        for s in ac { f.addAccel(s.0, s.1) }
        for s in gy { f.addGyro(s.0, s.1) }
        for s in ba { f.addBaro(s.0, kPa: s.1) }
        for s in at { f.addAttitude(s.0, s.1, gravity: s.2) }
        let beliefs = engine.process()
        let store = store
        if !beliefs.isEmpty || bad {
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    store.publish(beliefs)
                    if bad { store.message = "Inference needs 48 kHz audio from the phone's own microphone." }
                }
            }
        }
    }
}

/// What the replay loop reads as it goes, so that pausing, changing speed and skipping
/// take effect straight away.
final class ReplayControl: @unchecked Sendable {
    enum Command { case nextStop, previousStop, restart }

    private let lock = NSLock()
    private var _paused = false
    private var _speed: Double? = 16
    private var command: Command?

    var paused: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _paused }
        set { lock.lock(); _paused = newValue; lock.unlock() }
    }

    /// Recording seconds per second; nil = as fast as it goes.
    var speed: Double? {
        get { lock.lock(); defer { lock.unlock() }; return _speed }
        set { lock.lock(); _speed = newValue; lock.unlock() }
    }

    func send(_ c: Command) { lock.lock(); command = c; lock.unlock() }
    var hasCommand: Bool { lock.lock(); defer { lock.unlock() }; return command != nil }
    func takeCommand() -> Command? { lock.lock(); defer { lock.unlock() }; let c = command; command = nil; return c }
}

/// Plays an existing recording through the same engine, at a chosen speed, to try the
/// model without being on a train.
@MainActor
@Observable
final class ReplayPlayer {
    private(set) var recording: Recording?  // what is loaded
    private(set) var paused = false
    private(set) var atEnd = false
    private(set) var position = 0  // seconds into the recording
    /// Recording seconds per second; 0 = as fast as it goes.
    var speed = 16.0 { didSet { control.speed = speed > 0 ? speed : nil } }
    let store = InferenceStore()
    private let control = ReplayControl()
    private var task: Task<Void, Never>?

    var playing: Bool { recording != nil && !paused && !atEnd }

    /// Load `recording` and play it from the beginning. `dump`: also write every second's
    /// features and belief to Documents/replay_dump/<id>.jsonl.
    func play(_ recording: Recording, dump: Bool = false) {
        exit()
        store.reset()
        self.recording = recording
        control.paused = false
        control.speed = speed > 0 ? speed : nil
        _ = control.takeCommand()
        let control = control
        task = Task.detached(priority: .utility) { [weak self] in
            do {
                try await Replay.run(recording, control: control, dump: dump) { update in
                    await MainActor.run {
                        guard let self, self.recording?.id == recording.id else { return }
                        if update.restarted { self.store.reset() }
                        self.store.publish(update.beliefs)
                        self.position = update.second
                        self.atEnd = update.atEnd
                    }
                }
            } catch is CancellationError {
            } catch {
                await MainActor.run { self?.store.message = "Replay failed: \(error.localizedDescription)" }
            }
        }
    }

    /// Pause / resume; at the end, start over.
    func toggle() {
        guard recording != nil else { return }
        if atEnd {
            paused = false
            control.paused = false
            control.send(.restart)
        } else {
            paused.toggle()
            control.paused = paused
        }
    }

    /// Skip to the next door opening the model hears (or the end of the recording).
    func nextStop() { control.send(.nextStop) }

    /// Back to the latest door opening before now (or the beginning).
    func previousStop() { control.send(.previousStop) }

    /// Leave replay altogether.
    func exit() {
        task?.cancel()
        task = nil
        recording = nil
        (paused, atEnd, position) = (false, false, 0)
    }
}

enum Replay {
    struct Failure: LocalizedError { let errorDescription: String? }

    struct Update: Sendable {
        var beliefs: [Belief]
        var second: Int
        var restarted = false  // the beliefs start over from the beginning of the recording
        var atEnd = false
    }

    private enum Target { case stops(Int), second(Int) }

    /// Runs until cancelled: plays to the end at the control's speed, obeys its commands,
    /// and at the end waits for more. The model only runs forwards, so going back means
    /// starting over and running flat out to the place (a whole ride takes a few seconds).
    static func run(_ recording: Recording, control: ReplayControl, dump: Bool,
                    report: (Update) async -> Void) async throws {
        let model = try SharedModel.result.get()
        let source: ReplaySource = try isSample(recording.url) ? SampleRide(model: model) : FileSource(recording.url)
        let duration = source.duration

        var out: FileHandle?
        if dump {
            let folder = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("replay_dump")
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let url = folder.appendingPathComponent("\(recording.id).jsonl")
            FileManager.default.createFile(atPath: url.path, contents: nil)
            out = try FileHandle(forWritingTo: url)
        }
        defer { try? out?.close() }

        var engine = InferenceEngine(model: model)
        var second = 0
        var unsent: [Belief] = []
        var restarted = false
        var stops = 0  // heard so far in this pass
        var stopTimes: [Int] = []  // when each was heard, over all passes
        var target: Target?  // running flat out towards this

        func restart() {
            engine = InferenceEngine(model: model)
            source.rewind()
            (second, stops) = (0, 0)
            unsent.removeAll()
            restarted = true
        }

        while true {
            try Task.checkCancellation()
            switch control.takeCommand() {
            case .nextStop: target = .stops(stops + 1)
            case .previousStop:
                let t = stopTimes.last { $0 < second - 5 } ?? 0
                restart()
                target = .second(t)
            case .restart:
                restart()
                target = nil
            case nil: break
            }
            if Double(second) >= duration {  // the end: say so, and wait for a command
                target = nil
                await report(Update(beliefs: unsent, second: second, restarted: restarted, atEnd: true))
                unsent.removeAll(); restarted = false
                while !control.hasCommand {
                    try Task.checkCancellation()
                    try await Task.sleep(nanoseconds: 100_000_000)
                }
                continue
            }
            let tick = Clock.uptime()
            source.feed(second, into: engine.features)
            second += 1

            while let b = engine.processNext() {
                unsent.append(b)
                if b.stops > stops {
                    stops = b.stops
                    if b.second > (stopTimes.last ?? -1) { stopTimes.append(b.second) }
                }
                if let out, let fr = engine.lastFrame { out.write(dumpLine(b, fr, engine.lastAccel, engine.lastAccelSpec)) }
            }
            var arrived = false
            switch target {
            case .stops(let k): arrived = stops >= k
            case .second(let t): arrived = second >= t
            case nil: break
            }
            if arrived { target = nil }
            let speed = control.speed
            // the screen needs no more than a few updates a second, however fast this runs
            if arrived || (target == nil && speed != nil) || second % 20 == 0 {
                await report(Update(beliefs: unsent, second: second, restarted: restarted))
                unsent.removeAll(keepingCapacity: true); restarted = false
            }
            if target == nil {
                if let speed {
                    let wait = 1 / speed - (Clock.uptime() - tick)
                    if wait > 0 { try await Task.sleep(nanoseconds: UInt64(wait * 1e9)) }
                }
                while control.paused, !control.hasCommand {
                    try Task.checkCancellation()
                    try await Task.sleep(nanoseconds: 100_000_000)
                }
            }
        }
    }

    /// Whether the recording in `dir` is the generated sample ride (its meta.json says so).
    static func isSample(_ dir: URL) -> Bool {
        guard let meta = (try? JSONSerialization.jsonObject(with: Data(contentsOf: dir.appendingPathComponent("meta.json")))) as? [String: Any]
        else { return false }
        return meta["sample"] as? Bool == true
    }

    private static func dumpLine(_ b: Belief, _ f: Frame, _ accel: InferenceEngine.Accel?, _ late: (second: Int, spec: [Float])?) -> Data {
        var o: [String: Any] = [
            "n": b.second, "v": b.speedMean, "v_sd": b.speedSd, "mode": b.modeP, "phone": b.phoneP,
            "dist": b.distanceSinceStop, "stops": b.stops, "legs": b.legs,
            "stations": b.stations.map { ["name": $0.name, "south": $0.southbound, "p": $0.p] as [String: Any] },
            "stop_log": b.stopLog.map { ["opened": $0.opened, "closed": $0.closed ?? NSNull(), "station": $0.station ?? NSNull()] as [String: Any] },
            "open_chime": f.openChime, "close_chime": f.closeChime, "rumble_db": f.rumbleDb,
            "vib_low": f.vibDb[0], "vib_high": f.vibDb[1], "gyro_rms": f.gyroRms,
            "acc_mean": [f.accMean.x, f.accMean.y, f.accMean.z],
            "tone_spec": [150, 300, 475, 700].map { f.toneSpec[$0] },
        ]
        // the accelerometer spectrum is for an earlier second than the rest of the line
        if let late { o["accel_n"] = late.second; o["accel_spec"] = [100, 250, 500].map { late.spec[$0] } }
        if let alt = f.baroAlt { o["baro_alt"] = alt }
        if let e = b.toneEvidence { o["tone_ev"] = e }
        if let e = b.wheelEvidence { o["wheel_ev"] = e }
        if let accel { o["a_long"] = accel.a; o["a_sign"] = accel.sign; o["a_offset"] = accel.offset }
        var data = (try? JSONSerialization.data(withJSONObject: o)) ?? Data()
        data.append(0x0A)
        return data
    }
}

/// Where a replay's sensor streams come from: a recording's files, or the sample ride.
protocol ReplaySource: AnyObject {
    /// Seconds.
    var duration: Double { get }
    /// Feed one second of every stream (seconds are asked for in order from 0, and again
    /// from 0 after `rewind`).
    func feed(_ second: Int, into f: FeatureExtractor)
    func rewind()
}

/// A recording's files: its audio and the sensors' .csv.gz.
final class FileSource: ReplaySource {
    let duration: Double
    private let t0: Double, audioStart: Double
    private let file: AVAudioFile
    private let buffer: AVAudioPCMBuffer
    private let accel: [Double], gyro: [Double], baro: [Double], fused: [Double]
    private var (ia, ig, ib, ifu) = (0, 0, 0, 0)

    init(_ dir: URL) throws {
        guard let meta = (try? JSONSerialization.jsonObject(with: Data(contentsOf: dir.appendingPathComponent("meta.json")))) as? [String: Any],
              let t0 = meta["started_uptime_s"] as? Double,
              let segment = ((meta["audio"] as? [String: Any])?["segments"] as? [[String: Any]])?.first,
              let name = segment["file"] as? String, let audioStart = segment["first_frame_uptime_s"] as? Double
        else { throw Replay.Failure(errorDescription: "This recording's meta.json is incomplete.") }
        (self.t0, self.audioStart) = (t0, audioStart)
        duration = ((meta["ended_uptime_s"] as? Double) ?? t0) - t0
        file = try AVAudioFile(forReading: dir.appendingPathComponent(name))
        guard file.processingFormat.sampleRate == 48000,
              let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 48000)
        else { throw Replay.Failure(errorDescription: "Replay needs 48 kHz audio.") }
        self.buffer = buffer
        accel = GzipCSV.read(dir.appendingPathComponent("accel.csv.gz"), columns: [0, 1, 2, 3])
        gyro = GzipCSV.read(dir.appendingPathComponent("gyro.csv.gz"), columns: [0, 1, 2, 3])
        baro = GzipCSV.read(dir.appendingPathComponent("baro.csv.gz"), columns: [0, 1])
        fused = GzipCSV.read(dir.appendingPathComponent("device_motion.csv.gz"), columns: [0, 1, 2, 3, 4, 8, 9, 10])
    }

    func rewind() {
        file.framePosition = 0
        (ia, ig, ib, ifu) = (0, 0, 0, 0)
    }

    func feed(_ second: Int, into f: FeatureExtractor) {
        buffer.frameLength = 0
        try? file.read(into: buffer, frameCount: 48000)
        if buffer.frameLength > 0, let data = buffer.floatChannelData {
            f.addAudio48k(UnsafeBufferPointer(start: data[0], count: Int(buffer.frameLength)),
                          at: second == 0 ? audioStart - t0 : nil)
        }
        let until = t0 + Double(second + 1)
        while ia < accel.count / 4, accel[4 * ia] < until {
            f.addAccel(accel[4 * ia] - t0, SIMD3(accel[4 * ia + 1], accel[4 * ia + 2], accel[4 * ia + 3])); ia += 1
        }
        while ig < gyro.count / 4, gyro[4 * ig] < until {
            f.addGyro(gyro[4 * ig] - t0, SIMD3(gyro[4 * ig + 1], gyro[4 * ig + 2], gyro[4 * ig + 3])); ig += 1
        }
        while ib < baro.count / 2, baro[2 * ib] < until { f.addBaro(baro[2 * ib] - t0, kPa: baro[2 * ib + 1]); ib += 1 }
        while ifu < fused.count / 8, fused[8 * ifu] < until {
            let r = 8 * ifu
            f.addAttitude(fused[r] - t0, simd_quatd(ix: fused[r + 1], iy: fused[r + 2], iz: fused[r + 3], r: fused[r + 4]),
                          gravity: SIMD3(fused[r + 5], fused[r + 6], fused[r + 7]))
            ifu += 1
        }
    }
}

enum GzipCSV {
    /// The chosen columns of a numeric .csv.gz (header skipped), row after row in one flat
    /// array. A missing file gives an empty array (older recordings lack some sensors).
    static func read(_ url: URL, columns: [Int]) -> [Double] {
        guard let file = gzopen(url.path, "rb") else { return [] }
        defer { gzclose(file) }
        var out: [Double] = []
        var line = [CChar](repeating: 0, count: 1024)
        var first = true
        let last = columns.max() ?? 0
        while gzgets(file, &line, Int32(line.count)) != nil {
            if first { first = false; continue }
            line.withUnsafeBufferPointer { p in
                var cursor = p.baseAddress!
                var col = 0, want = 0
                while col <= last, cursor.pointee != 0, cursor.pointee != 10 {
                    var end: UnsafeMutablePointer<CChar>?
                    let v = strtod(cursor, &end)
                    guard let e = end, e != cursor else { break }
                    if want < columns.count, columns[want] == col { out.append(v); want += 1 }
                    cursor = UnsafePointer(e)
                    if cursor.pointee == 44 { cursor += 1 }
                    col += 1
                }
                if want < columns.count { out.removeLast(want) }  // a cut-off last line
            }
        }
        return out
    }
}
