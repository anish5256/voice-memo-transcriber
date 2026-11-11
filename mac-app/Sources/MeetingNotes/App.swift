import SwiftUI
import AppKit

enum Paths {
    static func url(_ path: String) -> URL {
        URL(fileURLWithPath: NSString(string: path).expandingTildeInPath)
    }
}

@main
struct MeetingNotesApp: App {
    @NSApplicationDelegateAdaptor(MeetingAppDelegate.self) private var delegate
    @StateObject private var queue: JobQueue
    @StateObject private var account: ProviderAccount
    @StateObject private var claudeAccount: ProviderAccount
    @StateObject private var recorder = Recorder()
    @AppStorage("meetingsPath") private var meetingsPath = "~/Meetings"

    init() {
        SettingsView.migrateSettings()
        let account = ProviderAccount(provider: .chatGPT)
        let claudeAccount = ProviderAccount(provider: .claude)
        _claudeAccount = StateObject(wrappedValue: claudeAccount)
        _account = StateObject(wrappedValue: account)
        _queue = StateObject(wrappedValue: JobQueue(account: account, claudeAccount: claudeAccount))
    }

    var body: some Scene {
        WindowGroup {
            ContentView(queue: queue, recorder: recorder, account: account, claudeAccount: claudeAccount)
                .frame(minWidth: 820, minHeight: 520)
                .onAppear { delegate.recorder = recorder }
        }
        .commands {
            CommandGroup(after: .newItem) {
                Button("Open Meetings Folder") {
                    let url = Paths.url(meetingsPath)
                    do {
                        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
                        NSWorkspace.shared.open(url)
                    } catch { queue.notice = error.localizedDescription }
                }
            }
        }
        Settings { SettingsView(account: account, claudeAccount: claudeAccount) }
    }
}

// Defer normal Quit until the final file callback and STOP marker have been handled.
@MainActor
final class MeetingAppDelegate: NSObject, NSApplicationDelegate {
    weak var recorder: Recorder?
    private var terminating = false
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let recorder, recorder.isBusy else { return .terminateNow }
        guard !terminating else { return .terminateLater }
        terminating = true
        Task {
            while recorder.requestingPermission {
                try? await Task.sleep(for: .milliseconds(50))
            }
            if recorder.isRecording { await recorder.stop() }
            while recorder.isBusy { try? await Task.sleep(for: .milliseconds(50)) }
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
    func applicationWillTerminate(_ notification: Notification) {
        // Normal termination has already drained the capture through the deferred reply.
        // A forced process kill leaves its session for launch recovery.
        recorder?.finishForTermination()
    }
}
