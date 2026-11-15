import CoreMotion
import Foundation

/// Raw accelerometer and gyroscope samples (not the fused "device motion"), each to its
/// own .csv.gz, stamped with the sensor's own sample time (uptime seconds).
final class MotionCapture {
    static let rateHz = 100.0  // the most iOS gives without a special entitlement

    /// One sensor's file and the serial queue its samples arrive and are written on.
    private final class Stream {
        let queue = OperationQueue()
        let writer: GzipWriter
        private let lock = NSLock()
        private var _count = 0
        private var _firstLatency: Double?

        init(url: URL, header: String) throws {
            writer = try GzipWriter(url: url, header: header)
            queue.maxConcurrentOperationCount = 1
        }

        func add(timestamp: Double, _ x: Double, _ y: Double, _ z: Double) {
            writer.write(String(format: "%.6f,%.6f,%.6f,%.6f\n", timestamp, x, y, z))
            lock.lock()
            if _count == 0 { _firstLatency = Clock.uptime() - timestamp }
            _count += 1
            lock.unlock()
        }

        var count: Int { lock.lock(); defer { lock.unlock() }; return _count }
        /// How long after its timestamp the first sample was delivered: a few ms if the
        /// sensor's clock is the uptime clock, as it should be.
        var firstLatency: Double? { lock.lock(); defer { lock.unlock() }; return _firstLatency }

        func close() {
            queue.addOperation { self.writer.close() }
            queue.waitUntilAllOperationsAreFinished()
        }
    }

    private let manager = CMMotionManager()
    private let accel: Stream
    private let gyro: Stream

    init(dir: URL) throws {
        accel = try Stream(url: dir.appendingPathComponent("accel.csv.gz"), header: "uptime_s,x_g,y_g,z_g\n")
        gyro = try Stream(url: dir.appendingPathComponent("gyro.csv.gz"), header: "uptime_s,x_rad_s,y_rad_s,z_rad_s\n")
    }

    func start() {
        manager.accelerometerUpdateInterval = 1 / Self.rateHz
        manager.gyroUpdateInterval = 1 / Self.rateHz
        manager.startAccelerometerUpdates(to: accel.queue) { [accel] data, _ in
            guard let d = data else { return }
            accel.add(timestamp: d.timestamp, d.acceleration.x, d.acceleration.y, d.acceleration.z)
        }
        manager.startGyroUpdates(to: gyro.queue) { [gyro] data, _ in
            guard let d = data else { return }
            gyro.add(timestamp: d.timestamp, d.rotationRate.x, d.rotationRate.y, d.rotationRate.z)
        }
    }

    func stop() {
        manager.stopAccelerometerUpdates()
        manager.stopGyroUpdates()
        accel.close()
        gyro.close()
    }

    var accelCount: Int { accel.count }
    var gyroCount: Int { gyro.count }

    var meta: [String: Any] {
        [
            "requested_rate_hz": Self.rateHz,
            "accel_samples": accel.count,
            "gyro_samples": gyro.count,
            "accel_first_sample_latency_s": accel.firstLatency as Any? ?? NSNull(),
            "gyro_first_sample_latency_s": gyro.firstLatency as Any? ?? NSNull(),
        ]
    }
}
