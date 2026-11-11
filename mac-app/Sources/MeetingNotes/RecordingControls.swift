import SwiftUI

struct RecordingControls: View {
    @ObservedObject var recorder: Recorder
    @ObservedObject var queue: JobQueue

    var body: some View {
        VStack(spacing: 12) {
            if let started = recorder.startedAt {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Text(Meeting.clock(context.date.timeIntervalSince(started)))
                        .monospacedDigit().foregroundStyle(.secondary)
                }
                HStack(alignment: .center, spacing: 3) {
                    ForEach(Array(recorder.levels.enumerated()), id: \.offset) { _, level in
                        Capsule().fill(.red).frame(width: 4, height: 3 + CGFloat(level) * 21)
                    }
                }
                .frame(height: 24)
                .animation(.easeOut(duration: 0.1), value: recorder.levels)
                .accessibilityLabel("Microphone level")
            }
            Button(action: toggleRecording) {
                ZStack {
                    Circle().strokeBorder(recorder.isRecording ? Color.red : Color.secondary.opacity(0.3), lineWidth: 2)
                        .frame(width: 48, height: 48)
                    if recorder.isRecording {
                        RoundedRectangle(cornerRadius: 3).fill(.red).frame(width: 21, height: 21)
                    } else {
                        Circle().fill(.red).frame(width: 40, height: 40)
                    }
                }
                .contentShape(Circle())
            }
            .buttonStyle(.plain)
            .disabled(recorder.requestingPermission)
            .accessibilityLabel(recorder.isRecording ? "Stop and queue recording" : "Record")
            .help(recorder.isRecording ? "Stop and queue recording" : "Record a meeting")
        }
        .frame(maxWidth: .infinity).padding(.vertical, 18).background(.bar)
    }

    private func toggleRecording() {
        if recorder.isRecording {
            if let (url, settings) = recorder.stop() { queue.enqueue(url, settings: settings) }
        } else {
            Task { await recorder.start(settings: SettingsView.snapshot()) }
        }
    }
}
