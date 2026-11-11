import AVFoundation
import Combine
import AppKit

// All capture mutations, including file switches, are serialized away from the UI.
private final class CaptureWriter: NSObject, AVCaptureFileOutputRecordingDelegate, @unchecked Sendable {
    private let queue = DispatchQueue(label: "MeetingNotes.capture")
    private let session = AVCaptureSession()
    private let output = AVCaptureAudioFileOutput()
    private let condition = NSCondition()
    private var unfinished = Set<URL>()
    private var observer: NSObjectProtocol?
    private var disconnectObserver: NSObjectProtocol?
    var finished: (@Sendable (URL, String?) -> Void)?
    var interrupted: (@Sendable () -> Void)?

    func start(device: AVCaptureDevice, url: URL) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            queue.async {
                do {
                    let input = try AVCaptureDeviceInput(device: device)
                    guard self.session.canAddInput(input), self.session.canAddOutput(self.output) else {
                        throw NSError(domain: "MeetingNotes", code: 1, userInfo: [NSLocalizedDescriptionKey: "Cannot configure microphone capture."])
                    }
                    self.session.addInput(input)
                    self.session.addOutput(self.output)
                    self.output.audioSettings = [AVFormatIDKey: kAudioFormatMPEG4AAC,
                        AVSampleRateKey: 48_000, AVNumberOfChannelsKey: 1,
                        AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue]
                    self.observer = NotificationCenter.default.addObserver(
                        forName: .AVCaptureSessionRuntimeError, object: self.session, queue: nil
                    ) { [weak self] _ in self?.interrupted?() }
                    self.disconnectObserver = NotificationCenter.default.addObserver(
                        forName: AVCaptureDevice.wasDisconnectedNotification, object: device, queue: nil
                    ) { [weak self] _ in self?.interrupted?() }
                    self.session.startRunning()
                    self.begin(url)
                    continuation.resume()
                } catch { continuation.resume(throwing: error) }
            }
        }
    }
    private func begin(_ url: URL) {
        condition.lock(); unfinished.insert(url); condition.unlock()
        output.startRecording(to: url, outputFileType: .m4a, recordingDelegate: self)
    }
    func rotate(_ url: URL) { queue.async { self.begin(url) } }
    func pause() { queue.async { self.output.pauseRecording() } }
    func resume() { queue.async { self.output.resumeRecording() } }
    func stop() { queue.async { self.output.stopRecording() } }
    // Used only when AppKit is already terminating. Delegate callbacks write markers
    // directly, so this bounded wait does not depend on servicing the main actor.
    func finishForTermination(timeout: TimeInterval) {
        let deadline = Date().addingTimeInterval(timeout)
        let requested = DispatchSemaphore(value: 0)
        queue.async { self.output.stopRecording(); requested.signal() }
        guard requested.wait(timeout: .now() + timeout) == .success else { return }
        condition.lock()
        while !unfinished.isEmpty && Date() < deadline {
            _ = condition.wait(until: deadline)
        }
        condition.unlock()
    }
    func shutdown() { queue.async {
        if let observer = self.observer { NotificationCenter.default.removeObserver(observer) }
        self.observer = nil
        if let observer = self.disconnectObserver { NotificationCenter.default.removeObserver(observer) }
        self.disconnectObserver = nil
        self.session.stopRunning()
    } }
    func meter() async -> Float {
        await withCheckedContinuation { continuation in
            queue.async {
                let channels = self.output.connection(with: .audio)?.audioChannels ?? []
                continuation.resume(returning: channels.map(\.averagePowerLevel).max() ?? -160)
            }
        }
    }
    func fileOutput(_ output: AVCaptureFileOutput, didFinishRecordingTo url: URL,
                    from connections: [AVCaptureConnection], error: Error?) {
        // This is the only place that creates completion markers, including error salvage.
        var issue: String?
        let size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
        if size > 0 {
            do { try Data().write(to: url.deletingPathExtension().appendingPathExtension("done"), options: .atomic) }
            catch { issue = "Could not mark completed segment: \(error.localizedDescription)" }
        } else {
            issue = "Recording segment is empty or missing: \(url.lastPathComponent). \(error?.localizedDescription ?? "")"
        }
        if let issue { NSLog("%@", issue) }
        condition.lock(); unfinished.remove(url); condition.broadcast(); condition.unlock()
        finished?(url, issue)
        // AVFoundation can report an "error" whose userInfo says the file finished fine
        // (e.g. on a file switch); only a real failure should stop the whole recording.
        if let error = error as NSError?,
           error.userInfo[AVErrorRecordingSuccessfullyFinishedKey] as? Bool != true { interrupted?() }
    }
    deinit {
        if let observer { NotificationCenter.default.removeObserver(observer) }
        if let disconnectObserver { NotificationCenter.default.removeObserver(disconnectObserver) }
    }
}

