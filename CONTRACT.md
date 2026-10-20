# Letterbox Desk and Agent Letterbox: the seam

Letterbox Desk is a separate Mac add-on with its own maintainers. Letterbox Bell, the phone, is an
extension of the Desk. Neither is part of Letterbox. This file says what the Desk relies on from
Letterbox, and which side owns each rule.

- **Part A** lists what Letterbox's maintainers commit to. That is narrow on purpose.
- **Part B** lists the Desk's own rules. They bind only the Desk.

Status: version 1, 8 Oct 2026. It is not yet a shared contract. It becomes one when the Letterbox
editions link to it from their own docs. Until then it is the Desk's record of what it relies on.

## Supported versions (tested)

| Edition | Version tested | How |
|---|---|---|
| cmux | 0.5.0 | `EveryPublicEditionTests`: setup and a desk send on a scratch letterbox, from each edition's published main |
| tmux | 0.5.0 | same |
| Zellij | 0.5.0 | same |
| Herdr | 0.6.1 | same |

Support and the setup check are two different things. Setup accepts any helper that reports
0.5.0 or newer, and refuses an older one or one that reports no version. Only the versions in
this table are tested and supported. A later version counts as supported once these tests pass
against it.

## Part A: what Letterbox's maintainers commit to

They will announce, in the edition's release notes, any intentional breaking change to these
public interfaces, as they are in the tested versions above:

1. **The letterbox folder.** It is chosen by `LETTERBOX_DIR`, else by the first line of
   `${XDG_CONFIG_HOME:-$HOME/.config}/agent-letterbox/default-dir` when that file exists. With
   neither, the helper falls back to `./.letterbox`, which the Desk refuses (Part B).
2. **Mailboxes.** A participant is `<root>/<name>/` with `inbox/` and `processed/`. Names match
   `^[a-z][a-z0-9-]*$`, and `locks` is reserved. A letter is `<root>/<to>/inbox/<id>.md`. It is
   written through a temporary file whose name starts with `.`. The recipient may later move it to
   `processed/`.
3. **The envelope.** Each letter has a first front-matter block of `key: value` lines between
   `---`, with `id` (the file name without `.md`), `sent`, `from`, `to`, `type`, `re`, `priority`,
   `requires_ack` and `deadline`. `priority` is `now` with `--now`.
4. **Sending.**
   `LETTERBOX_AGENT=<name> LETTERBOX_DIR=<root> letterbox send <to> <type> <slug> [--now] [--re <id>]`,
   with the body on stdin.
   - Success prints `sent: <path>`. That proves the letter was published. It never proves it was
     read or acknowledged.
   - With `--now`, one more line reports the doorbell:
     `doorbell-outcome v=1 outcome=<submitted|pasted_not_submitted|no_live_surface> reason=<...> target=<...>`.
     That is an attempt's outcome, not a delivery or read receipt.
5. **Any valid name can take part.** A mailbox-only participant such as `desk` can send and
   receive like any other.
6. **The doorbell settings file.** `<root>/env.sh`, written by `letterbox <edition> setup`,
   exports the edition's doorbell settings as `export KEY='value'` lines.

Nothing else is promised. In particular, Letterbox does not promise anything about where the
helper is installed, about the Desk's own files, or about the phone.

## Part B: the Desk's own rules

These are constraints the Desk places on itself.

**Finding things**
- The letterbox folder is the first of: `--letterbox-dir` at setup, `LETTERBOX_DIR`, then the
  install pointer read as text. It must be an absolute path to an existing folder. The helper's
  own fallback, `./.letterbox`, is never used.
- The helper is the one given with `--helper`, else `~/.local/bin/letterbox`, resolved through
  that launcher. That is where today's installers put it, not a Letterbox promise. A helper that
  doesn't report a supported version is refused. The edition name, read from the install folder,
  is only shown to the person. It never chooses an adapter.
- The person chooses which agents the Desk may write to, from folders with both `inbox/` and
  `processed/` (names up to 32 characters, never `desk`). Pane registries are not a roster.

**The desk seat**
- The Desk creates `<root>/desk/inbox` and `<root>/desk/processed` itself, with mode 700. It does not
  run `letterbox init` and doesn't create `locks`.

**Reading**
- The Desk reads at most 8 KB of a letter's header and uses `id`, `sent`, `from`, `to`, `type`, `re` and
  `priority`. A letter counts only if `id` matches its file name and `to` matches its mailbox.
  Dot-files are ignored. Symlinks are not followed.
- `priority: now` makes a new letter eligible to ring the phone. A ring also needs the phone side
  switched on, a paired phone, and a letter that arrived after the listener started. It is never
  a sign that anyone read the letter.

