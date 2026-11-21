import SwiftUI

/// Asking whether to upload a ride's recording: after a ride (Yes / No / Don't ask again)
/// or from History (Yes / No). The wording is the consent: keep it specific about what
/// goes, who gets it and why (docs/accounts-and-uploads.md).
struct UploadConsent: Identifiable {
    let rideID: String
    let bytes: Int64
    let afterRide: Bool

    var id: String { rideID }

    enum Answer { case yes, no, dontAskAgain }
}

struct UploadConsentSheet: View {
    let request: UploadConsent
    let answer: (UploadConsent.Answer) -> Void
    private let account = Account.shared

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text("Upload this recording to help improve the model?").font(.title2.bold())
                    point("doc", "**Size**: \(ByteCountFormatter.string(fromByteCount: request.bytes, countStyle: .file))")
                    point("waveform", "**Audio**: all of it, including anything said near the phone")
                    point("gauge.with.dots.needle.33percent", "**Sensor data**: motion, compass, barometer")
                    point("lock", "**Shared only with the app's developers**"
                        + (account.user != nil ? ", linked to your account" : ""))
                    point("trash", "Delete your uploads anytime in Settings")
                    VStack(spacing: 10) {
                        Button { answer(.yes) } label: { Text("Yes, upload").frame(maxWidth: .infinity) }
                        Button { answer(.no) } label: { Text("No").frame(maxWidth: .infinity) }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                    if request.afterRide {
                        Button("Don't ask again") { answer(.dontAskAgain) }
                            .frame(maxWidth: .infinity)
                    }
                }
                .padding()
            }
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.large])
    }

    /// `text` may have **bold** (Markdown).
    private func point(_ symbol: String, _ text: String) -> some View {
        Label { Text(LocalizedStringKey(text)) } icon: { Image(systemName: symbol).foregroundStyle(.tint) }
    }
}
