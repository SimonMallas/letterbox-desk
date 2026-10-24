import CryptoKit
import Network
import SwiftUI
import UIKit

@MainActor
final class AppModel: ObservableObject {
    enum Screen: Equatable { case welcome, pair, paired, idle, ring, listening, finishing, review, editing, sending, sent, failed }
    enum Link: Equatable { case connecting, up, down, untrusted(Mistrust) }
    /// Why the paired Mac can't be used right now. Bell never re-pairs by itself: only the person can (Pair again).
    enum Mistrust: Equatable { case keyChanged, notRecognised }
    /// `failed` stops the pairing loop until the person taps Try again.
    enum PairPhase: Equatable {
        case searching, unreachable(String), windowClosed, tooOld, code(String), denied, expired, failed
        /// The Mac accepted this phone's code; now the person types the code the Mac shows.
        case confirm(retry: Bool)
        case confirming
        /// The code typed here didn't match the Mac's twice: nothing was paired.
        case wrongMacCode
    }
    enum SendPhase: Equatable { case posting, checking }
    enum NetworkProblem: Equatable { case noWiFi, localNetworkDenied }

    /// What the screen shows. `offline` is idle with the link down.
    enum Face: Equatable { case welcome, pair, paired, idle, offline, ring, listening, finishing, review, editing, sending, sent, failed }

    /// One seat's copy of a sent letter.
    struct SeatOutcome: Equatable {
        enum State: Equatable {
            case published
            /// The Mac tried but couldn't confirm: the copy may have landed.
            case notConfirmed
            /// Provably never sent.
            case notAttempted
        }

        let seat: String
        let state: State
        var envelopeID: String?
        var ringOutcome: String?
        var reason: String?
    }

    /// The header of the failed screen; per-seat detail lives in `outcomes`.
    struct Failure: Equatable {
        let label: String
        let title: String
        let note: String

        static let notConfirmed = Failure(label: "Not confirmed", title: "The Mac didn't confirm this letter",
                                          note: "Nothing was retried. It may still have arrived.")
        static let notSent = Failure(label: "Not sent", title: "The Mac didn't send this letter",
                                     note: "Nothing was attempted.")
        static let neverReceived = Failure(label: "Not sent", title: "The Mac never got this letter",
                                           note: "Nothing was retried.")
        static let refused = Failure(label: "Not sent", title: "The Mac turned this letter down",
                                     note: "Nothing was attempted.")
        static let stopping = Failure(label: "Not sent", title: "The listener was stopping",
                                      note: "Nothing was attempted.")
        static let unreachable = Failure(label: "Not sent", title: "Can't reach the listener",
                                         note: "The letter never left the phone.")
    }

    @Published private(set) var screen: Screen = .pair
    @Published private(set) var link: Link = .connecting
    @Published private(set) var pairPhase: PairPhase = .searching
    @Published private(set) var pairReason: String?
    @Published private(set) var macName = "Mac"
    @Published private(set) var seats: [String] = []
    @Published private(set) var chosen: Set<String>
    @Published private(set) var everyone: Bool
    @Published private(set) var lastMail: MailEnvelope?
    @Published private(set) var ringMail: MailEnvelope?
    @Published private(set) var ringAt = Date()
    @Published private(set) var transcript = ""
    @Published private(set) var notice: String?
    @Published private(set) var reviewSeats: [String] = []
    @Published private(set) var sendPhase: SendPhase = .posting
    @Published private(set) var sendTo: [String] = []
    @Published private(set) var outcomes: [SeatOutcome] = []
    @Published private(set) var sentAt = Date()
    @Published private(set) var failure: Failure?
    /// Where the next dictation will be recognised, for the listening footnote.
    @Published private(set) var speechOnDevice = false
    @Published private(set) var onDeviceSpeechSupported = false
    /// The person chose Apple's online recognition over on-device.
    @Published var speechOnline: Bool {
        didSet {
            UserDefaults.standard.set(speechOnline, forKey: "speechOnline")
            speechOnDevice = onDeviceSpeechSupported && !speechOnline
        }
    }
    @Published private(set) var discoveredHost: String?
    @Published private(set) var hostOverride: String
    @Published private(set) var record: PairingRecord?
    @Published private(set) var onWiFi = true
    @Published private(set) var localNetworkDenied = false
    @Published private(set) var tipDismissed: Bool
    @Published private(set) var dimmed = false
    @Published var ringPolicy: RingPolicy {
        didSet { saveRingPolicy() }
    }
    #if DEBUG
    @Published var typedInsteadOfSpoken = ""
    #endif

