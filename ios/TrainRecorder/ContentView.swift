import SwiftUI

/// Three screens behind a tab bar. "Measure" shows what the model believes: about the
/// recording in progress, or about a recording being replayed. "History" lists the rides
/// on the phone; its replay buttons are the way into replay, and tapping the Measure tab
/// again is the way out. "Settings" has the settings and the account.
struct ContentView: View {
    enum Tab { case track, recordings, settings }

    @State private var recorder = Recorder()
    @State private var store = RideStore()
    @State private var replay = ReplayPlayer()
    @State private var tab = Tab.track
    /// The upload question being asked (after a ride, or from History).
    @State private var consent: UploadConsent?
    /// After signing in: what this phone shared before (to offer linking it to the account).
    @State private var unlinked: Unlinked?
    @Environment(\.scenePhase) private var scenePhase
    private let account = Account.shared

    struct Unlinked {
        let measurements: Int
        let uploads: Int
    }

    /// Recordings that mustn't be deleted from under the app.
    private var protecting: Set<String> { Set([replay.recording?.id, recorder.activeID].compactMap { $0 }) }

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
            HistoryScreen(
                store: store, canReplay: !recorder.isRecording, protecting: protecting,
                playing: replay.recording?.id,
                onReplay: { recording in
                    replay.play(recording)
                    tab = .track
                },
                onStopPlayback: { replay.exit() },
                onUpload: { ride in
                    consent = UploadConsent(rideID: ride.id, bytes: store.recordings[ride.id]?.bytes ?? 0, afterRide: false)
                })
                .tabItem { Label("History", systemImage: "clock.arrow.circlepath") }
                .tag(Tab.recordings)
            SettingsScreen(store: store, protecting: protecting)
                .tabItem { Label("Settings", systemImage: "gearshape") }
                .tag(Tab.settings)
        }
        .onAppear {
            refresh()
            autoReplay()
            if ProcessInfo.processInfo.arguments.contains("-history") { tab = .recordings }  // for screenshots
            if ProcessInfo.processInfo.arguments.contains("-settings") { tab = .settings }
            if ProcessInfo.processInfo.arguments.contains("-askupload"), let ride = store.rides.first {
                consent = UploadConsent(rideID: ride.id, bytes: store.recordings[ride.id]?.bytes ?? 0, afterRide: true)
            }
        }
        .onChange(of: recorder.activeID) { old, new in
            if let old, new == nil { rideEnded(old) } else { refresh() }
        }
        .onChange(of: scenePhase) {
            guard scenePhase == .active else { return }
            refresh()
            store.resumeUploads()
            Task {
                await store.sharePendingMeasurements()
                await store.syncSharing()
            }
        }
        .sheet(item: $consent) { request in
            UploadConsentSheet(request: request) { answer in
                consent = nil
                if answer.stopAsking { AppSettings.askToUpload = false }
                store.decide(request.rideID, upload: answer.upload, protecting: protecting)
            }
        }
        .onOpenURL { url in
            if account.handle(url: url) { tab = .settings }
        }
        .onChange(of: account.signIns) { Task { await offerAfterSignIn() } }
        .alert("Link to your account?", isPresented: Binding(get: { unlinked != nil }, set: { if !$0 { unlinked = nil } }),
               presenting: unlinked) { _ in
            Button("Link") { Task { try? await API.call("POST", "v1/claim", auth: .required, device: true) } }
            Button("Not now", role: .cancel) {}
        } message: { _ in
            Text("Link what this phone shared before you signed in?")
        }
    }

    private func refresh() {
        store.refresh(excluding: recorder.activeID)
        store.prune(protecting: protecting)
    }

    private func rideEnded(_ id: String) {
        guard let ride = store.rideEnded(id) else { return refresh() }
        if ride.upload == .undecided, AppSettings.askToUpload {
            consent = UploadConsent(rideID: id, bytes: store.recordings[id]?.bytes ?? 0, afterRide: true)
        } else {
            store.prune(protecting: protecting)
        }
    }

    /// Offers to link what this phone shared before signing in (its device secret's) to the
    /// account.
    private func offerAfterSignIn() async {
        guard account.user != nil else { return }
        var uploads = 0, measurements = 0
        if let body = try? await API.call("GET", "v1/uploads", device: true),
           let found = body["uploads"] as? [[String: Any]] {
            uploads = found.filter { $0["linked"] as? Bool == false && $0["deletion_requested"] as? Bool != true }.count
        }
        if let body = try? await API.call("GET", "v1/measurements", device: true),
           let found = body["measurements"] as? [[String: Any]] {
            measurements = found.filter { $0["linked"] as? Bool == false }.count
        }
        if uploads + measurements > 0 { unlinked = Unlinked(measurements: measurements, uploads: uploads) }
    }

    /// For testing from the command line (the simulator):
    ///   -autoreplay <recording id> [-replayspeed <x> | max] [-dump] [-replayskips n,n,p] [-expandsensors]
    /// (skips: next / previous stop, one every two seconds, to exercise the controls)
    private func autoReplay() {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "-autoreplay"), i + 1 < args.count,
              let recording = store.recordings[args[i + 1]] else { return }
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

