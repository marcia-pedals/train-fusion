import Foundation
import Observation

/// A recording's files on the phone (Documents/recordings/<id>/; docs/recorder.md).
struct Recording: Identifiable {
    let id: String  // the directory name: the start time, UTC
    let url: URL
    let started: Date?
    let duration: Double?  // nil if it was never stopped properly
    let bytes: Int64

    var tooLargeToUpload: Bool { bytes > AppSettings.maxUploadBytes }

    /// The files an upload sends: everything but hidden files (the .uploaded marker).
    var files: [URL] {
        ((try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: [.fileSizeKey])) ?? [])
            .filter { !$0.lastPathComponent.hasPrefix(".") }
    }
}

/// A recording's id is its start time in UTC, e.g. 20261115T181502Z.
enum RecordingID {
    private static let format: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyyMMdd'T'HHmmss'Z'"
        return f
    }()

    static func make(_ date: Date) -> String { format.string(from: date) }
    static func date(_ id: String) -> Date? { format.date(from: id) }
}

/// One ride: its Train Measurements (timings, and what was typed in about the train), kept
/// after its recording is deleted from the phone; what's shared (docs/api.md,
/// "Measurements").
struct Ride: Codable, Identifiable, Equatable {
    struct Stop: Codable, Equatable {
        var station: String?
        var doorsOpened: Double  // unix seconds, NTP-corrected when the recording had NTP
        var doorsClosed: Double?
    }

    struct Train: Codable, Equatable {
        var trip: Int
        var car: Int
        var position: String
    }

    /// Whether the recording is to be uploaded. Settled (may be deleted from the phone):
    /// uploaded, declined, tooLarge.
    enum Upload: String, Codable {
        case undecided  // not asked yet (or from before uploads were asked about)
        case declined
        case wanted  // queued or uploading
        case uploaded
        case tooLarge
    }

    let id: String
    var started: Double?
    var ended: Double?
    var stoppedProperly = false
    var appVersion: String?
    var train: Train?
    var direction: String?
    var stationConfidence: Double?
    var stops: [Stop] = []
    var upload = Upload.undecided
    /// When its Train Measurements were shared (unix seconds), if they are.
    var sharedAt: Double?
    /// To be shared as soon as possible (it ended with sharing on). Optional so that
    /// rides.json from before it decodes.
    var sharePending: Bool?

    var settled: Bool { [.uploaded, .declined, .tooLarge].contains(upload) }

    /// Where the model heard the ride start and end.
    var title: String {
        guard let origin = stops.first?.station else { return "Unknown trip" }
        let destination = stops.count > 1 ? stops.last?.station : nil
        let sure = (stationConfidence ?? 0) > 0.9
        return origin + (destination.map { " → " + $0 } ?? "") + (sure ? "" : " (unsure)")
    }

    /// The body of PUT /v1/measurements/<id>.
    var apiBody: [String: Any] {
        [
            "started_unix_s": started ?? NSNull(),
            "ended_unix_s": ended ?? NSNull(),
            "app_version": appVersion ?? NSNull(),
            "train": train.map { ["trip_number": $0.trip, "car_number": $0.car, "car_position": $0.position] as [String: Any] } ?? NSNull(),
            "direction": direction ?? NSNull(),
            "station_confidence": stationConfidence ?? NSNull(),
            "stops": stops.map {
                ["station": $0.station ?? NSNull(), "doors_opened_unix_s": $0.doorsOpened,
                 "doors_closed_unix_s": $0.doorsClosed ?? NSNull()] as [String: Any]
            },
        ]
    }

