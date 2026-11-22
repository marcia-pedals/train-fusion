import SwiftUI

/// The rides on the phone, newest first, then the rides that aren't on the phone any more
/// but still have something shared (`RideStore.elsewhere`), to unshare one by one. Each
/// row: the ride, its date, length and size, and whether its Train Measurements and full
/// recording are shared. Tap a row for the ride (play back, share or unshare either,
/// delete).
///
/// What's shared is kept on the phone and brought up to date with the server when History
/// appears, when it's pulled down, and when the app comes to the front
/// (`RideStore.syncSharing`). Anything marked for deletion counts as not shared, and
/// sharing it again undoes that.
struct HistoryScreen: View {
    let store: RideStore
    let canReplay: Bool  // not while a recording is in progress
    let protecting: Set<String>
    /// The recording being played back, if any.
    let playing: String?
    let onReplay: (Recording) -> Void
    let onStopPlayback: () -> Void
    /// Upload this ride's recording: asks first.
    let onUpload: (Ride) -> Void
    @State private var path: [String] = []  // ride ids
    @State private var askingMeasurements: Ride?
    @State private var onPhoneExpanded = true
    @State private var elsewhereExpanded = ProcessInfo.processInfo.arguments.contains("-elsewhere")  // for screenshots
    private let uploader = Uploader.shared

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                if store.rides.isEmpty && store.elsewhere.isEmpty {
                    ContentUnavailableView("No rides", systemImage: "waveform")
                } else {
                    List {
                        // headers (and collapsing) only once there are two kinds of ride
                        let both = !store.elsewhere.isEmpty
                        Section {
                            if onPhoneExpanded || !both {
                                ForEach(store.rides) { row($0) }
                            }
                        } header: {
                            if both { SectionToggle(title: "On this phone", count: store.rides.count, expanded: $onPhoneExpanded) }
                        }
                        if both {
                            Section {
                                if elsewhereExpanded { ForEach(store.elsewhere) { row($0) } }
                            } header: {
                                SectionToggle(title: "Shared, not on this phone", count: store.elsewhere.count,
                                              expanded: $elsewhereExpanded)
                            }
                        }
                    }
                    .listStyle(.plain)
                    .refreshable { await store.syncSharing() }
                }
            }
            .navigationTitle("History")
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: String.self) { id in
                RideDetail(store: store, id: id, uploadState: uploader.states[id],
                           replayProblem: replayProblem(id),
                           isPlaying: playing == id,
                           onReplay: { if let recording = store.recordings[id] { onReplay(recording) } },
                           onStopPlayback: onStopPlayback,
                           onShareMeasurements: { if let ride = store.ride(id) { askingMeasurements = ride } },
                           onShareRecording: { if let ride = store.ride(id) { shareRecording(ride) } },
                           onUnshare: { kind in Task { await store.unshare(kind, id) } },
                           onDelete: deleteAction(id),
                           onDeleteRecording: deleteRecordingAction(id))
            }
        }
        .task { await store.syncSharing() }
        .onAppear {  // for screenshots: -openride opens the newest ride, -elsewhere the newest not on the phone
            let args = ProcessInfo.processInfo.arguments
            if args.contains("-openride"), path.isEmpty, let ride = store.rides.first { path = [ride.id] }
            if args.contains("-elsewhere"), path.isEmpty, let ride = store.elsewhere.first { path = [ride.id] }
        }
        // a ride that's gone (deleted, or nothing of it shared any more): back to the list
        .onChange(of: store.rides.map(\.id) + store.elsewhere.map(\.id)) { path.removeAll { store.anyRide($0) == nil } }
        .sheet(item: $askingMeasurements) { ride in
            RideMeasurementsConsentSheet { share in
                askingMeasurements = nil
                if share { Task { await store.share([ride.id], undoDeletion: true) } }
            }
        }
    }

    private func row(_ ride: Ride) -> some View {
        NavigationLink(value: ride.id) {
            RideRow(ride: ride, recording: store.recordings[ride.id],
                    measurements: store.measurementsStatus(ride),
                    recordingStatus: store.recordingStatus(ride, uploader.states[ride.id]))
        }
    }

    /// Deletes it from the phone; what it shared now shows under "Shared, not on this phone".
    private func delete(_ ride: Ride) {
        path.removeAll { $0 == ride.id }
        store.delete(ride)
        Task { await store.syncSharing() }
    }

    private func shareRecording(_ ride: Ride) {
        guard let recording = store.recordings[ride.id] else { return }
        // already said yes: just try again
        if ride.upload == .wanted { uploader.start(recording) } else { onUpload(ride) }
    }

    /// Why this ride can't be played back, or nil if it can.
    private func replayProblem(_ id: String) -> String? {
        guard let recording = store.recordings[id] else { return "The Full Recording isn't on this phone any more." }
        if recording.duration == nil { return "This ride wasn't stopped properly, so it can't be played back." }
        if !canReplay { return "Not while recording." }
        return nil
    }

    /// Deleting it from the phone, once confirmed; nil if it can't be now.
    private func deleteAction(_ id: String) -> (() -> Void)? {
        guard let ride = store.ride(id), canDelete(ride) else { return nil }
        return { delete(ride) }
    }

    /// Deleting just its Full Recording, once confirmed; nil if there's none or it can't be now.
    private func deleteRecordingAction(_ id: String) -> (() -> Void)? {
        // (the sample's recording is a few hundred bytes of meta.json: just delete the trip)
        guard let ride = store.ride(id), !ride.isSample, store.recordings[id] != nil, canDelete(ride) else { return nil }
        return {
            store.deleteRecording(id)
            Task { await store.syncSharing() }
        }
    }

    private func canDelete(_ ride: Ride) -> Bool {
        switch uploader.states[ride.id] {
        case .starting, .uploading: return false
        default: return !protecting.contains(ride.id)
        }
    }
}

