import Foundation
import Combine

struct Job: Identifiable, Sendable {
    let id = UUID()
    let file: URL
    let name: String
    let settings: JobSettings
    var live = false
    var stage = "queued"
    var message: String?
    var folder: String?
    var title: String?
    var error: String?
    var log: String?
    var finished = false
}

private struct Event: Decodable, Sendable {
    let stage: String
    let folder: String?
    let title: String?
    let message: String?
    let log: String?
}

@MainActor
final class JobQueue: ObservableObject {
    static let extensions: Set<String> = ["m4a", "mp3", "wav", "aac", "caf", "ogg", "opus", "flac", "aiff", "mp4", "mov"]
    @Published private(set) var jobs: [Job] = []
    @Published var notice: String?
    private var running = false
    private var recovered = false
    private let recoveryCandidates: [URL]
    private let account: ProviderAccount
    private let claudeAccount: ProviderAccount

    init(account: ProviderAccount, claudeAccount: ProviderAccount) {
        self.account = account
        self.claudeAccount = claudeAccount
        recoveryCandidates = LiveSessionScanner.scan(meetings: Paths.url(SettingsView.snapshot().meetings), queued: [])
    }

    func importFiles(_ urls: [URL]) async {
        let settings = SettingsView.snapshot()
        for source in urls {
            guard Self.extensions.contains(source.pathExtension.lowercased()) else {
                notice = "Unsupported audio file: \(source.lastPathComponent)"
                continue
            }
            let access = source.startAccessingSecurityScopedResource()
            do {
                // Keep file access until the copy completes; copying never blocks the UI actor.
                let destination = try await Task.detached {
                    let inbox = Paths.url(settings.meetings).appendingPathComponent("_inbox")
                    try FileManager.default.createDirectory(at: inbox, withIntermediateDirectories: true)
                    let target = inbox.appendingPathComponent(UUID().uuidString + "-" + source.lastPathComponent)
                    try FileManager.default.copyItem(at: source, to: target)
                    return target
                }.value
                enqueue(destination, settings: settings, name: source.lastPathComponent)
            } catch { notice = "Could not import \(source.lastPathComponent): \(error.localizedDescription)" }
            if access { source.stopAccessingSecurityScopedResource() }
        }
    }

    func enqueue(_ url: URL, settings: JobSettings, name: String? = nil, live: Bool = false) {
        jobs.append(Job(file: url, name: name ?? url.lastPathComponent, settings: settings, live: live))
        runNext()
    }

    func recoverLiveSessions() {
        guard !recovered else { return }
        recovered = true
        let settings = SettingsView.snapshot()
        // This snapshot was taken at initialization, before any new capture can start.
        let sessions = recoveryCandidates
        for session in sessions where !jobs.contains(where: { $0.file.standardizedFileURL == session.standardizedFileURL }) {
            do {
                // No capture is active for these launch-recovered sessions.
                try Data().write(to: session.appendingPathComponent("STOP"), options: .atomic)
                enqueue(session, settings: settings, live: true)
            } catch { notice = "Could not recover session: \(error.localizedDescription)" }
        }
    }

