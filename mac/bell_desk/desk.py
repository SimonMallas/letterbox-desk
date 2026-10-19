"""Letterbox Desk data for the window on this Mac: the person's own letters (to `desk`), what they
have opened, and the Letterbox's read-only team check. Nothing here goes to the phone."""

import json
import os
import re
import threading
import time

from .server import ENVELOPE_ID_RE
from .watcher import SEAT_RE, TS_RE, TYPE_RE, read_header, read_letter_text

ID_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})(\d{2})-")
STATUS_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z_-]{0,31}$")
TEAM_RE = re.compile(r"^\s*([a-z][a-z0-9-]{0,31}): queue\[([^\]]*)\].*?\bstatus=(\S+)", re.M)
PREVIEW = 160
INBOX_MAX = 200
KEEP_READ = 2000


def letter_time(fields: dict, letter_id: str) -> str:
    """The `sent` header if well formed, else the UTC time the helper puts at the front of the id."""
    sent = fields.get("sent", "")
    if TS_RE.match(sent):
        return sent
    match = ID_TIME_RE.match(letter_id)
    return f"{match.group(1)}T{match.group(2)}:{match.group(3)}:{match.group(4)}Z" if match else ""


def parse_team(text: str, seats) -> list:
    """Per seat: requests awaiting ACK, accepted, and the seat's own reported status only if it is from today.
    Registration and folder activity are left out on purpose: neither shows an agent is present."""
    found = {}
    for seat, queue, status in TEAM_RE.findall(text):
        counts = dict(pair.split("=", 1) for pair in queue.split() if "=" in pair)
        state, _, age = status.partition(":")
        found[seat] = {
            "seat": seat,
            "unacked": int(counts["unacked"]) if counts.get("unacked", "").isdigit() else None,
            "accepted": int(counts["accepted"]) if counts.get("accepted", "").isdigit() else None,
            "reported": state.lower().replace("_", " ") if age == "0d" and STATUS_WORD_RE.match(state) else "",
            "has_status": state.lower() not in ("", "missing"),
        }
    return [found[seat] for seat in seats if seat in found]


class DeskMail:
    def __init__(self, bus_dir: str, state, seats):
        self.seat_dir = os.path.join(bus_dir, "desk")
        self.state = state
        self.seats = list(seats)
        self.lock = threading.RLock()
        self.cache = {}
        self.read = state.read_ids()

    def _files(self) -> dict:
        found = {}
        if os.path.islink(self.seat_dir):
            return found
        for sub in ("processed", "inbox"):
            folder = os.path.join(self.seat_dir, sub)
            if os.path.islink(folder):
                continue
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for entry in entries:
                if entry.name.endswith(".md") and not entry.name.startswith("."):
                    found[entry.name] = entry.path
        return found

    def _record(self, name: str, path: str):
        """Header fields and a short preview, re-read only when the file changes. None if it isn't a letter to desk."""
        try:
            info = os.stat(path, follow_symlinks=False)
        except OSError:
            return None
        signature = (path, info.st_mtime_ns, info.st_size)
        cached = self.cache.get(name)
        if cached and cached[0] == signature:
            return cached[1]
        letter_id = name[:-3]
        fields = read_header(path)
        record = None
        if (fields and fields.get("to") == "desk" and fields.get("id") == letter_id
                and ENVELOPE_ID_RE.match(letter_id) and SEAT_RE.match(fields.get("from", ""))):
            letter_type = fields.get("type", "")
            reply_to = fields.get("re", "")
            record = {
                "id": letter_id,
                "from": fields["from"],
                "type": letter_type if TYPE_RE.match(letter_type) else "",
                "ts": letter_time(fields, letter_id),
                "re": reply_to if ENVELOPE_ID_RE.match(reply_to) else "",
                "preview": " ".join(read_letter_text(path).split())[:PREVIEW],
            }
        self.cache[name] = (signature, record)
        return record

    def _decorate(self, record: dict) -> dict:
        return {**record, "read": record["id"] in self.read, "can_reply": record["from"] in self.seats}

    def inbox(self) -> list:
        with self.lock:
            files = self._files()
            self.cache = {name: value for name, value in self.cache.items() if name in files}
            letters = [self._record(name, files[name]) for name in sorted(files, reverse=True)]
            return [self._decorate(r) for r in letters if r][:INBOX_MAX]

    def letter(self, letter_id: str):
        """One letter to desk by id, header fields only, or None."""
        if not isinstance(letter_id, str) or not ENVELOPE_ID_RE.match(letter_id):
            return None
        with self.lock:
            path = self._files().get(letter_id + ".md")
            record = self._record(letter_id + ".md", path) if path else None
            return self._decorate(record) if record else None

    def open(self, letter_id: str):
        """The full letter for reading on this Mac; opening only marks it read here, the letter is not moved."""
        if not isinstance(letter_id, str) or not ENVELOPE_ID_RE.match(letter_id):
            return None
        with self.lock:
            path = self._files().get(letter_id + ".md")
            record = self._record(letter_id + ".md", path) if path else None
            if not record:
                return None
            text = read_letter_text(path)
            self.mark_read(letter_id)
            return {**self._decorate(record), "text": text}

    def mark_read(self, letter_id: str):
        with self.lock:
            if letter_id in self.read:
                return
            self.read = (self.read + [letter_id])[-KEEP_READ:]
            self.state.save_read_ids(self.read)


