import SwiftUI
import AppKit

enum Paths {
    static func url(_ path: String) -> URL {
        URL(fileURLWithPath: NSString(string: path).expandingTildeInPath)
    }
}

@main
struct MeetingNotesApp: App {
    @StateObject private var queue: JobQueue
    @StateObject private var account: ChatGPTAccount
    @StateObject private var recorder = Recorder()
    @AppStorage("meetingsPath") private var meetingsPath = "~/Meetings"

    init() {
        let account = ChatGPTAccount()
        _account = StateObject(wrappedValue: account)
        _queue = StateObject(wrappedValue: JobQueue(account: account))
    }

    var body: some Scene {
        WindowGroup {
            ContentView(queue: queue, recorder: recorder, account: account)
                .frame(minWidth: 640, minHeight: 480)
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
        Settings { SettingsView(account: account) }
    }
}
