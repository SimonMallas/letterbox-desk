# Letterbox Desk and Letterbox Bell — phone ⇄ Mac protocol, v1

The Mac listener (`mac/`, `bell-desk serve`) watches the letterbox inboxes and
rings the phone; the phone (`ios/`) can post one confirmed dictated letter as
the `desk` participant. Transport is plain HTTP/1.1 on the LAN. After pairing,
operational requests, answers and event streams are authenticated with the agreed key;
discovery and the pairing exchange are unauthenticated. `mac/tests/vectors.json` pins the exact bytes;
both sides must reproduce every value in it.

## Discovery

- Bonjour service `_belldesk._tcp`, instance name `Letterbox Desk`. Default port **8765**.
- The phone may also be given a host by hand (`desk-mac.local:8765`).
- `GET /v1/hello` (no auth) → `200 {"server":"bell-desk","version":"1","name":"<mac name>","pairing_open":bool,"pair_confirm":true,"server_pub":"<b64>"}`.
  `pair_confirm` means the Mac shows code B (step 7); a phone must not pair with a Mac without it.

## Encoding

- JSON, UTF-8. Binary values are standard base64 **with** padding.
- Times are integer Unix seconds unless a field says otherwise.
- Errors: `4xx {"error":"<code>","reason":"<code>"}`. Codes are listed per endpoint.
- The phone refuses an answer body over 1 MB as malformed.

## Pairing (X25519, commit-and-reveal nonces, two codes)

Each code depends on a random value from each side, and the Mac commits to its value before it
sees the phone's. A device in the middle therefore cannot search for keys that make the two
screens agree: it gets one 1-in-a-million guess per attempt, and every attempt shows in the pane.
Code A (shown on the phone, typed on the Mac) proves to the Mac which phone it reached. Code B
(shown on the Mac, typed on the phone) proves to the phone which Mac it reached: a listener the
person isn't looking at can approve code A for itself, but it can't put code B on the person's Mac.

1. Pairing is closed until a person opens it on the Mac: **Pair a phone…** in the menu bar,
   `bell-desk pair`, or turning the phone on with no phone paired. It then stays open for 3 minutes,
   and closes as soon as a phone finishes. Up to 3 requests may wait, one per source address
   (`429 {"error":"too_many_pairings"}`). After a refused attempt it pauses for 30 s
   (`pairing_open: false` in `hello`).
2. Phone creates an X25519 key pair (CryptoKit `Curve25519.KeyAgreement.PrivateKey`) and sends
   `POST /v1/pair {"device_pub":"<b64 32 bytes>","device_name":"<≤40 chars>"}`
   → `200 {"pair_id":"<32 hex>","server_pub":"<b64 32 bytes>","commitment":"<b64 32 bytes>"}`
   → `403 {"error":"pairing_closed"}` when no window is open.
   `commitment = SHA256("belldesk-commit-v1" ‖ server_pub ‖ device_pub ‖ server_nonce)`, where
   `server_nonce` is 16 random bytes the Mac keeps secret for now.
3. Only after receiving the commitment, the phone picks 16 random bytes `device_nonce` and sends
   `POST /v1/pair/<pair_id>/nonce {"device_nonce":"<b64 16 bytes>"}`
   → `200 {"server_nonce":"<b64 16 bytes>"}`
   → `409 {"error":"nonce_already_set"}` on a second call, `404` for an unknown or finished pairing.
   A request whose nonce doesn't arrive within 10 s is dropped and frees its slot; no pop-up appears for it.
   The nonce must also arrive while pairing is still open (`403 {"error":"pairing_closed"}` otherwise), so no new
   pop-up ever appears after pairing closes. A code already on the Mac's screen can still be typed until its own
   2 minutes run out, and an approved phone can still finish step 7.
4. The phone checks the commitment against the revealed `server_nonce`. **If it does not match,
   stop and show "Pairing failed: try again"; never show a code.**
5. Both sides compute the code:
   `SHA256("belldesk-pair-v2" ‖ server_pub ‖ device_pub ‖ device_nonce ‖ server_nonce)`, first
   4 bytes as a big-endian unsigned integer, mod 1 000 000, zero-padded to 6 digits.
   Show it as `461 702`. Once the nonce arrives, the Mac shows a pop-up asking the person to type
   the code from the phone. A typed code equal to the Mac's own approves; a different code or Cancel
   refuses (and starts the 30 s pause). As a fallback, typing `approve` and the phone's 6 digits in the listener
   pane approves the same way; the pane never shows code A, and a bare `approve` approves nothing. `deny`
   refuses. Nothing is approved before the nonce step.
   Approval does not pair the phone yet: the Mac keeps nothing until step 7.
