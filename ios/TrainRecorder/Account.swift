import AuthenticationServices
import CryptoKit
import Foundation
import Observation

/// The optional account: sign in with Apple, Google or an emailed link (Identity Platform,
/// through its REST API, so no SDK), registration with the backend, sign out, deletion.
/// docs/accounts-and-uploads.md.
@MainActor
@Observable
final class Account {
    static let shared = Account()

    struct User: Codable, Equatable {
        let uid: String
        let email: String?
        let provider: String  // apple.com, google.com or email

        var providerName: String {
            switch provider {
            case "apple.com": return "Apple"
            case "google.com": return "Google"
            default: return "email link"
            }
        }
    }

    private(set) var user: User?
    private(set) var busy = false
    var error: String?
    var notice: String?
    /// Goes up with every successful sign-in, so the app can offer what follows one
    /// (linking earlier uploads, syncing rides already on the phone).
    private(set) var signIns = 0

    @ObservationIgnored private var idToken: (value: String, expires: Date)?
    @ObservationIgnored private var appleNonce = ""

    private static let userKey = "account.user"
    private static let refreshKey = "refreshToken"
    private static let pendingEmailKey = "account.pendingEmail"

    init() {
        if Keychain.get(Self.refreshKey) != nil, let data = UserDefaults.standard.data(forKey: Self.userKey) {
            user = try? JSONDecoder().decode(User.self, from: data)
        }
    }

    /// A current ID token for the backend, refreshed when it's about to expire.
    func token() async throws -> String {
        if let idToken, idToken.expires > Date().addingTimeInterval(120) { return idToken.value }
        guard user != nil, let refresh = Keychain.get(Self.refreshKey) else {
            throw APIError(status: 401, code: "signed_out", message: "You're not signed in.")
        }
        do {
            let tokens = try await Identity.refresh(refresh)
            Keychain.set(Self.refreshKey, tokens.refreshToken)
            idToken = (tokens.idToken, Date().addingTimeInterval(tokens.expiresIn))
            return tokens.idToken
        } catch let failure as Identity.Failure where failure.signsOut {
            signOut()
            error = "You've been signed out: \(failure.message)"
            throw APIError(status: 401, code: "signed_out", message: error!)
        }
    }

    // MARK: sign in

    /// For SignInWithAppleButton's onRequest.
    func prepare(_ request: ASAuthorizationAppleIDRequest) {
        appleNonce = Self.randomString()
        request.requestedScopes = [.email]
        request.nonce = Self.sha256(appleNonce)
    }

    func signInWithApple(_ result: Result<ASAuthorization, Error>) async {
        guard let credential = appleCredential(result), let token = credential.identityToken.flatMap({ String(data: $0, encoding: .utf8) }) else { return }
        let nonce = appleNonce
        await run {
            let tokens = try await Identity.signInWithIdp(postBody: "id_token=\(token)&providerId=apple.com&nonce=\(nonce)")
            try await self.finishSignIn(tokens, provider: "apple.com")
        }
    }

    struct GoogleRequest {
        let url: URL
        let callbackScheme: String
        let redirect: String
        let verifier: String
        let state: String
    }

