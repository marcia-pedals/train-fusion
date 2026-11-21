import AVFoundation
import Observation
import UIKit

/// Which train and where on it, as the rider typed it in. Saved in meta.json; the model
/// doesn't look at it.
struct RideInfo: Equatable {
    enum Position: String, CaseIterable { case unspecified, upper, lower, vestibule }

    var trip: Int
    var car: Int
    var position: Position

    /// What's wrong with what was typed, or nil. Deliberately loose: the exact sets of
    /// trip and car numbers change with the timetable and the fleet.
    static func problem(trip: String, car: String) -> String? {
        let digits = { (text: String) in !text.isEmpty && text.allSatisfy { $0.isASCII && $0.isNumber } }
        guard digits(trip), trip.count == 3, "1459".contains(trip.first!) else {
            return "A trip number has 3 digits and starts with 1, 4, 5 or 9."
        }
        guard digits(car), car.count == 3 || car.count == 4, car.first == "3" else {
            return "A car number has 3 or 4 digits and starts with 3."
        }
        return nil
    }
}

/// One recording at a time: audio + motion sensors + barometer + clock log, into
/// Documents/recordings/<id>/. docs/recorder.md describes the files.
@MainActor
@Observable
final class Recorder {
    struct Live {
        var audio = AudioCapture.Status()
        var accelSamples = 0
        var gyroSamples = 0
        var magSamples = 0
        var baroSamples = 0
        var fusedSamples = 0
        var ntp: NTPSample?
    }

    private(set) var activeID: String?
    private(set) var startedAt: Date?
    private(set) var live = Live()
    private(set) var busy = false  // starting or stopping
    var error: String?
    /// Trip and car of the recording in progress (or, typed in before starting, of the
    /// next one). Set with `setRide`.
    private(set) var ride: RideInfo?
    /// What the model believes about the recording in progress (Inference/).
    let inference = InferenceStore()

    @ObservationIgnored private var dir: URL?
    @ObservationIgnored private var started = (uptime: 0.0, unix: 0.0)
    @ObservationIgnored private var audio: AudioCapture?
    @ObservationIgnored private var motion: MotionCapture?
    @ObservationIgnored private var clock: ClockLog?
    @ObservationIgnored private var poll: Timer?
    @ObservationIgnored private var live_: LiveInference?

    var isRecording: Bool { activeID != nil }

    init() {
        // Swiped away while recording: close the files properly (an .m4a that isn't
        // closed can't be played).
        NotificationCenter.default.addObserver(
            forName: UIApplication.willTerminateNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.stopNow() }
        }
    }

    func start() async {
        guard !isRecording, !busy else { return }
        busy = true
        defer { busy = false }
        error = nil
        guard await AVAudioApplication.requestRecordPermission() else {
            error = "Microphone access is off. Turn it on in Settings › Train Fusion."
            return
        }

        let now = Clock.now()
        let id = RecordingID.make(Date(timeIntervalSince1970: now.unix))
        let dir = RideStore.root.appendingPathComponent(id)
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let clock = try ClockLog(dir: dir)
            let motion = try MotionCapture(dir: dir)
            let audio = AudioCapture(dir: dir)
            // the model runs alongside, fed from the same callbacks that write the files
            let inference = LiveInference(store: self.inference, startedUptime: now.uptime)
            audio.onBuffer = { [inference] buffer, uptime in inference.audio(buffer, uptime: uptime) }
            motion.inference = inference
            do {
                // Off the main thread: starting the audio hardware can take a moment.
                try await Task.detached { try audio.start() }.value
            } catch {
                clock.stopNow()
                motion.stop()
                inference.stop()
                throw error
            }
            motion.start()
            (self.clock, self.motion, self.audio, self.live_) = (clock, motion, audio, inference)
        } catch {
            try? FileManager.default.removeItem(at: dir)
            self.error = "Couldn't start: \(error.localizedDescription)"
            return
        }
        self.dir = dir
        started = now
        startedAt = Date(timeIntervalSince1970: now.unix)
        activeID = id
        live = Live()
        writeMeta(ended: nil)  // so a recording that never gets stopped is still described

        poll = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refreshLive() }
        }
    }

    func setRide(_ ride: RideInfo?) {
        self.ride = ride
        writeMeta(ended: nil)  // nothing happens if no recording is in progress yet
    }

    func stop() async {
        guard isRecording, !busy else { return }
        busy = true
        defer { busy = false }
        audio?.stop()
        motion?.stop()
        await clock?.stop(finalNTP: true)
        finish()
    }

    private func stopNow() {
        guard isRecording else { return }
        audio?.stop()
        motion?.stop()
        clock?.stopNow()
        finish()
    }

    private func finish() {
        guard isRecording else { return }
        writeMeta(ended: Clock.now())
        poll?.invalidate()
        live_?.stop()
        (poll, audio, motion, clock, dir, live_) = (nil, nil, nil, nil, nil, nil)
        activeID = nil
        startedAt = nil
        ride = nil  // the next recording is another train
    }

    private func refreshLive() {
        guard let audio, let motion else { return }
        live = Live(audio: audio.status, accelSamples: motion.accelCount,
                    gyroSamples: motion.gyroCount, magSamples: motion.magCount,
                    baroSamples: motion.baroCount, fusedSamples: motion.fusedCount, ntp: clock?.latestNTP)
    }

    /// What the model believed when the recording was stopped: the stops it heard, when
    /// the doors opened and closed at each (the start of each chime), and its best guess
    /// at the stations. Null if it never ran.
    private func inferred() -> Any {
        guard let belief = inference.belief else { return NSNull() }
        let best = belief.stations.first
        return [
            "direction": best.map { $0.southbound ? "southbound" : "northbound" } ?? NSNull(),
            "station_confidence": best?.p ?? NSNull(),  // of the last stop's station and the direction
            "stops": belief.stopLog.map { stop in
                [
                    "station": stop.station ?? NSNull(),
                    "doors_opened_uptime_s": started.uptime + Double(stop.opened),
                    "doors_closed_uptime_s": stop.closed.map { started.uptime + Double($0) } ?? NSNull(),
                ] as [String: Any]
            },
        ] as [String: Any]
    }

    private func writeMeta(ended: (uptime: Double, unix: Double)?) {
        guard let dir, let id = activeID else { return }
        var system = utsname()
        uname(&system)
        let model = withUnsafeBytes(of: &system.machine) { String(cString: $0.bindMemory(to: CChar.self).baseAddress!) }
        let info = Bundle.main.infoDictionary ?? [:]
        let meta: [String: Any] = [
            "format_version": 1,
            "id": id,
            "complete": ended != nil,
            "started_uptime_s": started.uptime,
            "started_unix_s": started.unix,
            "ended_uptime_s": ended?.uptime ?? NSNull(),
            "ended_unix_s": ended?.unix ?? NSNull(),
            "device_model": model,
            "ios_version": UIDevice.current.systemVersion,
            "app_version": "\(info["CFBundleShortVersionString"] ?? "?") (\(info["CFBundleVersion"] ?? "?"))",
            "ntp_server": NTP.server,
            "audio": audio?.meta ?? [:],
            "motion": motion?.meta ?? [:],
            // typed in by the rider; null if they didn't
            "inferred": ended == nil ? NSNull() : inferred(),
            "ride": ride.map { ["trip_number": $0.trip, "car_number": $0.car, "car_position": $0.position.rawValue] as [String: Any] } ?? NSNull(),
        ]
        if let data = try? JSONSerialization.data(withJSONObject: meta, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: dir.appendingPathComponent("meta.json"), options: .atomic)
        }
    }
}