// MARK: - what's shared

/// Whether one kind of thing (Train Measurements, or the Full Recording) is shared.
enum ShareStatus: Equatable {
    case shared
    case notShared
    case working(Double?)  // sharing; uploading, with the fraction done
    case unsharing
    case waiting(String)  // will be shared without asking again
    case failed(String)
    case unavailable(String)  // not shared, and can't be from here

    /// Whether a Share button makes sense.
    var canShare: Bool {
        switch self {
        case .notShared, .failed: return true
        default: return false
        }
    }

    var canUnshare: Bool {
        if case .shared = self { return true }
        return false
    }

    var color: Color {
        switch self {
        case .shared: return .green
        case .notShared: return .secondary
        case .unavailable: return Color(.tertiaryLabel)
        case .working, .unsharing: return .accentColor
        case .waiting: return .orange
        case .failed: return .red
        }
    }

    /// For the ride's screen.
    var text: String {
        switch self {
        case .shared: return "Shared"
        case .notShared: return "Not shared"
        case .working(let fraction): return fraction.map { "Sharing… \(Int($0 * 100))%" } ?? "Sharing…"
        case .unsharing: return "Unsharing…"
        case .waiting(let message), .failed(let message), .unavailable(let message): return message
        }
    }

}

extension RideStore {
    func measurementsStatus(_ ride: Ride) -> ShareStatus {
        if ride.isSample { return .unavailable("Sample: not shareable") }
        if unsharing.contains(Self.unshareKey(.measurements, ride.id)) { return .unsharing }
        if sharingNow.contains(ride.id) { return .working(nil) }
        if ride.measurementsShared { return .shared }
        if let failure = shareFailures[ride.id] { return .failed(failure) }
        if self.ride(ride.id) == nil {  // only what the server has: can't be shared from here
            return .unavailable("Not shared")
        }
        if ride.sharePending == true, AppSettings.shareMeasurements { return .waiting("Waiting to share") }
        return .notShared
    }

    func recordingStatus(_ ride: Ride, _ state: Uploader.State?) -> ShareStatus {
        if ride.isSample { return .unavailable("Sample: not shareable") }
        if unsharing.contains(Self.unshareKey(.recording, ride.id)) { return .unsharing }
        switch state {
        case .starting: return .working(nil)
        case .uploading(let fraction): return .working(fraction)
        case .waiting(let message): return .waiting(message)
        case .failed(let message): return .failed(message)
        case nil: break
        }
        if ride.recordingShared { return .shared }
        if ride.upload == .wanted { return .waiting("Waiting to share") }
        if recordings[ride.id] == nil {
            return .unavailable(self.ride(ride.id) == nil || ride.recordingDeletionRequested == true ? "Not shared" : "Not on this phone")
        }
        if ride.upload == .tooLarge {
            return .unavailable("Too large (over \(ByteCountFormatter.string(fromByteCount: AppSettings.maxUploadBytes, countStyle: .file)))")
        }
        return .notShared
    }
}

// MARK: - the list

/// A section's header that folds it away: its title, how many rides, and a chevron.
struct SectionToggle: View {
    let title: String
    let count: Int
    @Binding var expanded: Bool

    var body: some View {
        Button {
            withAnimation { expanded.toggle() }
        } label: {
            HStack {
                Text(title)
                Text(String(count)).foregroundStyle(.secondary).monospacedDigit()
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.footnote.weight(.semibold))
                    .rotationEffect(.degrees(expanded ? 90 : 0))
                    .foregroundStyle(.secondary)
            }
            .font(.headline)
            .foregroundStyle(.primary)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityValue(expanded ? "Expanded" : "Collapsed")
    }
}

