import Foundation
import Combine
import Darwin

@MainActor
final class ProviderAccount: ObservableObject {
    enum State: Equatable, Sendable {
        case checking, connected, notConnected, connecting, missing
        case failed(String)
    }

    enum Provider: Equatable, Sendable {
        case chatGPT, claude

        var name: String { self == .chatGPT ? "ChatGPT" : "Claude" }
        var binary: String { self == .chatGPT ? "codex" : "claude" }
        var status: [String] { self == .chatGPT ? ["login", "status"] : ["auth", "status"] }
        var login: [String] { self == .chatGPT ? ["login"] : ["auth", "login", "--claudeai"] }
        var logout: [String] { self == .chatGPT ? ["logout"] : ["auth", "logout"] }
        var missingMessage: String {
            self == .chatGPT ? "Codex is not installed (brew install codex)" : "Claude Code is not installed"
        }
    }

    let provider: Provider

    init(provider: Provider) { self.provider = provider }

    @Published private(set) var state: State = .checking
    private var busy = false

    func refresh() async {
        guard !busy else { return }
        busy = true
        state = .checking
        let provider = self.provider
        state = await Task.detached { await Self.run(provider.status, provider: provider) }.value
        busy = false
    }

    func connect() async {
        await changeAccount(provider.login, connecting: true)
    }

    func disconnect() async {
        await changeAccount(provider.logout, connecting: false)
    }

    private func changeAccount(_ arguments: [String], connecting: Bool) async {
        guard !busy else { return }
        busy = true
        state = connecting ? .connecting : .checking
        let provider = self.provider
        let result = await Task.detached { await Self.run(arguments, provider: provider) }.value
        busy = false
        await refresh()
        // Keep actionable failures if the operation did not leave a connected account.
        if state != .connected {
            switch result {
            case .failed, .missing: state = result
            default: break
            }
        }
    }

    nonisolated private static func run(_ arguments: [String], provider: Provider) async -> State {
        var environment = ProcessInfo.processInfo.environment
        let path = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (environment["PATH"] ?? "")
        environment["PATH"] = path
        guard let executable = path.split(separator: ":").map({
            URL(fileURLWithPath: String($0)).appendingPathComponent(provider.binary)
        }).first(where: { FileManager.default.isExecutableFile(atPath: $0.path) }) else {
            return .missing
        }
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        process.standardInput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        let checking = arguments == provider.status
        let pipe = Pipe()
        if checking {
            process.standardOutput = pipe
            if provider == .chatGPT { process.standardError = pipe }
        }
        else { process.standardOutput = FileHandle.nullDevice }
        defer { try? pipe.fileHandleForReading.close(); try? pipe.fileHandleForWriting.close() }
        do { try process.run() }
        catch { return .failed("Could not start \(provider.name).") }
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
        if timedOut { return .failed("\(provider.name) sign-in timed out. Please try again.") }
        if checking {
            let loggedIn: Bool
            if provider == .claude {
                struct Status: Decodable { let loggedIn: Bool }
                loggedIn = (try? JSONDecoder().decode(Status.self, from: output))?.loggedIn == true
            } else {
                loggedIn = String(decoding: output, as: UTF8.self).contains("Logged in")
            }
            return process.terminationStatus == 0 && loggedIn ? .connected : .notConnected
        }
        return process.terminationStatus == 0 ? .notConnected : .failed("Could not update \(provider.name) sign-in. Please try again.")
    }
}
