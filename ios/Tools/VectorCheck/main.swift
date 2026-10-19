import CryptoKit
import Foundation

// Checks the app's BellCrypto.swift and EventParser.swift against mac/tests/vectors.json.
// Built and run by Tools/check-vectors.sh.

struct Vectors: Decodable {
    struct Request: Decodable { let method, path, nonce, body, canonical, sig: String; let ts: Int }
    struct EmptyGet: Decodable { let method, path, nonce, sig: String; let ts: Int }
    struct Event: Decodable { let json, sig: String }
    struct Response: Decodable { let method, path, nonce, body, canonical_sha256, sig: String; let status: Int }
    let server_private, device_private, server_public, device_public: String
    let device_nonce, server_nonce, commitment: String
    let pairing_code, device_id, auth_key: String
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
check("device_id", BellCrypto.deviceID(devicePub: devicePub), v.device_id)

let phoneKey = try BellCrypto.deriveKey(ownPrivate: device, peerPub: serverPub, serverPub: serverPub, devicePub: devicePub,
                                        deviceNonce: deviceNonce, serverNonce: serverNonce)
let macKey = try BellCrypto.deriveKey(ownPrivate: server, peerPub: devicePub, serverPub: serverPub, devicePub: devicePub,
                                      deviceNonce: deviceNonce, serverNonce: serverNonce)
check("auth_key (phone side)", phoneKey.base64EncodedString(), v.auth_key)
check("auth_key (Mac side)", macKey.base64EncodedString(), v.auth_key)

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
check("event via stream parser", parsed.hasPrefix("mail(") && parsed.contains("2026-10-07T082113-bob-result-demo-5c1e8a20") ? "mail accepted" : parsed, "mail accepted")

var tampered = vectorLine
tampered[tampered.count - 3] ^= 0x01
check("tampered event", outcome([hello, tampered]), "dropped(badSignature)")
check("wrong stream", outcome([line(#"{"type":"hello","stream":"ffffffffffffffffffffffffffffffff","seq":0,"seats":[]}"#)]), "dropped(wrongStream)")
check("seq gap", outcome([hello, line(#"{"type":"ping","stream":"\#(stream)","seq":2,"now":1}"#)]), "dropped(badSeq)")
check("mail before hello", outcome([line(#"{"type":"ping","stream":"\#(stream)","seq":0,"now":1}"#)]), "dropped(helloNotFirst)")

var splitter = LineSplitter()
let wire = Data("data: a\r\n\r\ndata: b\n".utf8)
let split = wire.compactMap { splitter.feed($0) }.map { String(decoding: $0, as: UTF8.self) }
check("line splitting", split.description, #"["data: a", "", "data: b"]"#)

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

print(failures == 0 ? "\nAll vector checks passed." : "\n\(failures) check(s) failed.")
exit(failures == 0 ? 0 : 1)
