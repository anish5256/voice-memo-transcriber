import SwiftUI

struct JobSettings: Sendable {
    let repo: String
    let meetings: String
    let backend: String
    let codexModel: String
    let proxyURL: String
    let proxyModel: String
    let proxyKey: String
}

struct SettingsView: View {
    @AppStorage("repoPath") private var repo = "~/voice-memo-transcriber"
    @AppStorage("meetingsPath") private var meetings = "~/Meetings"
    @AppStorage("backend") private var backend = "codex"
    @AppStorage("codexModel") private var codexModel = ""
    @AppStorage("proxyURL") private var proxyURL = "http://127.0.0.1:8317/v1"
    @AppStorage("proxyModel") private var proxyModel = "gpt-5"
    @AppStorage("proxyKey") private var proxyKey = ""

    var body: some View {
        Form {
            TextField("Repository path", text: $repo)
            TextField("Meetings folder", text: $meetings)
            Picker("Backend", selection: $backend) {
                ForEach(["codex", "proxy", "fake"], id: \.self) { Text($0) }
            }
            TextField("Codex model (optional)", text: $codexModel)
            TextField("Proxy URL", text: $proxyURL)
            TextField("Proxy model", text: $proxyModel)
            SecureField("Proxy API key", text: $proxyKey)
            Text("Settings apply to newly queued jobs. The proxy key is stored in this Mac’s app preferences.")
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
            proxyURL: d.string(forKey: "proxyURL") ?? "http://127.0.0.1:8317/v1",
            proxyModel: d.string(forKey: "proxyModel") ?? "gpt-5",
            proxyKey: d.string(forKey: "proxyKey") ?? "")
    }
}
