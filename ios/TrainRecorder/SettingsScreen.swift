import AuthenticationServices
import SwiftUI

/// Settings' choices for sharing Train Measurements (AppSettings' two flags).
enum MeasurementSharing: CaseIterable, Identifiable {
    case ask, always, never

    var id: Self { self }

    var label: String {
        switch self {
        case .ask: return "Ask each ride"
        case .always: return "Always"
        case .never: return "Never"
        }
    }
}

struct SettingsScreen: View {
    let store: RideStore
    let protecting: Set<String>
    @AppStorage(Units.key) private var metric = false
    @AppStorage(AppSettings.askToUploadKey) private var askToUpload = true
    @AppStorage(AppSettings.keepRecordingsKey) private var keep = KeepRecordings.last3.rawValue
    @AppStorage(AppSettings.shareMeasurementsKey) private var shareMeasurements = false
    @AppStorage(AppSettings.askedShareMeasurementsKey) private var askedShare = false
    @State private var info: SharingInfoSheet?
    /// For screenshots from the command line: -account opens the account screen.
    @State private var showingAccount = ProcessInfo.processInfo.arguments.contains("-account")
    /// A "keep" setting that deletes recordings now, waiting to be confirmed.
    @State private var pendingKeep: (setting: KeepRecordings, deletes: Int)?
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
                Section("Phone Data") {
                    Picker("Keep Recordings", selection: Binding(
                        get: { KeepRecordings(rawValue: keep) ?? .last3 },
                        set: { choose($0) })
                    ) {
                        ForEach(KeepRecordings.allCases) { Text($0.label).tag($0) }
                    }
                    .confirmationDialog(
                        "Delete \(pendingKeep?.deletes ?? 0) Full Recording\(pendingKeep?.deletes == 1 ? "" : "s") from this phone now?",
                        isPresented: Binding(get: { pendingKeep != nil }, set: { if !$0 { pendingKeep = nil } }),
                        titleVisibility: .visible
                    ) {
                        Button("Delete", role: .destructive) {
                            if let pending = pendingKeep { apply(pending.setting) }
                            pendingKeep = nil
                        }
                    } message: {
                        Text("Their Train Measurements stay.")
                    }
                }
                Section {
                    shareRow("Measurements", .measurements, selection: Binding(
                        get: { shareMeasurements ? .always : askedShare ? .never : .ask },
                        set: { (new: MeasurementSharing) in
                            shareMeasurements = new == .always
                            askedShare = new != .ask
                        }),
                        options: MeasurementSharing.allCases.map { ($0, $0.label) })
                    shareRow("Recordings", .recordings, selection: $askToUpload,
                             options: [(true, "Ask each ride"), (false, "Never")])
                    // a screen of its own, so it isn't tapped by accident
                    NavigationLink("Delete Shared Data") { DeleteSharedScreen(store: store) }
                } header: {
                    Text("Sharing")
                } footer: {
                    if let message = store.shareMessage { Text(message) }
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
            .navigationDestination(isPresented: $showingAccount) { AccountScreen(store: store) }
        }
        .sheet(item: $info) { $0 }
    }

    /// What's shared ("Measurements", "Recordings"), with a ? that shows what that is (like the question that asks), and the
    /// choice as a menu on the same line (a Picker moves a long value onto a line of its own).
    private func shareRow<V: Hashable>(_ title: String, _ kind: SharingInfoSheet.Kind, selection: Binding<V>,
                                       options: [(value: V, label: String)]) -> some View {
        HStack(spacing: 6) {
            Text(title)
            Button { info = SharingInfoSheet(kind: kind) } label: {
                Image(systemName: "questionmark.circle")
            }
            .buttonStyle(.borderless)
            .accessibilityLabel("What's shared")
            Spacer(minLength: 12)
            Menu {
                Picker(title, selection: selection) {
                    ForEach(options, id: \.value) { Text($0.label).tag($0.value) }
                }
            } label: {
                HStack(spacing: 4) {
                    Text(options.first { $0.value == selection.wrappedValue }?.label ?? "")
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                    Image(systemName: "chevron.up.chevron.down").font(.footnote.weight(.medium))
                }
            }
            .tint(.secondary)  // grey, like a Picker's value ("On this phone")
            .accessibilityLabel("Share " + title)
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
}

/// Asking for everything shared to be deleted: all Train Measurements, all recordings.
/// Behind its own row at the bottom of Settings so it isn't tapped by accident (one ride's
/// can be unshared from History).
struct DeleteSharedScreen: View {
    let store: RideStore
    @State private var confirmDeleteMeasurements = false
    @State private var deleteMeasurementsMessage: String?
    @State private var confirmDeleteUploads = false
    @State private var deleteUploadsMessage: String?
    @State private var working = false

    var body: some View {
        Form {
            Section {
                Text("Delete everything shared from this phone, and linked to your account. To delete just one ride, open it in History.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Section {
                Button("Delete all shared Train Measurements", role: .destructive) { confirmDeleteMeasurements = true }
                    .disabled(store.sharing || !BackendConfig.configured)
                    .confirmationDialog("Delete all shared Train Measurements?", isPresented: $confirmDeleteMeasurements,
                                        titleVisibility: .visible) {
                        Button("Delete", role: .destructive) {
                            Task {
                                if let n = await store.deleteShared() {
                                    deleteMeasurementsMessage = n == 0 ? "Nothing was shared."
                                        : "\(n) ride\(n == 1 ? "'s" : "s'") Train Measurements will be deleted within 30 days."
                                }
                            }
                        }
                    } message: {
                        Text("Deletes all your shared Train Measurements from the cloud. Train Measurements on your phone stay on your phone.")
                    }
            } footer: {
                if let message = deleteMeasurementsMessage ?? store.shareMessage { Text(message) }
            }
            Section {
                Button("Delete all shared Full Recordings", role: .destructive) { confirmDeleteUploads = true }
                    .disabled(working || !BackendConfig.configured)
                    .confirmationDialog("Delete all shared Full Recordings?", isPresented: $confirmDeleteUploads,
                                        titleVisibility: .visible) {
                        Button("Delete", role: .destructive) { Task { await requestDeletion() } }
                    } message: {
                        Text("Deletes all your shared Full Recordings from the cloud. Full Recordings on your phone stay on your phone.")
                    }
            } footer: {
                if let deleteUploadsMessage { Text(deleteUploadsMessage) }
            }
        }
        .navigationTitle("Delete Shared Data")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func requestDeletion() async {
        working = true
        defer { working = false }
        do {
            let body = try await API.call("POST", "v1/uploads/deletion-request", auth: .ifSignedIn, device: true)
            let n = body["requested"] as? Int ?? 0
            deleteUploadsMessage = n == 0
                ? "Nothing was shared."
                : "\(n) shared Full Recording\(n == 1 ? "" : "s") will be deleted within 30 days."
            await store.syncSharing()  // History shows them as not shared
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
            // the buttons on their own, not in a card (whose corners would cut them off)
            VStack(spacing: 12) {
                SignInWithAppleButton(.signIn, onRequest: account.prepare) { result in
                    Task { await account.signInWithApple(result) }
                }
                .signInWithAppleButtonStyle(colorScheme == .dark ? .white : .black)
                .frame(height: 50)
                .clipShape(Capsule())
                Button { Task { await signInWithGoogle() } } label: {
                    // white with a thin outline, as Google's own buttons are
                    Text("Sign in with Google")
                        .font(.system(size: 19, weight: .medium))
                        .foregroundStyle(.primary)
                        .frame(maxWidth: .infinity, minHeight: 50)
                        .background(Capsule().fill(Color(.systemBackground)))
                        .overlay(Capsule().strokeBorder(Color(.separator)))
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
            }
            .listRowInsets(EdgeInsets())
            .listRowBackground(Color.clear)
        } header: {
            Text("Optional. Links your shared Train Measurements and Full Recordings to an account so that you can view them on the web or download them.")
                .font(.subheadline)
                .textCase(nil)
                .padding(.bottom, 8)
        }
        Section {
            TextField("Email address", text: $email)  // (an address as the placeholder shows as a blue link)
                .keyboardType(.emailAddress)
                .textContentType(.emailAddress)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
        } header: {
            Text("Or with an email link")
        } footer: {
            if let notice = account.notice, !hasEmail { Text(notice) }
        }
        // only once there's an address, and on its own like the buttons above, so it can't be
        // taken for a second text field
        if hasEmail {
            Section {
                Button { Task { await account.sendEmailLink(to: email.trimmingCharacters(in: .whitespaces)) } } label: {
                    Text("Email me a sign-in link")
                        .font(.system(size: 19, weight: .semibold))
                        .frame(maxWidth: .infinity, minHeight: 50)
                }
                .buttonStyle(.borderedProminent)
                .buttonBorderShape(.capsule)
                .listRowInsets(EdgeInsets())
                .listRowBackground(Color.clear)
            } footer: {
                if let notice = account.notice { Text(notice) }
            }
        }
    }

    private var hasEmail: Bool { !email.trimmingCharacters(in: .whitespaces).isEmpty }

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
                .confirmationDialog("Delete your account?", isPresented: $confirmDeleteAccount, titleVisibility: .visible) {
                    Button("Delete account", role: .destructive) {
                        if user.provider == "apple.com" {
                            appleDeletion = true
                        } else {
                            Task { if await account.deleteAccount() { await store.syncSharing() } }
                        }
                    }
                } message: {
                    Text("Deletes your account and what it shared. Your phone keeps everything. This can't be undone.")
                }
        }
        .sheet(isPresented: $appleDeletion) {
            VStack(spacing: 20) {
                Text("Confirm with Apple").font(.title2.bold())
                Text("This also ends the app's access to your Apple ID.")
                    .multilineTextAlignment(.center)
                SignInWithAppleButton(.continue, onRequest: account.prepare) { result in
                    appleDeletion = false
                    Task { if await account.deleteAccount(apple: result) { await store.syncSharing() } }
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
