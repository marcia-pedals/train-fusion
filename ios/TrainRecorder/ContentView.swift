import SwiftUI

/// Two screens behind a tab bar. "Measure" shows what the model believes: about the
/// recording in progress, or about a recording being replayed. "History" lists the
/// recordings on the phone; its replay buttons are the way into replay, and tapping the
/// Measure tab again is the way out.
struct ContentView: View {
    enum Tab { case track, recordings, settings }

    @State private var recorder = Recorder()
    @State private var store = RecordingStore()
    @State private var replay = ReplayPlayer()
    @State private var tab = Tab.track
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        TabView(selection: Binding(
            get: { tab },
            set: { new in
                // tapping Measure while already on it, in replay: back to recording mode
                if new == .track, tab == .track, replay.recording != nil { replay.exit() }
                tab = new
            })
        ) {
            TrackScreen(recorder: recorder, replay: replay)
                .tabItem { Label("Measure", systemImage: "tram.fill") }
                .tag(Tab.track)
            RecordingsScreen(store: store, canReplay: !recorder.isRecording) { recording in
                replay.play(recording)
                tab = .track
            }
            .tabItem { Label("History", systemImage: "clock.arrow.circlepath") }
            .tag(Tab.recordings)
            SettingsScreen()
                .tabItem { Label("Settings", systemImage: "gearshape") }
                .tag(Tab.settings)
        }
        .onAppear {
            refresh()
            autoReplay()
            if ProcessInfo.processInfo.arguments.contains("-history") { tab = .recordings }  // for screenshots
        }
        .onChange(of: recorder.activeID) { refresh() }
        .onChange(of: scenePhase) { refresh() }
    }

    private func refresh() {
        store.refresh(excluding: recorder.activeID)
    }

    /// For testing from the command line (the simulator):
    ///   -autoreplay <recording id> [-replayspeed <x> | max] [-dump] [-replayskips n,n,p] [-expandsensors]
    /// (skips: next / previous stop, one every two seconds, to exercise the controls)
    private func autoReplay() {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "-autoreplay"), i + 1 < args.count,
              let recording = store.recordings.first(where: { $0.id == args[i + 1] }) else { return }
        if let j = args.firstIndex(of: "-replayspeed"), j + 1 < args.count { replay.speed = Double(args[j + 1]) ?? 0 }
        replay.play(recording, dump: args.contains("-dump"))
        if let k = args.firstIndex(of: "-replayskips"), k + 1 < args.count {
            let skips = args[k + 1].split(separator: ",")
            Task {
                for skip in skips {
                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                    if skip == "n" { replay.nextStop() } else { replay.previousStop() }
                }
            }
        }
    }
}

/// A thin header (record controls, or replay controls when a recording is being
/// replayed) over the model's beliefs.
private struct TrackScreen: View {
    let recorder: Recorder
    let replay: ReplayPlayer
    @State private var editingRide = false

    var body: some View {
        VStack(spacing: 0) {
            Group {
                if let recording = replay.recording { replayHeader(recording) } else { recordHeader.frame(height: 44) }
            }
            .padding(.horizontal)
            Divider()
            ScrollView {
                VStack(spacing: 12) {
                    if replay.recording != nil {
                        BeliefView(belief: replay.store.belief, start: replay.recording?.started,
                                   message: replay.store.message, waiting: "Loading the recording…")
                    } else if recorder.isRecording {
                        BeliefView(belief: recorder.inference.belief, start: recorder.startedAt,
                                   message: recorder.inference.message, waiting: "Listening…")
                        if let problem = recorder.live.audio.error {
                            Text(problem).font(.footnote).foregroundStyle(.red)
                        }
                    } else {
                        Text(recorder.busy ? "Saving…" : "Tap the red button to start measuring a ride.\nOr replay an earlier one from History.")
                            .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
                            .padding(.top, 40)
                    }
                    if let error = recorder.error {
                        Text(error).font(.footnote).foregroundStyle(.red).multilineTextAlignment(.center)
                    }
                }
                .padding()
                .padding(.bottom, 40)
                .frame(maxWidth: .infinity)
            }
        }
    }

