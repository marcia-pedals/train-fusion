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
                    Text("Upload this ride's recording?").font(.title2.bold())
                    Text("Uploading helps improve the model that works out the train's speed, stops and position.")
                    point("waveform", "**The whole recording is uploaded**: all of the audio, including anything said near the phone, and the raw sensor data (motion, compass, barometer, and their times). \(ByteCountFormatter.string(fromByteCount: request.bytes, countStyle: .file)) in all.")
                    point("person.crop.circle", "**It's probably linkable to you**: by voices, and by when and where you rode"
                        + (account.user != nil ? ", and it's linked to your account." : "."))
                    point("lock", "**It's shared only with the app's developers**, and used only to improve the model.")
                    point("trash", "You can ask for your uploaded recordings to be deleted at any time, in Settings.")
                    VStack(spacing: 10) {
                        Button { answer(.yes) } label: { Text("Yes, upload").frame(maxWidth: .infinity) }
                        Button { answer(.no) } label: { Text("No").frame(maxWidth: .infinity) }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                    if request.afterRide {
                        Button("Don't ask again") { answer(.dontAskAgain) }
                            .frame(maxWidth: .infinity)
                        Text("You can still upload a ride from History, and turn the question back on in Settings.")
                            .font(.footnote).foregroundStyle(.secondary)
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
