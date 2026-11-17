import SwiftUI

struct ContentView: View {
    @State private var recorder = Recorder()
    @State private var store = RecordingStore()
    @State private var deleting: Recording?
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        VStack(spacing: 0) {
            recordPanel
            Divider()
            if store.recordings.isEmpty {
                ContentUnavailableView("No recordings", systemImage: "waveform")
            } else {
                List(store.recordings) { recording in
                    RecordingRow(
                        recording: recording, upload: store.uploads[recording.id],
                        onUpload: { Task { await store.upload(recording) } },
                        onDelete: { deleting = recording })
                }
                .listStyle(.plain)
            }
        }
        .onAppear(perform: refresh)
        .onChange(of: recorder.activeID) { refresh() }
        .onChange(of: scenePhase) { refresh() }
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

    private func refresh() {
        store.refresh(excluding: recorder.activeID)
    }

    private var recordPanel: some View {
        VStack(spacing: 12) {
            Button {
                Task { if recorder.isRecording { await recorder.stop() } else { await recorder.start() } }
            } label: {
                ZStack {
                    Circle().strokeBorder(.primary, lineWidth: 4).frame(width: 84, height: 84)
                    RoundedRectangle(cornerRadius: recorder.isRecording ? 8 : 35)
                        .fill(.red)
                        .frame(width: recorder.isRecording ? 36 : 70, height: recorder.isRecording ? 36 : 70)
                }
                .animation(.easeInOut(duration: 0.2), value: recorder.isRecording)
            }
            .buttonStyle(.plain)
            .disabled(recorder.busy)
            .accessibilityLabel(recorder.isRecording ? "Stop recording" : "Start recording")

            if let started = recorder.startedAt {
                Text(started, style: .timer)
                    .font(.system(size: 34, weight: .medium).monospacedDigit())
                LiveStatus(live: recorder.live)
            } else {
                Text(recorder.busy ? "Saving…" : "Tap to record")
                    .font(.headline)
                    .foregroundStyle(.secondary)
            }
            if let error = recorder.error {
                Text(error).font(.footnote).foregroundStyle(.red).multilineTextAlignment(.center)
            }
        }
        .padding()
        .frame(maxWidth: .infinity)
    }
}

/// While recording: is each stream actually flowing, and how good is the clock.
private struct LiveStatus: View {
    let live: Recorder.Live

    var body: some View {
        VStack(spacing: 4) {
            HStack(spacing: 14) {
                item("Audio", live.audio.capturing ? String(format: "%.0f kHz", live.audio.sampleRate / 1000) : "—",
                     ok: live.audio.capturing)
                item("Accel", "\(live.accelSamples)", ok: live.accelSamples > 0)
                item("Gyro", "\(live.gyroSamples)", ok: live.gyroSamples > 0)
            }
            HStack(spacing: 14) {
                item("Mag", "\(live.magSamples)", ok: live.magSamples > 0)
                item("Baro", "\(live.baroSamples)", ok: live.baroSamples > 0)
                item("Fused", "\(live.fusedSamples)", ok: live.fusedSamples > 0)
            }
            if let ntp = live.ntp {
                Text(String(format: "Phone clock is %+.0f ms from NTP (±%.0f ms)", -ntp.offset * 1000, ntp.roundTrip * 500))
            } else {
                Text("No NTP measurement yet")
            }
            if let error = live.audio.error {
                Text(error).foregroundStyle(.red)
            } else if live.audio.segments > 1 {
                Text("Audio was interrupted: \(live.audio.segments) segments")
            }
        }
        .font(.footnote.monospacedDigit())
        .foregroundStyle(.secondary)
    }

    private func item(_ name: String, _ value: String, ok: Bool) -> some View {
        HStack(spacing: 4) {
            Circle().fill(ok ? .green : .orange).frame(width: 8, height: 8)
            Text("\(name) \(value)")
        }
    }
}

private struct RecordingRow: View {
    let recording: Recording
    let upload: UploadState?
    let onUpload: () -> Void
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 3) {
                Text(recording.started?.formatted(date: .abbreviated, time: .standard) ?? recording.id)
                    .font(.body.monospacedDigit())
                Text(detail).font(.footnote).foregroundStyle(.secondary)
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
            Button(role: .destructive, action: onDelete) { Image(systemName: "trash") }
                .accessibilityLabel("Delete")
                .disabled({ if case .uploading = upload { return true } else { return false } }())
        }
        .font(.title3)
        .buttonStyle(.borderless)  // so each button in the row takes its own taps
    }

    private var detail: String {
        let size = ByteCountFormatter.string(fromByteCount: recording.bytes, countStyle: .file)
        guard let duration = recording.duration else { return "not stopped properly · \(size)" }
        let length = Duration.seconds(duration.rounded()).formatted(.time(pattern: duration >= 3600 ? .hourMinuteSecond : .minuteSecond))
        return "\(length) · \(size)" + (recording.uploaded ? " · uploaded" : "")
    }
}

#Preview {
    ContentView()
}
