import AVFoundation

/// The microphone, to AAC in .m4a, with the capture time of every buffer logged.
///
/// Audio is a sequence of segments (audio_000.m4a, audio_001.m4a, ...): normally one, but
/// whenever capture has to restart (a phone call ends, the audio route changes) a new
/// segment starts. audio_timing.csv.gz has one row per captured buffer (~0.1 s): which
/// segment, the buffer's first frame within the segment's file, and the uptime at which
/// that frame was captured. So frames map to time exactly, and gaps are explicit.
final class AudioCapture: @unchecked Sendable {  // see `control` and `lock`
    static let bitRatePerChannel = 96_000

    struct Segment {
        let file: String
        let format: AVAudioFormat
        var frames: Int64 = 0
        var firstUptime: Double?
    }

    private let dir: URL
    /// Called on Core Audio's thread with every captured buffer and the uptime of its first
    /// frame (the live inference listens here). Set before `start`.
    var onBuffer: ((AVAudioPCMBuffer, Double?) -> Void)?
    /// start/stop/restart all run here; the tap callback runs on Core Audio's thread and
    /// shares the state below through `lock`.
    private let control = DispatchQueue(label: "audio-control")
    private var engine: AVAudioEngine?
    private var running = false
    private var watchdog: DispatchSourceTimer?
    private var observers: [NSObjectProtocol] = []

    private let lock = NSLock()
    private var file: AVAudioFile?
    private var timing: GzipWriter?
    private var segments: [Segment] = []
    private var lastBufferUptime = 0.0
    private var firstBufferLatency: Double?
    private var lastError: String?
    private var route: [String: Any] = [:]

    init(dir: URL) {
        self.dir = dir
    }

    func start() throws {
        try control.sync {
            let session = AVAudioSession.sharedInstance()
            // .measurement: no automatic gain control or voice processing, so levels and
            //   tones are as the mic heard them.
            // .mixWithOthers (needs .playAndRecord): music or a podcast keeps playing, and
            //   starting one doesn't interrupt the recording. The other two options keep
            //   that playback on its usual output (Bluetooth headphones / the speaker).
            try session.setCategory(.playAndRecord, mode: .measurement,
                                    options: [.mixWithOthers, .allowBluetoothA2DP, .defaultToSpeaker])
            try? session.setPreferredSampleRate(48_000)
            // Always the phone's own mic, even with a headset plugged in.
            if let mic = session.availableInputs?.first(where: { $0.portType == .builtInMic }) {
                try? session.setPreferredInput(mic)
            }
            timing = try GzipWriter(url: dir.appendingPathComponent("audio_timing.csv.gz"),
                                    header: "segment,frame,frames,uptime_s\n")
            running = true
            do {
                try startEngine()
            } catch {
                running = false
                timing?.close()
                throw error
            }

            // Anything that stops the engine: start it again (as a new segment).
            let center = NotificationCenter.default
            for name in [AVAudioSession.interruptionNotification,
                         AVAudioSession.mediaServicesWereResetNotification,
                         .AVAudioEngineConfigurationChange] {
                observers.append(center.addObserver(forName: name, object: nil, queue: nil) { [weak self] _ in
                    self?.control.async { self?.restartIfStopped() }
                })
            }
            // ... and keep trying, e.g. for as long as a phone call lasts.
            let timer = DispatchSource.makeTimerSource(queue: control)
            timer.schedule(deadline: .now() + 3, repeating: 3)
            timer.setEventHandler { [weak self] in self?.restartIfStopped() }
            timer.resume()
            watchdog = timer
        }
    }

    func stop() {
        control.sync {
            running = false
            watchdog?.cancel()
            watchdog = nil
            observers.forEach(NotificationCenter.default.removeObserver)
            observers = []
            stopEngine()
            lock.lock()
            timing?.close()
            timing = nil
            lock.unlock()
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        }
    }

    private func startEngine() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setActive(true)
        // A fresh engine each time: one that lived through a route change can hold on to
        // the old hardware format.
        let engine = AVAudioEngine()
        // format nil = whatever the input delivers; the file is opened to match the first buffer.
        engine.inputNode.installTap(onBus: 0, bufferSize: 4800, format: nil) { [weak self] buffer, when in
            self?.capture(buffer, when)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            engine.inputNode.removeTap(onBus: 0)
            throw error
        }
        self.engine = engine

