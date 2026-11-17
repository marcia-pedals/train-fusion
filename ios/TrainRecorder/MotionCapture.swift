import CoreMotion
import Foundation

/// Raw accelerometer, gyroscope and magnetometer samples, the barometer, and iOS's own
/// fusion of the motion sensors ("device motion"), each to its own .csv.gz, stamped with
/// the sensor's own sample time (uptime seconds).
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

        func add(timestamp: Double, _ values: Double...) {
            writer.write(String(format: "%.6f", timestamp) + values.map { String(format: ",%.6f", $0) }.joined() + "\n")
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
    private let altimeter = CMAltimeter()
    private let accel: Stream
    private let gyro: Stream
    private let mag: Stream
    private let baro: Stream
    private let fused: Stream
    private var referenceFrame = "none"

    init(dir: URL) throws {
        accel = try Stream(url: dir.appendingPathComponent("accel.csv.gz"), header: "uptime_s,x_g,y_g,z_g\n")
        gyro = try Stream(url: dir.appendingPathComponent("gyro.csv.gz"), header: "uptime_s,x_rad_s,y_rad_s,z_rad_s\n")
        mag = try Stream(url: dir.appendingPathComponent("mag.csv.gz"), header: "uptime_s,x_ut,y_ut,z_ut\n")
        baro = try Stream(url: dir.appendingPathComponent("baro.csv.gz"), header: "uptime_s,pressure_kpa,relative_altitude_m\n")
        fused = try Stream(
            url: dir.appendingPathComponent("device_motion.csv.gz"),
            header: "uptime_s,qx,qy,qz,qw,rot_x_rad_s,rot_y_rad_s,rot_z_rad_s,gravity_x_g,gravity_y_g,gravity_z_g,"
                + "user_accel_x_g,user_accel_y_g,user_accel_z_g,mag_x_ut,mag_y_ut,mag_z_ut,mag_accuracy,heading_deg\n")
    }

    func start() {
        manager.accelerometerUpdateInterval = 1 / Self.rateHz
        manager.gyroUpdateInterval = 1 / Self.rateHz
        manager.magnetometerUpdateInterval = 1 / Self.rateHz
        manager.startAccelerometerUpdates(to: accel.queue) { [accel] data, _ in
            guard let d = data else { return }
            accel.add(timestamp: d.timestamp, d.acceleration.x, d.acceleration.y, d.acceleration.z)
        }
        manager.startGyroUpdates(to: gyro.queue) { [gyro] data, _ in
            guard let d = data else { return }
            gyro.add(timestamp: d.timestamp, d.rotationRate.x, d.rotationRate.y, d.rotationRate.z)
        }
        // The raw field at the sensor: the earth's plus the phone's own and whatever is
        // nearby (iOS's calibrated field only comes with the fused device motion).
        manager.startMagnetometerUpdates(to: mag.queue) { [mag] data, _ in
            guard let d = data else { return }
            mag.add(timestamp: d.timestamp, d.magneticField.x, d.magneticField.y, d.magneticField.z)
        }
        // iOS's fused estimate. It knows things the raw streams above don't: the gyro and
        // magnetometer biases it has been tracking since long before this recording. A
        // frame that uses the magnetometer is needed to get the calibrated field at all;
        // magnetic north also makes the attitude's heading absolute.
        manager.deviceMotionUpdateInterval = 1 / Self.rateHz
        let frames = CMMotionManager.availableAttitudeReferenceFrames()
        let frame: CMAttitudeReferenceFrame
        if frames.contains(.xMagneticNorthZVertical) {
            (frame, referenceFrame) = (.xMagneticNorthZVertical, "xMagneticNorthZVertical")
        } else {
            (frame, referenceFrame) = (.xArbitraryZVertical, "xArbitraryZVertical")
        }
        manager.startDeviceMotionUpdates(using: frame, to: fused.queue) { [fused] data, _ in
            guard let d = data else { return }
            let q = d.attitude.quaternion
            let (r, g, a, m) = (d.rotationRate, d.gravity, d.userAcceleration, d.magneticField)
            fused.add(timestamp: d.timestamp, q.x, q.y, q.z, q.w, r.x, r.y, r.z, g.x, g.y, g.z, a.x, a.y, a.z,
                      m.field.x, m.field.y, m.field.z, Double(m.accuracy.rawValue), d.heading)
        }
        // About one sample a second. Asks for "Motion & Fitness" access the first time;
        // if that's refused, baro.csv.gz stays empty.
        if CMAltimeter.isRelativeAltitudeAvailable() {
            altimeter.startRelativeAltitudeUpdates(to: baro.queue) { [baro] data, _ in
                guard let d = data else { return }
                baro.add(timestamp: d.timestamp, d.pressure.doubleValue, d.relativeAltitude.doubleValue)
            }
        }
    }

    func stop() {
        manager.stopAccelerometerUpdates()
        manager.stopGyroUpdates()
        manager.stopMagnetometerUpdates()
        manager.stopDeviceMotionUpdates()
        altimeter.stopRelativeAltitudeUpdates()
        for stream in [accel, gyro, mag, baro, fused] { stream.close() }
    }

    var accelCount: Int { accel.count }
    var gyroCount: Int { gyro.count }
    var magCount: Int { mag.count }
    var baroCount: Int { baro.count }
    var fusedCount: Int { fused.count }

    var meta: [String: Any] {
        var meta: [String: Any] = ["requested_rate_hz": Self.rateHz, "device_motion_reference_frame": referenceFrame]
        for (name, stream) in [("accel", accel), ("gyro", gyro), ("mag", mag), ("baro", baro), ("device_motion", fused)] {
            meta["\(name)_samples"] = stream.count
            meta["\(name)_first_sample_latency_s"] = stream.firstLatency ?? NSNull()
        }
        return meta
    }
}