    private let client = ListenerClient()
    private let discovery = Discovery()
    private let dictation = Dictation()
    private let pathMonitor = NWPathMonitor()
    private var linkTask: Task<Void, Never>?
    private var sendTask: Task<Void, Never>?
    private var dimTask: Task<Void, Never>?
    private var timers: [String: Task<Void, Never>] = [:]
    private var started = false
    private var networkStarted = false
    private var streamWasLive = false
    /// The host whose signed event stream is live now: the only place letters and status checks go.
    private var streamHost: String?
    private var listeningTyped = false
    private var lastActivity = Date()
    private var dimAfter: TimeInterval = 600
    /// Seat order of the original send, and results for seats a retry didn't touch.
    private var sendOrder: [String] = []
    private var carried: [SeatOutcome] = []

    init() {
        let defaults = UserDefaults.standard
        if let saved = defaults.stringArray(forKey: "chosenSeats") {
            chosen = Set(saved)
        } else {
            chosen = Set([defaults.string(forKey: "seat")].compactMap { $0 })
        }
        everyone = defaults.bool(forKey: "everyone")
        hostOverride = defaults.string(forKey: "hostOverride") ?? ""
        tipDismissed = defaults.bool(forKey: "tipDismissed")
        speechOnline = defaults.bool(forKey: "speechOnline")
        var policy = RingPolicy()
        if let raw = defaults.string(forKey: "ringFor"), let ringFor = RingPolicy.RingFor(rawValue: raw) { policy.ringFor = ringFor }
        policy.quietHours = defaults.bool(forKey: "quietHours")
        if defaults.object(forKey: "quietFrom") != nil { policy.quietFrom = defaults.integer(forKey: "quietFrom") }
        if defaults.object(forKey: "quietTo") != nil { policy.quietTo = defaults.integer(forKey: "quietTo") }
        ringPolicy = policy
        #if DEBUG
        // Also settable as launch arguments: -typeInsteadOfSpeak "text", -dimAfter 5
        typedInsteadOfSpoken = defaults.string(forKey: "typeInsteadOfSpeak") ?? ""
        if defaults.double(forKey: "dimAfter") > 0 { dimAfter = defaults.double(forKey: "dimAfter") }
        #endif
    }

    var face: Face {
        switch screen {
        case .welcome: return .welcome
        case .pair: return .pair
        case .paired: return .paired
        case .idle: return link == .down || mistrust != nil ? .offline : .idle
        case .ring: return .ring
        case .listening: return .listening
        case .finishing: return .finishing
        case .review: return .review
        case .editing: return .editing
        case .sending: return .sending
        case .sent: return .sent
        case .failed: return .failed
        }
    }

    var networkProblem: NetworkProblem? {
        if !onWiFi { return .noWiFi }
        if localNetworkDenied { return .localNetworkDenied }
        return nil
    }

    /// Who the next letter goes to. "Everyone" is exactly the seats in the latest `hello`.
    var recipients: [String] {
        everyone ? seats : seats.filter { chosen.contains($0) }
    }

    /// Seats on the last send still needing a decision.
    var unresolved: [SeatOutcome] { outcomes.filter { $0.state != .published } }

    /// A sent screen with nothing left to decide; it can be interrupted by a ring and times out.
    private var sentIsSettled: Bool { screen == .sent && unresolved.isEmpty }

    var canHold: Bool {
        (screen == .idle || screen == .ring || sentIsSettled) && link == .up && !recipients.isEmpty
    }

    var mistrust: Mistrust? {
        if case .untrusted(let why) = link { return why }
        return nil
    }

    /// Where to look for the paired Mac: the saved host first, a discovered one only as a fallback. Either is
    /// used only if its key matches the pinned one and its event stream verifies.
    private func linkHosts(_ record: PairingRecord) -> [String] {
        if !hostOverride.isEmpty { return [ListenerClient.normalizeHost(hostOverride)] }
        guard let found = discoveredHost, found != record.host else { return [record.host] }
        return [record.host, found]
    }

    /// Where to pair: any Mac found, the one where the person opened pairing is the one that answers.
    private var pairingHosts: [String] {
        if !hostOverride.isEmpty { return [ListenerClient.normalizeHost(hostOverride)] }
        var hosts: [String] = []
        for host in [discoveredHost, record?.host].compactMap({ $0 }) where !hosts.contains(host) { hosts.append(host) }
        return hosts
    }