    private func runNext() {
        guard !running, let job = jobs.first(where: { !$0.finished }) else { return }
        running = true
        update(job.id) { $0.stage = "starting" }
        Task {
            let providers = [job.settings.summaryProvider, job.settings.correctProvider]
            // Launch recovery can race an activation-triggered account refresh.
            while (providers.contains("codex") && account.state == .checking) ||
                  (providers.contains("claude") && claudeAccount.state == .checking) {
                try? await Task.sleep(for: .milliseconds(100))
            }
            let error: String?
            if providers.contains("codex"), account.state != .connected {
                error = "Connect ChatGPT first (button at the top)"
            } else if providers.contains("claude"), claudeAccount.state != .connected {
                error = "Connect Claude first (button at the top)"
            } else if providers.contains("anthropic"),
                      job.settings.anthropicKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                error = "Add your Anthropic API key in Settings"
            } else { error = nil }
            if let error {
                update(job.id) {
                    $0.error = error
                    $0.stage = "error"
                    $0.finished = true
                }
                running = false
                runNext()
                return
            }
            let result = await Task.detached {
                await Self.execute(job) { event in
                    await self.receive(event, for: job.id)
                }
            }.value
            update(job.id) {
                if let result, $0.error == nil { $0.error = result }
                if $0.error != nil { $0.stage = "error" }
                $0.finished = true
                if $0.error == nil {
                    if !$0.live, let title = $0.title {
                        do { try RecordingRenamer.rename($0.file, title: title, meetings: Paths.url($0.settings.meetings)) }
                        catch { notice = "Meeting saved, but recording could not be renamed: \(error.localizedDescription)" }
                    }
                    let inbox = Paths.url($0.settings.meetings).appendingPathComponent("_inbox")
                        .standardizedFileURL
                    let file = $0.file.standardizedFileURL
                    let inboxParts = inbox.resolvingSymlinksInPath().pathComponents
                    let fileParts = file.resolvingSymlinksInPath().pathComponents
                    if file.pathComponents.starts(with: inbox.pathComponents),
                       file.pathComponents.count > inbox.pathComponents.count,
                       fileParts.starts(with: inboxParts), fileParts.count > inboxParts.count {
                        try? FileManager.default.removeItem(at: file)
                    }
                }
            }
            running = false
            runNext()
        }
    }

    private func update(_ id: UUID, _ change: (inout Job) -> Void) {
        if let index = jobs.firstIndex(where: { $0.id == id }) { change(&jobs[index]) }
    }

    private func receive(_ event: Event, for id: UUID) {
        update(id) {
            $0.stage = event.stage
            $0.message = event.message
            if let folder = event.folder { $0.folder = folder }
            if let title = event.title { $0.title = title }
            if event.stage == "error" { $0.error = event.message ?? "The pipeline failed." }
            if let log = event.log { $0.log = log }
        }
    }

    nonisolated private static func execute(
        _ job: Job, event: @escaping @Sendable (Event) async -> Void
    ) async -> String? {
        let process = Process()
        let repo = Paths.url(job.settings.repo)
        process.executableURL = repo.appendingPathComponent("venv/bin/python")
        process.currentDirectoryURL = repo
        // Part C accepts --live-session without an AUDIO positional.
        let input = job.live ? ["--live-session", job.file.path] : [job.file.path]
        let args = ["-I", repo.appendingPathComponent("summarize.py").path] + input + [
                    "--meetings-dir", Paths.url(job.settings.meetings).path,
                    "--summary-backend", job.settings.summaryProvider,
                    "--summary-model", job.settings.summaryModel,
                    "--correct-backend", job.settings.correctProvider,
                    "--correct-model", job.settings.correctModel,
                    "--proxy-url", job.settings.proxyURL]
        process.arguments = args
        var environment = ChildEnvironment.standard
        environment["CLIPROXY_API_KEY"] = job.settings.proxyKey
        environment.removeValue(forKey: "ANTHROPIC_API_KEY")
        if [job.settings.summaryProvider, job.settings.correctProvider].contains("anthropic") {
            environment["ANTHROPIC_API_KEY"] = job.settings.anthropicKey
        }
        process.environment = environment
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        process.standardInput = FileHandle.nullDevice
        defer { try? pipe.fileHandleForReading.close() }
        do { try process.run() }
        catch { return "Could not launch the pipeline: \(error.localizedDescription)" }
        var buffer = Data()
        var done = false
        var malformed = false
        func decode(_ line: Data) async {
            guard !line.isEmpty else { return }
            if let value = try? JSONDecoder().decode(Event.self, from: line) {
                if value.stage == "done", value.folder != nil { done = true }
                await event(value)
            } else { malformed = true }
        }
        // This entire function runs in a detached task, including blocking pipe reads and waitUntilExit.
        while true {
            let chunk = pipe.fileHandleForReading.availableData
            if chunk.isEmpty { break }
            buffer.append(chunk)
            while let newline = buffer.firstIndex(of: 10) {
                let line = Data(buffer[..<newline])
                buffer.removeSubrange(...newline)
                await decode(line)
            }
        }
        await decode(buffer)
        process.waitUntilExit()
        if process.terminationStatus != 0 { return "Pipeline exited with status \(process.terminationStatus)." }
        if !done { return malformed ? "Pipeline returned invalid JSON output." : "Pipeline exited without a done event." }
        return nil
    }
}
