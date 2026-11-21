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

    var body: some View {
        ConsentSheet(
            title: "Upload this recording to help improve the model?",
            rows: [
                .init("doc", "Size", ByteCountFormatter.string(fromByteCount: request.bytes, countStyle: .file)),
                .init("waveform", "Audio", "all of it, including anything said near the phone"),
                .init("sensor", "Sensor data", "motion, compass, barometer"),
                .init("lock", "Shared only with the app's developers", nil),
                .init("trash", nil, "Delete it anytime in Settings"),
            ],
            yes: ("Yes, upload", { answer(.yes) }),
            no: ("No", { answer(.no) }),
            extra: request.afterRide ? ("Don't ask again", { answer(.dontAskAgain) }) : nil)
    }
}

/// Asked once, before the first recording: whether to share each ride's Train Measurements.
/// Sets the setting of the same name (Settings).
struct MeasurementsConsentSheet: View {
    let answer: (Bool) -> Void

    var body: some View {
        ConsentSheet(
            title: "Share Train Measurements for research?",
            rows: [
                .init("tram", "Train Measurements", "train position, speed, and stops, with exact time and date"),
                .init("waveform.slash", nil, "Not the audio or sensor data"),
                .init("lock", "Shared only with the app's developers", nil),
                .init("gearshape", nil, "Change this, or delete what's shared, anytime in Settings"),
            ],
            yes: ("Share", { answer(true) }),
            no: ("Don't share", { answer(false) }),
            extra: nil)
    }
}

/// The layout both questions share: a title, a few icon rows, and the answers. Sized to
/// its content.
struct ConsentSheet: View {
    struct Row: Identifiable {
        let symbol: String
        let title: String?
        let detail: String?
        var id: String { symbol + (title ?? "") + (detail ?? "") }

        init(_ symbol: String, _ title: String?, _ detail: String?) {
            (self.symbol, self.title, self.detail) = (symbol, title, detail)
        }
    }

    let title: String
    let rows: [Row]
    let yes: (String, () -> Void)
    let no: (String, () -> Void)
    let extra: (String, () -> Void)?
    @State private var height: CGFloat = 480

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text(title)
                    .font(.title3.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 14, verticalSpacing: 14) {
                    ForEach(rows) { row in
                        GridRow {
                            Image(systemName: row.symbol)
                                .foregroundStyle(.tint)
                                .frame(width: 24)
                                .gridColumnAlignment(.center)
                            (Text(row.title.map { row.detail == nil ? $0 : $0 + ": " } ?? "").fontWeight(.semibold)
                                + Text(row.detail ?? ""))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                VStack(spacing: 12) {
                    Button(action: yes.1) {
                        Text(yes.0).fontWeight(.semibold).frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    Button(action: no.1) { Text(no.0).frame(maxWidth: .infinity) }
                        .buttonStyle(.bordered)
                    if let extra {
                        Button(extra.0, action: extra.1)
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
}