        // Noted now: once the session is deactivated at the end, the route is gone.
        let input = session.currentRoute.inputs.first
        let route: [String: Any] = [
            "input_port": input?.portName ?? NSNull(),
            "input_port_type": input?.portType.rawValue ?? NSNull(),
            "input_data_source": input?.selectedDataSource?.dataSourceName ?? NSNull(),
            "input_latency_s": session.inputLatency,
            "io_buffer_duration_s": session.ioBufferDuration,
        ]
        lock.lock()
        self.route = route
        lock.unlock()
    }

    private func stopEngine() {
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
        engine = nil
        lock.lock()
        file = nil  // closes it; the next buffer starts a new segment
        lock.unlock()
    }

    private func restartIfStopped() {
        guard running, engine?.isRunning != true else { return }
        stopEngine()
        do {
            try startEngine()
            setError(nil)
        } catch {
            setError("audio stopped: \(error.localizedDescription)")
        }
    }

    private func setError(_ message: String?) {
        lock.lock()
        lastError = message
        lock.unlock()
    }

    private func capture(_ buffer: AVAudioPCMBuffer, _ when: AVAudioTime) {
        let arrived = Clock.uptime()
        lock.lock()
        defer { lock.unlock() }
        guard let timing, buffer.frameLength > 0 else { return }
        do {
            if file == nil || segments.last?.format != buffer.format {
                let name = String(format: "audio_%03d.m4a", segments.count)
                let format = buffer.format
                file = try AVAudioFile(
                    forWriting: dir.appendingPathComponent(name),
                    settings: [
                        AVFormatIDKey: kAudioFormatMPEG4AAC,
                        AVSampleRateKey: format.sampleRate,
                        AVNumberOfChannelsKey: format.channelCount,
                        AVEncoderBitRateKey: Self.bitRatePerChannel * Int(format.channelCount),
                    ],
                    commonFormat: format.commonFormat,
                    interleaved: format.isInterleaved)
                segments.append(Segment(file: name, format: format))
            }
            let index = segments.count - 1
            // The host time is when the buffer's first frame was captured.
            let uptime = when.isHostTimeValid ? Clock.seconds(hostTime: when.hostTime) : nil
            timing.write("\(index),\(segments[index].frames),\(buffer.frameLength),"
                + (uptime.map { String(format: "%.6f", $0) } ?? "") + "\n")
            try file?.write(from: buffer)
            onBuffer?(buffer, uptime)
            if segments[index].frames == 0 { segments[index].firstUptime = uptime }
            segments[index].frames += Int64(buffer.frameLength)
            if firstBufferLatency == nil, let uptime { firstBufferLatency = arrived - uptime }
            lastBufferUptime = arrived
            lastError = nil
        } catch {
            file = nil
            lastError = "audio write failed: \(error.localizedDescription)"
        }
    }

    // MARK: for the UI and meta.json

    struct Status {
        var capturing = false  // a buffer arrived within the last second
        var seconds = 0.0
        var sampleRate = 0.0
        var segments = 0
        var error: String?
    }

    var status: Status {
        lock.lock()
        defer { lock.unlock() }
        return Status(
            capturing: Clock.uptime() - lastBufferUptime < 1,
            seconds: segments.reduce(0) { $0 + Double($1.frames) / $1.format.sampleRate },
            sampleRate: segments.last?.format.sampleRate ?? 0,
            segments: segments.count,
            error: lastError)
    }

    var meta: [String: Any] {
        lock.lock()
        defer { lock.unlock() }
        return [
            "codec": "AAC-LC in MPEG-4 (.m4a)",
            "bit_rate_per_channel": Self.bitRatePerChannel,
            "session_mode": "measurement",
            "first_buffer_latency_s": firstBufferLatency ?? NSNull(),
            "segments": segments.map { s -> [String: Any] in
                [
                    "file": s.file,
                    "sample_rate": s.format.sampleRate,
                    "channels": Int(s.format.channelCount),
                    "frames": s.frames,
                    "first_frame_uptime_s": s.firstUptime ?? NSNull(),
                ]
            },
        ].merging(route) { a, _ in a }
    }
}
