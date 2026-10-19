import Foundation

/// What the phone keeps after pairing (stored in the Keychain).
struct PairingRecord: Codable, Equatable {
    var host: String
    let deviceID: String
    let key: Data
    let serverPub: Data
}

struct Hello: Decodable {
    let name: String
    let pairingOpen: Bool
    let serverPub: String

    enum CodingKeys: String, CodingKey {
        case name
        case pairingOpen = "pairing_open"
        case serverPub = "server_pub"
    }
}

struct PairStart: Decodable {
    let pairID: String
    let serverPub: String
    let commitment: String

    enum CodingKeys: String, CodingKey {
        case pairID = "pair_id"
        case serverPub = "server_pub"
        case commitment
    }
}

private struct PairNonceReply: Decodable {
    let serverNonce: String

    enum CodingKeys: String, CodingKey {
        case serverNonce = "server_nonce"
    }
}

struct PairStatus: Decodable {
    let state: String
    let deviceID: String?

    enum CodingKeys: String, CodingKey {
        case state
        case deviceID = "device_id"
    }
}

/// Body of a dictation POST answer, a status check answer, or an error.
struct DictationReply: Decodable {
    struct Ring: Decodable {
        let outcome: String?
        let reason: String?
    }

    /// One seat's copy: status is "published", "not_confirmed" or "not_attempted".
    struct SeatResult: Decodable {
        let to: String
        let status: String?
        let id: String?
        let ring: Ring?
        let reason: String?
    }

    let status: String?
    let requestID: String?
    let id: String?
    let ring: Ring?
    let reason: String?
    let error: String?
    let results: [SeatResult]?

    enum CodingKeys: String, CodingKey {
        case status, id, ring, reason, error, results
        case requestID = "request_id"
    }
}

private struct DictationBody: Encodable {
    let requestID: String
    let to: [String]
    let text: String

    enum CodingKeys: String, CodingKey {
        case to, text
        case requestID = "request_id"
    }
}

enum ListenerError: Error {
    /// No HTTP answer at all: connection refused, timed out, no route.
    case unreachable
    case unauthorized
    case http(Int, String?)
    case badResponse
    case streamEnded
    case streamDead
}

/// Plain HTTP to the Mac listener. Integrity comes from the HMAC on every signed request, every event, and every
/// answer to a signed request; an answer without a valid signature is reported as untrusted.
final class ListenerClient {
    static let defaultPort = 8765

    private let session: URLSession

    init() {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 15
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        session = URLSession(configuration: config)
    }

    /// "desk-mac.local" -> "desk-mac.local:8765"; strips a scheme or trailing slash.
    static func normalizeHost(_ raw: String) -> String {
        var host = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        for scheme in ["http://", "https://"] where host.lowercased().hasPrefix(scheme) {
            host = String(host.dropFirst(scheme.count))
        }
        while host.hasSuffix("/") { host.removeLast() }
        if !host.isEmpty && !host.contains(":") { host += ":\(defaultPort)" }
        return host
    }

    // MARK: Unsigned

    func hello(host: String) async throws -> Hello {
        let (status, data) = try await perform(URLRequest(url: url(host, "/v1/hello")))
        guard status == 200 else { throw ListenerError.http(status, errorCode(data)) }
        return try decode(Hello.self, data)
    }