    private var recordHeader: some View {
        ZStack {
            // in the middle whatever is on either side: the train this is, tap to edit
            Button { editingRide = true } label: {
                if let ride = recorder.ride {
                    Text("Trip \(String(ride.trip)) · Car \(String(ride.car))" + (ride.position == .unspecified ? "" : " · \(ride.position.rawValue)"))
                        .font(.subheadline.monospacedDigit())
                        .foregroundStyle(Color.secondary)  // grey, not the button tint: quiet once it's filled in
                } else {
                    Text("Enter trip and car number").font(.subheadline)
                }
            }
            .lineLimit(1).minimumScaleFactor(0.7)
            .padding(.horizontal, 62)  // clear of the timer and the record button
            recordControls
        }
        .sheet(isPresented: $editingRide) {
            RideSheet(ride: recorder.ride) { recorder.setRide($0) }
        }
        .onAppear {
            // for screenshots from the command line: -editride, -ride <trip>,<car>,<position>
            let args = ProcessInfo.processInfo.arguments
            if let i = args.firstIndex(of: "-ride"), i + 1 < args.count {
                let parts = args[i + 1].split(separator: ",").map(String.init)
                if parts.count == 3, RideInfo.problem(trip: parts[0], car: parts[1]) == nil,
                   let position = RideInfo.Position(rawValue: parts[2]) {
                    recorder.setRide(RideInfo(trip: Int(parts[0])!, car: Int(parts[1])!, position: position))
                }
            }
            if args.contains("-editride") { editingRide = true }
        }
    }

    private var recordControls: some View {
        HStack(spacing: 12) {
            if let started = recorder.startedAt {
                Text(started, style: .timer).font(.title3.monospacedDigit())
            }
            Spacer()
            Button {
                Task { if recorder.isRecording { await recorder.stop() } else { await recorder.start() } }
            } label: {
                Image(systemName: recorder.isRecording ? "stop.circle.fill" : "record.circle")
                    .font(.system(size: 30))
                    .foregroundStyle(.red)
            }
            .buttonStyle(.plain)
            .disabled(recorder.busy)
            .accessibilityLabel(recorder.isRecording ? "Stop recording" : "Start recording")
        }
    }

    private func replayHeader(_ recording: Recording) -> some View {
        let total = Int(recording.duration ?? 0)
        return VStack(spacing: 6) {
            HStack(spacing: 10) {
                Text("\(clock(replay.position)) / \(clock(total))").font(.footnote.monospacedDigit())
                Spacer(minLength: 0)
                Text(recording.started?.formatted(.dateTime.month(.abbreviated).day().hour().minute()) ?? recording.id)
                    .font(.footnote).foregroundStyle(.secondary).lineLimit(1)
            }
            HStack(spacing: 28) {
                Button { replay.previousStop() } label: { Image(systemName: "backward.end.fill") }
                    .accessibilityLabel("Previous stop")
                Button { replay.toggle() } label: {
                    Image(systemName: replay.playing ? "pause.fill" : "play.fill").frame(width: 24)
                }
                .accessibilityLabel(replay.playing ? "Pause" : "Play")
                Button { replay.nextStop() } label: { Image(systemName: "forward.end.fill") }
                    .accessibilityLabel("Next stop")
                Spacer()
                Menu {
                    Picker("Speed", selection: Binding(get: { replay.speed }, set: { replay.speed = $0 })) {
                        ForEach([1.0, 4, 16, 64], id: \.self) { Text("\(Int($0))×").tag($0) }
                        Text("max").tag(0.0)
                    }
                } label: {
                    Text(replay.speed > 0 ? "\(Int(replay.speed))×" : "max").font(.body.monospacedDigit())
                }
                .accessibilityLabel("Replay speed")
            }
            .font(.title3)
            .buttonStyle(.plain)
        }
        .padding(.vertical, 8)
    }

    private func clock(_ s: Int) -> String { String(format: "%d:%02d", s / 60, s % 60) }
}

/// Trip number, car number and where in the car. Save refuses bad numbers; Clear forgets
/// what was saved.
private struct RideSheet: View {
    let hadRide: Bool
    let onSave: (RideInfo?) -> Void
    @State private var trip: String
    @State private var car: String
    @State private var position: RideInfo.Position
    @State private var problem: String?
    @FocusState private var focus: Field?
    @Environment(\.dismiss) private var dismiss

    private enum Field { case trip, car }

    init(ride: RideInfo?, onSave: @escaping (RideInfo?) -> Void) {
        hadRide = ride != nil
        self.onSave = onSave
        _trip = State(initialValue: ride.map { String($0.trip) } ?? "")
        _car = State(initialValue: ride.map { String($0.car) } ?? "")
        _position = State(initialValue: ride?.position ?? .unspecified)
    }

