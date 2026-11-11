import SwiftUI

private struct MeetingText: Sendable {
    var summary = ""
    var transcript = ""
    var english = ""
    var error: String?

    static func load(_ folder: URL) -> MeetingText {
        func read(_ name: String) -> String? {
            try? String(contentsOf: folder.appendingPathComponent(name), encoding: .utf8)
        }
        let summary = read("summary.md")
        let transcript = read("transcript.corrected.txt") ?? read("transcript.hinglish.txt")
        return MeetingText(summary: summary ?? "", transcript: transcript ?? "",
                           english: read("transcript.english.txt") ?? "",
                           error: summary == nil || transcript == nil ? "Some meeting files could not be read." : nil)
    }
}

struct MeetingDetailView: View {
    let meeting: Meeting
    @StateObject private var player = MeetingPlayer()
    @State private var tab = "Summary"
    @State private var english = false
    @State private var content = MeetingText()

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            VStack(alignment: .leading, spacing: 8) {
                Text(meeting.title).font(.largeTitle).fontWeight(.bold).textSelection(.enabled)
                Text(meeting.subtitle + " · \(meeting.speakers) \(meeting.speakers == 1 ? "speaker" : "speakers")")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            MeetingPlayerView(player: player)
            HStack {
                Picker("Content", selection: $tab) {
                    Text("Summary").tag("Summary")
                    Text("Transcript").tag("Transcript")
                }.labelsHidden().pickerStyle(.segmented).frame(maxWidth: 260)
                Spacer()
                if tab == "Transcript" {
                    Toggle("Show English", isOn: $english).toggleStyle(.checkbox)
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let error = content.error { Text(error).font(.caption).foregroundStyle(.secondary) }
                    if tab == "Summary" {
                        SummaryTextView(source: content.summary)
                    } else {
                        TranscriptTextView(source: english ? content.english : content.transcript)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
                .padding(.bottom, 24)
            }
        }
        .padding(28)
        .task(id: meeting.id) {
            player.load(meeting.folder)
            let folder = meeting.folder
            let loaded = await Task.detached { MeetingText.load(folder) }.value
            guard !Task.isCancelled else { return }
            content = loaded
        }
        .onDisappear { player.stop() }
    }
}
