import Foundation
import Network

/// Time. Every measurement in a recording is stamped with `uptime`: seconds on the
/// device's monotonic clock (mach_absolute_time; what CoreMotion and Core Audio stamp
/// their samples with). `ClockLog` records how uptime maps to wall-clock time.
enum Clock {
    private static let secondsPerTick: Double = {
        var info = mach_timebase_info_data_t()
        mach_timebase_info(&info)
        return Double(info.numer) / Double(info.denom) / 1e9
    }()

    static func seconds(hostTime: UInt64) -> Double {
        Double(hostTime) * secondsPerTick
    }

    static func uptime() -> Double {
        seconds(hostTime: mach_absolute_time())
    }

    /// The monotonic clock and the system's wall clock (unix seconds), read together.
    static func now() -> (uptime: Double, unix: Double) {
        let before = uptime()
        let unix = Date().timeIntervalSince1970
        let after = uptime()
        return ((before + after) / 2, unix)
    }
}

/// One NTP exchange: at `uptime` the system clock read `unix`, and true time was
/// `unix + offset` (to within about `roundTrip / 2`).
struct NTPSample {
    let uptime: Double
    let unix: Double
    let offset: Double
    let roundTrip: Double
}

enum NTP {
    static let server = "time.apple.com"
    private static let queue = DispatchQueue(label: "ntp")

    /// One SNTP query. Calls `completion` exactly once, on a private queue; nil on timeout
    /// or a bad reply.
    static func query(timeout: Double = 3, completion: @escaping (NTPSample?) -> Void) {
        let connection = NWConnection(host: NWEndpoint.Host(server), port: 123, using: .udp)
        var done = false
        func finish(_ sample: NTPSample?) {  // always on `queue`
            if done { return }
            done = true
            connection.cancel()
            completion(sample)
        }

        // Client request: version 4, mode 3. The server echoes our transmit timestamp (any
        // 8 bytes) as its "originate" field, which ties the reply to this request.
        var request = [UInt8](repeating: 0, count: 48)
        request[0] = 0x23
        for i in 40..<48 { request[i] = UInt8.random(in: 0...255) }
        var sent = (uptime: 0.0, unix: 0.0)

        connection.stateUpdateHandler = { state in
            switch state {
            case .ready:
                sent = Clock.now()
                connection.send(content: Data(request), completion: .contentProcessed { error in
                    if error != nil { finish(nil) }
                })
                connection.receiveMessage { data, _, _, _ in
                    let received = Clock.uptime()
                    guard let reply = data.map([UInt8].init), reply.count >= 48,
                          reply[0] & 0x07 == 4,  // mode: server
                          (1...15).contains(reply[1]),  // stratum 0 is "go away"
                          Array(reply[24..<32]) == Array(request[40..<48])
                    else { return finish(nil) }
                    let serverReceived = timestamp(reply, 32)
                    let serverSent = timestamp(reply, 40)
                    let elapsed = received - sent.uptime
                    let unixAtReceive = sent.unix + elapsed
                    finish(NTPSample(
                        uptime: received,
                        unix: unixAtReceive,
                        offset: ((serverReceived - sent.unix) + (serverSent - unixAtReceive)) / 2,
                        roundTrip: elapsed - (serverSent - serverReceived)))
                }
            case .failed, .cancelled:
                finish(nil)
            default:
                break
            }
        }
        connection.start(queue: queue)
        queue.asyncAfter(deadline: .now() + timeout) { finish(nil) }
    }

    /// A 64-bit NTP timestamp (seconds since 1900, 32.32 fixed point) as unix seconds.
    private static func timestamp(_ bytes: [UInt8], _ at: Int) -> Double {
        let seconds = bytes[at..<at + 4].reduce(UInt64(0)) { $0 << 8 | UInt64($1) }
        let fraction = bytes[at + 4..<at + 8].reduce(UInt64(0)) { $0 << 8 | UInt64($1) }
        return Double(seconds) - 2_208_988_800 + Double(fraction) / 4_294_967_296
    }
}

/// Writes clock.csv: every 10 s, what the system clock reads at an uptime; and, at the
/// start, every 2 min and at the end, an NTP measurement of how far off the system clock is.
final class ClockLog: @unchecked Sendable {  // all state is confined to `queue`
    static let header = "uptime_s,unix_s,ntp_offset_s,ntp_round_trip_s\n"

    private let queue = DispatchQueue(label: "clock-log")
    private var handle: FileHandle?
    private var timer: DispatchSourceTimer?
    private var ticks = 0
    private var latest: NTPSample?

    init(dir: URL) throws {
        let url = dir.appendingPathComponent("clock.csv")
        try Data(Self.header.utf8).write(to: url)
        handle = try FileHandle(forWritingTo: url)
        handle?.seekToEndOfFile()

        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now(), repeating: 10)
        timer.setEventHandler { [weak self] in self?.tick() }
        timer.resume()
        self.timer = timer
    }

    /// The most recent NTP measurement, if any succeeded.
    var latestNTP: NTPSample? { queue.sync { latest } }

    private func tick() {
        let now = Clock.now()
        write(String(format: "%.6f,%.6f,,\n", now.uptime, now.unix))
        // NTP on the first three ticks, then every 2 minutes.
        if ticks < 3 || ticks % 12 == 0 { measure() }
        ticks += 1
    }

    private func measure(then: (() -> Void)? = nil) {
        NTP.query { [weak self] sample in
            guard let self else { return }
            self.queue.async {
                if let s = sample {
                    self.latest = s
                    self.write(String(format: "%.6f,%.6f,%.6f,%.6f\n", s.uptime, s.unix, s.offset, s.roundTrip))
                }
                then?()
            }
        }
    }

    private func write(_ row: String) {
        handle?.write(Data(row.utf8))  // unbuffered: each row is on disk at once
    }

    /// Stops the periodic rows and closes the file; with `finalNTP`, after one last NTP
    /// measurement (up to 3 s).
    func stop(finalNTP: Bool) async {
        await withCheckedContinuation { (done: CheckedContinuation<Void, Never>) in
            queue.async {
                self.timer?.cancel()
                self.timer = nil
                let close = {
                    let now = Clock.now()
                    self.write(String(format: "%.6f,%.6f,,\n", now.uptime, now.unix))
                    try? self.handle?.close()
                    self.handle = nil
                    done.resume()
                }
                if finalNTP { self.measure(then: close) } else { close() }
            }
        }
    }

    /// For app termination: close now, no waiting.
    func stopNow() {
        queue.sync {
            timer?.cancel()
            timer = nil
            try? handle?.close()
            handle = nil
        }
    }
}