/// A thin header (the record button, with "Recording" and the time while recording; or
/// replay controls when a recording is being played back) over the model's beliefs, or,
/// with nothing going on, a word on how to start.
private struct TrackScreen: View {
    let recorder: Recorder
    let replay: ReplayPlayer
    @State private var editingRide = false
    /// Asking about sharing Train Measurements, once the first recording has started.
    @State private var askingShare = false

    var body: some View {
        VStack(spacing: 0) {
            Group {
                if let recording = replay.recording { replayHeader(recording) } else { recordHeader.frame(height: 44) }
            }
            .padding(.horizontal)
            Divider()
            if replay.recording == nil, !recorder.isRecording {
                idle
            } else {
                ScrollView {
                    VStack(spacing: 12) {
                        if replay.recording != nil {
                            BeliefView(belief: replay.store.belief, start: replay.recording?.started,
                                       message: replay.store.message, waiting: "Loading the recording…")
                        } else {
                            trainRow
                            BeliefView(belief: recorder.inference.belief, start: recorder.startedAt,
                                       message: recorder.inference.message, waiting: "Listening…")
                            if let problem = recorder.live.audio.error {
                                Text(problem).font(.footnote).foregroundStyle(.red)
                            }
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
    }

    /// Nothing recording or playing: in the middle of the screen, how to start.
    private var idle: some View {
        VStack(spacing: 16) {
            Image(systemName: "tram")
                .font(.system(size: 64, weight: .light))
                .foregroundStyle(.tertiary)
            Group {
                if recorder.starting || recorder.busy {
                    Text(recorder.starting ? "Starting…" : "Saving…")
                } else {
                    // the button's own symbol, not its colour: readable whatever colours one sees
                    Text("Tap \(Image(systemName: "record.circle")) to start.")
                        .accessibilityLabel("Tap the record button to start.")
                }
            }
            .font(.title3)
            .foregroundStyle(.secondary)
            if let error = recorder.error {
                Text(error).font(.footnote).foregroundStyle(.red)
            }
        }
        .multilineTextAlignment(.center)
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// The first row while recording: the train this is (typed in), tap to enter or edit.
    /// Blue, like a button, until it's filled in; plain text after (still tappable).
    private var trainRow: some View {
        Button { editingRide = true } label: {
            HStack(spacing: 10) {
                Image(systemName: "tram").foregroundStyle(recorder.ride == nil ? Color.accentColor : Color(.secondaryLabel))
                if let ride = recorder.ride {
                    Text("Trip \(String(ride.trip)) · Car \(String(ride.car))" + (ride.position == .unspecified ? "" : " · \(ride.position.rawValue)"))
                        .monospacedDigit()
                } else {
                    Text("Enter trip and car number")
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(Color(.tertiaryLabel))
            }
            .font(.subheadline)
            .lineLimit(1).minimumScaleFactor(0.7)
            .padding(.horizontal, 12).padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(.plain)
        .foregroundStyle(recorder.ride == nil ? Color.accentColor : Color.primary)
    }

    private var recordHeader: some View {
        recordControls
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
            if args.contains("-askshare") { askingShare = true }
        }
        .sheet(isPresented: $askingShare) {
            MeasurementsConsentSheet { answer in
                switch answer {
                case .always:
                    (AppSettings.shareMeasurements, AppSettings.askedShareMeasurements) = (true, true)
                case .thisRide:
                    if let id = recorder.activeID { AppSettings.shareOnce(id) }  // asked again next time
                case .never:
                    (AppSettings.shareMeasurements, AppSettings.askedShareMeasurements) = (false, true)
                }
                askingShare = false
            }
            // swiping it away or tapping outside answers nothing: asked again next recording
        }
    }

    private var recordControls: some View {
        HStack(spacing: 8) {
            if recorder.isRecording, let started = recorder.startedAt {
                Circle().fill(.red).frame(width: 8, height: 8)
                Text("Recording").font(.subheadline.weight(.medium))
                TimelineView(.periodic(from: started, by: 1)) { context in
                    Text(Self.elapsed(context.date.timeIntervalSince(started)))
                        .font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)
                }
            } else {
                Circle().fill(Color(.systemGray3)).frame(width: 8, height: 8)
                Text("Not recording").font(.subheadline.weight(.medium)).foregroundStyle(.secondary)
                Text("00:00").font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                if recorder.isRecording {
                    Task { await recorder.stop() }
                } else {
                    Task {
                        await recorder.start()
                        // the first recording: ask while it runs (the answer applies when it ends)
                        if recorder.isRecording, !AppSettings.askedShareMeasurements { askingShare = true }
                    }
                }
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

    /// 00:07, 12:34, 1:02:03: the same width from the start (the idle header shows 00:00).
    private static func elapsed(_ seconds: Double) -> String {
        let t = max(Int(seconds), 0)
        return t >= 3600 ? String(format: "%d:%02d:%02d", t / 3600, t / 60 % 60, t % 60) : String(format: "%02d:%02d", t / 60, t % 60)
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


#Preview {
    ContentView()
}
