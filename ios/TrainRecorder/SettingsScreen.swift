import AuthenticationServices
import SwiftUI

struct SettingsScreen: View {
    let store: RideStore
    let protecting: Set<String>
    @AppStorage(Units.key) private var metric = false
    @AppStorage(AppSettings.askToUploadKey) private var askToUpload = true
    @AppStorage(AppSettings.keepRecordingsKey) private var keep = KeepRecordings.last3.rawValue
    /// A "keep" setting that deletes recordings now, waiting to be confirmed.
    @State private var pendingKeep: (setting: KeepRecordings, deletes: Int)?
    @State private var confirmDeleteUploads = false
    @State private var deleteUploadsMessage: String?
    @State private var working = false
    private let account = Account.shared

    var body: some View {
        NavigationStack {
            Form {
                Section("Units") {
                    Picker("Units", selection: $metric) {
                        Text("Imperial").tag(false)
                        Text("Metric").tag(true)
                    }
                    .pickerStyle(.segmented)
                }
                Section {
                    Toggle("Ask to upload after each ride", isOn: $askToUpload)
                    Picker("On this phone", selection: Binding(
                        get: { KeepRecordings(rawValue: keep) ?? .last3 },
                        set: { choose($0) })
                    ) {
                        ForEach(KeepRecordings.allCases) { Text($0.label).tag($0) }
                    }
                } header: {
                    Text("Recordings")
                } footer: {
                    Text("Ride timings are always kept. A recording that's waiting to be uploaded, or that you haven't been asked about, is never deleted automatically.")
                }
                Section {
                    Button("Ask to delete my uploaded recordings", role: .destructive) { confirmDeleteUploads = true }
                        .disabled(working || !BackendConfig.configured)
                } header: {
                    Text("Uploaded recordings")
                } footer: {
                    Text(deleteUploadsMessage ?? "Covers the recordings uploaded from this phone"
                        + (account.user != nil ? " and those linked to your account" : "")
                        + ". The developers delete them by hand, within 30 days.")
                }
                Section("Account") {
                    NavigationLink {
                        AccountScreen(store: store)
                    } label: {
                        LabeledContent("Account", value: account.user.map { $0.email ?? "Signed in" } ?? "Not signed in")
                    }
                }
                if let policy = BackendConfig.privacyPolicy {
                    Section { Link("Privacy policy", destination: policy) }
                }
            }
            .navigationTitle("Settings")
        }
        .confirmationDialog(
            "Delete \(pendingKeep?.deletes ?? 0) recording\(pendingKeep?.deletes == 1 ? "" : "s") from this phone now?",
            isPresented: Binding(get: { pendingKeep != nil }, set: { if !$0 { pendingKeep = nil } }),
            titleVisibility: .visible
        ) {
            Button("Delete", role: .destructive) {
                if let pending = pendingKeep { apply(pending.setting) }
                pendingKeep = nil
            }
        } message: {
            Text("They're uploaded, or you chose not to upload them. Their ride timings stay.")
        }
        .confirmationDialog("Ask to delete your uploaded recordings?", isPresented: $confirmDeleteUploads, titleVisibility: .visible) {
            Button("Ask to delete them", role: .destructive) { Task { await requestDeletion() } }
        } message: {
            Text("Every recording uploaded from this phone" + (account.user != nil ? " or linked to your account" : "")
                + " will be deleted by the developers within 30 days. Recordings on this phone stay.")
        }
    }

    private func choose(_ setting: KeepRecordings) {
        let deletes = store.prunable(setting, protecting: protecting).count
        if deletes > 0 { pendingKeep = (setting, deletes) } else { apply(setting) }
    }

    private func apply(_ setting: KeepRecordings) {
        keep = setting.rawValue
        store.prune(protecting: protecting)
    }

    private func requestDeletion() async {
        working = true
        defer { working = false }
        do {
            let body = try await API.call("POST", "v1/uploads/deletion-request", auth: .ifSignedIn, device: true)
            let n = body["requested"] as? Int ?? 0
            deleteUploadsMessage = n == 0
                ? "There were no uploaded recordings left to delete."
                : "Asked: \(n) uploaded recording\(n == 1 ? "" : "s") will be deleted within 30 days."
        } catch {
            deleteUploadsMessage = "Couldn't ask: \(error.localizedDescription)"
        }
    }
}

/// Signing in (Apple, Google, email link), and once signed in: syncing ride timings, sign
/// out, delete account.
struct AccountScreen: View {
    let store: RideStore
    @AppStorage(AppSettings.syncRidesKey) private var syncRides = true
    @Environment(\.webAuthenticationSession) private var webAuthenticationSession
    @Environment(\.colorScheme) private var colorScheme
    @State private var email = ""
    @State private var confirmDeleteTimings = false
    @State private var confirmDeleteAccount = false
    @State private var appleDeletion = false
    private let account = Account.shared

