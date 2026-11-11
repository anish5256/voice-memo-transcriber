import AVFoundation
import CoreAudio
import Darwin

// The IO queue owns the converter and file. Control calls synchronously drain that queue.
final class SystemAudioCapture: @unchecked Sendable {
    private let queue = DispatchQueue(label: "MeetingNotes.systemAudio")
    private var tap: AudioObjectID = kAudioObjectUnknown
    private var device: AudioObjectID = kAudioObjectUnknown
    private var proc: AudioDeviceIOProcID?
    private var started = false
    private var paused = false
    private var failed = false
    private var file: AVAudioFile?
    private var url: URL?
    private var inputFormat: AVAudioFormat?
    private var converter: AVAudioConverter?
    private let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48_000,
                                     channels: 1, interleaved: false)!
    var closed: ((URL) -> Void)?
    var onError: ((Error) -> Void)?

    private static func check(_ status: OSStatus, _ operation: String) throws {
        guard status != noErr else { return }
        throw NSError(domain: "MeetingNotes.SystemAudio", code: Int(status), userInfo: [
            NSLocalizedDescriptionKey: "\(operation) failed (Core Audio OSStatus \(status)). Check System Settings → Privacy & Security → Screen & System Audio Recording."])
    }

    func start(_ firstURL: URL) throws {
        if #available(macOS 14.2, *) {
            do { try startTap(firstURL) }
            catch {
                stop()
                // A failed start must not leave a bogus second track for the Python watcher.
                try? FileManager.default.removeItem(at: firstURL)
                throw error
            }
        } else {
            throw NSError(domain: "MeetingNotes.SystemAudio", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "System audio requires macOS 14.2 or later."])
        }
    }

    @available(macOS 14.2, *)
    private func startTap(_ firstURL: URL) throws {
        var pid = getpid()
        var process = AudioObjectID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyTranslatePIDToProcessObject,
            mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        try Self.check(AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address,
            UInt32(MemoryLayout<pid_t>.size), &pid, &size, &process), "Find MeetingNotes audio process")
        guard process != kAudioObjectUnknown else {
            throw NSError(domain: "MeetingNotes.SystemAudio", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "Cannot exclude MeetingNotes from system audio."])
        }
        let description = CATapDescription(stereoGlobalTapButExcludeProcesses: [process])
        description.isPrivate = true
        try Self.check(AudioHardwareCreateProcessTap(description, &tap), "Create system audio tap")
        var asbd = AudioStreamBasicDescription()
        size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        address.mSelector = kAudioTapPropertyFormat
        try Self.check(AudioObjectGetPropertyData(tap, &address, 0, nil, &size, &asbd), "Read system audio format")
        guard asbd.mFormatID == kAudioFormatLinearPCM, asbd.mSampleRate > 0,
              let format = AVAudioFormat(streamDescription: &asbd),
              let converter = AVAudioConverter(from: format, to: mono) else {
            throw NSError(domain: "MeetingNotes.SystemAudio", code: 3,
                          userInfo: [NSLocalizedDescriptionKey: "Cannot convert system audio to mono 48 kHz."])
        }
        inputFormat = format
        self.converter = converter
        let specification: [String: Any] = [
            kAudioAggregateDeviceNameKey: "MeetingNotes Call Audio",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapUIDKey: description.uuid.uuidString,
                                              kAudioSubTapDriftCompensationKey: true]]
        ]
        try Self.check(AudioHardwareCreateAggregateDevice(specification as CFDictionary, &device), "Create private audio device")
        try queue.sync { try open(firstURL) }
        try Self.check(AudioDeviceCreateIOProcIDWithBlock(&proc, device, queue) { [weak self] _, input, _, _, _ in
            self?.consume(input)
        }, "Create system audio IO callback")
        try Self.check(AudioDeviceStart(device, proc), "Start system audio")
        started = true
    }

    private func open(_ next: URL) throws {
        file = try AVAudioFile(forWriting: next, settings: [AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 48_000, AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue], commonFormat: .pcmFormatFloat32,
            interleaved: false)
        url = next
    }
    private func close() {
        file = nil // AVAudioFile finalizes the AAC container on release.
        if let url { self.url = nil; closed?(url) }
    }
    func rotate(to next: URL) throws {
        do { try queue.sync { close(); try open(next) } }
        catch {
            stop()
            try? FileManager.default.removeItem(at: next)
            throw error
        }
    }
    func pause() { queue.sync { paused = true } }
    func resume() { queue.sync { paused = false } }

    private func consume(_ input: UnsafePointer<AudioBufferList>) {
        guard !paused, !failed, let format = inputFormat, let converter, let file else { return }
        do {
            guard input.pointee.mNumberBuffers > 0, input.pointee.mBuffers.mDataByteSize > 0 else { return }
            guard let source = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: input, deallocator: nil) else {
                throw NSError(domain: "MeetingNotes.SystemAudio", code: 6,
                              userInfo: [NSLocalizedDescriptionKey: "System audio buffers no longer match the tap format."])
            }
            source.frameLength = source.frameCapacity
            let capacity = AVAudioFrameCount(ceil(Double(source.frameLength) * 48_000 / format.sampleRate) + 256)
            guard let output = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: capacity) else {
                throw NSError(domain: "MeetingNotes.SystemAudio", code: 4,
                              userInfo: [NSLocalizedDescriptionKey: "Cannot allocate system audio conversion buffer."])
            }
            var supplied = false
            var error: NSError?
            let status = converter.convert(to: output, error: &error) { _, status in
                if supplied { status.pointee = .noDataNow; return nil }
                supplied = true; status.pointee = .haveData; return source
            }
            if let error { throw error }
            if status == .error {
                throw NSError(domain: "MeetingNotes.SystemAudio", code: 5,
                              userInfo: [NSLocalizedDescriptionKey: "System audio conversion failed."])
            }
            if output.frameLength > 0 { try file.write(from: output) }
        } catch {
            failed = true
            // Never stop a device from its own IO callback. The owner stops it on its control queue.
            onError?(error)
        }
    }

    func stop() {
        var issues: [Error] = []
        func check(_ status: OSStatus, _ operation: String) {
            do { try Self.check(status, operation) } catch { issues.append(error) }
        }
        if started { check(AudioDeviceStop(device, proc), "Stop system audio"); started = false }
        if let proc { check(AudioDeviceDestroyIOProcID(device, proc), "Destroy system audio callback"); self.proc = nil }
        queue.sync { close(); converter = nil; inputFormat = nil }
        if device != kAudioObjectUnknown {
            check(AudioHardwareDestroyAggregateDevice(device), "Destroy private audio device")
            device = kAudioObjectUnknown
        }
        if #available(macOS 14.2, *), tap != kAudioObjectUnknown {
            check(AudioHardwareDestroyProcessTap(tap), "Destroy system audio tap")
            tap = kAudioObjectUnknown
        }
        for issue in issues { onError?(issue) }
    }
    deinit { stop() }
}
