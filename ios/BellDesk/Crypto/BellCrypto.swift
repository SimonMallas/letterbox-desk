import CryptoKit
import Foundation

/// Pairing and message authentication, exactly as PROTOCOL.md defines them.
/// Shared verbatim with the vector check in Tools/, so it depends on Foundation and CryptoKit only.
enum BellCrypto {
    static let commitLabel = Data("belldesk-commit-v1".utf8)
    static let pairLabel = Data("belldesk-pair-v2".utf8)
    static let confirmLabel = Data("belldesk-confirm-v1".utf8)
    static let authInfo = Data("belldesk-v1-auth".utf8)
    static let eventLabel = Data("belldesk-v1-event\n".utf8)

    /// What the Mac committed to before it saw the phone's nonce.
    static func commitment(serverPub: Data, devicePub: Data, serverNonce: Data) -> Data {
        Data(SHA256.hash(data: commitLabel + serverPub + devicePub + serverNonce))
    }

    private static func sixDigits(_ label: Data, serverPub: Data, devicePub: Data, deviceNonce: Data, serverNonce: Data) -> String {
        let digest = Array(SHA256.hash(data: label + serverPub + devicePub + deviceNonce + serverNonce))
        let head = digest[0..<4].reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        return String(format: "%06u", head % 1_000_000)
    }

    /// Code A: shown here, typed on the Mac.
    static func pairingCode(serverPub: Data, devicePub: Data, deviceNonce: Data, serverNonce: Data) -> String {
        sixDigits(pairLabel, serverPub: serverPub, devicePub: devicePub, deviceNonce: deviceNonce, serverNonce: serverNonce)
    }

    /// Code B: shown only on the Mac the phone really reached, typed here. The phone never displays it.
    static func confirmCode(serverPub: Data, devicePub: Data, deviceNonce: Data, serverNonce: Data) -> String {
        sixDigits(confirmLabel, serverPub: serverPub, devicePub: devicePub, deviceNonce: deviceNonce, serverNonce: serverNonce)
    }

    /// Sent once code B matched; the Mac pairs the phone only after it.
    static func confirmProof(key: Data) -> String {
        Data(HMAC<SHA256>.authenticationCode(for: confirmLabel, using: SymmetricKey(data: key))).base64EncodedString()
    }

    static let confirmedLabel = Data("belldesk-confirmed-v1\n".utf8)

    /// The Mac's signed answer to that proof, bound to this pairing.
    static func confirmAck(key: Data, pairID: String, deviceID: String) -> String {
        let message = confirmedLabel + Data("\(pairID)\n\(deviceID)".utf8)
        return Data(HMAC<SHA256>.authenticationCode(for: message, using: SymmetricKey(data: key))).base64EncodedString()
    }

    /// Constant-time check of that answer; the phone saves nothing without it.
    static func verifyConfirmAck(key: Data, ack: String?, pairID: String, deviceID: String) -> Bool {
        guard let ack, let given = Data(base64Encoded: ack) else { return false }
        return HMAC<SHA256>.isValidAuthenticationCode(given, authenticating: confirmedLabel + Data("\(pairID)\n\(deviceID)".utf8),
                                                       using: SymmetricKey(data: key))
    }

    /// "585601" -> "585 601"
    static func displayCode(_ code: String) -> String {
        guard code.count == 6 else { return code }
        return "\(code.prefix(3)) \(code.suffix(3))"
    }

    /// The digits a person typed, in any script, as ASCII, the way the Mac reads a typed code: an Arabic-Indic
    /// or full-width 1 is a 1, and nothing else counts, not even ½ or ².
    static func typedDigits(_ text: String) -> String {
        text.unicodeScalars
            .filter { $0.properties.generalCategory == .decimalNumber }
            .compactMap { $0.properties.numericValue.map { String(Int($0)) } }
            .joined()
    }

    static func deviceID(devicePub: Data) -> String {
        String(hex(Data(SHA256.hash(data: devicePub))).prefix(16))
    }

    static func deriveKey(ownPrivate: Curve25519.KeyAgreement.PrivateKey, peerPub: Data,
                          serverPub: Data, devicePub: Data, deviceNonce: Data, serverNonce: Data) throws -> Data {
        let peer = try Curve25519.KeyAgreement.PublicKey(rawRepresentation: peerPub)
        let shared = try ownPrivate.sharedSecretFromKeyAgreement(with: peer)
        let salt = Data(SHA256.hash(data: serverPub + devicePub + deviceNonce + serverNonce))
        let key = shared.hkdfDerivedSymmetricKey(using: SHA256.self, salt: salt,
                                                 sharedInfo: authInfo, outputByteCount: 32)
        return key.withUnsafeBytes { Data($0) }
    }

    static func canonicalRequest(method: String, path: String, time: Int, nonce: String, body: Data) -> String {
        [method.uppercased(), path, String(time), nonce, hex(Data(SHA256.hash(data: body)))]
            .joined(separator: "\n")
    }

    static func signRequest(key: Data, method: String, path: String, time: Int, nonce: String, body: Data) -> String {
        let canonical = Data(canonicalRequest(method: method, path: path, time: time, nonce: nonce, body: body).utf8)
        let mac = HMAC<SHA256>.authenticationCode(for: canonical, using: SymmetricKey(data: key))
        return Data(mac).base64EncodedString()
    }

    static let responseLabel = Data("belldesk-v1-response\n".utf8)

    static func canonicalResponse(method: String, path: String, nonce: String, status: Int, body: Data) -> Data {
        responseLabel + Data([method.uppercased(), path, nonce, String(status), hex(Data(SHA256.hash(data: body)))]
            .joined(separator: "\n").utf8)
    }

    /// Constant-time check that an answer came from the paired Mac, for the request carrying `nonce`.
    static func verifyResponse(key: Data, signature: String?, method: String, path: String, nonce: String,
                               status: Int, body: Data) -> Bool {
        guard let signature, let given = Data(base64Encoded: signature) else { return false }
        return HMAC<SHA256>.isValidAuthenticationCode(
            given, authenticating: canonicalResponse(method: method, path: path, nonce: nonce, status: status, body: body),
            using: SymmetricKey(data: key))
    }

    /// Constant-time check of an event signature over the exact JSON bytes.
    static func verifyEvent(key: Data, signature: String, json: Data) -> Bool {
        guard let given = Data(base64Encoded: signature) else { return false }
        return HMAC<SHA256>.isValidAuthenticationCode(given, authenticating: eventLabel + json,
                                                       using: SymmetricKey(data: key))
    }

    /// 16 bytes from the system CSPRNG.
    static func randomBytes16() -> Data {
        SymmetricKey(size: .bits128).withUnsafeBytes { Data($0) }
    }

    /// 32 lowercase hex chars from the system CSPRNG.
    static func newNonce() -> String {
        hex(randomBytes16())
    }

    static func hex(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }
}
