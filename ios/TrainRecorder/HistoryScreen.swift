import SwiftUI

/// The rides on the phone, newest first, each with upload and replay while its recording
/// is still here. Swipe left to delete a ride from the phone.
struct HistoryScreen: View {
    let store: RideStore
    let canReplay: Bool  // not while a recording is in progress
    let protecting: Set<String>
    let onReplay: (Recording) -> Void
    /// Upload this ride's recording: asks first.
    let onUpload: (Ride) -> Void
    @State private var deleting: Ride?
    private let uploader = Uploader.shared

    var body: some View {
        Group {
            if store.rides.isEmpty {
                ContentUnavailableView("No rides", systemImage: "waveform")
            } else {
                List(store.rides) { ride in
                    let recording = store.recordings[ride.id]
                    RideRow(
                        ride: ride, recording: recording, state: uploader.states[ride.id],

                        onUpload: {
                            guard let recording else { return }
                            // already said yes: just try again
                            if ride.upload == .wanted { uploader.start(recording) } else { onUpload(ride) }
                        },
                        onReplay: recording?.duration == nil || !canReplay ? nil : { recording.map(onReplay) })
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        if !busy(ride), !protecting.contains(ride.id) {
                            Button { deleting = ride } label: { Label("Delete", systemImage: "trash") }.tint(.red)
                        }
                    }
                }
                .listStyle(.plain)
            }
        }
        .confirmationDialog(
            "Delete this ride from the phone?",
            isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
            titleVisibility: .visible
        ) {
            Button("Delete", role: .destructive) {
                if let ride = deleting { store.delete(ride) }
                deleting = nil
            }
        } message: {
            Text(deleting.map { ride in
                "Its timings" + (store.recordings[ride.id] != nil ? " and recording" : "") + " are deleted from this phone."
                    + (ride.upload == .undecided && store.recordings[ride.id] != nil ? " The recording hasn't been uploaded." : "")
                    + " What's shared stays shared (delete it in Settings)."
            } ?? "")
        }
    }

    private func busy(_ ride: Ride) -> Bool {
        switch uploader.states[ride.id] {
        case .starting, .uploading: return true
        default: return false
        }
    }
}

struct RideRow: View {
    let ride: Ride
    let recording: Recording?
    let state: Uploader.State?
    let onUpload: () -> Void
    var onReplay: (() -> Void)?

    var body: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 3) {
                Text(ride.title).font(.body.weight(.medium)).lineLimit(1).minimumScaleFactor(0.7)
                Text(detail).font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
                if let train = ride.train {
                    Text("Trip \(String(train.trip)) · Car \(String(train.car))" + (train.position == "unspecified" ? "" : " · \(train.position)"))
                        .font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
                }
                if let status {
                    Text(status.text).font(.footnote).foregroundStyle(status.problem ? .red : .secondary)
                }
            }
            Spacer()
            Group {
                switch state {
                case .starting:
                    ProgressView()
                case .uploading(let fraction):
                    ProgressView(value: fraction).progressViewStyle(.circular)
                default:
                    if ride.upload == .uploaded {
                        Image(systemName: "checkmark.icloud.fill").foregroundStyle(.green)
                            .accessibilityLabel("Uploaded")
                    } else if recording != nil, ride.upload != .tooLarge {
                        Button(action: onUpload) { Image(systemName: "icloud.and.arrow.up") }
                            .accessibilityLabel("Upload")
                    }
                }
            }
            .frame(width: 32)
            if let onReplay {
                Button(action: onReplay) { Image(systemName: "play.circle") }
                    .accessibilityLabel("Replay")
            }
        }
        .font(.title3)
        .buttonStyle(.borderless)  // so each button in the row takes its own taps
    }

    private var status: (text: String, problem: Bool)? {
        switch state {
        case .waiting(let message): return (message, false)
        case .failed(let message): return (message, true)
        default: break
        }
        if ride.upload == .tooLarge, recording != nil {
            return ("Too large to upload (over \(ByteCountFormatter.string(fromByteCount: AppSettings.maxUploadBytes, countStyle: .file)))", false)
        }
        if ride.upload == .wanted, state == nil { return ("Waiting to upload", false) }
        return nil
    }

    private static let dateFormat: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MMM d h:mma"  // Jan 5 9:05am
        f.amSymbol = "am"
        f.pmSymbol = "pm"
        return f
    }()

    private var detail: String {
        let when = RecordingID.date(ride.id).map(Self.dateFormat.string) ?? ride.id
        var parts = [when]
        if let started = ride.started, let ended = ride.ended, ride.stoppedProperly {
            let duration = ended - started
            parts.append(Duration.seconds(duration.rounded()).formatted(.time(pattern: duration >= 3600 ? .hourMinuteSecond : .minuteSecond)))
        } else if recording != nil {
            parts.append("not stopped properly")
        }
        if let recording {
            parts.append(ByteCountFormatter.string(fromByteCount: recording.bytes, countStyle: .file))
        } else {
            parts.append("recording not kept")
        }
        if ride.upload == .uploaded { parts.append("uploaded") }
        if ride.sharedAt != nil { parts.append("measurements shared") }
        return parts.joined(separator: " · ")
    }
}