    var body: some View {
        Form {
            if let user = account.user { signedIn(user) } else { signedOut }
            if let error = account.error {
                Section { Text(error).foregroundStyle(.red) }
            }
            if account.busy {
                Section { ProgressView().frame(maxWidth: .infinity) }
            }
        }
        .navigationTitle("Account")
        .disabled(account.busy)
        .onDisappear { account.error = nil }
    }

    // MARK: signed out

    @ViewBuilder private var signedOut: some View {
        Section {
            Text("Signing in is optional. With an account, each ride's stops and times are synced to it, and recordings you upload are linked to it, so you'll be able to see them on the web later.")
                .font(.callout)
        }
        Section {
            SignInWithAppleButton(.signIn, onRequest: account.prepare) { result in
                Task { await account.signInWithApple(result) }
            }
            .signInWithAppleButtonStyle(colorScheme == .dark ? .white : .black)
            .frame(height: 44)
            .listRowInsets(EdgeInsets())
            Button { Task { await signInWithGoogle() } } label: {
                Text("Sign in with Google").frame(maxWidth: .infinity)
            }
        }
        Section {
            TextField("you@example.com", text: $email)
                .keyboardType(.emailAddress)
                .textContentType(.emailAddress)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            Button("Email me a sign-in link") { Task { await account.sendEmailLink(to: email) } }
                .disabled(email.isEmpty)
        } header: {
            Text("Or with an email link")
        } footer: {
            if let notice = account.notice { Text(notice) }
        }
    }

    private func signInWithGoogle() async {
        guard let request = account.googleRequest() else { return }
        do {
            let callback = try await webAuthenticationSession.authenticate(
                using: request.url, callbackURLScheme: request.callbackScheme, preferredBrowserSession: .ephemeral)
            await account.signInWithGoogle(callback: callback, request: request)
        } catch let error as ASWebAuthenticationSessionError where error.code == .canceledLogin {
        } catch {
            account.error = "Google sign-in didn't complete: \(error.localizedDescription)"
        }
    }

    // MARK: signed in

    @ViewBuilder private func signedIn(_ user: Account.User) -> some View {
        Section {
            LabeledContent("Signed in", value: user.email ?? "–")
            LabeledContent("With", value: user.providerName)
        }
        Section {
            Toggle("Sync ride timings to my account", isOn: $syncRides)
            let unsynced = store.unsynced(to: user.uid).count
            Button(unsynced == 0 ? "All rides on this phone are synced" : "Sync \(unsynced) unsynced ride\(unsynced == 1 ? "" : "s")") {
                Task { await store.syncAll() }
            }
            .disabled(unsynced == 0 || store.syncing)
            Button("Delete all ride timings from my account", role: .destructive) { confirmDeleteTimings = true }
                .disabled(store.syncing)
        } header: {
            Text("Ride timings")
        } footer: {
            Text(store.syncMessage ?? "When sync is on, each ride's stops and times go to your account when you stop recording. Deleting them from your account leaves them on this phone.")
        }
        .confirmationDialog("Delete all ride timings from your account?", isPresented: $confirmDeleteTimings, titleVisibility: .visible) {
            Button("Delete", role: .destructive) { Task { _ = await store.deleteAllFromAccount() } }
        } message: {
            Text("The rides stay on this phone, and can be synced again.")
        }
        Section {
            Button("Sign out") { account.signOut() }
        } footer: {
            Text("Your rides and recordings stay on this phone.")
        }
        Section {
            Button("Delete account", role: .destructive) { confirmDeleteAccount = true }
        } footer: {
            Text("Deletes your account and the ride timings synced to it, and the recordings you uploaded that are linked to it (the developers delete those by hand, within 30 days).")
        }
        .confirmationDialog("Delete your account?", isPresented: $confirmDeleteAccount, titleVisibility: .visible) {
            Button("Delete account", role: .destructive) {
                if user.provider == "apple.com" {
                    appleDeletion = true
                } else {
                    Task { if await account.deleteAccount() { store.forgetSync() } }
                }
            }
        } message: {
            Text("Your account and its ride timings are deleted now. Recordings you uploaded that are linked to it are deleted within 30 days. Rides and recordings on this phone stay. This can't be undone.")
        }
        .sheet(isPresented: $appleDeletion) {
            VStack(spacing: 20) {
                Text("Confirm with Apple").font(.title2.bold())
                Text("To delete your account, confirm with Apple once more. This also stops the app's access to your Apple ID.")
                    .multilineTextAlignment(.center)
                SignInWithAppleButton(.continue, onRequest: account.prepare) { result in
                    appleDeletion = false
                    Task { if await account.deleteAccount(apple: result) { store.forgetSync() } }
                }
                .signInWithAppleButtonStyle(colorScheme == .dark ? .white : .black)
                .frame(height: 50)
                Button("Cancel") { appleDeletion = false }
            }
            .padding()
            .presentationDetents([.medium])
        }
    }
}
