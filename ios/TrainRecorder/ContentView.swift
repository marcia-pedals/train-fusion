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
                onReplay: { recording in
                    replay.play(recording)
                    tab = .track
                },
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
            Task { await store.sharePendingMeasurements() }
        }
        .sheet(item: $consent) { request in
            UploadConsentSheet(request: request) { answer in
                consent = nil
                if answer == .dontAskAgain { AppSettings.askToUpload = false }
                store.decide(request.rideID, upload: answer == .yes, protecting: protecting)
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
        } message: { found in
            Text("This phone shared \(Self.describe(found)) before you signed in. Link \(found.measurements + found.uploads == 1 ? "it" : "them") to your account, so you'll be able to see \(found.measurements + found.uploads == 1 ? "it" : "them") with it later?")
        }
    }

    private static func describe(_ found: Unlinked) -> String {
        var parts: [String] = []
        if found.measurements > 0 { parts.append(found.measurements == 1 ? "1 ride's Train Measurements" : "\(found.measurements) rides' Train Measurements") }
        if found.uploads > 0 { parts.append(found.uploads == 1 ? "1 full recording" : "\(found.uploads) full recordings") }
        return parts.joined(separator: " and ")
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

/// A thin header (record controls, or replay controls when a recording is being
/// replayed) over the model's beliefs.
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
                        Text(recorder.starting ? "Starting…" : recorder.busy ? "Saving…" : "Tap the red button to start measuring a ride.\nOr replay an earlier one from History.")
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
        HStack(spacing: 12) {
            if let started = recorder.startedAt {
                Text(started, style: .timer).font(.title3.monospacedDigit())
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