    /// From a recording's meta.json, with its times converted from uptime to clock time
    /// (docs/recorder.md, "Timestamps").
    static func read(_ dir: URL, id: String) -> Ride {
        var ride = Ride(id: id)
        guard let data = try? Data(contentsOf: dir.appendingPathComponent("meta.json")),
              let meta = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return ride }
        let startUptime = meta["started_uptime_s"] as? Double
        let startUnix = meta["started_unix_s"] as? Double
        guard let offset = ntpOffset(dir) ?? startUnix.flatMap({ unix in startUptime.map { unix - $0 } }) else { return ride }
        let unix = { (uptime: Double) in uptime + offset }
        ride.started = startUptime.map(unix)
        ride.ended = (meta["ended_uptime_s"] as? Double).map(unix)
        ride.stoppedProperly = meta["complete"] as? Bool ?? false
        ride.appVersion = meta["app_version"] as? String
        if let train = meta["ride"] as? [String: Any], let trip = train["trip_number"] as? Int, let car = train["car_number"] as? Int {
            ride.train = Train(trip: trip, car: car, position: train["car_position"] as? String ?? "unspecified")
        }
        if let inferred = meta["inferred"] as? [String: Any] {
            ride.direction = inferred["direction"] as? String
            ride.stationConfidence = inferred["station_confidence"] as? Double
            ride.stops = (inferred["stops"] as? [[String: Any]] ?? []).compactMap { stop in
                guard let opened = stop["doors_opened_uptime_s"] as? Double else { return nil }
                return Stop(station: stop["station"] as? String, doorsOpened: unix(opened),
                            doorsClosed: (stop["doors_closed_uptime_s"] as? Double).map(unix))
            }
        }
        return ride
    }

    /// true unix time − uptime, from the NTP exchange in clock.csv with the smallest round trip.
    private static func ntpOffset(_ dir: URL) -> Double? {
        guard let text = try? String(contentsOf: dir.appendingPathComponent("clock.csv"), encoding: .utf8) else { return nil }
        var best: (roundTrip: Double, offset: Double)?
        for line in text.split(separator: "\n").dropFirst() {
            let f = line.split(separator: ",", omittingEmptySubsequences: false).map { Double($0) }
            guard f.count >= 4, let uptime = f[0], let unix = f[1], let ntp = f[2], let roundTrip = f[3] else { continue }
            if best == nil || roundTrip < best!.roundTrip { best = (roundTrip, unix + ntp - uptime) }
        }
        return best?.offset
    }
}

/// The rides on the phone (Application Support/rides.json) and their recordings: which to
/// keep, uploading them (through `Uploader`) and sharing their Train Measurements.
@MainActor
@Observable
final class RideStore {
    static let root = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("recordings")
    private static let file = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("rides.json")
    /// In a recording's directory once all of it is uploaded (hidden, so never uploaded).
    static let uploadedMarker = ".uploaded"

    private(set) var rides: [Ride] = []  // newest first
    private(set) var recordings: [String: Recording] = [:]
    private(set) var sharing = false
    var shareMessage: String?

    init() {
        if let data = try? Data(contentsOf: Self.file), let saved = try? JSONDecoder().decode([Ride].self, from: data) {
            rides = saved
        }
        Uploader.shared.onFinished = { [weak self] id, outcome in self?.uploadFinished(id, outcome) }
    }

