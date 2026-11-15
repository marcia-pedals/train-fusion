import Foundation
import Observation
import UIKit

struct Recording: Identifiable {
    let id: String  // the directory name: the start time, UTC
    let url: URL
    let started: Date?
    let duration: Double?  // nil if it was never stopped properly
    let bytes: Int64
    let uploaded: Bool
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
            return Recording(
                id: dir.lastPathComponent, url: dir,
                started: RecordingID.date(dir.lastPathComponent),
                duration: duration, bytes: bytes,
                uploaded: fm.fileExists(atPath: dir.appendingPathComponent(Self.uploadedMarker).path))
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
            try await GCSUploader.shared.upload(files: files, prefix: recording.id + "/") { fraction in
                Task { @MainActor in
                    if case .uploading = self.uploads[recording.id] { self.uploads[recording.id] = .uploading(fraction) }
                }
            }
            FileManager.default.createFile(
                atPath: recording.url.appendingPathComponent(Self.uploadedMarker).path, contents: nil)
            uploads[recording.id] = nil
            if let i = recordings.firstIndex(where: { $0.id == recording.id }) {
                let r = recordings[i]
                recordings[i] = Recording(id: r.id, url: r.url, started: r.started, duration: r.duration,
                                          bytes: r.bytes, uploaded: true)
            }
        } catch {
            uploads[recording.id] = .failed(error.localizedDescription)
        }
    }
}
