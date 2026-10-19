"""Regenerate tests/vectors.json from fixed keys. The iOS tests read the same file."""

import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from bell_desk import crypto  # noqa: E402

server = crypto.load_private_key(bytes(range(32)))
device = crypto.load_private_key(bytes(range(32, 64)))
server_pub = crypto.public_bytes(server)
device_pub = crypto.public_bytes(device)
device_nonce = bytes(range(64, 80))
server_nonce = bytes(range(80, 96))
key = crypto.derive_key(server, device_pub, server_pub, device_pub, device_nonce, server_nonce)
assert key == crypto.derive_key(device, server_pub, server_pub, device_pub, device_nonce, server_nonce)

body = b'{"request_id":"3f2a8c1e-5b7d-4e9f-a1c2-0d4b6e8f9a01","to":"bob","text":"Check my relay is still up."}'
ts = 1791360000
nonce = "00112233445566778899aabbccddeeff"
response_body = b'{"status":"sent","request_id":"3f2a8c1e-5b7d-4e9f-a1c2-0d4b6e8f9a01","id":"2026-10-07T082200-desk-info-desk-dictation-1a2b3c4d"}'
event = b'{"type":"mail","stream":"00112233445566778899aabbccddeeff","seq":1,"to":"alice","from":"bob","letter_type":"result","id":"2026-10-07T082113-bob-result-demo-5c1e8a20","priority":"now","ts":"2026-10-07T08:21:13Z"}'

vectors = {
    "server_private": crypto.b64e(crypto.private_bytes(server)),
    "device_private": crypto.b64e(crypto.private_bytes(device)),
    "server_public": crypto.b64e(server_pub),
    "device_public": crypto.b64e(device_pub),
    "device_nonce": crypto.b64e(device_nonce),
    "server_nonce": crypto.b64e(server_nonce),
    "commitment": crypto.b64e(crypto.commitment(server_pub, device_pub, server_nonce)),
    "pairing_code": crypto.pairing_code(server_pub, device_pub, device_nonce, server_nonce),
    "device_id": crypto.device_id(device_pub),
    "auth_key": crypto.b64e(key),
    "request": {
        "method": "POST",
        "path": "/v1/dictation",
        "ts": ts,
        "nonce": nonce,
        "body": body.decode("utf-8"),
        "canonical": crypto.canonical_request("POST", "/v1/dictation", ts, nonce, body).decode("utf-8"),
        "sig": crypto.sign_request(key, "POST", "/v1/dictation", ts, nonce, body),
    },
    "empty_body_get": {
        "method": "GET",
        "path": "/v1/events",
        "ts": ts,
        "nonce": nonce,
        "sig": crypto.sign_request(key, "GET", "/v1/events", ts, nonce, b""),
    },
    "event": {"json": event.decode("utf-8"), "sig": crypto.sign_event(key, event)},
    "response": {
        "method": "POST",
        "path": "/v1/dictation",
        "nonce": nonce,
        "status": 200,
        "body": response_body.decode("utf-8"),
        "canonical_sha256": hashlib.sha256(crypto.canonical_response("POST", "/v1/dictation", nonce, 200, response_body)).hexdigest(),
        "sig": crypto.sign_response(key, "POST", "/v1/dictation", nonce, 200, response_body),
    },
}

out = pathlib.Path(__file__).with_name("vectors.json")
out.write_text(json.dumps(vectors, indent=2) + "\n")
print(json.dumps(vectors, indent=2))