    /// "a", "a and b", "3 agents", "everyone".
    func phrase(for list: [String]) -> String {
        switch list.count {
        case 0: return "nobody"
        case 1: return list[0]
        default:
            if Set(list) == Set(seats) { return "everyone" }
            if list.count == 2 { return "\(list[0]) and \(list[1])" }
            return "\(list.count) agents"
        }
    }

    // MARK: Lifecycle

    func start() {
        guard !started else { return }
        started = true
        record = PairingStore.load()
        discovery.onChange = { [weak self] host in self?.discoveredHost = host }
        discovery.onDenied = { [weak self] denied in self?.localNetworkDenied = denied }
        dictation.onText = { [weak self] text in
            guard let self, self.screen == .listening || self.screen == .finishing else { return }
            self.transcript = text
        }
        onDeviceSpeechSupported = dictation.supportsOnDevice
        speechOnDevice = onDeviceSpeechSupported && !speechOnline
        pathMonitor.pathUpdateHandler = { [weak self] path in
            let usable = path.status == .satisfied && (path.usesInterfaceType(.wifi) || path.usesInterfaceType(.wiredEthernet))
            Task { @MainActor in self?.onWiFi = usable }
        }
        pathMonitor.start(queue: .main)
        startDimWatch()

        if record == nil && !UserDefaults.standard.bool(forKey: "networkExplained") {
            screen = .welcome
            return
        }
        screen = record == nil ? .pair : .idle
        startNetworking()
    }

    /// "Continue" on the Local Network explainer: the browse that follows makes iOS ask.
    func continueFromWelcome() {
        guard screen == .welcome else { return }
        UserDefaults.standard.set(true, forKey: "networkExplained")
        screen = .pair
        startNetworking()
    }

    private func startNetworking() {
        networkStarted = true
        discovery.start()
        restartLink()
    }

    func scenePhaseChanged(_ phase: ScenePhase) {
        switch phase {
        case .active:
            UIApplication.shared.isIdleTimerDisabled = true
            noteActivity()
            if networkStarted && linkTask == nil { restartLink() }
        case .background:
            UIApplication.shared.isIdleTimerDisabled = false
            linkTask?.cancel()
            linkTask = nil
            if screen == .listening || screen == .finishing {
                dictation.cancel()
                transcript = ""
                screen = .idle
            }
        default:
            break
        }
    }

    private func restartLink() {
        linkTask?.cancel()
        streamHost = nil
        if let record, screen != .pair {
            link = .connecting
            linkTask = Task { await runLink(record) }
        } else if let pendingConfirm, pairPhase == .confirming {
            // Back mid-finish: send the same proof again; the Mac answers a repeat the same way.
            let attempt = pairingAttempt
            linkTask = Task { await finishPairing(pendingConfirm, attempt: attempt) }
        } else if pendingConfirm != nil {
            linkTask = nil  // waiting for the person to type the Mac's code
        } else {
            linkTask = Task { await runPairing() }
        }
    }

    // MARK: Events link

    private func runLink(_ record: PairingRecord) async {
        var backoff: Double = 1
        while !Task.isCancelled {
            streamWasLive = false
            var mistrust: Mistrust?
            for host in linkHosts(self.record ?? record) where !Task.isCancelled {
                do {
                    // `hello` is unsigned, so another key means only that this host isn't the paired Mac.
                    let hello = try await client.hello(host: host)
                    guard Data(base64Encoded: hello.serverPub) == record.serverPub else {
                        mistrust = mistrust ?? .keyChanged
                        continue
                    }
                    try await client.streamEvents(record: record, host: host) { [weak self] event in
                        await self?.verified(event, from: host, for: record)
                    }
                } catch ListenerError.unauthorized {
                    mistrust = .notRecognised
                } catch {
                    // Unreachable, or the stream dropped: try the next host, then back off.
                }
                if streamWasLive { break }
            }
            if Task.isCancelled { return }
            linkDropped()
            if let mistrust, !streamWasLive { link = .untrusted(mistrust) }
            if streamWasLive { backoff = 1 }
            await pause(backoff)
            backoff = min(backoff * 2, 30)
        }
    }

    /// Every event here passed its signature check, so the host it came from is the paired Mac.
    private func verified(_ event: BellEvent, from host: String, for linkRecord: PairingRecord) {
        // A stream for a pairing the person has since forgotten or replaced changes nothing.
        guard !Task.isCancelled, record?.deviceID == linkRecord.deviceID else { return }
        if case .hello = event {
            streamHost = host
            // Signed by the pinned key: this is the paired Mac, so remember where it is now.
            if var saved = record, saved.host != host {
                saved.host = host
                PairingStore.save(saved)
                record = saved
            }
        }
        handle(event)
    }

