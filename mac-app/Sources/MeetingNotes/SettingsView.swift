import SwiftUI

struct JobSettings: Sendable {
    let repo: String
    let meetings: String
    let summaryProvider: String
    let summaryModel: String
    let correctProvider: String
    let correctModel: String
    let anthropicKey: String
    let proxyURL: String
    let proxyKey: String
}

struct SettingsView: View {
    @ObservedObject var account: ProviderAccount
    @ObservedObject var claudeAccount: ProviderAccount
    @AppStorage("summaryProvider") private var summaryProvider = "codex"
    @AppStorage("summaryModel") private var summaryModel = ""
    @AppStorage("correctProvider") private var correctProvider = "none"
    @AppStorage("correctModel") private var correctModel = ""
    @AppStorage("anthropicKey") private var anthropicKey = ""
    @AppStorage("repoPath") private var repo = "~/voice-memo-transcriber"
    @AppStorage("meetingsPath") private var meetings = "~/Meetings"
    @AppStorage("proxyURL") private var proxyURL = "http://127.0.0.1:8317/v1"
    @AppStorage("proxyKey") private var proxyKey = ""

    var body: some View {
        Form {
            Section("Account") {
                AccountStatusView(account: account, allowsSignOut: true)
                AccountStatusView(account: claudeAccount, allowsSignOut: true)
            }
            TextField("Repository path", text: $repo)
            TextField("Meetings folder", text: $meetings)
            Section("Transcript correction") {
                ProviderModelPicker(provider: $correctProvider, model: $correctModel, correction: true)
            }
            Section("Summary") {
                ProviderModelPicker(provider: $summaryProvider, model: $summaryModel, correction: false)
            }
            if uses("proxy") || uses("anthropic") {
                DisclosureGroup("Advanced") {
                    if uses("anthropic") {
                        SecureField("Anthropic API key", text: $anthropicKey)
                    }
                    if uses("proxy") {
                        TextField("Endpoint URL", text: $proxyURL)
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

    private func uses(_ provider: String) -> Bool {
        summaryProvider == provider || correctProvider == provider
    }

    static func migrateSettings() {
        let d = UserDefaults.standard
        guard !d.bool(forKey: "providerSettingsMigrated") else { return }
        if d.object(forKey: "summaryProvider") == nil {
            let provider = d.string(forKey: "backend") ?? "codex"
            d.set(provider, forKey: "summaryProvider")
            let key = provider == "codex" ? "codexModel" :
                (["claude", "anthropic"].contains(provider) ? "claudeModel" : "proxyModel")
            let fallback = ["claude", "anthropic"].contains(provider) ? "claude-opus-5-5" :
                (provider == "proxy" ? "gpt-5" : "")
            if d.object(forKey: "summaryModel") == nil {
                d.set(d.string(forKey: key) ?? fallback, forKey: "summaryModel")
            }
        }
        d.set(true, forKey: "providerSettingsMigrated")
    }

    static func snapshot() -> JobSettings {
        migrateSettings()
        let d = UserDefaults.standard
        return JobSettings(
            repo: d.string(forKey: "repoPath") ?? "~/voice-memo-transcriber",
            meetings: d.string(forKey: "meetingsPath") ?? "~/Meetings",
            summaryProvider: d.string(forKey: "summaryProvider") ?? "codex",
            summaryModel: d.string(forKey: "summaryModel") ?? "",
            correctProvider: d.string(forKey: "correctProvider") ?? "none",
            correctModel: d.string(forKey: "correctModel") ?? "",
            anthropicKey: d.string(forKey: "anthropicKey") ?? "",
            proxyURL: d.string(forKey: "proxyURL") ?? "http://127.0.0.1:8317/v1",
            proxyKey: d.string(forKey: "proxyKey") ?? "")
    }
}

private struct ProviderModelPicker: View {
    @Binding var provider: String
    @Binding var model: String
    let correction: Bool
    @State private var custom = false

    private var claude: Bool { ["claude", "anthropic"].contains(provider) }
    private var models: [String] {
        claude ? ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"] : [""]
    }
    private var selection: Binding<String> {
        Binding(get: { custom || !models.contains(model) ? "custom" : model }, set: {
            custom = $0 == "custom"
            if !custom { model = $0 }
        })
    }

    var body: some View {
        Picker("Provider", selection: $provider) {
            if correction { Text("Off").tag("none") }
            Text("ChatGPT (your account)").tag("codex")
            Text("Claude (your account)").tag("claude")
            Text("Claude API key").tag("anthropic")
            Text("Custom OpenAI-compatible endpoint").tag("proxy")
            if !correction { Text("Offline test").tag("fake") }
        }
        .onChange(of: provider) { _, value in
            custom = false
            model = ["claude", "anthropic"].contains(value) ? "claude-opus-5-5" :
                (value == "proxy" ? "gpt-5" : "")
        }
        if provider == "proxy" {
            TextField("Model", text: $model)
        } else if provider != "none" {
            Picker("Model", selection: selection) {
                ForEach(models, id: \.self) { value in
                    Text(value.isEmpty ? (provider == "codex" ? "Default (Codex chooses)" : "Default") : value).tag(value)
                }
                Text("Custom…").tag("custom")
            }
            if selection.wrappedValue == "custom" {
                TextField("Custom model", text: $model)
            }
        }
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
