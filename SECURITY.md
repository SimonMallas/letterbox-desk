# Security and privacy

Letterbox Desk is for a trusted Mac account and local network. Letterbox Bell is an optional
paired iPhone client. There is no hosted Desk service, account or relay.

## Pairing and network scope

Pairing uses X25519, a committed random nonce and a six-digit numeric comparison. Type the
code shown on the phone into the Mac prompt; refuse a code you did not request or cannot
compare. Pairing needs a person's approval. The derived key authenticates requests, replies
and event streams with HMAC-SHA256. The phone pins the Mac's public key and verifies signed
answers before believing a send result. See [PROTOCOL.md](PROTOCOL.md) for exact bytes.

Traffic uses plain HTTP, not TLS: signatures authenticate it but do **not encrypt** text or
metadata from observers on the network. Use a trusted LAN. The Mac listener defaults to
port 8765 on all interfaces (`0.0.0.0`) when the phone side is enabled; do not expose it through
port forwarding, a public interface or an untrusted tunnel. Bonjour advertises it locally.
A Mac-only setup leaves the phone service off. Turning it off keeps pairings on disk.

The phone stores its pairing in a device-only Keychain item. Mac keys and paired-device
records are in the private `~/.bell-desk/` state directory. Someone with access to that Mac
account or its state can read letters, keys and send records; this is not an isolation boundary
against another process running as you. Unpair a lost phone from Desk.

## What reaches the phone

Incoming mail events contain envelope metadata: sender, recipient, letter type, id, time,
priority and whether it is addressed to `desk`. They contain no incoming letter body,
picture bytes or incoming picture path. The Mac conversation window does read local bodies
for display. Text dictated and confirmed on the phone is sent to the Mac; send results and
agent names are returned to the phone.

## Dictation

Mac Dictation is the operating system's service, invoked through its Edit menu. Its recognition
location and privacy controls follow macOS settings; Desk does not implement a separate Mac
speech service. The editor preserves provisional marked text while it is being recognized.

Letterbox Bell uses Apple's Speech framework. It prefers on-device recognition where the phone
and language support it; the settings allow Apple's online recognition, and recognition can
use that service where on-device speech is unavailable. The app indicates which is in use.
Online recognition can send audio to Apple. Review the text before Send. Desk and Letterbox Bell
do not store audio recordings in their own files, and recognition does not send a letter by
itself. Apple's speech behavior is separate from the local phone-to-Mac transport.

## Pictures and local files

Pictures are inert PNG/JPEG copies in a dedicated private directory outside the Letterbox
root. Setup shows and persists its canonical path. File paste/drop captures bounded bytes
immediately; a selected symlink file is refused, while linked parent folders are allowed only
when the opened regular file matches its device/inode. At Send, images are decoded/validated,
limited to 10 MB and 16 million pixels, and copied under unique names with size and SHA256.
No image is uploaded by Desk.

A recipient gets picture copies only after its own agent has read a unique local probe in its
normal sandbox and replied with the exact digest. Automatic acceptance checks the seat,
destination, request link, canonical directory, expected digest and unchanged regular probe
file. Pending probes expire after 24 hours; consumed/stale proofs cannot grant access again.
This is point-in-time read evidence on a trusted local mailbox, not authenticated agent
identity and not a permission grant. Setup never widens sandbox policy or shared-parent
permissions. Details are in [CONTRACT.md, Part B](CONTRACT.md#part-b-the-desks-own-rules).

Incoming paths are displayed as plain text and are never opened automatically. A recipient
should verify the stored picture's size and digest before using it. Copies/probes are retained
for explicit cleanup; Done and Keep only hide conversation history locally and delete no mail
or picture files. Local send records can contain text and picture references.

## Helper and delivery decisions

The public `letterbox` helper is SHA256-pinned at setup. A changed helper is refused until the
person deliberately verifies and pins the intended version. Doorbell settings are parsed as
text, never sourced as shell code; a changed pinned doorbell adapter is omitted, so a letter
may arrive without ringing. The selected roster limits whom Desk can write to.

A published letter is not a read receipt. If publication cannot be confirmed, it may already
have arrived. Neither a timeout nor reconnect automatically resends it. Explicit retries show
per-recipient results; confirmed copies are not sent again. Verification becoming ready also
does not send a retained picture draft automatically.

## Reporting a concern

Do not put pairing keys, tokens, private mail, picture files or personal logs into a public
issue. Share a minimal synthetic reproduction and the affected version. Report a security concern privately through GitHub's private vulnerability reporting for this repository: https://github.com/SimonMallas/letterbox-desk/security/advisories/new. If that link is unavailable, wait for private reporting to be enabled or contact the maintainer through the repository profile before sharing sensitive evidence. No response-time commitment is made.
