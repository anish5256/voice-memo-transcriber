import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct ContentView: View {
    @ObservedObject var queue: JobQueue
    @ObservedObject var recorder: Recorder
    @ObservedObject var account: ProviderAccount
    @ObservedObject var claudeAccount: ProviderAccount
    @Environment(\.scenePhase) private var scenePhase
    @State private var targeted = false

    var body: some View {
        VStack(spacing: 18) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    AccountStatusView(account: account)
                    AccountStatusView(account: claudeAccount)
                }
                Spacer()
            }.font(.caption)
            VStack(spacing: 10) {
                Image(systemName: "waveform.badge.plus").font(.largeTitle)
                Text("Drop meeting audio here").font(.title2)
                Text("m4a · mp3 · wav · aac · caf · ogg · opus · flac · aiff · mp4 · mov")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Choose Files…", action: chooseFiles)
            }
            .frame(maxWidth: .infinity).padding(28)
            .background(targeted ? Color.accentColor.opacity(0.15) : Color.secondary.opacity(0.08))
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .onDrop(of: [UTType.fileURL.identifier], isTargeted: $targeted) { providers in
                for provider in providers {
                    _ = provider.loadObject(ofClass: URL.self) { url, _ in
                        if let url { Task { @MainActor in await queue.importFiles([url]) } }
                    }
                }
                return !providers.isEmpty
            }
            HStack {
                Button(recorder.isRecording ? "Stop & Queue" : "Record") {
                    if recorder.isRecording {
                        if let (url, settings) = recorder.stop() { queue.enqueue(url, settings: settings) }
                    } else {
                        Task { await recorder.start(settings: SettingsView.snapshot()) }
                    }
                }
                .disabled(recorder.requestingPermission)
                if let started = recorder.startedAt {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        let seconds = max(0, Int(context.date.timeIntervalSince(started)))
                        Text(String(format: "%02d:%02d", seconds / 60, seconds % 60)).monospacedDigit()
                    }
                }
                Spacer()
                SettingsLink { Image(systemName: "gearshape") }
            }
            if let message = recorder.message { Text(message).foregroundStyle(.red).textSelection(.enabled) }
            if let notice = queue.notice {
                HStack {
                    Text(notice).foregroundStyle(.red).textSelection(.enabled)
                    Button("Dismiss") { queue.notice = nil }
                }
            }
            List(queue.jobs) { job in
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(job.title ?? job.name).font(.headline).lineLimit(2)
                        Spacer()
                        if !job.finished && job.stage != "queued" { ProgressView().controlSize(.small) }
                        Text(job.message.map { "\(job.stage.capitalized) – \($0)" } ?? job.stage.capitalized).foregroundStyle(.secondary)
                    }
                    if let error = job.error { Text(error).foregroundStyle(.red).textSelection(.enabled) }
                    HStack {
                        if job.finished, job.error == nil, let folder = job.folder {
                            Button("Open folder") { NSWorkspace.shared.open(Paths.url(folder)) }
                            Button("Open summary") {
                                NSWorkspace.shared.open(Paths.url(folder).appendingPathComponent("summary.md"))
                            }
                        }
                        if let log = job.log {
                            Button("Show log") { NSWorkspace.shared.activateFileViewerSelecting([Paths.url(log)]) }
                        }
                    }
                }.padding(.vertical, 6)
            }
            .overlay {
                if queue.jobs.isEmpty { Text("Queued meetings will appear here.").foregroundStyle(.secondary) }
            }
        }
        .padding(20)
        .task { await refreshAccounts() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await refreshAccounts() } }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didBecomeKeyNotification)) { _ in
            Task { await refreshAccounts() }
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