class DeskBridge:
    """Answers the Desk window over the menu-bar pipe. Sends run on their own thread, so opening a
    letter never waits behind a slow helper; the server still sends one at a time."""

    TEAM_MIN_GAP = 30

    def __init__(self, app, mail: DeskMail, send):
        self.app = app
        self.mail = mail
        self.send = send
        self.lock = threading.Lock()
        self.team_lock = threading.Lock()
        self.last = None
        self.last_conversations = None
        from .conversations import Conversations
        self.conversations = Conversations(app, mail)
        self.team = {"ok": False, "checked": 0, "tried": 0, "seats": []}

    def handle(self, message: dict):
        if isinstance(message.get("conversation_send"), dict):
            draft = message["conversation_send"]
            self._background("conversation", draft.get("agent") or "", lambda: self.conversations.send(draft))
        elif isinstance(message.get("picture_setup"), dict):
            seat = message["picture_setup"].get("seat")
            if seat not in self.app.seats:
                return
            def ask():
                from . import attachments
                try:
                    attachments.command(self.app.state, "request", seat)
                    return {"status": "sent"}
                except (OSError, ValueError, KeyError):
                    return {"status": "not_sent", "reason": "picture_setup_failed"}
            self._background("picture_setup", seat, ask)
        elif isinstance(message.get("conversation"), dict):
            self.conversations.update(message["conversation"])
            self.push(force=True)
        elif isinstance(message.get("compose"), dict):
            compose = message["compose"]
            self._background("compose", "", lambda: self.app.compose_from_mac(compose.get("to"), compose.get("text")))
        elif isinstance(message.get("reply"), dict):
            reply = message["reply"]
            letter = self.mail.letter(reply.get("id"))
            ref = letter["id"] if letter else ""
            if letter is None:
                self._result("reply", ref, {"status": "not_sent", "reason": "unknown_letter"})
            else:
                self._background("reply", ref, lambda: self.app.reply_from_mac(letter, reply.get("text")))
        elif isinstance(message.get("resend"), dict):
            resend = message["resend"]
            request_id, seat = resend.get("request_id"), resend.get("seat")
            ref = f"{request_id}/{seat}" if isinstance(request_id, str) and isinstance(seat, str) else ""
            self._background("resend", ref, lambda: self.app.resend_from_mac(request_id, seat))
        elif "open" in message:
            letter = self.mail.open(message["open"])
            self.send({"type": "letter", "id": letter["id"] if letter else "", "letter": letter})
            self.push()
        elif message.get("refresh"):
            self.push(force=True)
            self.send({"type": "team", **self.team})
        elif message.get("team"):
            if time.time() - self.team["tried"] >= self.TEAM_MIN_GAP:
                threading.Thread(target=self.refresh_team, name="desk-team", daemon=True).start()

    def _result(self, kind: str, ref: str, summary: dict):
        self.push()
        self.send({"type": "result", "kind": kind, "ref": ref, "summary": summary})

    def _background(self, kind: str, ref: str, work):
        threading.Thread(target=lambda: self._result(kind, ref, work()), name=f"desk-{kind}", daemon=True).start()

    def push(self, force: bool = False):
        """Inbox and Sent to the window, only when something changed."""
        with self.lock:
            snapshot = {"type": "mail", "inbox": self.mail.inbox(), "sent": self.app.state.sent_letters()}
            encoded = json.dumps(snapshot, sort_keys=True)
            conversations = self.conversations.snapshot()
            projected = json.dumps(conversations, sort_keys=True)
            if force or projected != self.last_conversations:
                self.last_conversations = projected
                self.send(conversations)
            if force or encoded != self.last:
                self.last = encoded
                self.send(snapshot)

    def refresh_team(self):
        """One bounded run of the Letterbox's own team check. A failure keeps the last good result and its time."""
        if not self.team_lock.acquire(blocking=False):
            return
        try:
            now = int(time.time())
            text = self.app.helper.team()
            if text is None:
                self.team = {**self.team, "ok": False, "tried": now}
            else:
                self.team = {"ok": True, "checked": now, "tried": now, "seats": parse_team(text, self.app.seats)}
            self.send({"type": "team", **self.team})
        finally:
            self.team_lock.release()