    /// The Google authorization page to open (in a web authentication session), with PKCE.
    func googleRequest() -> GoogleRequest? {
        guard let clientID = BackendConfig.googleClientID else {
            error = BackendConfig.notConfigured
            return nil
        }
        // an iOS OAuth client's redirect: its id reversed, as a URL scheme
        let scheme = clientID.split(separator: ".").reversed().joined(separator: ".")
        let redirect = "\(scheme):/oauth2redirect"
        let verifier = Self.randomString(64)
        let state = Self.randomString()
        let challenge = Data(SHA256.hash(data: Data(verifier.utf8))).base64URL
        var url = URLComponents(string: "https://accounts.google.com/o/oauth2/v2/auth")!
        url.queryItems = [
            URLQueryItem(name: "client_id", value: clientID),
            URLQueryItem(name: "redirect_uri", value: redirect),
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "scope", value: "openid email"),
            URLQueryItem(name: "code_challenge", value: challenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "state", value: state),
            URLQueryItem(name: "prompt", value: "select_account"),
        ]
        return GoogleRequest(url: url.url!, callbackScheme: scheme, redirect: redirect, verifier: verifier, state: state)
    }

    func signInWithGoogle(callback: URL, request: GoogleRequest) async {
        let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []
        let value = { (name: String) in items.first { $0.name == name }?.value }
        guard value("state") == request.state, let code = value("code") else {
            if value("error") != "access_denied" { error = "Google sign-in didn't complete." }
            return
        }
        await run {
            var exchange = URLRequest(url: URL(string: "https://oauth2.googleapis.com/token")!)
            exchange.httpMethod = "POST"
            exchange.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
            exchange.httpBody = Identity.form([
                "code": code, "client_id": BackendConfig.googleClientID ?? "", "redirect_uri": request.redirect,
                "grant_type": "authorization_code", "code_verifier": request.verifier,
            ])
            let (data, _) = try await URLSession.shared.data(for: exchange)
            guard let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let idToken = body["id_token"] as? String else {
                throw Identity.Failure(message: "Google sign-in didn't complete.", code: "")
            }
            let tokens = try await Identity.signInWithIdp(postBody: "id_token=\(idToken)&providerId=google.com")
            try await self.finishSignIn(tokens, provider: "google.com")
        }
    }

    func sendEmailLink(to email: String) async {
        let email = email.trimmingCharacters(in: .whitespacesAndNewlines)
        guard email.contains("@"), let base = BackendConfig.baseURL else {
            error = BackendConfig.configured ? "That doesn't look like an email address." : BackendConfig.notConfigured
            return
        }
        await run {
            try await Identity.post("accounts:sendOobCode", [
                "requestType": "EMAIL_SIGNIN", "email": email,
                "continueUrl": base.appendingPathComponent("auth/email-link").absoluteString,
                "canHandleCodeInApp": true, "iOSBundleId": Bundle.main.bundleIdentifier ?? "",
            ])
            UserDefaults.standard.set(email, forKey: Self.pendingEmailKey)
            self.notice = "We've emailed a sign-in link to \(email). Open it on this phone."
        }
    }

    /// An emailed sign-in link, opened on this phone (a universal link to the backend's
    /// /auth/…). Returns whether it was one.
    @discardableResult
    func handle(url: URL) -> Bool {
        guard url.host == BackendConfig.host, url.path.hasPrefix("/auth/") else { return false }
        guard let code = Self.oobCode(in: url) else {
            error = "That sign-in link isn't complete. Ask for a new one."
            return true
        }
        guard let email = UserDefaults.standard.string(forKey: Self.pendingEmailKey) else {
            error = "Open the sign-in link on the phone where you asked for it, or ask for a new one here."
            return true
        }
        Task {
            await run {
                let tokens = try await Identity.post("accounts:signInWithEmailLink", ["email": email, "oobCode": code])
                try await self.finishSignIn(Identity.Tokens(tokens), provider: "email")
                UserDefaults.standard.removeObject(forKey: Self.pendingEmailKey)
            }
        }
        return true
    }

    /// The link's one-time code: in its own query, or in a link nested in it.
    private static func oobCode(in url: URL) -> String? {
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        if let code = items.first(where: { $0.name == "oobCode" })?.value { return code }
        for nested in items where ["link", "continueUrl", "deep_link_id"].contains(nested.name) {
            if let value = nested.value, let inner = URL(string: value), let code = oobCode(in: inner) { return code }
        }
        return nil
    }

    /// Registers with the backend (which refuses new accounts beyond the day's limit), then
    /// remembers the account.
    private func finishSignIn(_ tokens: Identity.Tokens, provider: String) async throws {
        idToken = (tokens.idToken, Date().addingTimeInterval(tokens.expiresIn))
        do {
            try await API.call("POST", "v1/account", auth: .required)
        } catch {
            idToken = nil
            throw error
        }
        Keychain.set(Self.refreshKey, tokens.refreshToken)
        let user = User(uid: tokens.uid, email: tokens.email, provider: provider)
        UserDefaults.standard.set(try? JSONEncoder().encode(user), forKey: Self.userKey)
        self.user = user
        notice = nil
        signIns += 1
    }

    // MARK: sign out, delete

    /// Forgets the account on this phone. Rides and recordings stay.
    func signOut() {
        Keychain.set(Self.refreshKey, nil)
        UserDefaults.standard.removeObject(forKey: Self.userKey)
        idToken = nil
        user = nil
    }

    /// Deletes the account on the server (its synced rides at once; its uploads are marked
    /// for deletion), then signs out. An Apple account needs a fresh Apple authorization
    /// (`apple`), to revoke the app's Apple tokens as Apple requires. Returns whether it worked.
    func deleteAccount(apple: Result<ASAuthorization, Error>? = nil) async -> Bool {
        guard let user else { return false }
        var code: String?
        if user.provider == "apple.com" {
            guard let apple, let credential = appleCredential(apple),
                  let data = credential.authorizationCode, let value = String(data: data, encoding: .utf8) else { return false }
            code = value
        }
        var done = false
        await run {
            if let code {
                try await Identity.post("accounts:revokeToken", version: "v2", [
                    "idToken": try await self.token(), "providerId": "apple.com", "tokenType": "CODE", "token": code,
                ])
            }
            try await API.call("DELETE", "v1/account", auth: .required)
            self.signOut()
            done = true
        }
        return done
    }

    // MARK: helpers

    private func appleCredential(_ result: Result<ASAuthorization, Error>) -> ASAuthorizationAppleIDCredential? {
        switch result {
        case .success(let authorization):
            return authorization.credential as? ASAuthorizationAppleIDCredential
        case .failure(let failure):
            if (failure as? ASAuthorizationError)?.code != .canceled {
                error = "Sign in with Apple didn't complete: \(failure.localizedDescription)"
            }
            return nil
        }
    }

    private func run(_ body: @escaping () async throws -> Void) async {
        guard BackendConfig.configured else {
            error = BackendConfig.notConfigured
            return
        }
        busy = true
        error = nil
        defer { busy = false }
        do {
            try await body()
        } catch {
            self.error = error.localizedDescription
        }
    }

    static func randomString(_ length: Int = 32) -> String {
        let characters = Array("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        var generator = SystemRandomNumberGenerator()
        return String((0..<length).map { _ in characters.randomElement(using: &generator)! })
    }

    static func sha256(_ text: String) -> String {
        SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}

/// Identity Platform's REST API (what the Firebase SDKs call underneath).
enum Identity {
    struct Failure: LocalizedError {
        let message: String
        let code: String
        var errorDescription: String? { message }
        /// The refresh token is no good any more: sign out.
        var signsOut: Bool { ["TOKEN_EXPIRED", "USER_DISABLED", "USER_NOT_FOUND", "INVALID_REFRESH_TOKEN"].contains(code) }
    }

    struct Tokens {
        let idToken: String
        let refreshToken: String
        let expiresIn: Double
        let uid: String
        let email: String?

        init(_ body: [String: Any]) throws {
            guard let idToken = body["idToken"] as? String, let refreshToken = body["refreshToken"] as? String,
                  let uid = body["localId"] as? String else {
                throw Failure(message: "Sign-in failed: an unexpected answer from the server.", code: "")
            }
            (self.idToken, self.refreshToken, self.uid) = (idToken, refreshToken, uid)
            expiresIn = Double(body["expiresIn"] as? String ?? "") ?? 3600
            email = body["email"] as? String
        }
    }

    static func signInWithIdp(postBody: String) async throws -> Tokens {
        try Tokens(await post("accounts:signInWithIdp", [
            "postBody": postBody, "requestUri": "http://localhost",
            "returnSecureToken": true, "returnIdpCredential": true,
        ]))
    }

    static func refresh(_ refreshToken: String) async throws -> (idToken: String, refreshToken: String, expiresIn: Double) {
        var request = URLRequest(url: URL(string: "https://securetoken.googleapis.com/v1/token?key=\(BackendConfig.identityAPIKey ?? "")")!)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.httpBody = form(["grant_type": "refresh_token", "refresh_token": refreshToken])
        let body = try await send(request)
        guard let id = body["id_token"] as? String, let refresh = body["refresh_token"] as? String else {
            throw Failure(message: "Couldn't refresh the sign-in.", code: "")
        }
        return (id, refresh, Double(body["expires_in"] as? String ?? "") ?? 3600)
    }

    @discardableResult
    static func post(_ method: String, version: String = "v1", _ json: [String: Any]) async throws -> [String: Any] {
        var request = URLRequest(url: URL(string: "https://identitytoolkit.googleapis.com/\(version)/\(method)?key=\(BackendConfig.identityAPIKey ?? "")")!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: json)
        return try await send(request)
    }

    private static func send(_ request: URLRequest) async throws -> [String: Any] {
        var request = request
        // the API key only works from this app (infra: the key's iOS restriction)
        request.setValue(Bundle.main.bundleIdentifier, forHTTPHeaderField: "X-Ios-Bundle-Identifier")
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw Failure(message: "Couldn't reach the sign-in server: \(error.localizedDescription)", code: "network")
        }
        let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            // {"error": {"message": "INVALID_OOB_CODE"}} (securetoken: "TOKEN_EXPIRED" etc. the same way)
            let raw = (body["error"] as? [String: Any])?["message"] as? String ?? (body["error"] as? String) ?? "unknown error"
            let code = String(raw.split(separator: " ").first ?? "")
            throw Failure(message: describe(code, raw), code: code)
        }
        return body
    }

    private static func describe(_ code: String, _ raw: String) -> String {
        switch code {
        case "EXPIRED_OOB_CODE", "INVALID_OOB_CODE": return "This sign-in link has expired or was already used. Ask for a new one."
        case "INVALID_EMAIL": return "That doesn't look like an email address."
        case "USER_DISABLED": return "This account has been disabled."
        case "TOKEN_EXPIRED", "USER_NOT_FOUND", "INVALID_REFRESH_TOKEN": return "the account's sign-in is no longer valid."
        default: return "Sign-in failed (\(raw))."
        }
    }

    static func form(_ fields: [String: String]) -> Data {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "-._~")
        return Data(fields.map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: allowed) ?? "")" }
            .joined(separator: "&").utf8)
    }
}
