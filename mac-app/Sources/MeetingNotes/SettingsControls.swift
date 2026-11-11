import SwiftUI

struct ProviderModelPicker: View {
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

    private var busy: Bool { account.state == .checking || account.state == .connecting }
    private var status: String {
        switch account.state {
        case .checking: return "Checking…"
        case .connected: return "Connected"
        case .connecting: return "Finish signing in in your browser…"
        case .missing: return account.provider.missingMessage
        case .notConnected: return "Not connected"
        case .failed(let message): return message
        }
    }

    var body: some View {
        HStack(spacing: 10) {
            Circle().fill(account.state == .connected ? Color.green : Color.secondary)
                .frame(width: 7, height: 7).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(account.provider.name)
                Text(status).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            }
            Spacer()
            if busy { ProgressView().controlSize(.small) }
            if account.state == .connected {
                Button("Sign Out") { Task { await account.disconnect() } }
            } else {
                Button("Connect") { Task { await account.connect() } }.disabled(busy || account.state == .missing)
            }
        }.padding(.vertical, 4)
    }
}