    private func save() {
        rides.sort { $0.id > $1.id }
        try? FileManager.default.createDirectory(at: Self.file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? JSONEncoder().encode(rides).write(to: Self.file, options: .atomic)
    }

    func ride(_ id: String) -> Ride? { rides.first { $0.id == id } }

    private func update(_ id: String, _ change: (inout Ride) -> Void) {
        guard let i = rides.firstIndex(where: { $0.id == id }) else { return }
        change(&rides[i])
        save()
    }

    /// Rescan the disk. `active`: the recording in progress, which isn't a ride yet.
    /// Recordings without a ride (from before rides were kept, or not stopped properly)
    /// get one.
    func refresh(excluding active: String?) {
        let fm = FileManager.default
        let dirs = (try? fm.contentsOfDirectory(at: Self.root, includingPropertiesForKeys: nil)) ?? []
        var found: [String: Recording] = [:]
        for dir in dirs where dir.hasDirectoryPath && dir.lastPathComponent != active {
            let id = dir.lastPathComponent
            let files = (try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.fileSizeKey])) ?? []
            let bytes = files.reduce(Int64(0)) { $0 + Int64((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
            var ride = ride(id) ?? Ride.read(dir, id: id)
            let duration = ride.stoppedProperly ? ride.ended.flatMap { end in ride.started.map { end - $0 } } : nil
            let recording = Recording(id: id, url: dir, started: RecordingID.date(id), duration: duration, bytes: bytes)
            found[id] = recording
            if fm.fileExists(atPath: dir.appendingPathComponent(Self.uploadedMarker).path) {
                ride.upload = .uploaded
            } else if recording.tooLargeToUpload, ride.upload != .uploaded {
                ride.upload = .tooLarge
            }
            if let i = rides.firstIndex(where: { $0.id == id }) { rides[i] = ride } else { rides.append(ride) }
        }
        recordings = found
        save()
    }

    /// A recording has just been stopped: make its ride, decide about uploading (the
    /// caller asks if it's `undecided`), and share its measurements if sharing is on.
    func rideEnded(_ id: String) -> Ride? {
        refresh(excluding: nil)
        guard var ride = ride(id), let recording = recordings[id] else { return nil }
        ride = Ride.read(recording.url, id: id)
        ride.upload = recording.tooLargeToUpload ? .tooLarge : AppSettings.askToUpload ? .undecided : .declined
        if AppSettings.takeShareOnce(id) || AppSettings.shareMeasurements { ride.sharePending = true }
        update(id) { $0 = ride }
        if ride.sharePending == true { Task { await share([id]) } }
        return ride
    }

    /// The rider answered: upload or not.
    func decide(_ id: String, upload: Bool, protecting: Set<String>) {
        update(id) { $0.upload = upload ? .wanted : .declined }
        if upload, let recording = recordings[id] { Uploader.shared.start(recording) }
        prune(protecting: protecting)
    }

    private func uploadFinished(_ id: String, _ outcome: Uploader.Outcome) {
        update(id) { $0.upload = outcome == .uploaded ? .uploaded : .tooLarge }
        prune(protecting: [])
    }

    /// Start (or retry) every upload the rider asked for that isn't done, e.g. after a
    /// launch, or when an upload limit has passed.
    func resumeUploads() {
        for ride in rides where ride.upload == .wanted {
            if let recording = recordings[ride.id] { Uploader.shared.start(recording, automatically: true) }
        }
    }

    /// Deletes the ride from the phone: its recording and its timings.
    func delete(_ ride: Ride) {
        if let recording = recordings[ride.id] { try? FileManager.default.removeItem(at: recording.url) }
        Uploader.shared.forget(ride.id)
        recordings[ride.id] = nil
        rides.removeAll { $0.id == ride.id }
        save()
    }

    // MARK: keeping recordings

    /// The recordings `keep` would delete now: the settled ones beyond the newest N.
    func prunable(_ keep: KeepRecordings, protecting: Set<String>) -> [Recording] {
        guard let count = keep.count else { return [] }
        let withRecordings = rides.filter { recordings[$0.id] != nil }  // newest first
        return withRecordings.enumerated().compactMap { i, ride in
            i >= count && ride.settled && !protecting.contains(ride.id) ? recordings[ride.id] : nil
        }
    }

    /// Deletes what the "Keep recordings" setting says to. `protecting`: e.g. the one
    /// being replayed.
    func prune(protecting: Set<String>) {
        for recording in prunable(AppSettings.keepRecordings, protecting: protecting) {
            try? FileManager.default.removeItem(at: recording.url)
            recordings[recording.id] = nil
        }
    }

    // MARK: sharing Train Measurements

    /// Share these rides' measurements (linked to the account if signed in).
    func share(_ ids: [String]) async {
        guard !ids.isEmpty else { return }
        sharing = true
        shareMessage = nil
        defer { sharing = false }
        for id in ids {
            guard let ride = ride(id) else { continue }
            do {
                try await API.call("PUT", "v1/measurements/\(id)", json: ride.apiBody, auth: .ifSignedIn, device: true)
                update(id) { $0.sharedAt = Date().timeIntervalSince1970; $0.sharePending = nil }
            } catch {
                // left pending: tried again when the app is next opened
                shareMessage = "Couldn't share: \(error.localizedDescription)"
                return
            }
        }
    }

    /// Rides that ended with sharing on but haven't been shared yet.
    func sharePendingMeasurements() async {
        guard AppSettings.shareMeasurements else { return }
        await share(rides.filter { $0.sharePending == true && $0.sharedAt == nil }.map(\.id))
    }

    /// Deletes every measurement shared from this phone or linked to the account. Returns
    /// how many, or nil if it failed (`shareMessage` says why).
    func deleteShared() async -> Int? {
        sharing = true
        shareMessage = nil
        defer { sharing = false }
        do {
            let body = try await API.call("DELETE", "v1/measurements", auth: .ifSignedIn, device: true)
            for i in rides.indices { (rides[i].sharedAt, rides[i].sharePending) = (nil, nil) }
            save()
            return body["deleted"] as? Int ?? 0
        } catch {
            shareMessage = "Couldn't delete: \(error.localizedDescription)"
            return nil
        }
    }

    /// Marks as shared exactly the rides the server still has from this phone (e.g. after
    /// the account, and the measurements linked to it, were deleted).
    func reconcileShared() async {
        guard let body = try? await API.call("GET", "v1/measurements", device: true),
              let found = body["measurements"] as? [[String: Any]] else { return }
        let ids = Set(found.compactMap { $0["id"] as? String })
        for i in rides.indices where !ids.contains(rides[i].id) { rides[i].sharedAt = nil }
        save()
    }
}