6. Phone polls `GET /v1/pair/<pair_id>` about once a second
   → `200 {"state":"pending"|"confirm"|"confirmed"|"denied"|"abandoned"|"expired","device_id":"<16 hex>"?}`.
   `confirm` means code A matched and the Mac now shows code B. `device_id` is present from `confirm` on.
   (1.0 Macs answered `approved` here. A 1.0 phone treats only that as paired, so against this Mac it times out
   with nothing saved instead of keeping a record the Mac never stored.) `404` for an unknown pair_id.
7. Once approved, the Mac shows code B and the phone asks the person to type it. Both compute
   `SHA256("belldesk-confirm-v1" ‖ server_pub ‖ device_pub ‖ device_nonce ‖ server_nonce)` the same way as
   code A. **The phone never displays code B.** It keeps the key in memory only, saves nothing and sends
   nothing with it until the typed code matches. One wrong code may be retried once; a second ends the
   attempt with "Nothing was paired" and a best-effort `POST /v1/pair/<pair_id>/abandon` → `200 {"state":"abandoned"}`.
   On a match the phone sends
   `POST /v1/pair/<pair_id>/confirm {"proof":"<b64 HMAC-SHA256(key, "belldesk-confirm-v1")>"}`
   → `200 {"state":"confirmed","device_id":"<16 hex>","ack":"<b64>"}`: only now does the Mac keep the device.
   `ack = HMAC-SHA256(key, "belldesk-confirmed-v1\n" ‖ pair_id ‖ "\n" ‖ device_id)`. The phone saves its record
   only after this answer, and only if `ack` verifies, so a forged "confirmed" can't leave it believing it is
   paired. A late answer for an attempt the person has since left, forgotten or restarted changes nothing. `403 {"error":"bad_proof"}` refuses (and starts the 30 s pause);
   `404` means the attempt is over, including when 2 minutes have passed since approval. A repeated correct
   proof gets the same `200`.
8. Shared key (both sides): `shared = X25519(own_private, peer_public)`;
   `key = HKDF-SHA256(ikm=shared, salt=SHA256(server_pub ‖ device_pub ‖ device_nonce ‖ server_nonce), info="belldesk-v1-auth", L=32)`.
   CryptoKit: `sharedSecretFromKeyAgreement(with:)` then
   `hkdfDerivedSymmetricKey(using: SHA256.self, salt:, sharedInfo:, outputByteCount: 32)`.
9. `device_id = hex(SHA256(device_pub))[0:16]`. The phone stores `device_id`, `key`, `server_pub`
   and the host in the Keychain. The private key is not needed after pairing.

The phone never starts pairing by itself once paired. It tries the saved host first and a discovered
one only as a fallback; an unsigned `hello` with a different `server_pub` means only that this host is
not the paired Mac. If no host has the pinned key, or the stream answers `401`, the phone stops sending
and says so, and pairs again only when the person taps **Pair again**. When a signed stream comes from a
new address, the phone saves that address.

## Request authentication (everything except `/v1/hello` and `/v1/pair*`)

Headers:

| Header | Value |
|---|---|
| `X-Belldesk-Device` | `device_id` |
| `X-Belldesk-Time` | Unix seconds, integer |
| `X-Belldesk-Nonce` | 32 lowercase hex chars, fresh per request |
| `X-Belldesk-Sig` | `base64(HMAC-SHA256(key, canonical))` |

`canonical` = `METHOD \n PATH \n TIME \n NONCE \n hex(SHA256(body))`, joined with `\n`, no
trailing newline. PATH includes any query string. An empty body hashes the empty string.

The Mac rejects (`401 {"error":"unauthorized"}`) an unknown device, a time more than 120 s off,
a nonce already used by that device in the last 10 minutes, or a bad signature.

### Signed answers

Every answer to a request that passed authentication (any status, including `400`, `404` and
`503`) carries `X-Belldesk-Response-Sig: base64(HMAC-SHA256(key, response_canonical))`, where
`response_canonical` = `"belldesk-v1-response\n"` followed by `METHOD \n PATH \n NONCE \n STATUS \n
hex(SHA256(response body))`. NONCE is the request's own, so an answer can't be replayed onto
another request. A `401` is not signed.

The phone believes an answer only if this signature checks out. An unsigned or badly signed answer
proves nothing either way: the phone treats every seat as `not_confirmed` (the letter may have
arrived), never as sent and never as `not_attempted`.

### Where the phone sends

Letters and status checks go only to the host whose signed event stream is live: the host that
sent a correctly signed `hello` on the current stream. A newer Bonjour result, or an unsigned
`/v1/hello`, never changes where a letter goes; a stream from that host has to verify first.

## Events: `GET /v1/events` (signed)

`200 text/event-stream`. Each event is one line, then a blank line:

```
data: <sig> <event-json>
```

`sig` = `base64(HMAC-SHA256(key, "belldesk-v1-event\n" ‖ event-json-bytes))`. Split on the
first space, verify the signature over the exact JSON bytes, then parse. Every event carries
`"stream"` = the nonce of the `GET` that opened it and `"seq"`, counting from 0. The phone
drops the stream if a line runs past 64 KB, a signature fails, `stream` differs, or `seq`
does not increase by one.

