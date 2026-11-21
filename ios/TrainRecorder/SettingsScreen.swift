import AuthenticationServices
import SwiftUI

struct SettingsScreen: View {
    let store: RideStore
    let protecting: Set<String>
    @AppStorage(Units.key) private var metric = false
    @AppStorage(AppSettings.askToUploadKey) private var askToUpload = true
    @AppStorage(AppSettings.keepRecordingsKey) private var keep = KeepRecordings.last3.rawValue
    @AppStorage(AppSettings.shareMeasurementsKey) private var shareMeasurements = false
    @State private var confirmDeleteMeasurements = false
    @State private var deleteMeasurementsMessage: String?
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
                    Toggle("Always share", isOn: $shareMeasurements)
                        .onChange(of: shareMeasurements) { AppSettings.askedShareMeasurements = true }
                    Button("Delete shared measurements", role: .destructive) { confirmDeleteMeasurements = true }
                        .disabled(store.sharing || !BackendConfig.configured)
                } header: {
                    header("Train Measurement Sharing", "For train operations research", [
                        ("tram", "Position, speed, stops, exact times"),
                        ("waveform.slash", "No audio"),
                        ("person.2", "Anyone who asks gets it"),
                    ])
                } footer: {
                    if let message = deleteMeasurementsMessage ?? store.shareMessage { Text(message) }
                }
                Section {
                    Toggle("Ask to share after each ride", isOn: $askToUpload)
                    Button("Delete shared recordings", role: .destructive) { confirmDeleteUploads = true }
                        .disabled(working || !BackendConfig.configured)
                } header: {
                    header("Full Recording Sharing", "To improve the localizer", [
                        ("waveform", "Audio, including nearby voices"),
                        ("sensor", "Motion, compass, barometer"),
                        ("lock", "Only this app's devs get it"),
                    ])
                } footer: {
                    if let deleteUploadsMessage { Text(deleteUploadsMessage) }
                }
                Section {
                    Picker("On this phone", selection: Binding(
                        get: { KeepRecordings(rawValue: keep) ?? .last3 },
                        set: { choose($0) })
                    ) {
                        ForEach(KeepRecordings.allCases) { Text($0.label).tag($0) }
                    }
                }
                Section("Account") {
                    NavigationLink {
                        AccountScreen(store: store)
                    } label: {
                        Text(account.user.map { $0.email ?? "Signed in" } ?? "Not signed in")
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
            Text("They're shared, or you chose not to share them. Their Train Measurements stay.")
        }
        .confirmationDialog("Delete your shared Train Measurements?", isPresented: $confirmDeleteMeasurements,
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                Task {
                    if let n = await store.deleteShared() {
                        deleteMeasurementsMessage = n == 0 ? "Nothing was shared."
                            : "Deleted \(n) ride\(n == 1 ? "'s" : "s'") Train Measurements."
                    }
                }
            }
        } message: {
            Text("Deleted now. They stay on this phone.")
        }
        .confirmationDialog("Delete your shared recordings?", isPresented: $confirmDeleteUploads, titleVisibility: .visible) {
            Button("Delete", role: .destructive) { Task { await requestDeletion() } }
        } message: {
            Text("The developers delete them within 30 days. They stay on this phone.")
        }
    }

    /// A section's title, then what it's for and what's shared as icon lines, like the
    /// questions that ask (UploadConsent.swift).
    private func header(_ title: String, _ subtitle: String, _ rows: [(symbol: String, text: String)]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                Text(subtitle).font(.footnote).foregroundStyle(.secondary)
            }
            Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 8, verticalSpacing: 3) {
                ForEach(rows, id: \.text) { row in
                    GridRow {
                        Image(systemName: row.symbol)
                            .foregroundStyle(.tint)
                            .frame(width: 18)
                            .gridColumnAlignment(.center)
                        Text(row.text)
                    }
                }
            }
            .font(.footnote)
            .foregroundStyle(.secondary)
        }
        .textCase(nil)
        .padding(.bottom, 2)
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
                ? "Nothing was shared."
                : "\(n) shared recording\(n == 1 ? "" : "s") will be deleted within 30 days."
        } catch {
            deleteUploadsMessage = "Couldn't delete: \(error.localizedDescription)"
        }
    }
}

/// Signing in (Apple, Google, email link), and once signed in: the account, sign out,
/// delete account.
struct AccountScreen: View {
    let store: RideStore
    @Environment(\.webAuthenticationSession) private var webAuthenticationSession
    @Environment(\.colorScheme) private var colorScheme
    @State private var email = ""
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
            Text("Optional. What you share is linked to your account, to see on the web later.")
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
            Button("Sign out") { account.signOut() }
        }
        Section {
            Button("Delete account", role: .destructive) { confirmDeleteAccount = true }
        }
        .confirmationDialog("Delete your account?", isPresented: $confirmDeleteAccount, titleVisibility: .visible) {
            Button("Delete account", role: .destructive) {
                if user.provider == "apple.com" {
                    appleDeletion = true
                } else {
                    Task { if await account.deleteAccount() { await store.reconcileShared() } }
                }
            }
        } message: {
            Text("Its Train Measurements are deleted now, its full recordings within 30 days. Everything on this phone stays. This can't be undone.")
        }
        .sheet(isPresented: $appleDeletion) {
            VStack(spacing: 20) {
                Text("Confirm with Apple").font(.title2.bold())
                Text("Confirm with Apple once more. This also ends the app's access to your Apple ID.")
                    .multilineTextAlignment(.center)
                SignInWithAppleButton(.continue, onRequest: account.prepare) { result in
                    appleDeletion = false
                    Task { if await account.deleteAccount(apple: result) { await store.reconcileShared() } }
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
