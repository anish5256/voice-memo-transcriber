import AVFoundation
import Combine

@MainActor
final class Recorder: ObservableObject {
    @Published private(set) var isRecording = false
    @Published private(set) var requestingPermission = false
    @Published private(set) var startedAt: Date?
    @Published var message: String?
    @Published private(set) var levels: [Float] = Array(repeating: 0, count: 12)
    private var meteringTask: Task<Void, Never>?
    private var audio: AVAudioRecorder?
    private var settings: JobSettings?

    func start(settings: JobSettings) async {
        guard !isRecording, !requestingPermission else { return }
        requestingPermission = true
        defer { requestingPermission = false }
        let granted = await AVCaptureDevice.requestAccess(for: .audio)
        guard granted else {
            message = "Microphone access was denied. Enable MeetingNotes in System Settings → Privacy & Security → Microphone."
            return
        }
        do {
            let directory = Paths.url(settings.meetings).appendingPathComponent("_recordings")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd HHmm"
            let name = "Recording \(formatter.string(from: Date()))"
            var url = directory.appendingPathComponent(name + ".m4a")
            var suffix = 2
            while FileManager.default.fileExists(atPath: url.path) {
                url = directory.appendingPathComponent("\(name) (\(suffix)).m4a")
                suffix += 1
            }
            let recorder = try AVAudioRecorder(url: url, settings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: 48_000,
                AVNumberOfChannelsKey: 1,
                AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue
            ])
            recorder.isMeteringEnabled = true
            guard recorder.record() else {
                throw NSError(domain: "MeetingNotes", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "The microphone could not start recording."])
            }
            audio = recorder
            self.settings = settings
            startedAt = Date()
            isRecording = true
            meteringTask = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(for: .milliseconds(80))
                    guard !Task.isCancelled, let self, let audio = self.audio else { break }
                    audio.updateMeters()
                    let power = audio.averagePower(forChannel: 0)
                    let level = min(1, max(0, (power + 55) / 55))
                    self.levels.removeFirst()
                    self.levels.append(level)
                }
            }
            message = nil
        } catch { message = error.localizedDescription }
    }

    func stop() -> (URL, JobSettings)? {
        guard let audio, let settings else { return nil }
        meteringTask?.cancel()
        meteringTask = nil
        levels = Array(repeating: 0, count: 12)
        audio.stop()
        self.audio = nil
        self.settings = nil
        isRecording = false
        startedAt = nil
        return (audio.url, settings)
    }
}
