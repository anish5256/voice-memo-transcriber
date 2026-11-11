import SwiftUI

struct RecordingControls: View {
    @ObservedObject var recorder: Recorder
    @ObservedObject var queue: JobQueue
    @AppStorage("microphoneID") private var microphoneID = ""

    var body: some View {
        VStack(spacing: 12) {
            if recorder.isBusy {
                Text(Meeting.clock(recorder.elapsed)).monospacedDigit().foregroundStyle(.secondary)
                HStack(spacing: 3) {
                    ForEach(Array(recorder.levels.enumerated()), id: \.offset) { _, level in
                        Capsule().fill(.red).frame(width: 4, height: 3 + CGFloat(level) * 21)
                    }
                }.frame(height: 24).accessibilityLabel("Microphone level")
            }
            HStack(spacing: 16) {
                Menu {
                    Picker("Microphone", selection: $microphoneID) {
                        Text("System Default").tag("")
                        ForEach(AudioDevices.inputs, id: \.uniqueID) { device in
                            Text(device.localizedName).tag(device.uniqueID)
                        }
                    }
                } label: { Image(systemName: "mic.fill") }
                .menuStyle(.borderlessButton).fixedSize().disabled(recorder.isBusy)
                .help("Recording microphone")
                Button(action: toggleRecording) {
                    ZStack {
                        Circle().strokeBorder(recorder.isBusy ? Color.red : Color.secondary.opacity(0.3), lineWidth: 2)
                            .frame(width: 48, height: 48)
                        if recorder.isBusy {
                            RoundedRectangle(cornerRadius: 3).fill(.red).frame(width: 21, height: 21)
                        } else { Circle().fill(.red).frame(width: 40, height: 40) }
                    }.contentShape(Circle())
                }
                .buttonStyle(.plain)
                .disabled(recorder.isBusy && !recorder.isRecording)
                .accessibilityLabel(recorder.isRecording ? "Stop and queue recording" : "Record")
                if recorder.isRecording {
                    Button(action: recorder.togglePause) {
                        Image(systemName: recorder.isPaused ? "play.fill" : "pause.fill")
                    }.buttonStyle(.plain).help(recorder.isPaused ? "Resume recording" : "Pause recording")
                }
            }
        }.frame(maxWidth: .infinity).padding(.vertical, 18).background(.bar)
    }
    private func toggleRecording() {
        if recorder.isRecording { Task { await recorder.stop() } }
        else {
            Task {
                await recorder.start(settings: SettingsView.snapshot(), microphoneID: microphoneID) { url, settings in
                    queue.enqueue(url, settings: settings, live: true)
                }
            }
        }
    }
}

struct LiveRecordingView: View {
    @ObservedObject var recorder: Recorder
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(recorder.isPaused ? "Recording paused" : "Live recording").font(.largeTitle).bold()
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading) {
                        Text(recorder.transcript.isEmpty ? "Transcript will appear as segments finish." : recorder.transcript)
                            .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                        Color.clear.frame(height: 1).id("bottom")
                    }
                }.onChange(of: recorder.transcript) { _, _ in proxy.scrollTo("bottom", anchor: .bottom) }
            }
        }.padding(28)
    }
}
