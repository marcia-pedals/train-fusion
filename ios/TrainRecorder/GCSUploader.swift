import Foundation
import Security

/// Uploads files to the recordings bucket as the `recorder` service account, whose key
/// (uploader-key.json, from `just ios-key`) is bundled into the app. That account can only
/// create objects in this one bucket: it can't read, replace or delete anything.
final class GCSUploader {
    static let shared = GCSUploader()
    static let bucket = "train-fusion-recordings"

    struct Failure: LocalizedError {
        let errorDescription: String?
        init(_ message: String) { errorDescription = message }
    }

    private var token: (value: String, expires: Date)?

    /// Uploads each file as `<prefix><file name>`, meta.json last (so its presence in the
    /// bucket means the recording is all there). Objects that already exist are left alone
    /// and count as done, so a failed upload can simply be retried.
    func upload(files: [URL], prefix: String, progress: @escaping (Double) -> Void) async throws {
        let ordered = files.sorted { a, b in
            (a.lastPathComponent == "meta.json" ? 1 : 0, a.lastPathComponent)
                < (b.lastPathComponent == "meta.json" ? 1 : 0, b.lastPathComponent)
        }
        let sizes = ordered.map { Int64((try? $0.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
        let total = max(sizes.reduce(0, +), 1)
        var done: Int64 = 0
        for (file, size) in zip(ordered, sizes) {
            let before = done
            try await upload(file: file, name: prefix + file.lastPathComponent) { sent in
                progress(Double(before + min(sent, size)) / Double(total))
            }
            done += size
            progress(Double(done) / Double(total))
        }
    }

    private func upload(file: URL, name: String, sent: @escaping (Int64) -> Void) async throws {
        var url = URLComponents(string: "https://storage.googleapis.com/upload/storage/v1/b/\(Self.bucket)/o")!
        url.queryItems = [
            URLQueryItem(name: "uploadType", value: "media"),
            URLQueryItem(name: "name", value: name),
            URLQueryItem(name: "ifGenerationMatch", value: "0"),  // create only
        ]
        var request = URLRequest(url: url.url!)
        request.httpMethod = "POST"
        request.setValue("Bearer \(try await accessToken())", forHTTPHeaderField: "Authorization")
        request.setValue(Self.contentType(file), forHTTPHeaderField: "Content-Type")
        let (body, response) = try await URLSession.shared.upload(
            for: request, fromFile: file, delegate: ProgressDelegate(sent: sent))
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if status == 412 { return }  // already there
        guard (200..<300).contains(status) else {
            throw Failure("Upload of \(file.lastPathComponent) failed (HTTP \(status)): "
                + String(decoding: body.prefix(300), as: UTF8.self))
        }
    }

    private static func contentType(_ file: URL) -> String {
        switch file.pathExtension {
        case "m4a": return "audio/mp4"
        case "gz": return "application/gzip"
        case "csv": return "text/csv"
        case "json": return "application/json"
        default: return "application/octet-stream"
        }
    }

    private final class ProgressDelegate: NSObject, URLSessionTaskDelegate {
        let sent: (Int64) -> Void
        init(sent: @escaping (Int64) -> Void) { self.sent = sent }
        func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                        totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
            sent(totalBytesSent)
        }
    }

    // MARK: service-account auth (OAuth 2 JWT bearer grant)

    private struct Key: Decodable {
        let client_email: String
        let private_key: String
        let token_uri: String
    }

    private func accessToken() async throws -> String {
        if let token, token.expires > Date().addingTimeInterval(60) { return token.value }
        guard let keyFile = Bundle.main.url(forResource: "uploader-key", withExtension: "json") else {
            throw Failure("No uploader-key.json in the app. Run `just ios-key`, then `just ios`, and rebuild.")
        }
        let key = try JSONDecoder().decode(Key.self, from: Data(contentsOf: keyFile))
        let assertion = try Self.signedJWT(
            issuer: key.client_email, audience: key.token_uri,
            scope: "https://www.googleapis.com/auth/devstorage.read_write",
            privateKeyPEM: key.private_key, now: Date())

        var request = URLRequest(url: URL(string: key.token_uri)!)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data(
            "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion=\(assertion)".utf8)
        let (body, response) = try await URLSession.shared.data(for: request)
        struct Reply: Decodable {
            let access_token: String
            let expires_in: Double
        }
        guard (response as? HTTPURLResponse)?.statusCode == 200,
              let reply = try? JSONDecoder().decode(Reply.self, from: body)
        else {
            throw Failure("Sign-in to Google Cloud failed: " + String(decoding: body.prefix(300), as: UTF8.self))
        }
        token = (reply.access_token, Date().addingTimeInterval(reply.expires_in))
        return reply.access_token
    }

    /// An RS256 JWT asking for `scope`, signed with a service account's private key
    /// (PEM, PKCS#8: the `private_key` field of a key file).
    static func signedJWT(issuer: String, audience: String, scope: String, privateKeyPEM: String,
                          now: Date) throws -> String {
        let iat = Int(now.timeIntervalSince1970)
        let header = try JSONSerialization.data(withJSONObject: ["alg": "RS256", "typ": "JWT"])
        let claims = try JSONSerialization.data(withJSONObject: [
            "iss": issuer, "scope": scope, "aud": audience, "iat": iat, "exp": iat + 3600,
        ] as [String: Any])
        let signingInput = base64URL(header) + "." + base64URL(claims)

        let base64 = privateKeyPEM.split(separator: "\n").filter { !$0.hasPrefix("-----") }.joined()
        guard let pkcs8 = Data(base64Encoded: base64) else { throw Failure("The service account key isn't valid PEM.") }
        var error: Unmanaged<CFError>?
        let attributes = [kSecAttrKeyType: kSecAttrKeyTypeRSA, kSecAttrKeyClass: kSecAttrKeyClassPrivate] as CFDictionary
        guard let secKey = SecKeyCreateWithData(try rsaKey(fromPKCS8: pkcs8) as CFData, attributes, &error),
              let signature = SecKeyCreateSignature(
                  secKey, .rsaSignatureMessagePKCS1v15SHA256, Data(signingInput.utf8) as CFData, &error)
        else {
            throw Failure("Couldn't sign with the service account key: \(String(describing: error?.takeRetainedValue()))")
        }
        return signingInput + "." + base64URL(signature as Data)
    }

    private static func base64URL(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    /// The Security framework wants the bare RSA key (PKCS#1); a key file has it wrapped in
    /// PKCS#8: SEQUENCE { INTEGER version, SEQUENCE algorithm, OCTET STRING <the PKCS#1 key> }.
    private static func rsaKey(fromPKCS8 der: Data) throws -> Data {
        let bytes = [UInt8](der)
        var at = 0
        /// Reads one DER element's header at `at`; returns its content range and leaves
        /// `at` at the start of the content.
        func element(_ tag: UInt8) throws -> Range<Int> {
            guard at + 2 <= bytes.count, bytes[at] == tag else { throw Failure("Unexpected service account key format.") }
            var length = Int(bytes[at + 1])
            at += 2
            if length & 0x80 != 0 {
                let count = length & 0x7f
                guard count <= 4, at + count <= bytes.count else { throw Failure("Unexpected service account key format.") }
                length = bytes[at..<at + count].reduce(0) { $0 << 8 | Int($1) }
                at += count
            }
            guard at + length <= bytes.count else { throw Failure("Unexpected service account key format.") }
            return at..<at + length
        }
        _ = try element(0x30)  // outer SEQUENCE: step inside
        at = try element(0x02).upperBound  // version: skip
        at = try element(0x30).upperBound  // algorithm: skip
        return Data(bytes[try element(0x04)])
    }
}
