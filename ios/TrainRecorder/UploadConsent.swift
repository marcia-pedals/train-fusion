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
            choices: [
                .init("Yes, upload", .primary) { answer(.yes) },
                .init("No", .secondary) { answer(.no) },
            ] + (request.afterRide ? [.init("Don't ask again", .link) { answer(.dontAskAgain) }] : []))
    }
}

/// Asked as a recording starts, until answered for good: whether to share Train
/// Measurements. "Always" turns the setting on (Settings); "just this ride" shares the ride
/// being recorded and asks again next time; "No" turns it off for good. Dismissing it
/// answers nothing (asked again next time).
struct MeasurementsConsentSheet: View {
    enum Answer { case always, thisRide, never }

    let answer: (Answer) -> Void

    var body: some View {
        ConsentSheet(
            title: "Share Train Measurements for train operations research?",
            rows: [
                .init("tram", "Train Measurements", "train position, speed, and stops, with exact time and date"),
                .init("waveform.slash", nil, "Not the audio or sensor data"),
                .init("person.2", "Shared with anyone who asks for the data", nil,
                      more: "They must agree to use the data only for train operations research, and only publish aggregated statistics, but we cannot guarantee what they do after they receive the data."),
                .init("gearshape", nil, "Change this, or delete what's shared, anytime in Settings"),
            ],
            choices: [
                .init("Always share", .primary) { answer(.always) },
                .init("Share just this ride", .secondary) { answer(.thisRide) },
                .init("No, and don't ask again", .secondary) { answer(.never) },
            ])
    }
}

/// The layout both questions share: a title, a few icon rows, and the answers. Sized to
/// its content.
struct ConsentSheet: View {
    struct Row: Identifiable {
        let symbol: String
        let title: String?
        let detail: String?
        /// Shown under the row when its "Details" is tapped.
        let more: String?
        var id: String { symbol + (title ?? "") + (detail ?? "") }

        init(_ symbol: String, _ title: String?, _ detail: String?, more: String? = nil) {
            (self.symbol, self.title, self.detail, self.more) = (symbol, title, detail, more)
        }
    }

    /// An answer button: the first is usually `.primary`; `.link` is a small text button.
    struct Choice: Identifiable {
        enum Style { case primary, secondary, link }
        let label: String
        let style: Style
        let action: () -> Void
        var id: String { label }

        init(_ label: String, _ style: Style, action: @escaping () -> Void) {
            (self.label, self.style, self.action) = (label, style, action)
        }
    }

    let title: String
    let rows: [Row]
    let choices: [Choice]
    @State private var height: CGFloat = 480
    @State private var expanded: Set<String> = []

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
                            VStack(alignment: .leading, spacing: 6) {
                                (Text(row.title.map { row.detail == nil ? $0 : $0 + ": " } ?? "").fontWeight(.semibold)
                                    + Text(row.detail ?? ""))
                                    .fixedSize(horizontal: false, vertical: true)
                                if let more = row.more {
                                    if expanded.contains(row.id) {
                                        Text(more)
                                            .font(.subheadline).foregroundStyle(.secondary)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                    Button(expanded.contains(row.id) ? "Less" : "Details") {
                                        withAnimation { expanded.formSymmetricDifference([row.id]) }
                                    }
                                    .font(.subheadline)
                                }
                            }
                        }
                    }
                }
                VStack(spacing: 8) {
                    ForEach(choices) { choice in
                        switch choice.style {
                        case .primary:
                            Button(action: choice.action) {
                                Text(choice.label).fontWeight(.semibold).frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.borderedProminent)
                        case .secondary:
                            Button(action: choice.action) { Text(choice.label).frame(maxWidth: .infinity) }
                                .buttonStyle(.bordered)
                        case .link:
                            Button(choice.label, action: choice.action)
                                .font(.subheadline)
                                .padding(.top, 4)
                        }
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
