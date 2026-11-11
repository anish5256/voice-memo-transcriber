import SwiftUI
import AVFoundation
import Combine

@MainActor
final class MeetingPlayer: ObservableObject {
    @Published private(set) var isPlaying = false
    @Published private(set) var duration = 0.0
    @Published var position = 0.0
    @Published private(set) var error: String?
    private var audio: AVPlayer?
    private var loading: Task<Void, Never>?
    private var ticker: Task<Void, Never>?

    func load(_ folder: URL) {
        stop()
        error = nil
        position = 0
        duration = 0
        audio = nil
        loading?.cancel()
        do {
            let files = try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)
            guard let url = files.sorted(by: { $0.path < $1.path }).first(where: {
                $0.deletingPathExtension().lastPathComponent == "audio" && JobQueue.extensions.contains($0.pathExtension.lowercased())
            }) else {
                error = "Audio is unavailable."
                return
            }
            let player = AVPlayer(url: url)
            audio = player
            setOutput(UserDefaults.standard.string(forKey: "playbackOutputID") ?? "")
            loading = Task { [weak self] in
                do {
                    let time = try await player.currentItem?.asset.load(.duration)
                    guard !Task.isCancelled, let self, self.audio === player else { return }
                    let seconds = time?.seconds ?? 0
                    self.duration = seconds.isFinite ? max(0, seconds) : 0
                } catch {
                    guard !Task.isCancelled else { return }
                    self?.error = "Could not load audio: \(error.localizedDescription)"
                }
            }
        } catch { self.error = "Could not play audio: \(error.localizedDescription)" }
    }

    func toggle() {
        guard let audio else { return }
        if isPlaying { stop(); return }
        if position >= duration { seek(0) }
        setOutput(UserDefaults.standard.string(forKey: "playbackOutputID") ?? "")
        audio.play()
        isPlaying = true
        ticker = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(100))
                guard !Task.isCancelled, let self, let audio = self.audio else { break }
                let seconds = audio.currentTime().seconds
                if seconds.isFinite { self.position = max(0, seconds) }
                if audio.currentItem?.status == .failed {
                    self.error = "This audio could not be played."
                    self.stop()
                    break
                }
                if self.duration > 0 && self.position >= self.duration - 0.05 {
                    self.position = self.duration
                    self.stop()
                    break
                }
            }
        }
    }

    func seek(_ value: Double) {
        position = min(duration, max(0, value))
        audio?.seek(to: CMTime(seconds: position, preferredTimescale: 600))
    }

    func setOutput(_ id: String) {
        let available = AudioDevices.outputs.contains { $0.id == id }
        audio?.audioOutputDeviceUniqueID = id.isEmpty || !available ? nil : id
    }

    func stop() {
        ticker?.cancel()
        ticker = nil
        audio?.pause()
        isPlaying = false
    }
}

struct MeetingPlayerView: View {
    @ObservedObject var player: MeetingPlayer
    @AppStorage("playbackOutputID") private var outputID = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 16) {
                Button { player.toggle() } label: {
                    Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                        .font(.title2).frame(width: 30, height: 30)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(player.isPlaying ? "Pause" : "Play")
                .disabled(player.duration <= 0)
                VStack(spacing: 4) {
                    Slider(value: Binding(get: { player.position }, set: { player.seek($0) }),
                           in: 0...max(player.duration, 0.01))
                        .accessibilityLabel("Playback position")
                        .disabled(player.duration <= 0)
                    HStack {
                        Text(Meeting.clock(player.position))
                        Spacer()
                        Text("−" + Meeting.clock(player.duration - player.position))
                    }.font(.caption).monospacedDigit().foregroundStyle(.secondary)
                }
            }
            if let error = player.error { Text(error).font(.caption).foregroundStyle(.secondary) }
        }.onChange(of: outputID) { _, value in player.setOutput(value) }
    }
}
