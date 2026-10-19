import Foundation

/// Envelope fields of a ring-eligible letter. Never a path or a body.
struct MailEnvelope: Decodable, Equatable {
    let to: String
    let from: String
    let letterType: String
    let id: String
    let priority: String?
    let ts: String?
    /// v1.1: true when an agent wrote to the person (`to` is "desk").
    let forYou: Bool?

    enum CodingKeys: String, CodingKey {
        case to, from, id, priority, ts
        case letterType = "letter_type"
        case forYou = "for_you"
    }

    var isForYou: Bool { forYou ?? false }
}

enum BellEvent: Equatable {
    case hello(seats: [String], last: MailEnvelope?, name: String?)
    case mail(MailEnvelope)
    case ping
    case other(String)
}

/// Splits a byte stream into lines on "\n", dropping a trailing "\r".
/// Works on raw bytes so the JSON that was signed is the JSON that is verified.
struct LineSplitter {
    private var buffer = Data()

    mutating func feed(_ byte: UInt8) -> Data? {
        guard byte == 0x0A else {
            buffer.append(byte)
            return nil
        }
        var line = buffer
        buffer = Data()
        if line.last == 0x0D { line.removeLast() }
        return line
    }
}

/// Checks each `data:` line of one `/v1/events` stream: signature first, then stream nonce and seq.
struct EventVerifier {
    enum Failure: Error, Equatable {
        case malformed, badSignature, wrongStream, badSeq, helloNotFirst
    }

    let key: Data
    let streamNonce: String
    private(set) var nextSeq = 0

    init(key: Data, streamNonce: String) {
        self.key = key
        self.streamNonce = streamNonce
    }

    private static let prefix = Data("data: ".utf8)

    /// Returns nil for lines that carry no event (the blank separator, SSE comments).
    mutating func accept(line: Data) throws -> BellEvent? {
        guard line.starts(with: Self.prefix) else { return nil }
        let payload = line.dropFirst(Self.prefix.count)
        guard let space = payload.firstIndex(of: 0x20) else { throw Failure.malformed }
        let signature = String(decoding: payload[payload.startIndex..<space], as: UTF8.self)
        let json = Data(payload[payload.index(after: space)...])

        guard BellCrypto.verifyEvent(key: key, signature: signature, json: json) else {
            throw Failure.badSignature
        }
        guard let raw = try? JSONDecoder().decode(RawEvent.self, from: json) else {
            throw Failure.malformed
        }
        guard raw.stream == streamNonce else { throw Failure.wrongStream }
        guard raw.seq == nextSeq else { throw Failure.badSeq }
        if nextSeq == 0 && raw.type != "hello" { throw Failure.helloNotFirst }
        nextSeq += 1

        switch raw.type {
        case "hello":
            return .hello(seats: raw.seats ?? [], last: raw.last, name: raw.name)
        case "mail":
            guard let mail = try? JSONDecoder().decode(MailEnvelope.self, from: json) else {
                throw Failure.malformed
            }
            return .mail(mail)
        case "ping":
            return .ping
        default:
            return .other(raw.type)
        }
    }

    private struct RawEvent: Decodable {
        let type: String
        let stream: String
        let seq: Int
        let seats: [String]?
        let last: MailEnvelope?
        let name: String?
    }
}