    private func handle(_ event: BellEvent) {
        guard record != nil, screen != .pair, screen != .welcome else { return }
        switch event {
        case .hello(let seats, let last, let name):
            if let name, !name.isEmpty { macName = String(name.prefix(40)) }
            self.seats = seats
            if !everyone {
                chosen = chosen.intersection(seats)
                if chosen.isEmpty, let first = seats.first { chosen = [first] }
                saveChoice()
            }
            lastMail = last
            link = .up
            streamWasLive = true
        case .mail(let mail):
            lastMail = mail
            ring(mail)
        case .ping, .other:
            break
        }
    }

    private func ring(_ mail: MailEnvelope) {
        // Filtered or quiet mail only updates the idle line: no chime, no swing.
        guard ringPolicy.shouldRing(forYou: mail.isForYou, at: Date()) else { return }
        noteActivity()
        // The microphone is open while listening; a chime would end up in the transcript.
        if screen != .listening && screen != .finishing { Chime.play(forYou: mail.isForYou) }
        guard screen == .idle || screen == .ring || sentIsSettled else { return }
        cancelTimer("sent")
        notice = nil
        ringMail = mail
        ringAt = Date()
        screen = .ring
        after("ring", 7) { [weak self] in
            if self?.screen == .ring { self?.screen = .idle }
        }
    }

    private func linkDropped() {
        link = .down
        streamHost = nil
        discovery.refresh()
        if screen == .ring || sentIsSettled { screen = .idle }
    }

    // MARK: Pairing

    /// Everything needed to finish pairing, in memory only, until the person types the code the Mac shows.
    /// Nothing is saved, and the key is used for nothing but the proof, before that code matches.
    private struct PendingConfirm {
        let record: PairingRecord
        let pairID: String
        let macCode: String
        var triesLeft: Int
    }
    private var pendingConfirm: PendingConfirm?
    /// Bumped whenever the person leaves, forgets or restarts a pairing, so a late answer to an older
    /// attempt changes nothing.
    private var pairingAttempt = 0

    /// The only way a paired phone starts pairing again: the person asks.
    func pairAgain() {
        guard mistrust != nil else { return }
        leaveForPairing(reason: nil)
    }

    private func leaveForPairing(reason: String?) {
        linkTask?.cancel()
        sendTask?.cancel()
        dictation.cancel()
        pairReason = reason
        seats = []
        lastMail = nil
        transcript = ""
        link = .connecting
        pairPhase = .searching
        screen = .pair
        pairingAttempt += 1
        pendingConfirm = nil
        linkTask = Task { await runPairing() }
    }