struct RideRow: View {
    let ride: Ride
    let recording: Recording?
    let measurements: ShareStatus
    let recordingStatus: ShareStatus

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(ride.title).font(.body.weight(.medium)).lineLimit(1).minimumScaleFactor(0.7)
            Text(ride.summary(recording)).font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
            sharing
                .font(.footnote.monospacedDigit())
        }
    }

    /// "Not Shared" / "Shared: Measurements, Recording", then anything going on.
    private var sharing: Text {
        let shared = [("Measurements", measurements), ("Recording", recordingStatus)].filter { $0.1.canUnshare }.map(\.0)
        var text = Text(shared.isEmpty ? "Not Shared" : "Shared: " + shared.joined(separator: ", ")).foregroundStyle(.secondary)
        for (name, status) in [("Measurements", measurements), ("Recording", recordingStatus)] {
            if let (activity, color) = Self.activity(name, status) {
                text = text + Text(" · ").foregroundStyle(.secondary) + Text(activity).foregroundStyle(color)
            }
        }
        return text
    }

    private static func activity(_ name: String, _ status: ShareStatus) -> (String, Color)? {
        switch status {
        case .working(let fraction?): return ("Sharing \(name) \(Int(fraction * 100))%", .accentColor)
        case .working(nil): return ("Sharing \(name)…", .accentColor)
        case .unsharing: return ("Unsharing \(name)…", .accentColor)
        case .waiting: return ("\(name) waiting to share", .orange)
        case .failed: return ("Couldn't share \(name)", .red)
        default: return nil
        }
    }
}

extension Ride {
    private static let dateFormat: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MMM d h:mma"  // Jan 5 9:05am
        f.amSymbol = "am"
        f.pmSymbol = "pm"
        return f
    }()

    var date: Date? { RecordingID.date(id) }

    var duration: String? {
        guard let started, let ended, stoppedProperly else { return nil }
        let duration = ended - started
        return Duration.seconds(duration.rounded()).formatted(.time(pattern: duration >= 3600 ? .hourMinuteSecond : .minuteSecond))
    }

    /// Date · length · size (while the recording is on the phone), for the row.
    func summary(_ recording: Recording?) -> String {
        var parts = [date.map(Self.dateFormat.string) ?? id]
        if let duration {
            parts.append(duration)
        } else if recording != nil {
            parts.append("not stopped properly")
        }
        if let recording, !isSample {  // nothing when it isn't on the phone
            parts.append(ByteCountFormatter.string(fromByteCount: recording.bytes, countStyle: .file))
        }
        return parts.joined(separator: " · ")
    }

    var trainText: String? {
        train.map { "Trip \(String($0.trip)) · Car \(String($0.car))" + ($0.position == "unspecified" ? "" : " · \($0.position)") }
    }
}

// MARK: - one ride

struct RideDetail: View {
    let store: RideStore
    let id: String
    let uploadState: Uploader.State?
    let replayProblem: String?
    let isPlaying: Bool
    let onReplay: () -> Void
    let onStopPlayback: () -> Void
    let onShareMeasurements: () -> Void
    let onShareRecording: () -> Void
    /// Done once confirmed (the confirmations are asked here, from their buttons).
    let onUnshare: (RideStore.Shared) -> Void
    let onDelete: (() -> Void)?
    let onDeleteRecording: (() -> Void)?
    @State private var confirmDelete = false
    @State private var confirmDeleteRecording = false
    @State private var confirmUnshare: RideStore.Shared?