    private func save() {
        let (t, c) = (trip.trimmingCharacters(in: .whitespaces), car.trimmingCharacters(in: .whitespaces))
        problem = RideInfo.problem(trip: t, car: c)
        if problem == nil, let t = Int(t), let c = Int(c) {
            onSave(RideInfo(trip: t, car: c, position: position))
            dismiss()
        }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    LabeledContent("Trip number") {
                        TextField("123", text: $trip).keyboardType(.numberPad).multilineTextAlignment(.trailing)
                            .focused($focus, equals: .trip)
                    }
                    LabeledContent("Car number") {
                        TextField("312", text: $car).keyboardType(.numberPad).multilineTextAlignment(.trailing)
                            .focused($focus, equals: .car)
                    }
                } footer: {
                    if let problem { Text(problem).foregroundStyle(.red) }
                }
                Section("Where in the car") {
                    Picker("Where in the car", selection: $position) {
                        ForEach(RideInfo.Position.allCases, id: \.self) { Text($0 == .unspecified ? "Unset" : $0.rawValue.capitalized).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .listRowInsets(EdgeInsets(top: 8, leading: 8, bottom: 8, trailing: 8))
                }
                if hadRide {
                    Section {
                        Button("Clear", role: .destructive) {
                            onSave(nil)
                            dismiss()
                        }
                        .frame(maxWidth: .infinity)
                    }
                }
            }
            .navigationTitle("This train")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) { Button("Save", action: save) }
            }
            .onAppear { focus = trip.isEmpty ? .trip : nil }
            // the number pad has no return key: a full trip number moves on to the car
            .onChange(of: trip) { old, new in
                if focus == .trip, new.count == 3, old.count == 2 { focus = .car }
            }
        }
    }
}

private struct SettingsScreen: View {
    @AppStorage(Units.key) private var metric = false

    var body: some View {
        Form {
            Section("Units") {
                Picker("Units", selection: $metric) {
                    Text("Imperial").tag(false)
                    Text("Metric").tag(true)
                }
                .pickerStyle(.segmented)
            }
        }
    }
}

private struct RecordingsScreen: View {
    let store: RecordingStore
    let canReplay: Bool  // not while a recording is in progress
    let onReplay: (Recording) -> Void
    @State private var deleting: Recording?

    var body: some View {
        Group {
            if store.recordings.isEmpty {
                ContentUnavailableView("No recordings", systemImage: "waveform")
            } else {
                List(store.recordings) { recording in
                    RecordingRow(
                        recording: recording, upload: store.uploads[recording.id],
                        onUpload: { Task { await store.upload(recording) } },
                        onReplay: recording.duration == nil || !canReplay ? nil : { onReplay(recording) })
                    // swipe left to delete (it still asks first); not in the middle of an upload
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        if case .uploading = store.uploads[recording.id] {} else {
                            Button { deleting = recording } label: { Label("Delete", systemImage: "trash") }.tint(.red)
                        }
                    }
                }
                .listStyle(.plain)
            }
        }
        .confirmationDialog(
            deleting.map { $0.uploaded ? "Delete this recording from the phone?" : "This recording hasn't been uploaded. Delete it anyway?" } ?? "",
            isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
            titleVisibility: .visible
        ) {
            Button("Delete", role: .destructive) {
                if let recording = deleting { store.delete(recording) }
                deleting = nil
            }
        }
    }
}

struct RecordingRow: View {
    let recording: Recording
    let upload: UploadState?
    let onUpload: () -> Void
    var onReplay: (() -> Void)?

    var body: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.body.weight(.medium)).lineLimit(1).minimumScaleFactor(0.7)
                Text(detail).font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
                if let ride = recording.ride {
                    Text("Trip \(String(ride.trip)) · Car \(String(ride.car))" + (ride.position == .unspecified ? "" : " · \(ride.position.rawValue)"))
                        .font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
                }
                if case .failed(let message) = upload {
                    Text(message).font(.footnote).foregroundStyle(.red)
                }
            }
            Spacer()
            Group {
                if case .uploading(let fraction) = upload {
                    ProgressView(value: fraction).progressViewStyle(.circular)
                } else if recording.uploaded {
                    Image(systemName: "checkmark.icloud.fill").foregroundStyle(.green)
                        .accessibilityLabel("Uploaded")
                } else {
                    Button(action: onUpload) { Image(systemName: "icloud.and.arrow.up") }
                        .accessibilityLabel("Upload")
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

    /// Where the model heard the ride start and end.
    private var title: String {
        guard let origin = recording.origin else { return "Unknown trip" }
        return origin + (recording.destination.map { " → " + $0 } ?? "") + (recording.stationsSure ? "" : " (unsure)")
    }

    private static let dateFormat: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MMM d h:mma"  // Jan 5 9:05am
        f.amSymbol = "am"
        f.pmSymbol = "pm"
        return f
    }()

    private var detail: String {
        let when = recording.started.map(Self.dateFormat.string) ?? recording.id
        return "\(when) · \(lengthAndSize)"
    }

    private var lengthAndSize: String {
        let size = ByteCountFormatter.string(fromByteCount: recording.bytes, countStyle: .file)
        guard let duration = recording.duration else { return "not stopped properly · \(size)" }
        let length = Duration.seconds(duration.rounded()).formatted(.time(pattern: duration >= 3600 ? .hourMinuteSecond : .minuteSecond))
        return "\(length) · \(size)" + (recording.uploaded ? " · uploaded" : "")
    }
}

#Preview {
    ContentView()
}