    private func runPairing() async {
        let attempt = pairingAttempt
        // After every wait: a task for an attempt the person has since left must not touch the screen.
        func current() -> Bool { !Task.isCancelled && attempt == pairingAttempt }
        while current() {
            let hosts = pairingHosts
            guard let first = hosts.first else {
                pairPhase = .searching
                await pause(1)
                continue
            }
            var reached: Hello?
            var open: (host: String, hello: Hello)?
            for candidate in hosts {
                guard let answer = try? await client.hello(host: candidate) else { continue }
                reached = reached ?? answer
                if answer.pairingOpen { open = (candidate, answer); break }
            }
            guard current() else { return }
            // Where pairing can't go ahead, also try the next Mac found: with two on the Wi-Fi, the person may
            // have opened pairing on the other one.
            guard let reached else {
                pairPhase = .unreachable(first)
                discovery.next()
                await pause(2)
                continue
            }
            guard let open else {
                macName = reached.name
                pairPhase = .windowClosed
                discovery.next()
                await pause(2)
                continue
            }
            let host = open.host, hello = open.hello
            macName = hello.name
            guard hello.pairConfirm == true else {
                pairPhase = .tooOld
                discovery.next()
                await pause(5)
                continue
            }

            let privateKey = Curve25519.KeyAgreement.PrivateKey()
            let devicePub = privateKey.publicKey.rawRepresentation
            guard let start = try? await client.startPair(host: host, devicePub: devicePub, deviceName: UIDevice.current.name),
                  let serverPub = Data(base64Encoded: start.serverPub), serverPub.count == 32,
                  let commitment = Data(base64Encoded: start.commitment), commitment.count == 32 else {
                await pause(2)
                continue
            }
            guard current() else { return }

            // The Mac is committed to its nonce; only now pick ours and ask it to reveal.
            let deviceNonce = BellCrypto.randomBytes16()
            let serverNonce: Data
            do {
                serverNonce = try await client.revealPairNonce(host: host, pairID: start.pairID, deviceNonce: deviceNonce)
            } catch ListenerError.http(let code, _) where code == 409 {
                if current() { pairPhase = .failed }
                return
            } catch {
                guard current() else { return }
                await pause(2)
                continue
            }
            guard current() else { return }
            guard serverNonce.count == 16,
                  BellCrypto.commitment(serverPub: serverPub, devicePub: devicePub, serverNonce: serverNonce) == commitment else {
                // Never show a code for a reveal that doesn't match the commitment.
                pairPhase = .failed
                return
            }
            pairPhase = .code(BellCrypto.pairingCode(serverPub: serverPub, devicePub: devicePub,
                                                     deviceNonce: deviceNonce, serverNonce: serverNonce))

            let outcome = await waitForApproval(host: host, pairID: start.pairID)
            guard current() else { return }
            switch outcome {
            case .approved(let deviceID):
                guard deviceID == BellCrypto.deviceID(devicePub: devicePub),
                      let key = try? BellCrypto.deriveKey(ownPrivate: privateKey, peerPub: serverPub,
                                                          serverPub: serverPub, devicePub: devicePub,
                                                          deviceNonce: deviceNonce, serverNonce: serverNonce) else {
                    pairPhase = .failed
                    return
                }
                // Approved by whoever answered. Only the Mac the person is looking at can show this code.
                pendingConfirm = PendingConfirm(
                    record: PairingRecord(host: host, deviceID: deviceID, key: key, serverPub: serverPub),
                    pairID: start.pairID,
                    macCode: BellCrypto.confirmCode(serverPub: serverPub, devicePub: devicePub,
                                                    deviceNonce: deviceNonce, serverNonce: serverNonce),
                    triesLeft: 2)
                pairPhase = .confirm(retry: false)
                return
            case .denied:
                pairPhase = .denied
                await pause(4)
            case .expired:
                pairPhase = .expired
                await pause(4)
            case .abandoned:
                pairPhase = .failed
                return
            case .lost:
                await pause(1)
            }
        }
    }

    /// The person typed the code the Mac shows. A match proves the phone reached that Mac; two misses end the attempt.
    func submitMacCode(_ typed: String) {
        guard case .confirm = pairPhase, var pending = pendingConfirm else { return }
        let digits = BellCrypto.typedDigits(typed)
        guard digits.count == 6 else { return }
        guard digits == pending.macCode else {
            pending.triesLeft -= 1
            if pending.triesLeft > 0 {
                pendingConfirm = pending
                pairPhase = .confirm(retry: true)
            } else {
                pendingConfirm = nil
                pairPhase = .wrongMacCode
                Task { [client] in await client.abandonPair(host: pending.record.host, pairID: pending.pairID) }
            }
            return
        }
        pairPhase = .confirming
        linkTask?.cancel()
        let attempt = pairingAttempt
        linkTask = Task { await finishPairing(pending, attempt: attempt) }
    }

    private func finishPairing(_ pending: PendingConfirm, attempt: Int) async {
        let proof = BellCrypto.confirmProof(key: pending.record.key)
        // After every wait: an answer for an attempt the person has since left changes nothing.
        func current() -> Bool { !Task.isCancelled && attempt == pairingAttempt }
        for round in 1...3 {
            do {
                let status = try await client.confirmPair(host: pending.record.host, pairID: pending.pairID, proof: proof)
                guard current() else { return }
                // Only the Mac holding this pairing's key can sign the answer, so a forged "confirmed" is refused.
                guard status.state == "confirmed", status.deviceID == pending.record.deviceID,
                      BellCrypto.verifyConfirmAck(key: pending.record.key, ack: status.ack,
                                                  pairID: pending.pairID, deviceID: pending.record.deviceID) else { break }
                pendingConfirm = nil
                PairingStore.save(pending.record)
                record = pending.record
                pairReason = nil
                link = .connecting
                screen = .paired
                noteActivity()
                after("paired", 3) { [weak self] in
                    if self?.screen == .paired { self?.screen = .idle }
                }
                linkTask = Task { await runLink(pending.record) }
                return
            } catch ListenerError.http(let code, _) {
                guard current() else { return }
                // 404: the Mac's two minutes ran out. Anything else: the Mac refused. Nothing was paired either way.
                pendingConfirm = nil
                pairPhase = code == 404 ? .expired : .failed
                if code == 404 {
                    await pause(4)
                    if current() { await runPairing() }
                }
                return
            } catch {
                guard current() else { return }
                await pause(Double(round))
            }
        }
        guard current() else { return }
        pendingConfirm = nil
        pairPhase = .failed
    }

