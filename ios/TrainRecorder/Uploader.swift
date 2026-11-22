import Foundation
import Observation
import UIKit

/// Uploads recordings through the backend (docs/api.md, "Uploads"): it hands out one signed
/// URL per file, the files go up in a background URL session (so they finish with the app
/// closed), meta.json last, and then the backend is told the upload is complete.
///
/// What's in flight is saved (Application Support/uploads.json), so an upload picks up
/// where it was after the app is relaunched; files already in the bucket answer 412 and
/// count as done.
@MainActor
@Observable
final class Uploader {
    static let shared = Uploader()

    enum Outcome { case uploaded, tooLarge }

    enum State: Equatable {
        case starting
        case uploading(Double)  // fraction done
        case waiting(String)  // over today's limit: retried later
        case failed(String)
    }

    private(set) var states: [String: State] = [:]  // by recording id
    /// Told when a recording is all uploaded, or turns out too large.
    @ObservationIgnored var onFinished: ((String, Outcome) -> Void)?
    /// From the app delegate, when iOS woke the app for this session's events.
    @ObservationIgnored var backgroundCompletion: (() -> Void)?

    struct Job: Codable {
        struct File: Codable {
            let name: String
            let size: Int64
            let url: URL
            let headers: [String: String]
            var done = false
        }

        let recordingID: String
        let uploadID: String
        let expires: Double  // unix seconds, when the URLs stop working
        var files: [File]
    }

    @ObservationIgnored private var jobs: [String: Job] = [:]
    @ObservationIgnored private var retryAt: [String: Date] = [:]
    @ObservationIgnored private var sent: [String: [String: Int64]] = [:]
    @ObservationIgnored private var session: URLSession!

    private static let file = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("uploads.json")

    private init() {
        if let data = try? Data(contentsOf: Self.file), let saved = try? JSONDecoder().decode([String: Job].self, from: data) {
            jobs = saved
        }
        let configuration = URLSessionConfiguration.background(withIdentifier: "uploads")
        configuration.sessionSendsLaunchEvents = true
        configuration.isDiscretionary = false
        session = URLSession(configuration: configuration, delegate: SessionDelegate(), delegateQueue: nil)
    }

