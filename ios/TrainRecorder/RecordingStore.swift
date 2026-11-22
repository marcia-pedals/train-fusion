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
    /// What's shared is marked for deletion (the server says so; `RideStore.syncSharing`).
    /// Counts as not shared; sharing again from History undoes it. Optional, like sharePending.
    var measurementsDeletionRequested: Bool?
    var recordingDeletionRequested: Bool?
    /// The generated sample ride (`SampleRide`): plays back, is never shared.
    var sample: Bool?

    var isSample: Bool { sample == true }

    var settled: Bool { [.uploaded, .declined, .tooLarge].contains(upload) }
    var measurementsShared: Bool { sharedAt != nil && measurementsDeletionRequested != true }
    var recordingShared: Bool { upload == .uploaded && recordingDeletionRequested != true }

    /// Where the model heard the ride start and end.
    var title: String {
        guard let origin = stops.first?.station.map(stationShortName) else { return isSample ? "Sample ride" : "Unknown ride" }
        let destination = stops.count > 1 ? stops.last?.station.map(stationShortName) : nil
        let sure = (stationConfidence ?? 0) > 0.9
        return (isSample ? "Sample: " : "") + origin + (destination.map { " → " + $0 } ?? "") + (sure ? "" : " (unsure)")
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

    /// From what the server has (GET /v1/measurements), for a ride that isn't on the phone.
    static func fromServer(_ id: String, _ m: [String: Any]) -> Ride {
        var ride = Ride(id: id)
        ride.started = m["started_unix_s"] as? Double
        ride.ended = m["ended_unix_s"] as? Double
        ride.stoppedProperly = ride.ended != nil
        ride.appVersion = m["app_version"] as? String
        if let train = m["train"] as? [String: Any], let trip = train["trip_number"] as? Int, let car = train["car_number"] as? Int {
            ride.train = Train(trip: trip, car: car, position: train["car_position"] as? String ?? "unspecified")
        }
        ride.direction = m["direction"] as? String
        ride.stationConfidence = m["station_confidence"] as? Double
        ride.stops = (m["stops"] as? [[String: Any]] ?? []).compactMap { stop in
            guard let opened = stop["doors_opened_unix_s"] as? Double else { return nil }
            return Stop(station: stop["station"] as? String, doorsOpened: opened, doorsClosed: stop["doors_closed_unix_s"] as? Double)
        }
        return ride
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
        if meta["sample"] as? Bool == true { ride.sample = true }
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
    /// Rides whose measurements are being shared right now, and why the last try failed.
    private(set) var sharingNow: Set<String> = []
    private(set) var shareFailures: [String: String] = [:]
    private var syncing = false
    /// What's shared (from this phone, or linked to the account) of rides that aren't on the
    /// phone any more, newest first: made from what the server has (its measurements, if
    /// shared), so they can be unshared one by one. Kept in shared-elsewhere.json.
    private(set) var elsewhere: [Ride] = []
    /// What's being unshared right now: `unshareKey`s.
    private(set) var unsharing: Set<String> = []
    private static let elsewhereFile = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("shared-elsewhere.json")

    enum Shared { case measurements, recording }
    static func unshareKey(_ kind: Shared, _ id: String) -> String { (kind == .measurements ? "m:" : "r:") + id }

    init() {
        if let data = try? Data(contentsOf: Self.file), let saved = try? JSONDecoder().decode([Ride].self, from: data) {
            rides = saved
        }
        if let data = try? Data(contentsOf: Self.elsewhereFile), let saved = try? JSONDecoder().decode([Ride].self, from: data) {
            elsewhere = saved
        }
        installSample()
        Uploader.shared.onFinished = { [weak self] id, outcome in self?.uploadFinished(id, outcome) }
    }

    private func save() {
        rides.sort { $0.id > $1.id }
        try? FileManager.default.createDirectory(at: Self.file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? JSONEncoder().encode(rides).write(to: Self.file, options: .atomic)
    }

    func ride(_ id: String) -> Ride? { rides.first { $0.id == id } }
    /// On the phone, or only shared.
    func anyRide(_ id: String) -> Ride? { ride(id) ?? elsewhere.first { $0.id == id } }

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
            // (the sample's ride is always read afresh: its meta.json follows the generator)
            var ride = ride(id).flatMap { $0.isSample ? nil : $0 } ?? Ride.read(dir, id: id)
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

    private static let sampleInstalledKey = "sampleInstalled"

    /// The first time the app runs, a sample ride to play back (`SampleRide`): just a
    /// meta.json saying so, since it's generated as it plays. Deleted like any other ride,
    /// and not put back. Later launches rewrite its meta.json (keeping its date), so its stops
    /// and length follow the generator as it changes.
    private func installSample() {
        guard let model = try? SharedModel.result.get() else { return }
        let fm = FileManager.default
        func write(_ sample: SampleRide, _ dir: URL, started: Double) {
            guard let data = try? JSONSerialization.data(withJSONObject: sample.meta(startedUnix: started),
                                                         options: [.prettyPrinted, .sortedKeys]) else { return }
            try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
            try? data.write(to: dir.appendingPathComponent("meta.json"))
        }
        if UserDefaults.standard.bool(forKey: Self.sampleInstalledKey) {
            let dirs = (try? fm.contentsOfDirectory(at: Self.root, includingPropertiesForKeys: nil)) ?? []
            for dir in dirs where Replay.isSample(dir) {
                let meta = (try? JSONSerialization.jsonObject(with: Data(contentsOf: dir.appendingPathComponent("meta.json")))) as? [String: Any]
                if let started = meta?["started_unix_s"] as? Double { write(SampleRide(model: model), dir, started: started) }
            }
            return
        }
        UserDefaults.standard.set(true, forKey: Self.sampleInstalledKey)
        let sample = SampleRide(model: model)
        let started = Date().addingTimeInterval(-sample.duration - 3600)  // as if ridden an hour ago
        write(sample, Self.root.appendingPathComponent(RecordingID.make(started)), started: started.timeIntervalSince1970)
    }

    /// The rider answered: upload or not. Uploading one whose upload is marked for deletion
    /// uploads it afresh (the marked upload is still deleted).
    func decide(_ id: String, upload: Bool, protecting: Set<String>) {
        if upload, ride(id)?.isSample == true { return }  // made up: never shared
        if upload {
            update(id) { $0.upload = .wanted; $0.recordingDeletionRequested = nil }
            if let recording = recordings[id] {
                try? FileManager.default.removeItem(at: recording.url.appendingPathComponent(Self.uploadedMarker))
                Uploader.shared.start(recording)
            }
        } else if ride(id)?.upload != .uploaded {  // "no" to sharing again leaves it as it was
            update(id) { $0.upload = .declined }
        }
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

    /// Deletes just the ride's Full Recording from the phone; the ride (its Train
    /// Measurements) stays. One that wasn't uploaded counts as declined.
    func deleteRecording(_ id: String) {
        guard let recording = recordings[id] else { return }
        try? FileManager.default.removeItem(at: recording.url)
        Uploader.shared.forget(id)
        recordings[id] = nil
        update(id) { if $0.upload != .uploaded { $0.upload = .declined } }
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
    /// `undoDeletion`: the rider asked to share these rides (from History), so measurements
    /// marked for deletion are shared again; never for automatic sharing.
    func share(_ ids: [String], undoDeletion: Bool = false) async {
        guard !ids.isEmpty else { return }
        sharing = true
        shareMessage = nil
        defer { sharing = false }
        for id in ids {
            guard let ride = ride(id), !ride.isSample, !sharingNow.contains(id) else { continue }
            sharingNow.insert(id)
            shareFailures[id] = nil
            defer { sharingNow.remove(id) }
            do {
                try await API.call("PUT", "v1/measurements/\(id)", query: undoDeletion ? ["undo_deletion": "true"] : [:],
                                   json: ride.apiBody, auth: .ifSignedIn, device: true)
                update(id) {
                    $0.sharedAt = Date().timeIntervalSince1970
                    $0.sharePending = nil
                    $0.measurementsDeletionRequested = nil
                }
            } catch let error as APIError where error.code == "deletion_pending" {
                // waiting to be deleted: not shared again unless asked for
                update(id) { $0.sharePending = nil; $0.sharedAt = nil; $0.measurementsDeletionRequested = true }
            } catch {
                // left pending (if it was): tried again when the app is next opened
                shareMessage = "Couldn't share: \(error.localizedDescription)"
                shareFailures[id] = shareMessage
                return
            }
        }
    }

    /// Rides that ended with sharing on but haven't been shared yet.
    func sharePendingMeasurements() async {
        guard AppSettings.shareMeasurements else { return }
        await share(rides.filter { $0.sharePending == true && $0.sharedAt == nil }.map(\.id))
    }

    /// Asks for every measurement shared from this phone or linked to the account to be
    /// deleted (the developers do it by hand, within 30 days). Returns how many, or nil if it
    /// failed (`shareMessage` says why).
    func deleteShared() async -> Int? {
        sharing = true
        shareMessage = nil
        defer { sharing = false }
        do {
            let body = try await API.call("POST", "v1/measurements/deletion-request", auth: .ifSignedIn, device: true)
            for i in rides.indices {
                if rides[i].sharedAt != nil { rides[i].measurementsDeletionRequested = true }
                (rides[i].sharedAt, rides[i].sharePending) = (nil, nil)
            }
            save()
            await syncSharing()
            return body["requested"] as? Int ?? 0
        } catch {
            shareMessage = "Couldn't delete: \(error.localizedDescription)"
            return nil
        }
    }

    /// Asks for one ride's shared measurements, or every upload of its recording, to be
    /// deleted (by hand, within 30 days). Returns whether the request went through.
    func unshare(_ kind: Shared, _ id: String) async -> Bool {
        let key = Self.unshareKey(kind, id)
        guard !unsharing.contains(key) else { return false }
        unsharing.insert(key)
        defer { unsharing.remove(key) }
        shareMessage = nil
        do {
            switch kind {
            case .measurements:
                try await API.call("POST", "v1/measurements/deletion-request", query: ["ride_id": id], auth: .ifSignedIn, device: true)
                update(id) { $0.sharedAt = nil; $0.sharePending = nil; $0.measurementsDeletionRequested = true }
            case .recording:
                try await API.call("POST", "v1/uploads/deletion-request", query: ["recording_id": id], auth: .ifSignedIn, device: true)
                update(id) { $0.recordingDeletionRequested = true }
            }
        } catch {
            shareMessage = "Couldn't unshare: \(error.localizedDescription)"
            return false
        }
        await syncSharing()
        return true
    }

    /// Brings what's shared up to date with the server (this phone's measurements and
    /// uploads, and the account's if signed in): each ride's flags, and `elsewhere`. E.g.
    /// after asking for deletion, deleting the account, or the developers deleting what was
    /// asked for. Kept on the phone, so History shows the last known state offline.
    func syncSharing() async {
        guard BackendConfig.configured, !syncing else { return }
        syncing = true
        defer { syncing = false }
        guard let measurementsBody = try? await API.call("GET", "v1/measurements", auth: .ifSignedIn, device: true),
              let measurements = measurementsBody["measurements"] as? [[String: Any]],
              let uploadsBody = try? await API.call("GET", "v1/uploads", auth: .ifSignedIn, device: true),
              let uploads = uploadsBody["uploads"] as? [[String: Any]] else { return }

        var server: [String: (body: [String: Any], marked: Bool)] = [:]
        for m in measurements {
            guard let id = m["id"] as? String else { continue }
            server[id] = (m, m["deletion_requested"] as? Bool == true)
        }
        for i in rides.indices where !sharingNow.contains(rides[i].id) {
            // not listed: never shared, or deleted by the developers
            let s = server[rides[i].id]
            rides[i].measurementsDeletionRequested = s?.marked == true ? true : nil
            rides[i].sharedAt = s.flatMap {
                $0.marked ? nil : $0.body["shared_at_unix_s"] as? Double ?? rides[i].sharedAt ?? Date().timeIntervalSince1970
            }
        }

        // a recording may have been uploaded more than once (again after a deletion request)
        var live: Set<String> = [], marked: Set<String> = []
        for u in uploads where u["status"] as? String == "complete" {
            guard let id = u["recording_id"] as? String else { continue }
            if u["deletion_requested"] as? Bool == true { marked.insert(id) } else { live.insert(id) }
        }
        for i in rides.indices {
            let id = rides[i].id
            if live.contains(id) {
                rides[i].upload = .uploaded
                rides[i].recordingDeletionRequested = nil
            } else if marked.contains(id), rides[i].upload != .wanted {  // .wanted: being uploaded again
                rides[i].upload = .uploaded
                rides[i].recordingDeletionRequested = true
            }
            // not listed: never uploaded, or uploaded before uploads went through the
            // backend (the server doesn't know those): left as it is
        }
        save()

        // rides not on the phone with something still shared
        let onPhone = Set(rides.map(\.id))
        let sharedIDs = Set(server.filter { !$0.value.marked }.keys).union(live).subtracting(onPhone)
        elsewhere = sharedIDs.sorted(by: >).map { id in
            var ride = server[id].map { Ride.fromServer(id, $0.body) } ?? Ride(id: id)
            let s = server[id]
            ride.sharedAt = s.flatMap { $0.marked ? nil : $0.body["shared_at_unix_s"] as? Double ?? Date().timeIntervalSince1970 }
            ride.measurementsDeletionRequested = s?.marked == true ? true : nil
            ride.upload = live.contains(id) || marked.contains(id) ? .uploaded : .declined
            ride.recordingDeletionRequested = !live.contains(id) && marked.contains(id) ? true : nil
            return ride
        }
        try? FileManager.default.createDirectory(at: Self.elsewhereFile.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? JSONEncoder().encode(elsewhere).write(to: Self.elsewhereFile, options: .atomic)
    }
}