**Sending**
- The Desk sends as `desk`, always with type `info`, slug `desk-dictation` and `--now`, and with
  `--re` for a reply. It uses only the id at the end of the `sent:` line. With no `sent:` line, the
  letter counts as not confirmed and is never resent on its own.
- The helper is pinned by sha256 at setup. If it changes, nothing is sent.
- Doorbell settings are read from `env.sh` as text and never run. Only single-quoted
  `LETTERBOX_DOORBELL`, `LETTERBOX_<EDITION>_REGISTRY`, `_PATTERNS` and `_SUBMIT` are kept, and the
  adapter has to be a file inside the helper's own install. The adapter is pinned by sha256. If it
  changes, the Desk stops passing the doorbell settings, so letters still go but don't ring.

**What the Desk writes**
- In the letterbox: only the letters it sends through the helper, plus the desk mailbox at setup. It never
  moves, edits or deletes another letter. Opening a letter doesn't file it.
- Its own state is in `~/.bell-desk/` (mode 700):
  - `config.json` holds the folder, the agents, the pins and the phone switch;
  - `read.json` holds what was opened;
  - `desk-view.json` holds conversation, Done, Keep, Look and ring preferences;
  - `requests.jsonl` holds each send and its outcome per agent;
  - `devices.json` and `server_key` are only used with the phone on;
  - `listener.lock` (one listener per state folder) holds its pid.

**Not relied on**
- A team-health endpoint or command. The public helper does not supply one; the menu reports
  team status as unavailable.
- `status.md`, self-reported agent state, pane registries, cmux titles, folder times.
- A letter's body, beyond showing it to the person as plain text.

**Letterbox Bell**
- The phone ⇄ Mac protocol (pairing, signed requests, events and answers) is in `PROTOCOL.md`. It
  belongs to the Desk and Letterbox Bell.

**Desk conversations and pictures (Mac-local convention)**
- Conversation, Done, Keep, Look and ring preferences live in Desk-local `desk-view.json`.
  Done and Keep never move, ACK or delete Letterbox files. The ring switch suppresses new mail
  events to the phone; it does not disconnect a paired phone or disable dictation.
- Pictures are Desk-owned copies outside the Letterbox root. `bell-desk attachments setup
  [absolute-folder]` configures a dedicated private directory and sends read probes to the roster.
  Without an override, it resolves the Letterbox root and uses its parent's `desk-attachments`.
  It prints and persists the canonical path. A new folder is created with mode 700; unsafe
  symlinks, collisions, non-private/non-owned directories, unwritable parents, and destinations
  inside the root are refused. Existing verified access and valid pending probes are left alone.
  Builds do not create the store. The installer asks once before setup; without a terminal it
  skips the question and prints `bell-desk attachments setup`. No Part A guarantee is added.
- Each seat must read its unique random probe in its real sandbox and reply with only its
  SHA256. The listener automatically checks replies in desk's inbox/processed folders, including
  after restart. It requires the exact sender, destination, request link and digest, a matching
  canonical folder, and unchanged regular probe bytes. New pending probes expire after 24 hours;
  consumed, unsolicited, stale and replayed proofs cannot grant access. Sender is a claim on the
  trusted local bus, not authenticated identity. Each accepted seat gets one local log line.
  Reconfiguring a folder or re-probing a seat invalidates its previous verification; a failed or
  expired request leaves pictures disabled for that seat. This is point-in-time read evidence,
  never a permission grant. No sandbox or shared-parent permissions are changed.
- Manual `attachments configure`, `verify` and `accept <seat> --reply <reply-id>` remain available;
  manual acceptance uses the same proof checks. `attachments status` retains its per-seat output.
  A blocked send offers text-only sending and an `Ask <seat> to set up pictures` action to send a
  fresh probe. Neither verification nor a reply automatically sends the retained picture draft.
- File paste/drop captures at most 10 MB immediately, so temporary screenshot files may disappear
  before Send. A selected file must be regular and not a symlink; symlinked parent directories
  are allowed, and the opened resolved file must match its lstat device/inode. At Send, PNG/JPEG
  data is limited to 10 MB and 16 million decoded pixels. ImageIO via `sips` validates and decodes
  in temporary private storage. Stored copies are hashed before any letter references them.
  No image is uploaded or opened automatically.
- The last body line is `Desk picture: <JSON>` containing absolute path, MIME type, byte size and
  SHA256; JSON escaping prevents newline injection. This is body text, never new envelope fields.
  Outgoing copies shown by Desk are checked for size/hash and labelled missing or changed when
  appropriate. Incoming paths are displayed as text and never opened automatically.
- Readers must verify size and SHA256 before using the image. Copies and probes may be orphaned
  by failed/unknown sends. They are retained for explicit operator cleanup, never removed by
  Done, Keep or hiding a conversation. No global permission or sandbox policy is widened.