    func retryPairing() {
        guard screen == .pair, pairPhase == .failed || pairPhase == .wrongMacCode else { return }
        linkTask?.cancel()
        pairingAttempt += 1
        pendingConfirm = nil
        pairPhase = .searching
        linkTask = Task { await runPairing() }
    }

    private func waitForApproval(host: String, pairID: String) async -> PairOutcome {
        let deadline = Date().addingTimeInterval(130)
        while !Task.isCancelled && Date() < deadline {
            await pause(1)
            do {
                if let outcome = try await client.pairStatus(host: host, pairID: pairID).outcome { return outcome }
            } catch ListenerError.http(let code, _) where code == 404 {
                return .lost
            } catch {
                // Keep polling until the window would have closed anyway.
            }
        }
        return .expired
    }

    /// iOS can gain on-device recognition later (e.g. once its speech model has downloaded).
    func refreshSpeechSupport() {
        onDeviceSpeechSupported = dictation.supportsOnDevice
        speechOnDevice = onDeviceSpeechSupported && !speechOnline
    }

    func dismissTip() {
        tipDismissed = true
        UserDefaults.standard.set(true, forKey: "tipDismissed")
    }

    // MARK: Seat picker

    /// Toggle one seat; at least one seat always stays chosen.
    func toggle(_ seat: String) {
        var next = Set(recipients)
        if next.contains(seat) {
            guard next.count > 1 else { return }
            next.remove(seat)
        } else {
            next.insert(seat)
        }
        chosen = next
        everyone = !seats.isEmpty && next.isSuperset(of: seats)
        saveChoice()
    }

    func chooseEveryone() {
        everyone = true
        chosen = Set(seats)
        saveChoice()
    }

    private func saveChoice() {
        UserDefaults.standard.set(Array(chosen).sorted(), forKey: "chosenSeats")
        UserDefaults.standard.set(everyone, forKey: "everyone")
    }

    // MARK: Dictation

    func holdBegan() {
        guard canHold else { return }
        #if DEBUG
        if !typedInsteadOfSpoken.isEmpty {
            beginListening(typed: true)
            transcript = typedInsteadOfSpoken
            return
        }
        #endif
        guard Dictation.isAuthorized else {
            Task {
                let granted = await Dictation.requestAuthorization()
                flash(granted ? "Ready · hold the button to talk" : "Allow the microphone and speech recognition in Settings")
            }
            return
        }
        do {
            let names = seats.flatMap { [$0, $0.split(separator: "-").map { $0.capitalized }.joined(separator: " ")] }
            try dictation.start(vocabulary: names, preferOnDevice: !speechOnline)
        } catch {
            flash("Speech recognition isn't available right now")
            return
        }
        speechOnDevice = dictation.usingOnDevice
        beginListening(typed: false)
    }

    private func beginListening(typed: Bool) {
        cancelTimer("ring")
        cancelTimer("sent")
        notice = nil
        transcript = ""
        outcomes = []
        listeningTyped = typed
        screen = .listening
    }

    func holdEnded() {
        guard screen == .listening else { return }
        if listeningTyped {
            finishListening(transcript)
            return
        }
        // The recogniser still owes its final result; say so while it arrives.
        screen = .finishing
        Task {
            let text = await dictation.stop()
            guard screen == .finishing else { return }
            finishListening(text)
        }
    }

    private func finishListening(_ text: String) {
        transcript = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if transcript.isEmpty {
            screen = .idle
            flash("Nothing heard · discarded")
        } else {
            // The review card shows exactly this set; it is what Send uses.
            reviewSeats = recipients
            screen = .review
        }
    }

    // MARK: Review

    static let maxLetterLength = 2000

    /// Length as the Mac counts it: Unicode code points after trimming.
    var letterLength: Int {
        transcript.trimmingCharacters(in: .whitespacesAndNewlines).unicodeScalars.count
    }

    var canSend: Bool { (1...Self.maxLetterLength).contains(letterLength) }

    func beginEditing() {
        guard screen == .review else { return }
        screen = .editing
    }

    func setDraft(_ text: String) {
        guard screen == .editing else { return }
        transcript = text
    }

