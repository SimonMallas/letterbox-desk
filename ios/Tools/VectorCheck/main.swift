import CryptoKit
import Foundation

// Checks the app's BellCrypto.swift, EventParser.swift and ListenerClient.swift against mac/tests/vectors.json,
// and RingPolicy.swift's rules for when a letter rings.
// Built and run by Tools/check-vectors.sh.

struct Vectors: Decodable {
    struct Request: Decodable { let method, path, nonce, body, canonical, sig: String; let ts: Int }
    struct EmptyGet: Decodable { let method, path, nonce, sig: String; let ts: Int }
    struct Event: Decodable { let json, sig: String }
    struct Response: Decodable { let method, path, nonce, body, canonical_sha256, sig: String; let status: Int }
    let server_private, device_private, server_public, device_public: String
    let device_nonce, server_nonce, commitment: String
    struct ConfirmAck: Decodable { let pair_id, ack: String }
    let pairing_code, confirm_code, confirm_proof, device_id, auth_key: String
    let confirm_ack: ConfirmAck
    let request: Request
    let empty_body_get: EmptyGet
    let event: Event
    let response: Response
}

var failures = 0
func check(_ name: String, _ got: String, _ want: String) {
    if got == want {
        print("OK    \(name): \(got)")
    } else {
        failures += 1
        print("FAIL  \(name): got \(got), want \(want)")
    }
}