@MainActor
final class Recorder: ObservableObject {
    @Published private var machine = RecorderStateMachine()
    var isRecording: Bool { machine.state == .recording || machine.state == .paused }
    var isPaused: Bool { machine.state == .paused }
    var isBusy: Bool { machine.state != .idle }
    var requestingPermission: Bool { machine.state == .starting }
    @Published private(set) var elapsed = 0.0
    @Published private(set) var transcript = ""
    @Published var message: String?
    @Published private(set) var levels: [Float] = Array(repeating: 0, count: 12)
    private var ticker: Task<Void, Never>?
    private var writer: CaptureWriter?
    private var sessionURL: URL?
    private var settings: JobSettings?
    private var pending = Set<URL>()
    private var index = 1
    private var segmentElapsed = 0.0
    private var lastTick = Date()
    private var completion: ((URL, JobSettings) -> Void)?

    func start(settings: JobSettings, microphoneID: String,
               completion: @escaping (URL, JobSettings) -> Void) async {
        guard machine.apply(.start) else { return }
        guard await AVCaptureDevice.requestAccess(for: .audio) else {
            machine.apply(.fail)
            message = "Microphone access was denied. Enable MeetingNotes in System Settings → Privacy & Security → Microphone."
            return
        }
        var created: URL?
        do {
            let devices = AudioDevices.inputs
            let selected = devices.first { $0.uniqueID == microphoneID }
            guard let device = selected ?? AVCaptureDevice.default(for: .audio) else {
                throw NSError(domain: "MeetingNotes", code: 2, userInfo: [NSLocalizedDescriptionKey: "No microphone is available."])
            }
            message = !microphoneID.isEmpty && selected == nil ? "Selected microphone is unavailable; using System Default." : nil
            let root = Paths.url(settings.meetings).appendingPathComponent("_live")
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.dateFormat = "yyyyMMdd-HHmmss"
            var date = Date()
            var folder = root.appendingPathComponent(formatter.string(from: date))
            while FileManager.default.fileExists(atPath: folder.path) {
                date = date.addingTimeInterval(1)
                folder = root.appendingPathComponent(formatter.string(from: date))
            }
            try FileManager.default.createDirectory(at: folder.appendingPathComponent("segments"), withIntermediateDirectories: true)
            created = folder
            let metadata: [String: Any] = ["started_at": ISO8601DateFormatter.local.string(from: Date()),
                "input_device_name": device.localizedName, "segment_seconds": 30]
            try JSONSerialization.data(withJSONObject: metadata).write(to: folder.appendingPathComponent("session.json"), options: .atomic)
            let callbackSession = folder
            let capture = CaptureWriter()
            capture.finished = { [weak self] url, issue in
                Task { @MainActor in
                    guard let self, self.sessionURL == callbackSession else { return }
                    self.pending.remove(url)
                    if let issue { self.message = issue }
                }
            }
            capture.interrupted = { [weak self] in
                Task { @MainActor in
                    guard let self else { return }
                    while self.requestingPermission && self.sessionURL == callbackSession {
                        try? await Task.sleep(for: .milliseconds(20))
                    }
                    guard self.sessionURL == callbackSession, self.isRecording else { return }
                    self.message = "Microphone capture ended. Finishing the recorded audio."
                    await self.stop()
                }
            }
            writer = capture
            sessionURL = folder
            self.settings = settings
            self.completion = completion
            index = 1
            let first = segmentURL()
            pending = [first]
            try await capture.start(device: device, url: first)
            machine.apply(.ready)
            elapsed = 0; segmentElapsed = 0; transcript = ""; lastTick = Date()
            let currentSession = folder
            Task.detached { [weak self] in
                await LiveWatcher.run(session: currentSession, settings: settings, text: { value in
                    await self?.appendTranscript(value, session: currentSession)
                }, notice: { value in
                    await self?.watcherNotice(value, session: currentSession)
                })
            }
            ticker = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(for: .milliseconds(80))
                    guard !Task.isCancelled, let self else { break }
                    await self.tick()
                }
            }
        } catch {
            writer?.shutdown(); writer = nil; pending = []
            sessionURL = nil; self.settings = nil; self.completion = nil
            machine.apply(.fail)
            if let created, SegmentNaming.segments(in: created).isEmpty { try? FileManager.default.removeItem(at: created) }
            message = error.localizedDescription
        }
    }
    private func appendTranscript(_ text: String, session: URL) {
        guard session == sessionURL else { return }
        transcript += (transcript.isEmpty ? "" : "\n") + text
    }
    private func watcherNotice(_ text: String, session: URL) {
        if session == sessionURL && isRecording { message = text }
    }
    private func segmentURL() -> URL {
        sessionURL!.appendingPathComponent("segments/" + SegmentNaming.name(index) + ".m4a")
    }
    private func tick() async {
        let now = Date()
        let delta = now.timeIntervalSince(lastTick); lastTick = now
        guard machine.state == .recording, let writer else { return }
        elapsed += delta; segmentElapsed += delta
        if segmentElapsed >= 30 {
            index += 1; segmentElapsed = 0
            let url = segmentURL(); pending.insert(url); writer.rotate(url)
        }
        let power = await writer.meter()
        guard !Task.isCancelled, machine.state == .recording, self.writer === writer else { return }
        levels.removeFirst(); levels.append(min(1, max(0, (power + 55) / 55)))
    }
    func togglePause() {
        if machine.apply(.pause) { writer?.pause() }
        else if machine.apply(.resume) { writer?.resume() }
        lastTick = Date()
        levels = Array(repeating: 0, count: 12)
    }
    func finishForTermination() {
        guard let writer, let sessionURL else { return }
        ticker?.cancel()
        writer.finishForTermination(timeout: 8)
        do { try Data().write(to: sessionURL.appendingPathComponent("STOP"), options: .atomic) }
        catch { NSLog("Could not write STOP on termination: %@", error.localizedDescription) }
    }
    func stop() async {
        guard machine.apply(.stop), let writer, let folder = sessionURL, let settings else { return }
        ticker?.cancel(); ticker = nil
        writer.stop()
        let deadline = Date().addingTimeInterval(8)
        while !pending.isEmpty && Date() < deadline { try? await Task.sleep(for: .milliseconds(40)) }
        if !pending.isEmpty { message = "Timed out finishing audio. Completed segments will be recovered." }
        do {
            try Data().write(to: folder.appendingPathComponent("STOP"), options: .atomic)
            completion?(folder, settings)
        } catch { message = "Could not finish session: \(error.localizedDescription). It will be recovered on launch." }
        writer.shutdown(); self.writer = nil
        sessionURL = nil; self.settings = nil; completion = nil
        machine.apply(.finish)
        levels = Array(repeating: 0, count: 12)
    }
}

private extension ISO8601DateFormatter {
    static var local: ISO8601DateFormatter {
        let value = ISO8601DateFormatter(); value.timeZone = .current; return value
    }
}
