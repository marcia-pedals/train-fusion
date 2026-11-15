import AVFoundation
import Observation
import UIKit

/// One recording at a time: audio + accelerometer + gyroscope + clock log, into
/// Documents/recordings/<id>/. docs/recorder.md describes the files.
@MainActor
@Observable
final class Recorder {
    struct Live {
        var audio = AudioCapture.Status()
        var accelSamples = 0
        var gyroSamples = 0
        var ntp: NTPSample?
    }

    private(set) var activeID: String?
    private(set) var startedAt: Date?
    private(set) var live = Live()
    private(set) var busy = false  // starting or stopping
    var error: String?

    @ObservationIgnored private var dir: URL?
    @ObservationIgnored private var started = (uptime: 0.0, unix: 0.0)
    @ObservationIgnored private var audio: AudioCapture?
    @ObservationIgnored private var motion: MotionCapture?
    @ObservationIgnored private var clock: ClockLog?
    @ObservationIgnored private var poll: Timer?

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
            error = "Microphone access is off. Turn it on in Settings › Train Recorder."
            return
        }

        let now = Clock.now()
        let id = RecordingID.make(Date(timeIntervalSince1970: now.unix))
        let dir = RecordingStore.root.appendingPathComponent(id)
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let clock = try ClockLog(dir: dir)
            let motion = try MotionCapture(dir: dir)
            let audio = AudioCapture(dir: dir)
            do {
                // Off the main thread: starting the audio hardware can take a moment.
                try await Task.detached { try audio.start() }.value
            } catch {
                clock.stopNow()
                motion.stop()
                throw error
            }
            motion.start()
            (self.clock, self.motion, self.audio) = (clock, motion, audio)
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
        (poll, audio, motion, clock, dir) = (nil, nil, nil, nil, nil)
        activeID = nil
        startedAt = nil
    }

    private func refreshLive() {
        guard let audio, let motion else { return }
        live = Live(audio: audio.status, accelSamples: motion.accelCount,
                    gyroSamples: motion.gyroCount, ntp: clock?.latestNTP)
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
        ]
        if let data = try? JSONSerialization.data(withJSONObject: meta, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: dir.appendingPathComponent("meta.json"), options: .atomic)
        }
    }
}
