import AVFoundation
import os
import Speech

/// Push-to-talk speech to text: on this phone when supported and preferred, otherwise Apple's online service.
@MainActor
final class Dictation {
    enum Failure: Error { case unavailable }

    var onText: (String) -> Void = { _ in }

    private static let log = Logger(subsystem: "dev.aitortoise.agentbell", category: "dictation")

    private let recognizer = SFSpeechRecognizer() ?? SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var transcript = Transcript()
    private var text: String { transcript.text }
    private var finished = false
    private var gotFinal = false
    private var generation = 0

    /// Whether this phone and locale can recognise speech without the network.
    var supportsOnDevice: Bool { recognizer?.supportsOnDeviceRecognition ?? false }

    /// Where the current (or last) dictation was recognised.
    private(set) var usingOnDevice = false

    static var isAuthorized: Bool {
        SFSpeechRecognizer.authorizationStatus() == .authorized
            && AVAudioSession.sharedInstance().recordPermission == .granted
    }

    static func requestAuthorization() async -> Bool {
        let speech = await withCheckedContinuation { done in
            SFSpeechRecognizer.requestAuthorization { done.resume(returning: $0) }
        }
        guard speech == .authorized else { return false }
        return await withCheckedContinuation { done in
            AVAudioSession.sharedInstance().requestRecordPermission { done.resume(returning: $0) }
        }
    }

    func start(vocabulary: [String] = [], preferOnDevice: Bool = true) throws {
        cancel()
        guard let recognizer, recognizer.isAvailable else { throw Failure.unavailable }

        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement, options: .duckOthers)
        try session.setActive(true, options: .notifyOthersOnDeactivation)

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        if #available(iOS 16.0, *) {
            request.addsPunctuation = true
        }
        request.contextualStrings = vocabulary
        usingOnDevice = preferOnDevice && recognizer.supportsOnDeviceRecognition
        request.requiresOnDeviceRecognition = usingOnDevice

        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        // No usable input (e.g. a Simulator without a microphone): installTap would raise.
        guard format.sampleRate > 0, format.channelCount > 0 else { throw Failure.unavailable }
        input.installTap(onBus: 0, bufferSize: 1024, format: format, block: Self.tap(into: request))
        engine.prepare()
        try engine.start()

        generation += 1
        transcript = Transcript()
        finished = false
        gotFinal = false
        self.request = request
        task = recognizer.recognitionTask(with: request, resultHandler: Self.handler(for: self, generation: generation))
    }

    /// Ends the audio and waits for the recogniser's final result, up to 1.5 s. Only if none comes
    /// in time (or the task fails) is the latest partial result used.
    func stop() async -> String {
        guard request != nil else { return text }
        let began = Date()
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        request?.endAudio()
        while !finished && Date().timeIntervalSince(began) < 1.5 {
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        let waited = Date().timeIntervalSince(began)
        Self.log.info("dictation ended: \(self.gotFinal ? "final result" : "latest partial", privacy: .public) after \(waited, format: .fixed(precision: 2), privacy: .public) s, on device: \(self.usingOnDevice, privacy: .public)")
        let result = text
        cancel()
        return result
    }

    func cancel() {
        generation += 1
        task?.cancel()
        task = nil
        request = nil
        if engine.isRunning {
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
        }
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func update(_ newText: String?, closesUtterance: Bool, start: Double, end: Double, isFinal: Bool, failed: Bool, generation: Int) {
        // Nothing after the final result (or a failure) may overwrite it.
        guard generation == self.generation, !finished else { return }
        if let newText {
            transcript.add(newText, closesUtterance: closesUtterance, start: start, end: end)
            onText(text)
        }
        if isFinal { gotFinal = true }
        if isFinal || failed { finished = true }
    }

    // Built outside the main actor: the tap runs on the audio thread.
    nonisolated private static func tap(into request: SFSpeechAudioBufferRecognitionRequest) -> AVAudioNodeTapBlock {
        { buffer, _ in request.append(buffer) }
    }

    nonisolated private static func handler(for owner: Dictation, generation: Int) -> (SFSpeechRecognitionResult?, Error?) -> Void {
        { [weak owner] result, error in
            let text = result?.bestTranscription.formattedString
            let isFinal = result?.isFinal ?? false
            let failed = error != nil
            // Speech metadata comes with the result that closes an utterance, at a pause or at the end.
            let closes = result?.speechRecognitionMetadata != nil
            let segments = result?.bestTranscription.segments ?? []
            let start = segments.first?.timestamp ?? 0
            let end = segments.last.map { $0.timestamp + $0.duration } ?? 0
            Task { @MainActor in
                owner?.update(text, closesUtterance: closes, start: start, end: end, isFinal: isFinal, failed: failed, generation: generation)
            }
        }
    }
}
