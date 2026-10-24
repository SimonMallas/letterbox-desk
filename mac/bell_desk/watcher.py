"""Ring-eligible mail tap: a new letter with `priority: now` in any inbox.

A durable-mail tap, not proof a doorbell fired. Only envelope header fields
leave this module; never a path or a body.
"""

import os
import re
import stat
import threading

from .server import ENVELOPE_ID_RE

HEADER_MAX = 32768  # as far as the Letterbox's own query reads a header
SEAT_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
TYPE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
TS_RE = re.compile(r"^[0-9A-Za-z:.+-]{1,40}$")
KEY_RE = re.compile(r"^[a-z_]{1,32}$")
MAX_PARSE_TRIES = 5


def read_header(path: str):
    """Flat `key: value` fields of the first frontmatter block, or None if unreadable or unfenced."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        head = os.read(fd, HEADER_MAX)
    finally:
        os.close(fd)
    lines = head.decode("utf-8", errors="replace").split("\n")
    if not lines or lines[0].rstrip("\r") != "---":
        return None
    fields = {}
    for line in lines[1:]:
        line = line.rstrip("\r")
        if line == "---":
            return fields
        key, sep, value = line.partition(":")
        key = key.strip()
        if sep and KEY_RE.match(key) and key not in fields:
            fields[key] = value.strip()
    return None


LETTER_MAX = 20000
BIDI_MARKS = set("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def read_letter_text(path: str) -> str:
    """The body of a letter after its frontmatter, bounded, for showing on this Mac only.
    A letter too long to show whole ends with a line saying so and naming its file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return ""
        raw = os.read(fd, HEADER_MAX + 4 * LETTER_MAX)  # LETTER_MAX characters in any script
    finally:
        os.close(fd)
    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[0].rstrip("\r") == "---":
        for i, line in enumerate(lines[1:], start=1):
            if line.rstrip("\r") == "---":
                text = "\n".join(lines[i + 1:])
                break
    # Control characters (Unicode Cc: below 32, and 127 to 159) and direction marks go, as the sender refuses them.
    text = "".join(ch for ch in text if (ch in "\n\t" or 32 <= ord(ch) < 127 or ord(ch) > 159) and ch not in BIDI_MARKS).strip()
    if len(raw) < info.st_size or len(text) > LETTER_MAX:
        return (text[:LETTER_MAX].rstrip("\ufffd")
                + f"\n\n[This letter is too long to show here in full. Read all of it in its file: {path}]")
    return text


def mail_event(seat: str, name: str, fields: dict):
    """The event for a ring-eligible letter, or None. Headers must agree with where the file sits."""
    stem = name[:-3]
    sender = fields.get("from", "")
    letter_type = fields.get("type", "")
    sent = fields.get("sent", "")
    if fields.get("priority") != "now":
        return None
    if fields.get("to") != seat or fields.get("id") != stem or not ENVELOPE_ID_RE.match(stem):
        return None
    if sender == "desk" or not SEAT_RE.match(sender):
        return None
    return {
        "to": seat,
        "from": sender,
        "letter_type": letter_type if TYPE_RE.match(letter_type) else "",
        "id": stem,
        "priority": "now",
        "ts": sent if TS_RE.match(sent) else "",
        "for_you": seat == "desk",
    }


class InboxWatcher:
    """Polls every inbox, the phone's own (desk) included, so an agent writing to the person rings.
    Existing letters at start, and in seats that appear later, never ring."""

    def __init__(self, bus_dir: str, on_mail, interval: float = 1.0, exclude=(), log=None, on_letter_for_person=None):
        self.bus_dir = bus_dir
        self.on_mail = on_mail
        self.on_letter_for_person = on_letter_for_person
        self.interval = interval
        self.exclude = set(exclude)
        self.log = log or (lambda message: None)
        self.known = {}
        self.tries = {}
        self._stop = threading.Event()
        self._thread = None

    def _seats(self):
        try:
            entries = list(os.scandir(self.bus_dir))
        except OSError:
            return
        for entry in entries:
            if entry.name in self.exclude or not SEAT_RE.match(entry.name):
                continue
            if not entry.is_dir(follow_symlinks=False):
                continue
            inbox = os.path.join(entry.path, "inbox")
            if os.path.isdir(inbox) and not os.path.islink(inbox):
                yield entry.name, entry.path

    @staticmethod
    def _letters(seat_dir: str) -> dict:
        """Letter name -> folder, across inbox and processed, so a letter filed between two scans still counts."""
        found = {}
        for sub in ("processed", "inbox"):
            folder = os.path.join(seat_dir, sub)
            if os.path.islink(folder):
                continue
            try:
                for entry in os.scandir(folder):
                    if entry.name.endswith(".md") and not entry.name.startswith("."):
                        found[entry.name] = folder
            except OSError:
                continue
        return found

    def baseline(self):
        self.known = {seat: set(self._letters(seat_dir)) for seat, seat_dir in self._seats()}

    def scan_once(self):
        for seat, seat_dir in self._seats():
            letters = self._letters(seat_dir)
            current = set(letters)
            if seat not in self.known:
                self.known[seat] = current
                continue
            seen = self.known[seat]
            for name in sorted(current - seen):
                fields = read_header(os.path.join(letters[name], name))
                key = (seat, name)
                if fields is None:
                    tries = self.tries.get(key, 0) + 1
                    if tries < MAX_PARSE_TRIES:
                        self.tries[key] = tries
                        continue
                self.tries.pop(key, None)
                seen.add(name)
                event = mail_event(seat, name, fields) if fields else None
                if event:
                    self.on_mail(event)
                    if event["for_you"] and self.on_letter_for_person:
                        self.on_letter_for_person(event, read_letter_text(os.path.join(letters[name], name)))
            seen &= current

    def _run(self):
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as exc:  # the watcher must outlive a bad scan
                self.log(f"watcher: scan failed: {type(exc).__name__}")
            self._stop.wait(self.interval)

    def start(self):
        self.baseline()
        self._thread = threading.Thread(target=self._run, name="inbox-watcher", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
