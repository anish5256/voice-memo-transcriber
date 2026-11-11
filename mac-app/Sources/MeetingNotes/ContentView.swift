import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct ContentView: View {
    @ObservedObject var queue: JobQueue
    @ObservedObject var recorder: Recorder
    @ObservedObject var account: ProviderAccount
    @ObservedObject var claudeAccount: ProviderAccount
    @StateObject private var library = MeetingLibrary()
    @AppStorage("meetingsPath") private var meetingsPath = "~/Meetings"
    @AppStorage("summaryProvider") private var summaryProvider = "codex"
    @AppStorage("correctProvider") private var correctProvider = "none"
    @AppStorage("anthropicKey") private var anthropicKey = ""
    @Environment(\.scenePhase) private var scenePhase
    @State private var selection: URL?
    @State private var search = ""
    @State private var targeted = false

    private var successfulJobs: [Job] { queue.jobs.filter { $0.finished && $0.error == nil } }
    private var finishedIDs: [UUID] { queue.jobs.filter(\.finished).map(\.id) }
    private var processing: [Job] { queue.jobs.filter { !$0.finished || $0.error != nil } }

    var body: some View {
        NavigationSplitView {
            List(selection: $selection) {
                if !processing.isEmpty {
                    Section("Processing") {
                        ForEach(processing) { job in ProcessingRow(job: job) }
                    }
                }
                Section("All Meetings") {
                    ForEach(library.meetings.filter { search.isEmpty || $0.title.localizedCaseInsensitiveContains(search) }) { meeting in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(meeting.title).font(.headline).lineLimit(2)
                            Text(meeting.subtitle).font(.caption).foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 6)
                        .tag(meeting.id)
                        .contextMenu {
                            Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([meeting.folder]) }
                            Button("Open Summary in Default App") {
                                NSWorkspace.shared.open(meeting.folder.appendingPathComponent("summary.md"))
                            }
                            Divider()
                            Button("Move to Trash", role: .destructive) { trash(meeting) }
                        }
                    }
                }
            }
            .listStyle(.sidebar)
            .searchable(text: $search, prompt: "Search meetings")
            .navigationTitle("All Meetings")
            .navigationSplitViewColumnWidth(min: 260, ideal: 300, max: 400)
            .safeAreaInset(edge: .bottom, spacing: 0) {
                RecordingControls(recorder: recorder, queue: queue)
            }
        } detail: {
            VStack(spacing: 0) {
                connectionNotice
                if let meeting = library.meetings.first(where: { $0.id == selection }) {
                    MeetingDetailView(meeting: meeting).id(meeting.id)
                } else {
                    ContentUnavailableView("No Meeting Selected", systemImage: "waveform",
                                           description: Text("Record or drop an audio file to get started."))
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(nsColor: .textBackgroundColor))
        }
        .toolbar {
            ToolbarItem { Button(action: chooseFiles) { Label("Import Audio", systemImage: "plus") }.help("Import audio files") }
        }
        .overlay {
            if targeted {
                RoundedRectangle(cornerRadius: 16).fill(Color.accentColor.opacity(0.06))
                    .overlay {
                        RoundedRectangle(cornerRadius: 16).strokeBorder(.secondary, style: StrokeStyle(lineWidth: 2, dash: [8]))
                        Label("Drop to transcribe", systemImage: "waveform.badge.plus").font(.title2)
                            .padding(20).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                    }
                    .padding(12).allowsHitTesting(false)
            }
        }
        .onDrop(of: [UTType.fileURL.identifier], isTargeted: $targeted) { providers in
            for provider in providers {
                _ = provider.loadObject(ofClass: URL.self) { url, _ in
                    if let url { Task { @MainActor in await queue.importFiles([url]) } }
                }
            }
            return !providers.isEmpty
        }
        .safeAreaInset(edge: .bottom) {
            if let message = queue.notice ?? recorder.message ?? library.error {
                HStack {
                    Text(message).textSelection(.enabled)
                    Spacer()
                    Button("Dismiss") { queue.notice = nil; recorder.message = nil; library.error = nil }
                }.font(.callout).padding(10).background(.bar)
            }
        }
        .task { await reload(); await refreshAccounts() }
        .onChange(of: meetingsPath) { _, _ in Task { await reload() } }
        .onChange(of: finishedIDs) { old, new in
            let completed = new.filter { !old.contains($0) }
            let folder = successfulJobs.last(where: { completed.contains($0.id) })?.folder
            Task {
                await reload()
                if let folder {
                    search = ""
                    selection = Paths.url(folder).standardizedFileURL
                }
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await reload(); await refreshAccounts() } }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didBecomeKeyNotification)) { _ in
            Task { await reload(); await refreshAccounts() }
        }
    }

    @ViewBuilder private var connectionNotice: some View {
        let providers = [summaryProvider, correctProvider]
        if providers.contains("codex"), account.state != .connected {
            notice("ChatGPT is not connected.")
        } else if providers.contains("claude"), claudeAccount.state != .connected {
            notice("Claude is not connected.")
        } else if providers.contains("anthropic"), anthropicKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            notice("Add an Anthropic API key to continue.")
        }
    }

    private func notice(_ message: String) -> some View {
        HStack {
            Image(systemName: "person.crop.circle.badge.exclamationmark")
            Text(message)
            Spacer()
            SettingsLink { Text("Connect…") }
        }.font(.callout).padding(12).background(.bar)
    }

    private func reload() async {
        await library.refresh(path: meetingsPath, including: successfulJobs.compactMap { $0.folder.map(Paths.url) })
        if !library.meetings.contains(where: { $0.id == selection }) { selection = library.meetings.first?.id }
    }

    private func trash(_ meeting: Meeting) {
        NSWorkspace.shared.recycle([meeting.folder]) { _, error in
            Task { @MainActor in
                if let error { library.error = error.localizedDescription }
                await reload()
            }
        }
    }

    private func refreshAccounts() async {
        async let chatGPT: Void = account.refresh()
        async let claude: Void = claudeAccount.refresh()
        _ = await (chatGPT, claude)
    }

    private func chooseFiles() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.allowedContentTypes = JobQueue.extensions.compactMap { UTType(filenameExtension: $0) }
        if panel.runModal() == .OK {
            let urls = panel.urls
            Task { await queue.importFiles(urls) }
        }
    }
}

private struct ProcessingRow: View {
    let job: Job
    private var stage: String {
        let name: String
        switch job.stage {
        case "queued": name = "Queued"
        case "starting": name = "Starting…"
        case "transcribing": name = "Transcribing…"
        case "cleaning": name = "Cleaning…"
        case "correcting": name = "Correcting"
        case "summarizing": name = "Summarizing…"
        case "organizing": name = "Organizing…"
        default: name = job.stage.capitalized
        }
        return job.message.map { name + " – " + $0 } ?? (job.stage == "correcting" ? "Correcting…" : name)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(job.name).font(.headline).lineLimit(2)
            HStack(alignment: .top, spacing: 6) {
                if let error = job.error {
                    Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.red)
                    Text(error).textSelection(.enabled)
                } else {
                    ProgressView().controlSize(.mini)
                    Text(stage)
                }
            }.font(.caption).foregroundStyle(.secondary)
        }
        .padding(.vertical, 6)
        .contextMenu {
            if let log = job.log {
                Button("Show Log") { NSWorkspace.shared.activateFileViewerSelecting([Paths.url(log)]) }
            }
        }
    }
}