let path = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "../mac/tests/vectors.json"
let v = try JSONDecoder().decode(Vectors.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
let b64 = { (s: String) in Data(base64Encoded: s)! }

let server = try Curve25519.KeyAgreement.PrivateKey(rawRepresentation: b64(v.server_private))
let device = try Curve25519.KeyAgreement.PrivateKey(rawRepresentation: b64(v.device_private))
let serverPub = server.publicKey.rawRepresentation
let devicePub = device.publicKey.rawRepresentation
check("server_public", serverPub.base64EncodedString(), v.server_public)
check("device_public", devicePub.base64EncodedString(), v.device_public)

let deviceNonce = b64(v.device_nonce)
let serverNonce = b64(v.server_nonce)
check("commitment", BellCrypto.commitment(serverPub: serverPub, devicePub: devicePub, serverNonce: serverNonce).base64EncodedString(), v.commitment)
var otherNonce = serverNonce
otherNonce[otherNonce.startIndex] ^= 0x01
check("commitment rejects another server_nonce",
      String(BellCrypto.commitment(serverPub: serverPub, devicePub: devicePub, serverNonce: otherNonce) == b64(v.commitment)), "false")

let code = BellCrypto.pairingCode(serverPub: serverPub, devicePub: devicePub, deviceNonce: deviceNonce, serverNonce: serverNonce)
check("pairing_code", code, v.pairing_code)
check("pairing_code shown", BellCrypto.displayCode(code), "461 702")
// A typed code is read as the Mac reads one: decimal digits in any script, as ASCII, and nothing else.
check("typed code, spaced", BellCrypto.typedDigits("461 702"), "461702")
check("typed code, Arabic-Indic", BellCrypto.typedDigits("٤٦١٧٠٢"), "461702")
check("typed code, Persian", BellCrypto.typedDigits("۴۶۱۷۰۲"), "461702")
check("typed code, Devanagari", BellCrypto.typedDigits("४६१७०२"), "461702")
check("typed code, full-width", BellCrypto.typedDigits("４６１７０２"), "461702")
check("typed code, numerals that aren't digits", BellCrypto.typedDigits("½²Ⅻ一"), "")
check("confirm_code", BellCrypto.confirmCode(serverPub: serverPub, devicePub: devicePub, deviceNonce: deviceNonce, serverNonce: serverNonce), v.confirm_code)
check("device_id", BellCrypto.deviceID(devicePub: devicePub), v.device_id)

let phoneKey = try BellCrypto.deriveKey(ownPrivate: device, peerPub: serverPub, serverPub: serverPub, devicePub: devicePub,
                                        deviceNonce: deviceNonce, serverNonce: serverNonce)
let macKey = try BellCrypto.deriveKey(ownPrivate: server, peerPub: devicePub, serverPub: serverPub, devicePub: devicePub,
                                      deviceNonce: deviceNonce, serverNonce: serverNonce)
check("auth_key (phone side)", phoneKey.base64EncodedString(), v.auth_key)
check("auth_key (Mac side)", macKey.base64EncodedString(), v.auth_key)
check("confirm_proof", BellCrypto.confirmProof(key: phoneKey), v.confirm_proof)
check("confirm_ack", BellCrypto.confirmAck(key: phoneKey, pairID: v.confirm_ack.pair_id, deviceID: v.device_id), v.confirm_ack.ack)
check("confirm_ack verifies", String(BellCrypto.verifyConfirmAck(key: phoneKey, ack: v.confirm_ack.ack,
                                                                 pairID: v.confirm_ack.pair_id, deviceID: v.device_id)), "true")
check("confirm_ack refused for another pairing", String(BellCrypto.verifyConfirmAck(key: phoneKey, ack: v.confirm_ack.ack,
                                                                                    pairID: String(repeating: "0", count: 32), deviceID: v.device_id)), "false")
check("confirm_ack refused when missing", String(BellCrypto.verifyConfirmAck(key: phoneKey, ack: nil,
                                                                             pairID: v.confirm_ack.pair_id, deviceID: v.device_id)), "false")

let r = v.request
let body = Data(r.body.utf8)
check("request canonical",
      BellCrypto.canonicalRequest(method: r.method, path: r.path, time: r.ts, nonce: r.nonce, body: body)
          .replacingOccurrences(of: "\n", with: "\\n"),
      r.canonical.replacingOccurrences(of: "\n", with: "\\n"))
check("request sig", BellCrypto.signRequest(key: phoneKey, method: r.method, path: r.path, time: r.ts, nonce: r.nonce, body: body), r.sig)

let g = v.empty_body_get
check("empty-body GET sig", BellCrypto.signRequest(key: phoneKey, method: g.method, path: g.path, time: g.ts, nonce: g.nonce, body: Data()), g.sig)

let eventJSON = Data(v.event.json.utf8)
check("event sig verifies", String(BellCrypto.verifyEvent(key: phoneKey, signature: v.event.sig, json: eventJSON)), "true")

// The vector event is seq 1, so the stream parser first gets a hello at seq 0, signed here with CryptoKit directly.
let stream = "00112233445566778899aabbccddeeff"
func line(_ json: String) -> Data {
    let mac = HMAC<SHA256>.authenticationCode(for: Data("belldesk-v1-event\n".utf8) + Data(json.utf8), using: SymmetricKey(data: phoneKey))
    return Data("data: \(Data(mac).base64EncodedString()) \(json)".utf8)
}
let hello = line(#"{"type":"hello","stream":"\#(stream)","seq":0,"seats":["alice","bob"],"last":null,"now":1791360000}"#)
let vectorLine = Data("data: \(v.event.sig) \(v.event.json)".utf8)

func outcome(_ lines: [Data]) -> String {
    var verifier = EventVerifier(key: phoneKey, streamNonce: stream)
    var last = "nothing"
    for l in lines {
        do {
            if let e = try verifier.accept(line: l) { last = "\(e)" }
        } catch {
            return "dropped(\(error))"
        }
    }
    return last
}

let parsed = outcome([hello, Data(), vectorLine])
check("event via stream parser", parsed.hasPrefix("mail(") && parsed.contains("2026-10-07T082113-5c1e8a20") ? "mail accepted" : parsed, "mail accepted")

var tampered = vectorLine
tampered[tampered.count - 3] ^= 0x01
check("tampered event", outcome([hello, tampered]), "dropped(badSignature)")
check("wrong stream", outcome([line(#"{"type":"hello","stream":"ffffffffffffffffffffffffffffffff","seq":0,"seats":[]}"#)]), "dropped(wrongStream)")
check("seq gap", outcome([hello, line(#"{"type":"ping","stream":"\#(stream)","seq":2,"now":1}"#)]), "dropped(badSeq)")
check("mail before hello", outcome([line(#"{"type":"ping","stream":"\#(stream)","seq":0,"now":1}"#)]), "dropped(helloNotFirst)")

var splitter = LineSplitter()
let wire = Data("data: a\r\n\r\ndata: b\n".utf8)
let split = try wire.compactMap { try splitter.feed($0) }.map { String(decoding: $0, as: UTF8.self) }
check("line splitting", split.description, #"["data: a", "", "data: b"]"#)

// Lines are split before any signature check, so one without a newline must not grow without limit.
func lines(_ wire: Data) -> String {
    var splitter = LineSplitter()
    do {
        return try wire.compactMap { try splitter.feed($0) }.map { "\($0.count) bytes" }.description
    } catch {
        return "dropped(\(error))"
    }
}
check("line at the 64 KB cap", lines(Data(repeating: 0x61, count: 65536) + Data("\n".utf8)), #"["65536 bytes"]"#)
check("line past the cap", lines(Data(repeating: 0x61, count: 65537)), "dropped(malformed)")

// Answer bodies are read as they arrive, so one that never ends must not grow without limit either.
func answerBody(_ count: Int) async -> String {
    let bytes = AsyncStream<UInt8> { stream in
        for _ in 0..<count { stream.yield(0x20) }
        stream.finish()
    }
    do {
        return "\(try await ListenerClient.body(bytes).count) bytes"
    } catch {
        return "dropped(\(error))"
    }
}
check("answer at the 1 MB cap", await answerBody(1 << 20), "1048576 bytes")
check("answer past the cap", await answerBody((1 << 20) + 1), "dropped(badResponse)")

// Polling for code A: only a final state ends the wait.
func poll(_ json: String) -> String {
    let status = try! JSONDecoder().decode(PairStatus.self, from: Data(json.utf8))
    return status.outcome.map { "\($0)" } ?? "keeps waiting"
}
check("poll, pending", poll(#"{"state":"pending"}"#), "keeps waiting")
check("poll, confirm", poll(#"{"state":"confirm","device_id":"0011223344556677"}"#), #"approved("0011223344556677")"#)
check("poll, confirm without device_id", poll(#"{"state":"confirm"}"#), "keeps waiting")
check("poll, denied", poll(#"{"state":"denied"}"#), "denied")
check("poll, expired", poll(#"{"state":"expired"}"#), "expired")
check("poll, abandoned by another device", poll(#"{"state":"abandoned"}"#), "abandoned")

// A signed answer is this letter's result only if it names this letter's request_id.
let requestID = "0b7d6c44-8f3e-4d3a-9b8e-2f1f6a9c3d10"
func verdict(_ json: String) -> String {
    let reply = try! JSONDecoder().decode(DictationReply.self, from: Data(json.utf8))
    return reply.verdict(for: requestID) ?? "not this request's result"
}
check("reply for this request", verdict(#"{"status":"sent","request_id":"\#(requestID)","results":[]}"#), "sent")
check("reply for another request",
      verdict(#"{"status":"sent","request_id":"ffffffff-ffff-4fff-bfff-ffffffffffff","results":[]}"#), "not this request's result")
check("reply naming no request", verdict(#"{"status":"sent","results":[]}"#), "not this request's result")
check("reply with an unknown status", verdict(#"{"status":"queued","request_id":"\#(requestID)"}"#), "not this request's result")

// Answers from the Mac: the exact signed answer verifies; another status, nonce, or body does not.
let resp = v.response
let respBody = Data(resp.body.utf8)
check("response canonical",
      BellCrypto.hex(Data(SHA256.hash(data: BellCrypto.canonicalResponse(method: resp.method, path: resp.path, nonce: resp.nonce,
                                                                         status: resp.status, body: respBody)))),
      resp.canonical_sha256)
func answer(_ status: Int, _ nonce: String, _ body: Data, _ sig: String?) -> String {
    String(BellCrypto.verifyResponse(key: phoneKey, signature: sig, method: resp.method, path: resp.path, nonce: nonce,
                                     status: status, body: body))
}
check("response sig verifies", answer(resp.status, resp.nonce, respBody, resp.sig), "true")
check("response, other status", answer(404, resp.nonce, respBody, resp.sig), "false")
check("response, other request nonce", answer(resp.status, String(repeating: "f", count: 32), respBody, resp.sig), "false")
check("response, altered body", answer(resp.status, resp.nonce, Data(resp.body.replacingOccurrences(of: "sent", with: "SENT").utf8), resp.sig), "false")
check("response, no signature", answer(resp.status, resp.nonce, respBody, nil), "false")

// When a ring-eligible letter rings: replies only, and quiet hours that may cross midnight.
var utc = Calendar(identifier: .gregorian)
utc.timeZone = TimeZone(identifier: "UTC")!
func rings(_ policy: RingPolicy, forYou: Bool, _ hour: Int, _ minute: Int) -> String {
    String(policy.shouldRing(forYou: forYou, at: Date(timeIntervalSince1970: TimeInterval(hour * 3600 + minute * 60)), calendar: utc))
}
var policy = RingPolicy()
check("ring, everything", rings(policy, forYou: false, 12, 0), "true")
policy.ringFor = .repliesOnly
check("ring, replies only, not for you", rings(policy, forYou: false, 12, 0), "false")
check("ring, replies only, for you", rings(policy, forYou: true, 12, 0), "true")
policy = RingPolicy(quietHours: true)
check("ring, quiet 22:00-07:00 at 23:30", rings(policy, forYou: true, 23, 30), "false")
check("ring, quiet 22:00-07:00 at 06:59", rings(policy, forYou: true, 6, 59), "false")
check("ring, quiet 22:00-07:00 at 07:00", rings(policy, forYou: true, 7, 0), "true")
policy.quietFrom = 13 * 60
policy.quietTo = 14 * 60
check("ring, quiet 13:00-14:00 at 13:30", rings(policy, forYou: true, 13, 30), "false")
check("ring, quiet 13:00-14:00 at 12:59", rings(policy, forYou: true, 12, 59), "true")
policy.quietTo = policy.quietFrom
check("ring, quiet with equal ends", rings(policy, forYou: true, 13, 0), "true")

print(failures == 0 ? "\nAll vector checks passed." : "\n\(failures) check(s) failed.")
exit(failures == 0 ? 0 : 1)
