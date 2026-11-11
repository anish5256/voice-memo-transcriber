import Foundation

struct RecorderStateMachine {
    enum State: Equatable { case idle, starting, recording, paused, stopping }
    enum Action { case start, ready, pause, resume, stop, finish, fail }
    private(set) var state: State = .idle
    @discardableResult mutating func apply(_ action: Action) -> Bool {
        switch (state, action) {
        case (.idle, .start): state = .starting
        case (.starting, .ready): state = .recording
        case (.recording, .pause): state = .paused
        case (.paused, .resume): state = .recording
        case (.recording, .stop), (.paused, .stop): state = .stopping
        case (.stopping, .finish), (.starting, .fail): state = .idle
        default: return false
        }
        return true
    }
}

enum SegmentNaming {
    static func name(_ index: Int) -> String { String(format: "seg-%05d", index) }
    static func segments(in session: URL) -> [URL] {
        let files = (try? FileManager.default.contentsOfDirectory(
            at: session.appendingPathComponent("segments"), includingPropertiesForKeys: [.isRegularFileKey])) ?? []
        return files.filter {
            $0.pathExtension == "m4a" && $0.deletingPathExtension().lastPathComponent.range(
                of: #"^seg-[0-9]{5,}$"#, options: .regularExpression) != nil &&
            (try? $0.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true
        }.sorted { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }
    }
}

enum LiveSessionScanner {
    static func scan(meetings: URL, queued: Set<URL>) -> [URL] {
        // Resolve symlinks on both sides (/var vs /private/var) or a queued session is re-enqueued.
        let canonical = { (url: URL) in url.resolvingSymlinksInPath().standardizedFileURL }
        let queued = Set(queued.map(canonical))
        let folders = ((try? FileManager.default.contentsOfDirectory(
            at: meetings.appendingPathComponent("_live"), includingPropertiesForKeys: [.isDirectoryKey])) ?? []).map(canonical)
        return folders.filter {
            !queued.contains($0) &&
            (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true &&
            !SegmentNaming.segments(in: $0).isEmpty
        }.sorted { $0.path < $1.path }
    }
}

enum RecordingRenamer {
    static func sanitized(_ title: String) -> String {
        let forbidden = CharacterSet(charactersIn: "/\\:\0").union(.controlCharacters)
        let value = title.components(separatedBy: forbidden).joined(separator: " ")
            .split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
            .trimmingCharacters(in: CharacterSet(charactersIn: ". "))
        return value.isEmpty ? "Recording" : String(value.prefix(60))
    }
    static func rename(_ source: URL, title: String, meetings: URL) throws {
        let root = meetings.appendingPathComponent("_recordings").resolvingSymlinksInPath().standardizedFileURL
        let file = source.resolvingSymlinksInPath().standardizedFileURL
        guard file.pathComponents.starts(with: root.pathComponents),
              file.pathComponents.count > root.pathComponents.count else { return }
        let stem = sanitized(title)
        var index = 1
        while true {
            let suffix = index == 1 ? "" : " (\(index))"
            let target = file.deletingLastPathComponent().appendingPathComponent(stem + suffix + ".m4a")
            if target == file { return }
            if !FileManager.default.fileExists(atPath: target.path) {
                // moveItem fails if another writer wins the name; it never overwrites.
                try FileManager.default.moveItem(at: file, to: target)
                return
            }
            index += 1
        }
    }
}

enum ChildEnvironment {
    static var standard: [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
        return env
    }
}
