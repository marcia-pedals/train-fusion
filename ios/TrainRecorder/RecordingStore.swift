import Foundation
import Observation
import UIKit

struct Recording: Identifiable {
    let id: String  // the directory name: the start time, UTC
    let url: URL
    let started: Date?
    let duration: Double?  // nil if it was never stopped properly
    let bytes: Int64
    var uploaded: Bool
    var ride: RideInfo?  // as typed in by the rider
    /// The first and last stations the model heard the doors open at, and whether it was
    /// confident of them (meta.json's `inferred`).
    var origin: String?
    var destination: String?
    var stationsSure = true
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

enum UploadState {
    case uploading(Double)  // fraction done
    case failed(String)
}

/// The recordings on the phone (Documents/recordings/<id>/), and uploading them.
@MainActor
@Observable
final class RecordingStore {
    static let root = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("recordings")
    /// Written into a recording's directory once all of it is in the bucket (never uploaded).
    private static let uploadedMarker = ".uploaded"

    private(set) var recordings: [Recording] = []
    private(set) var uploads: [String: UploadState] = [:]

    /// Rescan the disk. `excluding`: the recording in progress, which isn't listed.
    func refresh(excluding active: String?) {
        let fm = FileManager.default
        let dirs = (try? fm.contentsOfDirectory(at: Self.root, includingPropertiesForKeys: nil)) ?? []
        recordings = dirs.filter { $0.hasDirectoryPath && $0.lastPathComponent != active }.map { dir in
            let files = (try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.fileSizeKey])) ?? []
            let bytes = files.reduce(Int64(0)) { $0 + Int64((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
            let meta = (try? Data(contentsOf: dir.appendingPathComponent("meta.json")))
                .flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]
            var duration: Double?
            if let start = meta?["started_uptime_s"] as? Double, let end = meta?["ended_uptime_s"] as? Double {
                duration = end - start
            }
            var recording = Recording(
                id: dir.lastPathComponent, url: dir,
                started: RecordingID.date(dir.lastPathComponent),
                duration: duration, bytes: bytes,
                uploaded: fm.fileExists(atPath: dir.appendingPathComponent(Self.uploadedMarker).path))
            if let ride = meta?["ride"] as? [String: Any], let trip = ride["trip_number"] as? Int, let car = ride["car_number"] as? Int {
                recording.ride = RideInfo(trip: trip, car: car,
                                          position: (ride["car_position"] as? String).flatMap(RideInfo.Position.init) ?? .unspecified)
            }
            if let inferred = meta?["inferred"] as? [String: Any], let stops = inferred["stops"] as? [[String: Any]] {
                recording.origin = stops.first?["station"] as? String
                recording.destination = stops.count > 1 ? stops.last?["station"] as? String : nil
                recording.stationsSure = (inferred["station_confidence"] as? Double ?? 0) > 0.9
            }
            return recording
        }.sorted { $0.id > $1.id }
    }

    func delete(_ recording: Recording) {
        try? FileManager.default.removeItem(at: recording.url)
        uploads[recording.id] = nil
        recordings.removeAll { $0.id == recording.id }
    }

    func upload(_ recording: Recording) async {
        if case .uploading = uploads[recording.id] { return }
        uploads[recording.id] = .uploading(0)
        // A little extra running time if the app is backgrounded mid-upload.
        let task = UIApplication.shared.beginBackgroundTask()
        defer { UIApplication.shared.endBackgroundTask(task) }
        do {
            let files = try FileManager.default.contentsOfDirectory(at: recording.url, includingPropertiesForKeys: nil)
                .filter { !$0.lastPathComponent.hasPrefix(".") }
            try await GCSUploader.shared.upload(files: files, prefix: GCSUploader.prefix + recording.id + "/") { fraction in
                Task { @MainActor in
                    if case .uploading = self.uploads[recording.id] { self.uploads[recording.id] = .uploading(fraction) }
                }
            }
            FileManager.default.createFile(
                atPath: recording.url.appendingPathComponent(Self.uploadedMarker).path, contents: nil)
            uploads[recording.id] = nil
            if let i = recordings.firstIndex(where: { $0.id == recording.id }) {
                recordings[i].uploaded = true
            }
        } catch {
            uploads[recording.id] = .failed(error.localizedDescription)
        }
    }
}