    var body: some View {
        if let ride = store.anyRide(id) {
            List {
                Section {
                    LabeledContent("Date", value: ride.date?.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day().hour().minute()) ?? ride.id)
                    if let duration = ride.duration {
                        LabeledContent("Duration", value: duration)
                    } else if store.recordings[id] != nil {
                        LabeledContent("Duration", value: "not stopped properly")
                    }
                    if let train = ride.trainText {
                        LabeledContent("Train", value: train)
                    }
                }
                .monospacedDigit()
                if store.recordings[id] != nil {  // only while there's something to play
                    Section {
                        if isPlaying {
                            Button(action: onStopPlayback) { Label("Stop playback", systemImage: "stop.fill") }
                        } else {
                            Button(action: onReplay) { Label("Play back", systemImage: "play.fill") }
                                .disabled(replayProblem != nil)
                        }
                    } footer: {
                        if let replayProblem, !isPlaying { Text(replayProblem) }
                    }
                }
                // freeing space (the ride stays) apart from deleting the ride (at the bottom)
                if let onDeleteRecording, let recording = store.recordings[id] {
                    Section("Phone Data") {
                        Button(role: .destructive) { confirmDeleteRecording = true } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Delete Full Recording (\(ByteCountFormatter.string(fromByteCount: recording.bytes, countStyle: .file)))")
                                Text("Keeps the ride in History").font(.footnote).foregroundStyle(Color(.secondaryLabel))
                            }
                        }
                        .confirmDeleteRecording(ride, isPresented: $confirmDeleteRecording, onDelete: onDeleteRecording)
                    }
                }
                if !ride.isSample {  // (made up: never shared)
                    Section("Sharing") {
                        sharing(.measurements, status: store.measurementsStatus(ride), onShare: onShareMeasurements)
                        sharing(.recording, status: store.recordingStatus(ride, uploadState), onShare: onShareRecording)
                    }
                }
                if let onDelete {
                    Section {
                        Button("Delete Ride", role: .destructive) { confirmDelete = true }
                            .frame(maxWidth: .infinity)
                            .confirmDelete(ride, store: store, isPresented: $confirmDelete, onDelete: onDelete)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .refreshable { await store.syncSharing() }
            .navigationTitle(ride.title)
            .navigationBarTitleDisplayMode(.inline)
        } else {
            Color.clear  // gone (deleted, or nothing of it shared any more): on its way back to the list
        }
    }

    /// One kind of sharing: its name, its state in words, and Share or Unshare.
    private func sharing(_ kind: RideStore.Shared, status: ShareStatus, onShare: @escaping () -> Void) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(kind == .measurements ? "Train Measurements" : "Full Recording")
                HStack(spacing: 6) {
                    switch status {
                    case .working(let fraction):
                        ProgressView(value: fraction).progressViewStyle(.circular).controlSize(.mini)
                    case .unsharing:
                        ProgressView().controlSize(.mini)
                    default:
                        EmptyView()
                    }
                    Text(status.text)
                        .foregroundStyle(status.color == .red ? .red : status.canUnshare ? .green : .secondary)
                }
                .font(.subheadline)
            }
            Spacer()
                if status.canShare {
                    Button(isFailed(status) ? "Try again" : "Share", action: onShare)
                        .buttonStyle(.bordered)
                        .disabled(!BackendConfig.configured)
                } else if status.canUnshare {
                    Button("Unshare", role: .destructive) { confirmUnshare = kind }
                        .buttonStyle(.bordered)
                        .disabled(!BackendConfig.configured)
                        .confirmUnshare(Binding(get: { confirmUnshare == kind ? kind : nil },
                                                set: { if $0 == nil { confirmUnshare = nil } }),
                                        onUnshare: onUnshare)
            }
        }
    }

    private func isFailed(_ status: ShareStatus) -> Bool {
        if case .failed = status { return true }
        return false
    }
}

// MARK: - confirmations

/// The questions asked before deleting a ride from the phone, or unsharing part of it. A
/// modifier, so each is attached to (and shown from) the button that asks.
extension View {
    func confirmDelete(_ ride: Ride, store: RideStore, isPresented: Binding<Bool>,
                       onDelete: @escaping () -> Void) -> some View {
        confirmationDialog("Delete this ride from the phone?", isPresented: isPresented, titleVisibility: .visible) {
            Button("Delete", role: .destructive, action: onDelete)
        } message: {
            if ride.measurementsShared || ride.recordingShared { Text("Shared data stays shared.") }
        }
    }

    func confirmDeleteRecording(_ ride: Ride, isPresented: Binding<Bool>, onDelete: @escaping () -> Void) -> some View {
        confirmationDialog("Delete this ride's Full Recording from the phone?", isPresented: isPresented,
                           titleVisibility: .visible) {
            Button("Delete", role: .destructive, action: onDelete)
        } message: {
            // (deleting the recording leaves the measurements alone: only its being shared matters)
            if ride.recordingShared { Text("Shared data stays shared.") }
        }
    }

    /// `kind`: what's being asked about; nil when not asking.
    func confirmUnshare(_ kind: Binding<RideStore.Shared?>, onUnshare: @escaping (RideStore.Shared) -> Void) -> some View {
        confirmationDialog(
            kind.wrappedValue == .recording ? "Unshare this ride's Full Recording?" : "Unshare this ride's Train Measurements?",
            isPresented: Binding(get: { kind.wrappedValue != nil }, set: { if !$0 { kind.wrappedValue = nil } }),
            titleVisibility: .visible, presenting: kind.wrappedValue
        ) { kind in
            Button("Unshare", role: .destructive) { onUnshare(kind) }
        }
    }
}
