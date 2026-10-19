"""Pairing and message authentication for the phone <-> Mac link.

X25519 key agreement, then commit-and-reveal nonces so neither side's random
value is known to the other until both are fixed, then a six-digit
numeric-comparison code a person checks on both screens. HKDF-SHA256 for the
shared key, HMAC-SHA256 on every request and every streamed event.
PROTOCOL.md is the contract; tests/vectors.json pins the exact bytes both
sides must reproduce.
"""

import base64
import hashlib
import hmac

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

COMMIT_LABEL = b"belldesk-commit-v1"
PAIR_LABEL = b"belldesk-pair-v2"
NONCE_LEN = 16
AUTH_INFO = b"belldesk-v1-auth"
EVENT_LABEL = b"belldesk-v1-event\n"


def b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def b64d(text: str) -> bytes:
    return base64.b64decode(text, validate=True)


def new_private_key() -> X25519PrivateKey:
    return X25519PrivateKey.generate()


def load_private_key(raw: bytes) -> X25519PrivateKey:
    return X25519PrivateKey.from_private_bytes(raw)


def private_bytes(key: X25519PrivateKey) -> bytes:
    return key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def public_bytes(key: X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def commitment(server_pub: bytes, device_pub: bytes, server_nonce: bytes) -> bytes:
    return hashlib.sha256(COMMIT_LABEL + server_pub + device_pub + server_nonce).digest()


def pairing_code(server_pub: bytes, device_pub: bytes, device_nonce: bytes, server_nonce: bytes) -> str:
    digest = hashlib.sha256(PAIR_LABEL + server_pub + device_pub + device_nonce + server_nonce).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


def usable_peer(own_private: X25519PrivateKey, peer_pub: bytes) -> bool:
    try:
        own_private.exchange(X25519PublicKey.from_public_bytes(peer_pub))
    except ValueError:
        return False
    return True


def device_id(device_pub: bytes) -> str:
    return hashlib.sha256(device_pub).hexdigest()[:16]


def derive_key(own_private: X25519PrivateKey, peer_pub: bytes, server_pub: bytes, device_pub: bytes,
               device_nonce: bytes, server_nonce: bytes) -> bytes:
    shared = own_private.exchange(X25519PublicKey.from_public_bytes(peer_pub))
    salt = hashlib.sha256(server_pub + device_pub + device_nonce + server_nonce).digest()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=AUTH_INFO).derive(shared)


def canonical_request(method: str, path: str, ts: int, nonce: str, body: bytes) -> bytes:
    return "\n".join([method.upper(), path, str(ts), nonce, hashlib.sha256(body).hexdigest()]).encode("utf-8")


def sign_request(key: bytes, method: str, path: str, ts: int, nonce: str, body: bytes) -> str:
    return b64e(hmac.new(key, canonical_request(method, path, ts, nonce, body), hashlib.sha256).digest())


def verify_request(key: bytes, sig: str, method: str, path: str, ts: int, nonce: str, body: bytes) -> bool:
    try:
        given = b64d(sig)
    except ValueError:
        return False
    want = hmac.new(key, canonical_request(method, path, ts, nonce, body), hashlib.sha256).digest()
    return hmac.compare_digest(given, want)


RESPONSE_LABEL = b"belldesk-v1-response\n"


def canonical_response(method: str, path: str, nonce: str, status: int, body: bytes) -> bytes:
    """An answer bound to the exact signed request it answers, through that request's nonce."""
    return RESPONSE_LABEL + "\n".join([method.upper(), path, nonce, str(status), hashlib.sha256(body).hexdigest()]).encode("utf-8")


def sign_response(key: bytes, method: str, path: str, nonce: str, status: int, body: bytes) -> str:
    return b64e(hmac.new(key, canonical_response(method, path, nonce, status, body), hashlib.sha256).digest())


def sign_event(key: bytes, event_json: bytes) -> str:
    return b64e(hmac.new(key, EVENT_LABEL + event_json, hashlib.sha256).digest())
