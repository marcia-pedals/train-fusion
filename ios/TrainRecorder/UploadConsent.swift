import SwiftUI

/// Asking whether to share (upload) a ride's full recording: after a ride (Share / Don't
/// share / Don't ask again) or from History (Share / Don't share).
/// There's deliberately no "always", nor anything that could read as one. The wording is
/// the consent: keep it specific about what goes, who gets it and why
/// (docs/accounts-and-uploads.md).
struct UploadConsent: Identifiable {
    let rideID: String
    let bytes: Int64
    let afterRide: Bool

    var id: String { rideID }

    struct Answer {
        let upload: Bool
        /// Stop asking after each ride (Settings' "Ask to share with devs" off).
        let stopAsking: Bool
    }
}

struct UploadConsentSheet: View {
    let request: UploadConsent
    let answer: (UploadConsent.Answer) -> Void

    var body: some View {
        ConsentSheet(
            title: "Share full recording?",
            subtitle: "To improve the localizer · " + ByteCountFormatter.string(fromByteCount: request.bytes, countStyle: .file),
            rows: [
                .init("waveform", "Audio, including nearby voices"),
                .init("sensor", "Motion, compass, barometer"),
                .init("lock", "Only app's developers get it"),
                .init("trash", "Delete any time in Settings"),
            ],
            choices: [
                .init("Share", primary: true) { answer(.init(upload: true, stopAsking: false)) },
                .init("Don't share") { answer(.init(upload: false, stopAsking: false)) },
            ] + (request.afterRide
                ? [.init("Don't ask again") { answer(.init(upload: false, stopAsking: true)) }]
                : []))
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
            title: "Share Train Measurements?",
            subtitle: "For train operations research",
            rows: [
                .init("tram", "Position, speed, stops, exact times"),
                .init("waveform.slash", "No audio"),
                .init("person.2", "Anyone who asks gets it",
                      more: "We remove your account and device IDs first. They must agree to use the data only for train operations research, and only publish aggregated statistics, but we cannot guarantee what they do after they receive the data."),
                .init("trash", "Delete any time in Settings"),
            ],
            choices: [
                .init("Always share", primary: true) { answer(.always) },
                .init("Share just this ride") { answer(.thisRide) },
                .init("No, and don't ask again") { answer(.never) },
            ])
    }
}

/// The layout both questions share: a title and what it's for, a few icon lines, then the
/// answers as full-width buttons, one per row. Sized to its content.
struct ConsentSheet: View {
    struct Row: Identifiable {
        let symbol: String
        let text: String
        /// Shown in a popover when its ⓘ is tapped.
        let more: String?
        var id: String { symbol + text }

        init(_ symbol: String, _ text: String, more: String? = nil) {
            (self.symbol, self.text, self.more) = (symbol, text, more)
        }
    }

    struct Choice: Identifiable {
        let label: String
        let primary: Bool
        let action: () -> Void
        var id: String { label }

        init(_ label: String, primary: Bool = false, action: @escaping () -> Void) {
            (self.label, self.primary, self.action) = (label, primary, action)
        }
    }

    let title: String
    let subtitle: String
    let rows: [Row]
    let choices: [Choice]
    @State private var height: CGFloat = 400
    @State private var showingMore: String?  // the row whose details are showing

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.title3.weight(.semibold))
                    Text(subtitle)
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                .fixedSize(horizontal: false, vertical: true)
                Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 14, verticalSpacing: 12) {
                    ForEach(rows) { row in
                        GridRow {
                            Image(systemName: row.symbol)
                                .foregroundStyle(.tint)
                                .frame(width: 24)
                                .gridColumnAlignment(.center)
                            HStack(spacing: 6) {
                                Text(row.text)
                                    .lineLimit(1)
                                    .minimumScaleFactor(0.8)  // one line each, even on small phones
                                if let more = row.more {
                                    // a popover, so nothing in the sheet moves or resizes
                                    Button { showingMore = row.id } label: {
                                        Image(systemName: "info.circle")
                                    }
                                    .accessibilityLabel("Details")
                                    .popover(isPresented: Binding(
                                        get: { showingMore == row.id },
                                        set: { if !$0 { showingMore = nil } })
                                    ) {
                                        Text(more)
                                            .font(.subheadline)
                                            .fixedSize(horizontal: false, vertical: true)
                                            .frame(width: 280, alignment: .leading)
                                            .padding(.horizontal, 22)
                                            .padding(.vertical, 20)
                                            .presentationCompactAdaptation(.popover)
                                    }
                                }
                            }
                        }
                    }
                }
                VStack(spacing: 10) {
                    ForEach(choices) { choice in
                        if choice.primary {
                            Button(action: choice.action) {
                                Text(choice.label).fontWeight(.semibold).frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.borderedProminent)
                        } else {
                            Button(action: choice.action) { Text(choice.label).frame(maxWidth: .infinity) }
                                .buttonStyle(.bordered)
                        }
                    }
                }
                .controlSize(.large)
                .padding(.top, 4)
            }
            .padding(.horizontal, 24)
            .padding(.top, 28)
            .padding(.bottom, 12)
            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height = $0 }
        }
        .scrollBounceBehavior(.basedOnSize)
        .presentationDetents([.height(height)])
        // plain, not the default grey: the lighter buttons' edges show, so the gaps read even
        .presentationBackground(Color(.systemBackground))
        .presentationDragIndicator(.visible)
    }
}
