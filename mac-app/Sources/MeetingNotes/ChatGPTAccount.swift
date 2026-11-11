import Foundation
import Combine
import Darwin

@MainActor
final class ChatGPTAccount: ObservableObject {
    enum State: Equatable, Sendable {
        case checking, connected, notConnected, connecting, codexMissing
        case failed(String)
    }

    @Published private(set) var state: State = .checking
    private var busy = false

    func refresh() async {
        guard !busy else { return }
        busy = true
        state = .checking
        state = await Task.detached { await Self.run(["login", "status"]) }.value
        busy = false
    }

    func connect() async {
        await changeAccount(["login"], connecting: true)
    }

    func disconnect() async {
        await changeAccount(["logout"], connecting: false)
    }

    private func changeAccount(_ arguments: [String], connecting: Bool) async {
        guard !busy else { return }
        busy = true
        state = connecting ? .connecting : .checking
        let result = await Task.detached { await Self.run(arguments) }.value
        busy = false
        await refresh()
        // Keep actionable failures if the operation did not leave a connected account.
        if state != .connected {
            switch result {
            case .failed, .codexMissing: state = result
            default: break
            }
        }
    }

    nonisolated private static func run(_ arguments: [String]) async -> State {
        var environment = ProcessInfo.processInfo.environment
        let path = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (environment["PATH"] ?? "")
        environment["PATH"] = path
        guard let executable = path.split(separator: ":").map({
            URL(fileURLWithPath: String($0)).appendingPathComponent("codex")
        }).first(where: { FileManager.default.isExecutableFile(atPath: $0.path) }) else {
            return .codexMissing
        }
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        process.standardInput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        let checking = arguments == ["login", "status"]
        let pipe = Pipe()
        if checking {
            process.standardOutput = pipe
            process.standardError = pipe
        }
        else { process.standardOutput = FileHandle.nullDevice }
        defer { try? pipe.fileHandleForReading.close(); try? pipe.fileHandleForWriting.close() }
        do { try process.run() }
        catch { return .failed("Could not start Codex.") }
        try? pipe.fileHandleForWriting.close()
        // Drain status output away from the main actor; never surface account details.
        let reader = Task.detached { pipe.fileHandleForReading.readDataToEndOfFile() }
        let deadline = Date().addingTimeInterval(checking ? 30 : 600)
        var timedOut = false
        while process.isRunning {
            if Date() >= deadline {
                timedOut = true
                process.terminate()
                try? await Task.sleep(for: .seconds(1))
                if process.isRunning { kill(process.processIdentifier, SIGKILL) }
                break
            }
            try? await Task.sleep(for: .milliseconds(100))
        }
        process.waitUntilExit()
        let output = await reader.value
        if timedOut { return .failed("ChatGPT sign-in timed out. Please try again.") }
        if checking {
            return process.terminationStatus == 0 && String(decoding: output, as: UTF8.self).contains("Logged in")
                ? .connected : .notConnected
        }
        return process.terminationStatus == 0 ? .notConnected : .failed("Could not update ChatGPT sign-in. Please try again.")
    }
}
