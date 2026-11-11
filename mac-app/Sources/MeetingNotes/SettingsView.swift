import SwiftUI

struct JobSettings: Sendable {
    let repo: String
    let meetings: String
    let backend: String
    let codexModel: String
    let claudeModel: String
    let anthropicKey: String
    let proxyURL: String
    let proxyModel: String
    let proxyKey: String
}

struct SettingsView: View {
    @ObservedObject var account: ProviderAccount
    @ObservedObject var claudeAccount: ProviderAccount
    @AppStorage("claudeModel") private var claudeModel = "claude-opus-5-5"
    @AppStorage("anthropicKey") private var anthropicKey = ""
    @AppStorage("repoPath") private var repo = "~/voice-memo-transcriber"
    @AppStorage("meetingsPath") private var meetings = "~/Meetings"
    @AppStorage("backend") private var backend = "codex"
    @AppStorage("codexModel") private var codexModel = ""
    @AppStorage("proxyURL") private var proxyURL = "http://127.0.0.1:8317/v1"
    @AppStorage("proxyModel") private var proxyModel = "gpt-5"
    @AppStorage("proxyKey") private var proxyKey = ""

    var body: some View {
        Form {
            Section("Account") {
                AccountStatusView(account: account, allowsSignOut: true)
                AccountStatusView(account: claudeAccount, allowsSignOut: true)
            }
            TextField("Repository path", text: $repo)
            TextField("Meetings folder", text: $meetings)
            Picker("Backend", selection: $backend) {
                Text("ChatGPT (your account)").tag("codex")
                Text("Claude (your account)").tag("claude")
                Text("Claude API key").tag("anthropic")
                Text("Custom OpenAI-compatible endpoint").tag("proxy")
                Text("Offline test").tag("fake")
            }
            if backend == "codex" {
                TextField("ChatGPT model (optional)", text: $codexModel)
            }
            if ["claude", "anthropic"].contains(backend) {
                TextField("Claude model", text: $claudeModel)
            }
            if backend == "proxy" || backend == "anthropic" {
                DisclosureGroup("Advanced") {
                    if backend == "anthropic" {
                        SecureField("Anthropic API key", text: $anthropicKey)
                    } else {
                        TextField("Endpoint URL", text: $proxyURL)
                        TextField("Model", text: $proxyModel)
                        SecureField("API key", text: $proxyKey)
                    }
                    Text("The API key is stored in this Mac’s app preferences.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Text("Settings apply to newly queued jobs.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(24).frame(width: 540)
    }

    static func snapshot() -> JobSettings {
        let d = UserDefaults.standard
        return JobSettings(
            repo: d.string(forKey: "repoPath") ?? "~/voice-memo-transcriber",
            meetings: d.string(forKey: "meetingsPath") ?? "~/Meetings",
            backend: d.string(forKey: "backend") ?? "codex",
            codexModel: d.string(forKey: "codexModel") ?? "",
            claudeModel: d.string(forKey: "claudeModel") ?? "claude-opus-5-5",
            anthropicKey: d.string(forKey: "anthropicKey") ?? "",
            proxyURL: d.string(forKey: "proxyURL") ?? "http://127.0.0.1:8317/v1",
            proxyModel: d.string(forKey: "proxyModel") ?? "gpt-5",
            proxyKey: d.string(forKey: "proxyKey") ?? "")
    }
}

struct AccountStatusView: View {
    @ObservedObject var account: ProviderAccount
    var allowsSignOut = false

    var body: some View {
        HStack {
            switch account.state {
            case .checking:
                ProgressView().controlSize(.small)
                Text("\(account.provider.name): Checking…")
            case .connected:
                Text("\(account.provider.name): Connected").foregroundStyle(.green)
                if allowsSignOut {
                    Button("Sign out") { Task { await account.disconnect() } }
                }
            case .connecting:
                ProgressView().controlSize(.small)
                Text("\(account.provider.name): Finish signing in in your browser…")
            case .missing:
                Text(account.provider.missingMessage)
            case .notConnected:
                Text("\(account.provider.name): Not connected")
                connectButton
            case .failed(let message):
                Text("\(account.provider.name): Not connected")
                Text(message).foregroundStyle(.red)
                connectButton
            }
        }
    }

    private var connectButton: some View {
        Button("Connect \(account.provider.name)") { Task { await account.connect() } }
    }
}
