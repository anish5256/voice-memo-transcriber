import SwiftUI
import AppKit

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
    @ObservedObject var calendar: CalendarService
    @AppStorage("summaryProvider") private var summaryProvider = "codex"
    @AppStorage("summaryModel") private var summaryModel = ""
    @AppStorage("correctProvider") private var correctProvider = "none"
    @AppStorage("correctModel") private var correctModel = ""
    @AppStorage("anthropicKey") private var anthropicKey = ""
    @AppStorage("repoPath") private var repo = "~/voice-memo-transcriber"
    @AppStorage("meetingsPath") private var meetings = "~/Meetings"
    @AppStorage("proxyURL") private var proxyURL = "http://127.0.0.1:8317/v1"
    @AppStorage("playbackOutputID") private var playbackOutputID = ""
    @AppStorage("proxyKey") private var proxyKey = ""

    var body: some View {
        TabView {
            Form {
                Section {
                    ProviderModelPicker(provider: $correctProvider, model: $correctModel, correction: true)
                } header: {
                    Text("Transcript correction")
                } footer: {
                    Text("Optionally correct the transcript before summarizing.")
                }
                Section {
                    ProviderModelPicker(provider: $summaryProvider, model: $summaryModel, correction: false)
                } header: {
                    Text("Summary")
                } footer: {
                    Text("Settings apply to newly queued jobs.")
                }
            }
            .tabItem { Label("Models", systemImage: "cpu") }

            Form {
                Section {
                    AccountStatusView(account: account)
                    AccountStatusView(account: claudeAccount)
                } header: {
                    Text("Provider accounts")
                } footer: {
                    Text("Connect using your browser to use your ChatGPT or Claude account.")
                }
            }
            .tabItem { Label("Accounts", systemImage: "person.crop.circle") }

            Form {
                Section {
                    HStack {
                        TextField("Meetings folder", text: $meetings)
                        Button("Choose…", action: chooseMeetingsFolder)
                    }
                } header: {
                    Text("Storage")
                } footer: {
                    Text("Recordings and processed meetings are saved here.")
                }
                Section {
                    Picker("Playback output", selection: $playbackOutputID) {
                        Text("System Default").tag("")
                        ForEach(AudioDevices.outputs) { device in Text(device.name).tag(device.id) }
                    }
                }
                Section {
                    TextField("Repository path", text: $repo)
                } header: {
                    Text("Repository")
                } footer: {
                    Text("The folder containing summarize.py and venv.")
                }
            }
            .tabItem { Label("General", systemImage: "gearshape") }

            CalendarSettingsView(calendar: calendar)
                .tabItem { Label("Calendar", systemImage: "calendar") }

            Form {
                Section {
                    TextField("Endpoint URL", text: $proxyURL)
                    if summaryProvider == "proxy" {
                        TextField("Summary model", text: $summaryModel)
                    }
                    if correctProvider == "proxy" {
                        TextField("Correction model", text: $correctModel)
                    }
                    if summaryProvider != "proxy" && correctProvider != "proxy" {
                        TextField("Model", text: .constant("gpt-5")).disabled(true)
                    }
                    SecureField("API key", text: $proxyKey)
                } header: {
                    Text("Custom OpenAI-compatible endpoint")
                } footer: {
                    Text("Choose this provider in Models. Each stage keeps its own model selection.")
                }
                Section {
                    SecureField("Anthropic API key", text: $anthropicKey)
                } header: {
                    Text("Anthropic")
                } footer: {
                    Text("API keys are stored in this Mac’s app preferences.")
                }
            }
            .tabItem { Label("Advanced", systemImage: "slider.horizontal.3") }
        }
        .formStyle(.grouped)
        .frame(width: 520, height: 460)
    }

    private func chooseMeetingsFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.directoryURL = Paths.url(meetings)
        if panel.runModal() == .OK, let url = panel.url { meetings = url.path }
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

