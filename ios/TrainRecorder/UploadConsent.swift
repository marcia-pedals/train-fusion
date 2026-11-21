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
    @State private var height: CGFloat = 480
    private let account = Account.shared

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Upload this recording to help improve the model?")
                    .font(.title3.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 14, verticalSpacing: 14) {
                    row("doc", "Size", ByteCountFormatter.string(fromByteCount: request.bytes, countStyle: .file))
                    row("waveform", "Audio", "all of it, including anything said near the phone")
                    row("sensor", "Sensor data", "motion, compass, barometer")
                    row("lock", "Shared only with the app's developers", account.user != nil ? "linked to your account" : nil)
                    row("trash", nil, "Delete it anytime in Settings")
                }
                VStack(spacing: 12) {
                    Button { answer(.yes) } label: {
                        Text("Yes, upload").fontWeight(.semibold).frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    Button { answer(.no) } label: { Text("No").frame(maxWidth: .infinity) }
                        .buttonStyle(.bordered)
                    if request.afterRide {
                        Button("Don't ask again") { answer(.dontAskAgain) }
                            .font(.subheadline)
                            .padding(.top, 4)
                    }
                }
                .controlSize(.large)
            }
            .padding(.horizontal, 24)
            .padding(.top, 32)
            .padding(.bottom, 16)
            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height = $0 }
        }
        .scrollBounceBehavior(.basedOnSize)
        .presentationDetents([.height(height)])
        .presentationDragIndicator(.visible)
    }

    /// One line: an icon, then a **title**: detail (either may be left out).
    private func row(_ symbol: String, _ title: String?, _ detail: String?) -> some View {
        GridRow {
            Image(systemName: symbol)
                .foregroundStyle(.tint)
                .frame(width: 24)
                .gridColumnAlignment(.center)
            (Text(title.map { detail == nil ? $0 : $0 + ": " } ?? "").fontWeight(.semibold) + Text(detail ?? ""))
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
