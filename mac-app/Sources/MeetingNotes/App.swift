import SwiftUI
import AppKit
import UserNotifications

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
    @StateObject private var calendar = CalendarService()
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
        // One main window (like Voice Memos); Window ▸ MeetingNotes and the Dock icon reopen it.
        Window("MeetingNotes", id: "main") {
            ContentView(queue: queue, recorder: recorder, account: account, claudeAccount: claudeAccount, calendar: calendar)
                .frame(minWidth: 820, minHeight: 520)
                .onAppear {
                    delegate.recorder = recorder
                    delegate.recordMeeting = { id, start in
                        calendar.refresh()
                        guard !recorder.isBusy else { return }
                        guard let event = UpcomingMeetingPicker.eligible(calendar.events, now: Date()).first(where: {
                            $0.id == id && abs($0.start.timeIntervalSince1970 - start) < 1
                        }) else {
                            queue.notice = "This calendar meeting is no longer available to record."
                            return
                        }
                        Task { await recorder.startLinked(event.context, queue: queue) }
                    }
                }
                .background(ReopenBridge { delegate.openMain = $0 })
        }
        // No title in the toolbar (like Voice Memos): AppKit re-positions toolbar titles without
        // animating them, so it jumped to the right while the sidebar expanded.
        .windowToolbarStyle(.unified(showsTitle: false))
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
        Settings { SettingsView(account: account, claudeAccount: claudeAccount, calendar: calendar) }
    }
}

// Defer normal Quit until the final file callback and STOP marker have been handled.
@MainActor
final class MeetingAppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    weak var recorder: Recorder?
    var openMain: (() -> Void)? { didSet { handlePendingNotification() } }
    var recordMeeting: ((String, Double) -> Void)? { didSet { handlePendingNotification() } }
    private var pendingRecord: (String, Double)?
    func applicationDidFinishLaunching(_ notification: Notification) {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let record = UNNotificationAction(identifier: "RECORD_MEETING", title: "Record", options: [.foreground])
        center.setNotificationCategories([UNNotificationCategory(identifier: "MEETING_RECORD", actions: [record],
                                                                 intentIdentifiers: [], options: [])])
    }
    private func handlePendingNotification() {
        guard let pendingRecord, let openMain, let recordMeeting else { return }
        self.pendingRecord = nil
        openMain()
        NSApp.activate(ignoringOtherApps: true)
        recordMeeting(pendingRecord.0, pendingRecord.1)
    }
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        let info = response.notification.request.content.userInfo
        let id = info["eventIdentifier"] as? String
        let start = info["eventStart"] as? Double
        let record = response.actionIdentifier == "RECORD_MEETING"
        Task { @MainActor in
            self.openMain?()
            NSApp.activate(ignoringOtherApps: true)
            if record, let id, let start {
                self.pendingRecord = (id, start)
                self.handlePendingNotification()
            }
            completionHandler()
        }
    }
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
        willPresent notification: UNNotification, withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
    private var terminating = false
    // Without this, closing the window left a running app that never showed a window again.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { openMain?() }
        return true
    }
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

// Hands SwiftUI's openWindow action to the app delegate so a Dock click can reopen the window.
private struct ReopenBridge: View {
    @Environment(\.openWindow) private var openWindow
    let register: (@escaping () -> Void) -> Void
    var body: some View {
        Color.clear.frame(width: 0, height: 0).onAppear {
            let open = openWindow
            register { open(id: "main") }
        }
    }
}