    func finishEditing() {
        guard screen == .editing else { return }
        screen = .review
    }

    /// Throw this transcript away and go back to ready-to-hold, same recipients.
    func sayItAgain() {
        guard screen == .review else { return }
        transcript = ""
        screen = .idle
        // Stays until the next hold or ring.
        cancelTimer("notice")
        notice = "Hold the button and speak again"
    }

    // MARK: Sending

    func send() {
        guard screen == .review, canSend else { return }
        dispatch(to: reviewSeats)
    }

    /// Seats that provably never got a copy. Sending to them cannot duplicate anything.
    func sendAgainToUnattempted() {
        dispatch(to: unresolved.filter { $0.state == .notAttempted }.map(\.seat))
    }

    /// Seats whose copy may have landed. The view asks for a second confirmation before calling this.
    func sendAgainToUnconfirmed() {
        dispatch(to: unresolved.filter { $0.state == .notConfirmed }.map(\.seat))
    }

    /// Always a new request_id, and only the named seats. Never called automatically.
    private func dispatch(to targets: [String]) {
        guard !targets.isEmpty, let record, [.review, .failed, .sent].contains(screen) else { return }
        let text = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        // A retry keeps the other seats' results on screen; a first send starts fresh.
        carried = screen == .review ? [] : outcomes.filter { !targets.contains($0.seat) }
        if screen == .review { sendOrder = targets }
        sendTo = targets
        cancelTimer("sent")
        guard link == .up, let host = streamHost else {
            conclude(targets.map { SeatOutcome(seat: $0, state: .notAttempted, reason: "listener unreachable") },
                     failure: .unreachable)
            return
        }
        let requestID = UUID().uuidString.lowercased()
        sendPhase = .posting
        screen = .sending
        sendTask = Task { await deliver(record: record, host: host, requestID: requestID, to: targets, text: text) }
    }

    private func deliver(record: PairingRecord, host: String, requestID: String, to: [String], text: String) async {
        do {
            let (status, reply, trusted) = try await client.postDictation(record: record, host: host, requestID: requestID, to: to, text: text)
            settle(status: status, reply: reply, trusted: trusted, requestID: requestID, to: to, isStatusCheck: false)
            return
        } catch {
            if Task.isCancelled { return }
        }
        // No answer to the POST: one status check once the listener answers again. Never a re-send.
        sendPhase = .checking
        await waitUntilReachable()
        if Task.isCancelled { return }
        do {
            let (status, reply, trusted) = try await client.dictationStatus(record: record, host: streamHost ?? host, requestID: requestID)
            settle(status: status, reply: reply, trusted: trusted, requestID: requestID, to: to, isStatusCheck: true)
        } catch {
            if screen == .sending {
                conclude(to.map { SeatOutcome(seat: $0, state: .notConfirmed, reason: "no answer to the status check") })
            }
        }
    }

    /// Until the signed event stream is back, which proves the host is the paired Mac.
    private func waitUntilReachable() async {
        while !Task.isCancelled && streamHost == nil {
            await pause(1)
        }
    }

    private func settle(status: Int, reply: DictationReply?, trusted: Bool, requestID: String, to targets: [String],
                        isStatusCheck: Bool) {
        guard screen == .sending else { return }
        // An answer the paired Mac didn't sign proves nothing either way: the letter may have arrived.
        guard trusted else {
            conclude(targets.map { SeatOutcome(seat: $0, state: .notConfirmed, reason: "the answer wasn't signed by your Mac") })
            return
        }
        if status == 200, let reply, let top = reply.verdict(for: requestID) {
            conclude(targets.map { seatOutcome($0, in: reply, top: top, single: targets.count == 1) },
                     failure: top == "not_sent" ? .notSent : nil)
            return
        }
        let reason = reply?.reason ?? reply?.error ?? "HTTP \(status)"
        if isStatusCheck && status == 404 {
            conclude(targets.map { SeatOutcome(seat: $0, state: .notAttempted, reason: "unknown_request") }, failure: .neverReceived)
        } else if !isStatusCheck && [400, 401, 503].contains(status) {
            conclude(targets.map { SeatOutcome(seat: $0, state: .notAttempted, reason: reason) },
                     failure: status == 503 ? .stopping : .refused)
        } else {
            conclude(targets.map { SeatOutcome(seat: $0, state: .notConfirmed, reason: reason) })
        }
    }

