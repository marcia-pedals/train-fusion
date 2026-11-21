import Foundation

/// Where the backend is (docs/api.md), and the Identity Platform settings sign-in needs.
/// They come from Info.plist, filled from ios/Config.xcconfig (`just ios-config`); a build
/// without it records and replays as usual but can't upload or sign in.
enum BackendConfig {
    private static func info(_ key: String) -> String? {
        guard let value = Bundle.main.object(forInfoDictionaryKey: key) as? String,
              !value.isEmpty, !value.hasPrefix("$("), !value.hasSuffix(".invalid") else { return nil }
        return value
    }

    static let host = info("TRBackendHost")
    static let identityAPIKey = info("TRIdentityAPIKey")
    static let googleClientID = info("TRGoogleClientID")

    static var configured: Bool { host != nil && identityAPIKey != nil }
    static var baseURL: URL? { host.flatMap { URL(string: "https://\($0)") } }
    static var privacyPolicy: URL? { baseURL?.appendingPathComponent("privacy") }

    static let notConfigured = "This build of the app isn't connected to a server, so it can't upload or sign in."
}

/// An error the backend (or the network) gave, with a message to show.
struct APIError: LocalizedError {
    let status: Int  // 0: no answer
    let code: String
    let message: String
    var retryAfter: Double?
    var missing: [String] = []

    var errorDescription: String? { message }
}

/// Calls to the backend. `auth`: whether to send the account's token; `device`: whether to
/// send the device secret.
@MainActor
enum API {
    enum Auth { case none, required, ifSignedIn }

    @discardableResult
    static func call(_ method: String, _ path: String, json: Any? = nil,
                     auth: Auth = .none, device: Bool = false) async throws -> [String: Any] {
        guard let base = BackendConfig.baseURL else {
            throw APIError(status: 0, code: "not_configured", message: BackendConfig.notConfigured)
        }
        var request = URLRequest(url: base.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = 30
        if let json {
            request.httpBody = try JSONSerialization.data(withJSONObject: json)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        switch auth {
        case .none: break
        case .required:
            request.setValue("Bearer \(try await Account.shared.token())", forHTTPHeaderField: "Authorization")
        case .ifSignedIn:
            if Account.shared.user != nil {
                request.setValue("Bearer \(try await Account.shared.token())", forHTTPHeaderField: "Authorization")
            }
        }
        if device { request.setValue(DeviceSecret.value, forHTTPHeaderField: "X-Device-Secret") }
        return try await send(request)
    }

    /// Sends `request` and returns its JSON body, or throws an `APIError` for anything but 2xx.
    static func send(_ request: URLRequest) async throws -> [String: Any] {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw APIError(status: 0, code: "network", message: "Couldn't reach the server: \(error.localizedDescription)")
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        guard (200..<300).contains(status) else {
            throw APIError(
                status: status,
                code: body["error"] as? String ?? "http_\(status)",
                message: body["message"] as? String ?? "The server answered \(status).",
                retryAfter: body["retry_after_s"] as? Double ?? (body["retry_after_s"] as? Int).map(Double.init),
                missing: body["missing"] as? [String] ?? [])
        }
        return body
    }
}
