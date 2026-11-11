import SwiftUI
import AVFoundation
import Combine

@MainActor
final class MeetingPlayer: ObservableObject {
    @Published private(set) var isPlaying = false
    @Published private(set) var duration = 0.0
    @Published var position = 0.0
    @Published private(set) var error: String?
    private var audio: AVAudioPlayer?
    private var ticker: Task<Void, Never>?

    func load(_ folder: URL) {
        stop()
        error = nil
        position = 0
        duration = 0
        audio = nil
        do {
            let files = try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)
            guard let url = files.sorted(by: { $0.path < $1.path }).first(where: {
                $0.deletingPathExtension().lastPathComponent == "audio" && JobQueue.extensions.contains($0.pathExtension.lowercased())
            }) else {
                error = "Audio is unavailable."
                return
            }
            let player = try AVAudioPlayer(contentsOf: url)
            player.prepareToPlay()
            audio = player
            duration = player.duration
        } catch { self.error = "Could not play audio: \(error.localizedDescription)" }
    }

    func toggle() {
        guard let audio else { return }
        if isPlaying { stop(); return }
        if position >= duration { seek(0) }
        guard audio.play() else { error = "This audio format cannot be played."; return }
        isPlaying = true
        ticker = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(100))
                guard !Task.isCancelled, let self, let audio = self.audio else { break }
                self.position = audio.currentTime
                if !audio.isPlaying {
                    self.position = self.duration
                    self.isPlaying = false
                    break
                }
            }
        }
    }

    func seek(_ value: Double) {
        position = min(duration, max(0, value))
        audio?.currentTime = position
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
        }
    }
}
