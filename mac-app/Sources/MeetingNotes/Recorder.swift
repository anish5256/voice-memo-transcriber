import AVFoundation
import Combine

@MainActor
final class Recorder: ObservableObject {
    @Published private(set) var isRecording = false
    @Published private(set) var requestingPermission = false
    @Published private(set) var startedAt: Date?
    @Published var message: String?
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
            guard recorder.record() else {
                throw NSError(domain: "MeetingNotes", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "The microphone could not start recording."])
            }
            audio = recorder
            self.settings = settings
            startedAt = Date()
            isRecording = true
            message = nil
        } catch { message = error.localizedDescription }
    }

    func stop() -> (URL, JobSettings)? {
        guard let audio, let settings else { return nil }
        audio.stop()
        self.audio = nil
        self.settings = nil
        isRecording = false
        startedAt = nil
        return (audio.url, settings)
    }
}