    func startPair(host: String, devicePub: Data, deviceName: String) async throws -> PairStart {
        var req = URLRequest(url: try url(host, "/v1/pair"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: [
            "device_pub": devicePub.base64EncodedString(),
            "device_name": String(deviceName.prefix(40)),
        ])
        let (status, data) = try await perform(req)
        guard status == 200 else { throw ListenerError.http(status, errorCode(data)) }
        return try decode(PairStart.self, data)
    }

    /// Sends the phone's nonce, only after the commitment arrived; returns the Mac's revealed nonce.
    func revealPairNonce(host: String, pairID: String, deviceNonce: Data) async throws -> Data {
        var req = URLRequest(url: try url(host, "/v1/pair/\(pairID)/nonce"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["device_nonce": deviceNonce.base64EncodedString()])
        let (status, data) = try await perform(req)
        guard status == 200 else { throw ListenerError.http(status, errorCode(data)) }
        guard let nonce = Data(base64Encoded: try decode(PairNonceReply.self, data).serverNonce) else {
            throw ListenerError.badResponse
        }
        return nonce
    }

    func pairStatus(host: String, pairID: String) async throws -> PairStatus {
        let (status, data) = try await perform(URLRequest(url: url(host, "/v1/pair/\(pairID)")))
        guard status == 200 else { throw ListenerError.http(status, errorCode(data)) }
        return try decode(PairStatus.self, data)
    }

    // MARK: Signed

    /// (status, reply, trusted): trusted only when the paired Mac signed this answer for this request.
    func postDictation(record: PairingRecord, host: String, requestID: String, to: [String], text: String) async throws -> (Int, DictationReply?, Bool) {
        let encoder = JSONEncoder()
        encoder.outputFormatting = .withoutEscapingSlashes
        let body = try encoder.encode(DictationBody(requestID: requestID, to: to, text: text))
        return try await signedCall("POST", "/v1/dictation", body: body, record: record, host: host)
    }

    func dictationStatus(record: PairingRecord, host: String, requestID: String) async throws -> (Int, DictationReply?, Bool) {
        try await signedCall("GET", "/v1/dictation/\(requestID)", body: Data(), record: record, host: host)
    }

    private func signedCall(_ method: String, _ path: String, body: Data, record: PairingRecord,
                            host: String) async throws -> (Int, DictationReply?, Bool) {
        let nonce = BellCrypto.newNonce()
        let req = try signed(method, path, body: body, record: record, host: host, nonce: nonce)
        let (status, data, response) = try await performWithHeaders(req)
        let trusted = BellCrypto.verifyResponse(key: record.key, signature: response.value(forHTTPHeaderField: "X-Belldesk-Response-Sig"),
                                                method: method, path: path, nonce: nonce, status: status, body: data)
        return (status, try? JSONDecoder().decode(DictationReply.self, from: data), trusted)
    }

    /// Reads `/v1/events` until it fails. Returns only by throwing: the stream ended, went quiet
    /// for 40 s, or an event failed its signature, stream nonce or seq check.
    func streamEvents(record: PairingRecord, host: String,
                      onEvent: @escaping @Sendable (BellEvent) async -> Void) async throws {
        let nonce = BellCrypto.newNonce()
        var req = try signed("GET", "/v1/events", body: Data(), record: record, host: host, nonce: nonce)
        req.timeoutInterval = 60
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")

        let bytes: URLSession.AsyncBytes
        let response: URLResponse
        do {
            (bytes, response) = try await session.bytes(for: req)
        } catch is URLError {
            throw ListenerError.unreachable
        }
        guard let http = response as? HTTPURLResponse else { throw ListenerError.badResponse }
        if http.statusCode == 401 { throw ListenerError.unauthorized }
        guard http.statusCode == 200 else { throw ListenerError.http(http.statusCode, nil) }

        let lastEvent = Heartbeat()
        let key = record.key
        try await withThrowingTaskGroup(of: Void.self) { group in
            group.addTask {
                var verifier = EventVerifier(key: key, streamNonce: nonce)
                var splitter = LineSplitter()
                for try await byte in bytes {
                    guard let line = splitter.feed(byte), let event = try verifier.accept(line: line) else { continue }
                    lastEvent.beat()
                    await onEvent(event)
                }
                throw ListenerError.streamEnded
            }
            group.addTask {
                while true {
                    try await Task.sleep(nanoseconds: 2_000_000_000)
                    if lastEvent.secondsSince > 40 { throw ListenerError.streamDead }
                }
            }
            // Either child only ever ends by throwing; leaving the group cancels the other.
            try await group.next()
        }
    }

    // MARK: Plumbing

    private func signed(_ method: String, _ path: String, body: Data, record: PairingRecord, host: String,
                        nonce: String = BellCrypto.newNonce()) throws -> URLRequest {
        var req = URLRequest(url: try url(host, path))
        req.httpMethod = method
        let time = Int(Date().timeIntervalSince1970)
        req.setValue(record.deviceID, forHTTPHeaderField: "X-Belldesk-Device")
        req.setValue(String(time), forHTTPHeaderField: "X-Belldesk-Time")
        req.setValue(nonce, forHTTPHeaderField: "X-Belldesk-Nonce")
        req.setValue(BellCrypto.signRequest(key: record.key, method: method, path: path, time: time, nonce: nonce, body: body),
                     forHTTPHeaderField: "X-Belldesk-Sig")
        if !body.isEmpty {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return req
    }

    private func url(_ host: String, _ path: String) throws -> URL {
        guard let url = URL(string: "http://\(host)\(path)") else { throw ListenerError.badResponse }
        return url
    }

    private func perform(_ req: URLRequest) async throws -> (Int, Data) {
        let (status, data, _) = try await performWithHeaders(req)
        return (status, data)
    }

    private func performWithHeaders(_ req: URLRequest) async throws -> (Int, Data, HTTPURLResponse) {
        do {
            let (data, response) = try await session.data(for: req)
            guard let http = response as? HTTPURLResponse else { throw ListenerError.badResponse }
            return (http.statusCode, data, http)
        } catch is URLError {
            throw ListenerError.unreachable
        }
    }

    private func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do {
            return try JSONDecoder().decode(type, from: data)
        } catch {
            throw ListenerError.badResponse
        }
    }

    private func errorCode(_ data: Data) -> String? {
        (try? JSONDecoder().decode(DictationReply.self, from: data))?.error
    }
}

/// Time of the last verified event, shared between the reader and the watchdog.
private final class Heartbeat: @unchecked Sendable {
    private let lock = NSLock()
    private var last = Date()

    func beat() {
        lock.lock()
        last = Date()
        lock.unlock()
    }

    var secondsSince: TimeInterval {
        lock.lock()
        defer { lock.unlock() }
        return Date().timeIntervalSince(last)
    }
}
