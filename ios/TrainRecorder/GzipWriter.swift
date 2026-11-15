import Foundation
import zlib

/// Appends text to a .gz file as it arrives. Not thread-safe: use from one queue.
/// Flushed every few seconds, so a file cut short (app killed) still decompresses up to
/// the last flush (`gzip -dc` complains about the missing end but outputs the data).
final class GzipWriter {
    private var file: gzFile?
    private var lastFlush = Clock.uptime()

    init(url: URL, header: String) throws {
        guard let file = gzopen(url.path, "wb") else {
            throw CocoaError(.fileWriteUnknown, userInfo: [NSFilePathErrorKey: url.path])
        }
        self.file = file
        write(header)
    }

    func write(_ text: String) {
        guard let file else { return }
        _ = text.withCString { gzputs(file, $0) }
        let now = Clock.uptime()
        if now - lastFlush > 5 {
            gzflush(file, Z_SYNC_FLUSH)
            lastFlush = now
        }
    }

    func close() {
        if let file { gzclose(file) }
        file = nil
    }

    deinit { close() }
}