    private func seatOutcome(_ seat: String, in reply: DictationReply, top: String, single: Bool) -> SeatOutcome {
        if let r = reply.results?.first(where: { $0.to == seat }) {
            let state: SeatOutcome.State
            switch r.status {
            case "published": state = .published
            case "not_attempted": state = .notAttempted
            default: state = .notConfirmed
            }
            return SeatOutcome(seat: seat, state: state, envelopeID: r.id, ringOutcome: r.ring?.outcome,
                               reason: r.reason ?? r.ring?.reason)
        }
        // A v1-shaped reply for one seat carries the fields at the top level.
        if single && reply.results == nil {
            switch top {
            case "sent":
                return SeatOutcome(seat: seat, state: .published, envelopeID: reply.id,
                                   ringOutcome: reply.ring?.outcome, reason: reply.ring?.reason)
            case "not_sent":
                return SeatOutcome(seat: seat, state: .notAttempted, reason: reply.reason)
            default:
                return SeatOutcome(seat: seat, state: .notConfirmed, reason: reply.reason)
            }
        }
        // The Mac said nothing about this seat: treat it as possibly delivered.
        return SeatOutcome(seat: seat, state: top == "not_sent" ? .notAttempted : .notConfirmed,
                           reason: "no result for this seat")
    }

    private func conclude(_ fresh: [SeatOutcome], failure: Failure? = nil) {
        let order = sendOrder
        let outcomes = (carried + fresh).sorted {
            (order.firstIndex(of: $0.seat) ?? .max) < (order.firstIndex(of: $1.seat) ?? .max)
        }
        carried = []
        self.outcomes = outcomes
        sentAt = Date()
        let published = outcomes.filter { $0.state == .published }.count
        guard published > 0 else {
            self.failure = failure ?? (outcomes.contains { $0.state == .notConfirmed } ? .notConfirmed : .notSent)
            screen = .failed
            return
        }
        screen = .sent
        if published == outcomes.count {
            transcript = ""
            after("sent", min(4 + Double(outcomes.count - 1), 8)) { [weak self] in
                if self?.screen == .sent { self?.screen = .idle }
            }
        }
    }

    func stopChecking() {
        guard screen == .sending, sendPhase == .checking else { return }
        sendTask?.cancel()
        conclude(sendTo.map { SeatOutcome(seat: $0, state: .notConfirmed, reason: "status check stopped") })
    }

    /// "Discard" on review and failed, "Done" on a partly sent screen.
    func discard() {
        guard [.review, .failed, .sent].contains(screen) else { return }
        let from = screen
        transcript = ""
        outcomes = []
        screen = .idle
        if from == .review { flash("Discarded · nothing was sent") }
        if from == .failed { flash("Discarded") }
    }

    // MARK: Settings

    func setHostOverride(_ raw: String) {
        hostOverride = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        UserDefaults.standard.set(hostOverride, forKey: "hostOverride")
        if networkStarted { restartLink() }
    }

    func forgetMac() {
        PairingStore.delete()
        record = nil
        leaveForPairing(reason: nil)
    }

    private func saveRingPolicy() {
        let defaults = UserDefaults.standard
        defaults.set(ringPolicy.ringFor.rawValue, forKey: "ringFor")
        defaults.set(ringPolicy.quietHours, forKey: "quietHours")
        defaults.set(ringPolicy.quietFrom, forKey: "quietFrom")
        defaults.set(ringPolicy.quietTo, forKey: "quietTo")
    }

    // MARK: Dimming

    /// Any touch, a ring, or coming back to the app. Wakes a dimmed screen at once.
    func noteActivity() {
        lastActivity = Date()
        if dimmed { dimmed = false }
    }

    private func startDimWatch() {
        dimTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.pause(5)
                self?.checkDim()
            }
        }
    }

    private func checkDim() {
        guard !dimmed, ![.listening, .finishing, .editing, .sending].contains(screen) else { return }
        if Date().timeIntervalSince(lastActivity) >= dimAfter { dimmed = true }
    }

    // MARK: Helpers

    private func flash(_ text: String) {
        if screen == .ring || sentIsSettled { screen = .idle }
        notice = text
        after("notice", 2.8) { [weak self] in self?.notice = nil }
    }

    private func after(_ key: String, _ seconds: Double, _ action: @escaping () -> Void) {
        timers[key]?.cancel()
        timers[key] = Task { [weak self] in
            await self?.pause(seconds)
            if !Task.isCancelled { action() }
        }
    }

    private func cancelTimer(_ key: String) {
        timers[key]?.cancel()
        timers[key] = nil
    }

    private func pause(_ seconds: Double) async {
        try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }
}