    private func save() {
        try? FileManager.default.createDirectory(at: Self.file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? JSONEncoder().encode(jobs).write(to: Self.file, options: .atomic)
    }

    /// Upload `recording`, or carry on uploading it. `automatically`: a retry nobody asked
    /// for, which waits out a limit.
    func start(_ recording: Recording, automatically: Bool = false) {
        let id = recording.id
        switch states[id] {
        case .starting, .uploading: return
        default: break
        }
        if automatically, let at = retryAt[id], at > Date() { return }
        guard BackendConfig.configured else {
            states[id] = .failed(BackendConfig.notConfigured)
            return
        }
        states[id] = .starting
        Task {
            let background = UIApplication.shared.beginBackgroundTask()
            defer { UIApplication.shared.endBackgroundTask(background) }
            do {
                let files = recording.files.map { ($0.lastPathComponent, Int64((try? $0.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)) }
                let current = jobs[id].flatMap { job in
                    // still good for an hour, and for these same files
                    job.expires > Date().timeIntervalSince1970 + 3600
                        && Set(job.files.map { "\($0.name):\($0.size)" }) == Set(files.map { "\($0.0):\($0.1)" }) ? job : nil
                }
                if let current { jobs[id] = current } else { jobs[id] = try await requestJob(id, files: files) }
                save()
                retryAt[id] = nil
                await enqueue(id)
            } catch let error as APIError {
                switch error.code {
                case "rate_limited":
                    let at = Date().addingTimeInterval(error.retryAfter ?? 3600)
                    retryAt[id] = at
                    states[id] = .waiting("Daily limit reached; retries \(at.formatted(date: .omitted, time: .shortened))")
                case "too_large":
                    states[id] = nil
                    onFinished?(id, .tooLarge)
                default:
                    states[id] = .failed(error.message)
                }
            } catch {
                states[id] = .failed(error.localizedDescription)
            }
        }
    }

    /// Stop uploading this recording and forget about it (it's being deleted).
    func forget(_ id: String) {
        session.getAllTasks { tasks in
            for task in tasks where task.taskDescription?.hasPrefix(id + "/") == true { task.cancel() }
        }
        jobs[id] = nil
        states[id] = nil
        save()
    }

    private func requestJob(_ id: String, files: [(String, Int64)]) async throws -> Job {
        let body = try await API.call("POST", "v1/uploads", json: [
            "recording_id": id,
            "files": files.map { ["name": $0.0, "size": $0.1, "content_type": Self.contentType($0.0)] as [String: Any] },
        ], auth: .ifSignedIn, device: true)
        guard let uploadID = body["upload_id"] as? String, let urls = body["files"] as? [[String: Any]] else {
            throw APIError(status: 0, code: "bad_answer", message: "The server's answer didn't make sense.")
        }
        let sizes = Dictionary(files, uniquingKeysWith: { a, _ in a })
        return Job(
            recordingID: id, uploadID: uploadID,
            expires: body["expires_unix_s"] as? Double ?? Date().timeIntervalSince1970 + 3600 * 12,
            files: try urls.map { file in
                guard let name = file["name"] as? String, let url = (file["url"] as? String).flatMap(URL.init(string:)), let size = sizes[name] else {
                    throw APIError(status: 0, code: "bad_answer", message: "The server's answer didn't make sense.")
                }
                return Job.File(name: name, size: size, url: url, headers: file["headers"] as? [String: String] ?? [:])
            })
    }

    /// Start whatever comes next: the data files, then meta.json once they're all in, then
    /// the completion call.
    private func enqueue(_ id: String) async {
        guard let job = jobs[id] else { return }
        let pending = job.files.filter { !$0.done }
        if pending.isEmpty {
            await complete(id)
            return
        }
        let data = pending.filter { $0.name != "meta.json" }
        let next = data.isEmpty ? pending : data
        let running = Set(await session.allTasks.filter { $0.state == .running || $0.state == .suspended }.compactMap(\.taskDescription))
        let dir = RideStore.root.appendingPathComponent(id)
        for file in next where !running.contains("\(id)/\(file.name)") {
            var request = URLRequest(url: file.url)
            request.httpMethod = "PUT"
            for (name, value) in file.headers { request.setValue(value, forHTTPHeaderField: name) }
            let task = session.uploadTask(with: request, fromFile: dir.appendingPathComponent(file.name))
            task.taskDescription = "\(id)/\(file.name)"
            task.countOfBytesClientExpectsToSend = file.size
            task.resume()
        }
        states[id] = .uploading(progress(id))
    }

    private func complete(_ id: String) async {
        guard let job = jobs[id] else { return }
        let background = UIApplication.shared.beginBackgroundTask()
        defer { UIApplication.shared.endBackgroundTask(background) }
        do {
            try await API.call("POST", "v1/uploads/\(job.uploadID)/complete", auth: .ifSignedIn, device: true)
            FileManager.default.createFile(
                atPath: RideStore.root.appendingPathComponent(id).appendingPathComponent(RideStore.uploadedMarker).path, contents: nil)
            jobs[id] = nil
            sent[id] = nil
            states[id] = nil
            save()
            onFinished?(id, .uploaded)
        } catch let error as APIError where error.code == "incomplete" {
            // start over with fresh URLs next time (files that are there answer 412)
            jobs[id] = nil
            save()
            states[id] = .failed("The upload didn't arrive complete. Tap to try again.")
        } catch {
            states[id] = .failed("Couldn't finish the upload: \(error.localizedDescription)")
        }
    }

    private func progress(_ id: String) -> Double {
        guard let job = jobs[id] else { return 0 }
        let total = max(job.files.reduce(0) { $0 + $1.size }, 1)
        let done = job.files.reduce(Int64(0)) { $0 + ($1.done ? $1.size : min(sent[id]?[$1.name] ?? 0, $1.size)) }
        return Double(done) / Double(total)
    }

    // MARK: from the session's delegate

    fileprivate func sent(_ description: String?, bytes: Int64) {
        guard let (id, name) = Self.split(description), jobs[id] != nil else { return }
        sent[id, default: [:]][name] = bytes
        if case .uploading = states[id] { states[id] = .uploading(progress(id)) }
    }

    fileprivate func finished(_ description: String?, status: Int, error: String?, cancelled: Bool) async {
        guard let (id, name) = Self.split(description), var job = jobs[id], !cancelled else { return }
        if error == nil, (200..<300).contains(status) || status == 412 {  // 412: already there
            if let i = job.files.firstIndex(where: { $0.name == name }) { job.files[i].done = true }
            jobs[id] = job
            save()
            await enqueue(id)
        } else {
            if [400, 403].contains(status) {
                // the URL was refused (e.g. expired): ask for new ones next time
                jobs[id] = nil
                save()
            }
            states[id] = .failed("Upload of \(name) failed (\(error ?? "HTTP \(status)")). Tap to try again.")
        }
    }

    fileprivate func backgroundEventsDone() {
        backgroundCompletion?()
        backgroundCompletion = nil
    }

    private static func split(_ description: String?) -> (String, String)? {
        guard let parts = description?.split(separator: "/", maxSplits: 1), parts.count == 2 else { return nil }
        return (String(parts[0]), String(parts[1]))
    }

    private static func contentType(_ name: String) -> String {
        switch (name as NSString).pathExtension {
        case "m4a": return "audio/mp4"
        case "gz": return "application/gzip"
        case "csv": return "text/csv"
        case "json": return "application/json"
        default: return "application/octet-stream"
        }
    }
}

/// The background session's delegate: hands everything to the uploader on the main actor.
private final class SessionDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        let description = task.taskDescription
        Task { @MainActor in Uploader.shared.sent(description, bytes: totalBytesSent) }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let description = task.taskDescription
        let status = (task.response as? HTTPURLResponse)?.statusCode ?? 0
        let message = error?.localizedDescription
        let cancelled = (error as? URLError)?.code == .cancelled
        Task { @MainActor in
            await Uploader.shared.finished(description, status: status, error: message, cancelled: cancelled)
        }
    }

    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        Task { @MainActor in Uploader.shared.backgroundEventsDone() }
    }
}