| `type` | Fields | Meaning |
|---|---|---|
| `hello` | `name` (the Mac's name), `seats` (array of seat IDs, the server's allowlist), `last` (a `mail` object or null), `now` | First event on every stream. The phone shows exactly these seats in its picker, and this `name` (not the unsigned one from `/v1/hello`) once paired. |
| `mail` | `to`, `from`, `letter_type`, `id`, `priority`, `ts`, `for_you` | A ring-eligible letter landed: `priority: now`, any inbox including `desk`'s, not sent by `desk`. `for_you` is true when `to` is `desk`: an agent wrote to the person. Envelope fields only. |
| `ping` | `now` | Every 15 s. No event for 40 s means the stream is dead: reconnect. |

`id` is the letter's time and short token, `<YYYY-MM-DDTHHMMSS>-<token>`, never its subject. The token
is the envelope id's last `-`-separated part when that is 8 lowercase hex characters, otherwise the first
8 hex characters of `SHA256(id)`: the same token `letterbox` prints. Without a leading time, `id` is the
token alone. Show its last 8 characters.
`ts` is the envelope's `sent` field, a string. Nothing in an event is a path or a letter body.

## Dictation: `POST /v1/dictation` (signed)

Body: `{"request_id":"<uuid v4, lowercase>","to":["<seat>", …],"text":"<string>"}`. `to` may also
be a single seat as a string.

The Mac checks: every seat in `to` is one of the allowlisted seats, appears once, and has an
inbox; `text` after trimming is 1–2000 characters with no control characters except newline
and tab; `request_id` is a UUID. Then, one seat at a time, it runs the pinned helper once per
seat: `LETTERBOX_AGENT=desk LETTERBOX_DIR=<root> letterbox send <seat> info desk-dictation --now`, text on stdin. With more
than one seat, each copy ends with `Also addressed to: carol, frank (intended recipients; delivery
and reading are not guaranteed).` It names who the person addressed, not who received it. The
whole seat set and the text are validated before the first send. If the helper's pin or the
helper itself fails, the remaining seats are not attempted. Nothing is ever retried by the Mac.

`200 {"status","request_id","results":[{"to","status","id"?,"ring"?,"reason"?}, …]}`

Per seat, `status` is:

| Seat `status` | Meaning |
|---|---|
| `published` | The copy is in that seat's inbox: `id` is its envelope id, `ring` is `{"outcome","reason"}` as the helper reported (`submitted`, or `no_live_surface` with a reason). Neither means it was read. |
| `not_confirmed` | The Mac ran the helper but could not confirm the copy. **Ambiguous: it may have landed.** |
| `not_attempted` | The Mac never ran the helper for this seat (it stopped first). Provably not sent. |

Top-level `status`: `sent` (every copy published), `partial` (some published), `not_confirmed`
(none published, at least one ambiguous), `not_sent` (nothing attempted).

With exactly one seat the response also carries that seat's `id` and `ring` (or `reason`) at the
top level, as in v1.

| Error | Meaning |
|---|---|
| `400 {"error":"bad_request","reason":"unknown_seat"\|"duplicate_seat"\|"no_seats"\|"inbox_missing"\|"text_empty"\|"text_too_long"\|"text_control_chars"\|"bad_request_id"\|"bad_json"}` | Nothing was attempted. |
| `503 {"error":"shutting_down"}` | The listener is stopping. Nothing was attempted; show "Not sent". |

Repeating a `request_id` returns the stored result with `"replayed": true` and runs nothing. If
the listener stopped part-way through a group, the stored result is `partial` or
`not_confirmed`, with `"reason":"interrupted"` on the seats it never confirmed.

`GET /v1/dictation/<request_id>` (signed) → the stored result, or `404 {"error":"unknown_request"}`
if the Mac never received it. Use this after a network failure on the `POST`: it is a status
check, not a retry. "Send again" on the phone is a person's decision and always uses a new
`request_id`. It is offered plainly only for `not_attempted` seats. For a `not_confirmed` seat
the phone must say the agent may get it twice and ask for a second confirmation.

## What the phone shows

- Per seat: `submitted` → "In alice's inbox · doorbell rang". `no_live_surface` → "In carol's inbox · the doorbell didn't ring". Never "read" or "delivered to the agent".
- A `mail` event with `for_you: true` rings differently: "bob wrote to you". (The event carries no `re`, so the phone can't tell a reply from a new letter.)
- `not_confirmed` or a POST with no answer whose status check is not `sent` → "The Mac didn't confirm this letter. Nothing was retried."
- `unknown_request` after a failed POST, in a signed answer → the Mac never got it → "Not sent". Unsigned → `not_confirmed`.
