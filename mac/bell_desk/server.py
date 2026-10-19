"""The phone-facing HTTP service. PROTOCOL.md is the contract."""

import hmac
import json
import os
import queue
import re
import secrets
import socket
import socketserver
import threading
import time
import unicodedata
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import crypto
from .store import summarize

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
PAIR_ID_RE = re.compile(r"^[0-9a-f]{32}$")
PAIR_NONCE_RE = re.compile(r"^/v1/pair/([0-9a-f]{32})/nonce$")
ENVELOPE_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._:-]{0,127}$")
BIDI_CONTROLS = set("‪‫‬‭‮⁦⁧⁨⁩")
MAX_BODY = 16 * 1024
MAX_TEXT = 2000
CLOCK_SKEW = 120
NONCE_TTL = 600
PAIR_WINDOW = 120
MAX_PENDING_PAIRS = 1
PAIR_COOLDOWN = 30
NEVER_RAN = {"helper_changed", "helper_unavailable"}


def clean_text(text):
    if not isinstance(text, str):
        return None, "bad_json"
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").strip()
    if not text:
        return None, "text_empty"
    if len(text) > MAX_TEXT:
        return None, "text_too_long"
    for ch in text:
        if ch in BIDI_CONTROLS or (unicodedata.category(ch) == "Cc" and ch not in "\n\t"):
            return None, "text_control_chars"
    return text, None


def clean_name(name) -> str:
    if not isinstance(name, str):
        return "phone"
    kept = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    return kept[:40] or "phone"


class App:
    def __init__(self, state, helper, bus_dir: str, seats, console=print, ping_interval: float = 15.0,
                 ask_for_code=None, tell=None, paired=None, wait_for_lock: bool = False, phone: bool = True):
        self.state = state
        self.helper = helper
        self.bus_dir = bus_dir
        self.seats = list(seats)
        self.console = console
        self.ping_interval = ping_interval
        self.ask_for_code = ask_for_code
        self.tell = tell
        self.paired = paired
        state.lock_instance(wait=wait_for_lock,
                            waiting=lambda: console("another listener is running here; waiting for it to stop"))
        self.server_key = state.server_key() if phone else None
        self.server_pub = crypto.public_bytes(self.server_key) if phone else None
        self.phone_active = phone
        self.name = socket.gethostname().removesuffix(".local")
        self.lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.stopping = threading.Event()
        self.cooldown_until = 0.0
        self.pairings = {}
        self.subscribers = {}
        self.last_mail = None
        self.nonces = {}
        self.resending = set()

    def log(self, message: str):
        self.state.log(message)
        self.console(message)

    def hello(self) -> dict:
        return {
            "server": "bell-desk",
            "version": "1",
            "name": self.name,
            "pairing_open": self.pairing_open(),
            "server_pub": crypto.b64e(self.server_pub),
        }

    def open_pairing(self):
        """Lift the pause that follows a failed attempt."""
        self.cooldown_until = 0.0
        self.console("[pair] pairing is open: open Bell on the phone")

    def pairing_open(self) -> bool:
        """Always open, except for a short pause after a refused attempt, so a stranger can't keep popping dialogs."""
        return time.time() >= self.cooldown_until

    def pair_request(self, device_pub: bytes, name: str):
        with self.lock:
            if not self.phone_active:
                return None
            now = time.time()
            pending = [p for p in self.pairings.values() if p["state"] == "pending" and now - p["created"] < PAIR_WINDOW]
            if len(pending) >= MAX_PENDING_PAIRS:
                return None
            pair_id = secrets.token_hex(16)
            server_nonce = secrets.token_bytes(crypto.NONCE_LEN)
            self.pairings[pair_id] = {
                "device_pub": device_pub, "name": name, "server_nonce": server_nonce,
                "device_nonce": None, "code": None,
                "state": "pending", "created": now, "device_id": None,
            }
        self.console(f"[pair] request {pair_id[:6]} from '{name}': waiting for the phone")
        return pair_id, crypto.commitment(self.server_pub, device_pub, server_nonce)

    def pair_nonce(self, pair_id: str, device_nonce: bytes):
        """Reveal the server nonce once, after the phone has fixed its own."""
        with self.lock:
            if not self.phone_active:
                return 503, {"error": "phone_off"}
            pairing = self.pairings.get(pair_id)
            if pairing is None or pairing["state"] != "pending" or time.time() - pairing["created"] >= PAIR_WINDOW:
                return 404, {"error": "unknown_pairing"}
            if pairing["device_nonce"] is not None:
                return 409, {"error": "nonce_already_set"}
            pairing["device_nonce"] = device_nonce
            code = crypto.pairing_code(self.server_pub, pairing["device_pub"], device_nonce, pairing["server_nonce"])
            pairing["code"] = code
            server_nonce = pairing["server_nonce"]
        name = pairing["name"]
        if self.ask_for_code:
            self.console(f"[pair] {pair_id[:6]} '{name}': type the phone's code into the pop-up on this Mac")
            threading.Thread(target=self._ask, args=(pair_id, name, code), name="pair-prompt", daemon=True).start()
        else:
            self.console(f"[pair] {pair_id[:6]} '{name}': code {code[:3]} {code[3:]}")
            self.console("[pair] if the phone shows the same code, type: approve   (otherwise: deny)")
        return 200, {"server_nonce": crypto.b64e(server_nonce)}

    def _ask(self, pair_id: str, name: str, code: str):
        digits = re.sub(r"\D", "", self.ask_for_code(name) or "")
        if digits and hmac.compare_digest(digits, code):
            outcome = self._approve(pair_id)
            self.console(outcome)
            if outcome.startswith("approved") and self.paired:
                self.paired(name)
            return
        refused = self._deny(pair_id, "the typed code did not match" if digits else "cancelled on the Mac")
        if refused and digits and self.tell:
            self.tell(name)

    def pair_status(self, pair_id: str):
        with self.lock:
            pairing = self.pairings.get(pair_id)
            if pairing is None:
                return None
            if pairing["state"] == "pending" and time.time() - pairing["created"] >= PAIR_WINDOW:
                pairing["state"] = "expired"
            status = {"state": pairing["state"]}
            if pairing["device_id"]:
                status["device_id"] = pairing["device_id"]
            return status

    def decide(self, prefix: str, approve: bool) -> str:
        """The listener-pane fallback: a person compares both screens and answers here."""
        with self.lock:
            now = time.time()
            pending = [pid for pid, p in self.pairings.items()
                       if p["state"] == "pending" and now - p["created"] < PAIR_WINDOW and pid.startswith(prefix)]
        if len(pending) != 1:
            return "no single pending request matches; type 'approve <id>' with the id shown"
        if not approve:
            self._deny(pending[0], "denied in the listener")
            return f"denied {pending[0][:6]}"
        return self._approve(pending[0])

    def _approve(self, pair_id: str) -> str:
        with self.lock:
            if not self.phone_active:
                return f"{pair_id[:6]} is no longer waiting"
            pairing = self.pairings.get(pair_id)
            now = time.time()
            if pairing is None or pairing["state"] != "pending" or now - pairing["created"] >= PAIR_WINDOW:
                return f"{pair_id[:6]} is no longer waiting"
            if pairing["device_nonce"] is None:
                return f"{pair_id[:6]} has no code yet: wait for the phone to show one"
            device_pub = pairing["device_pub"]
            key = crypto.derive_key(self.server_key, device_pub, self.server_pub, device_pub,
                                    pairing["device_nonce"], pairing["server_nonce"])
            device_id = crypto.device_id(device_pub)
            self.state.add_device(device_id, {
                "name": pairing["name"],
                "device_pub": crypto.b64e(device_pub),
                "key": crypto.b64e(key),
                "paired_at": int(now),
            })
            pairing["state"] = "approved"
            pairing["device_id"] = device_id
        self.state.log(f"pair approved {device_id} '{pairing['name']}'")
        return f"approved: '{pairing['name']}' is device {device_id}"

    def _deny(self, pair_id: str, why: str) -> bool:
        with self.lock:
            pairing = self.pairings.get(pair_id)
            if pairing is None or pairing["state"] != "pending":
                return False
            pairing["state"] = "denied"
            self.cooldown_until = time.time() + PAIR_COOLDOWN
        self.console(f"[pair] {pair_id[:6]} refused: {why}")
        self.state.log(f"pair refused {pair_id[:6]} '{pairing['name']}': {why}")
        return True

    @staticmethod
    def _close(q):
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                break
        try:
            q.put_nowait(None)
        except queue.Full:
            pass

    def device_active(self, device_id: str) -> bool:
        return device_id in self.state.devices()

    def unpair(self, device_id: str) -> bool:
        if not self.state.remove_device(device_id):
            return False
        with self.lock:
            revoked = [q for q, owner in self.subscribers.items() if owner == device_id]
            for q in revoked:
                del self.subscribers[q]
        for q in revoked:
            self._close(q)
        self.state.log(f"unpaired {device_id}")
        return True

    def authenticate(self, headers, method: str, path: str, body: bytes):
        device_id = headers.get("X-Belldesk-Device", "")
        ts = headers.get("X-Belldesk-Time", "")
        nonce = headers.get("X-Belldesk-Nonce", "")
        sig = headers.get("X-Belldesk-Sig", "")
        device = self.state.devices().get(device_id)
        if not device or not ts.isdigit() or not NONCE_RE.match(nonce):
            return None
        now = time.time()
        if abs(now - int(ts)) > CLOCK_SKEW:
            return None
        key = crypto.b64d(device["key"])
        if not crypto.verify_request(key, sig, method, path, int(ts), nonce, body):
            return None
        with self.lock:
            seen = self.nonces.setdefault(device_id, {})
            for old in [n for n, at in seen.items() if now - at > NONCE_TTL]:
                del seen[old]
            if nonce in seen:
                return None
            seen[nonce] = now
        return {"device_id": device_id, "key": key, "nonce": nonce}

    def subscribe(self, q, device_id: str):
        with self.lock:
            self.subscribers[q] = device_id

    def unsubscribe(self, q):
        with self.lock:
            self.subscribers.pop(q, None)

    def publish_mail(self, mail: dict):
        if not self.phone_active or not self.state.desk_view().get("rings", True):
            return
        event = {"type": "mail", **mail}
        with self.lock:
            self.last_mail = event
            targets = list(self.subscribers)
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass
        self.log(f"mail {mail['from']} -> {mail['to']} {mail['letter_type']} {mail['id']} (phones: {len(targets)})")

    def close_phone_streams(self):
        with self.lock:
            open_streams = list(self.subscribers)
            self.subscribers.clear()
            for pairing in self.pairings.values():
                if pairing["state"] == "pending":
                    pairing["state"] = "expired"
        for q in open_streams:
            self._close(q)

    def stop(self):
        """Refuse new sends, let one already running finish and be recorded, then release the state lock."""
        self.stopping.set()
        self.close_phone_streams()
        with self.send_lock:
            self.state.unlock_instance()

    def inbox_ok(self, seat: str) -> bool:
        seat_dir = os.path.join(self.bus_dir, seat)
        inbox = os.path.join(seat_dir, "inbox")
        return (os.path.isdir(inbox) and not os.path.islink(seat_dir) and not os.path.islink(inbox))

    def _from_mac(self, to, text, re_id: str = "", reply_seat=None, retry_of="", blocked_seats=(), picture_copy=False) -> dict:
        body = json.dumps({"request_id": str(uuid.uuid4()), "to": to, "text": text}).encode()
        code, result = self.dictation(body, re_id=re_id, via="mac", reply_seat=reply_seat, retry_of=retry_of, blocked_seats=blocked_seats, picture_copy=picture_copy)
        if code != 200:
            return {"status": "not_sent", "reason": result.get("reason", result.get("error", "unknown"))}
        return result

    def compose_from_mac(self, to, text) -> dict:
        """A letter the person wrote on the Mac: same validated, recorded, never-retried path as the phone."""
        return self._from_mac(to, text)

    def resend_from_mac(self, request_id, seat) -> dict:
        """The recorded text again, as a new letter to one seat the first send did not reach for sure.
        Never to a seat it reached; the window asks the person first when the first copy may be there."""
        if not isinstance(request_id, str) or not isinstance(seat, str):
            return {"status": "not_sent", "reason": "unknown_letter"}
        item = self.state.sent_letter(request_id)
        key = (item.get("retry_of") if item else "") or f"{request_id}/{seat}"
        with self.lock:
            if key in self.resending:
                return {"status": "not_sent", "reason": "still_sending"}
            self.resending.add(key)
        try:
            return self._resend(request_id, seat)
        finally:
            with self.lock:
                self.resending.discard(key)

    def _resend(self, request_id: str, seat: str) -> dict:
        item = self.state.sent_letter(request_id)
        if item is None or not isinstance(item.get("text"), str):
            return {"status": "not_sent", "reason": "unknown_letter"}
        if item["status"] == "sending":
            return {"status": "not_sent", "reason": "still_sending"}
        first = next((r for r in item["results"] if r.get("to") == seat), None)
        if first is None:
            return {"status": "not_sent", "reason": "not_a_recipient"}
        if first.get("status") == "published":
            return {"status": "not_sent", "reason": "already_delivered"}
        if item.get("via") == "mac" and item.get("re") and len(item["results"]) > 1 and ("text" not in first or "re" not in first):
            return {"status": "not_sent", "reason": "copy_plan_missing"}
        key = item.get("retry_of") or f"{request_id}/{seat}"
        if any(r.get("retry_of") == key and any(x.get("to") == seat and x.get("status") == "published" for x in r["results"])
               for r in self.state.sent_letters(limit=None)):
            return {"status": "not_sent", "reason": "already_delivered"}
        recorded_text = first.get("text", item["text"])
        if "\nDesk picture: " in recorded_text:
            from . import attachments
            try:
                picture = json.loads(recorded_text.rsplit("\nDesk picture: ", 1)[1])
                if (not attachments.verified(self.state.config(), self.bus_dir, seat)
                        or attachments.inspect_copy(self.state.config(), self.bus_dir, picture) != "ok"):
                    return {"status": "not_sent", "reason": "picture_access"}
            except (ValueError, TypeError):
                return {"status": "not_sent", "reason": "picture_changed"}
        return self._from_mac([seat], first.get("text", item["text"]), re_id=first.get("re", item.get("re", "")), retry_of=key, picture_copy="\nDesk picture: " in recorded_text)

    def reply_from_mac(self, event: dict, text: str) -> dict:
        """A reply the person typed on the Mac card: one letter as desk to the seat that wrote, same path as the phone."""
        seat = event.get("from", "")
        if seat not in self.seats:
            self.log(f"reply to {seat} refused: not one of the seats")
            return {"status": "not_sent", "reason": "unknown_seat"}
        letter_id = event.get("id", "")
        if not ENVELOPE_ID_RE.match(letter_id):
            self.log("reply refused: the letter id is not a valid envelope id")
            return {"status": "not_sent", "reason": "bad_letter_id"}
        return self._from_mac([seat], text, re_id=letter_id)

    def _seats_from(self, to):
        if isinstance(to, str):
            to = [to]
        if not isinstance(to, list) or not to:
            return None, "no_seats"
        if not all(isinstance(seat, str) for seat in to):
            return None, "unknown_seat"
        if len(set(to)) != len(to):
            return None, "duplicate_seat"
        if any(seat not in self.seats for seat in to):
            return None, "unknown_seat"
        return to, None

    @staticmethod
    def _copy_text(text: str, seat: str, seats: list, picture=False) -> str:
        others = [s for s in seats if s != seat]
        if not others:
            return text
        body, marker, picture = text.rpartition("\nDesk picture: ")
        note = f"Also addressed to: {', '.join(others)} (intended recipients; delivery and reading are not guaranteed)."
        return f"{body}\n\n{note}\nDesk picture: {picture}" if marker and picture else f"{text}\n\n{note}"

    @staticmethod
    def _seat_result(seat: str, outcome: dict) -> dict:
        if outcome["status"] == "sent":
            return {"to": seat, "status": "published", "id": outcome["id"], "ring": outcome["ring"]}
        if outcome.get("reason") in NEVER_RAN:
            return {"to": seat, "status": "not_attempted", "reason": outcome["reason"]}
        return {"to": seat, "status": "not_confirmed", "reason": outcome.get("reason", "unknown")}

    def dictation(self, body: bytes, re_id: str = "", via: str = "phone", reply_seat=None, retry_of="", blocked_seats=(), picture_copy=False):
        try:
            payload = json.loads(body)
        except ValueError:
            return 400, {"error": "bad_request", "reason": "bad_json"}
        if not isinstance(payload, dict):
            return 400, {"error": "bad_request", "reason": "bad_json"}
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not UUID_RE.match(request_id):
            return 400, {"error": "bad_request", "reason": "bad_request_id"}
        with self.send_lock:
            if self.stopping.is_set():
                return 503, {"error": "shutting_down"}
            if via == "phone" and not self.phone_active:
                return 503, {"error": "phone_off"}
            prior = self.state.request_result(request_id)
            if prior is not None:
                return 200, {**prior, "replayed": True}
            seats, problem = self._seats_from(payload.get("to"))
            if problem:
                return 400, {"error": "bad_request", "reason": problem}
            raw = payload.get("text")
            if picture_copy and isinstance(raw, str) and "\nDesk picture: " in raw:
                plain, _, metadata = raw.rpartition("\nDesk picture: ")
                text, problem = clean_text(plain) if plain.strip() else ("", None)
                if not problem:
                    text += "\nDesk picture: " + metadata
            else:
                text, problem = clean_text(raw)
            if problem:
                return 400, {"error": "bad_request", "reason": problem}
            if not all(self.inbox_ok(seat) for seat in seats):
                return 400, {"error": "bad_request", "reason": "inbox_missing"}
            copies = {seat: {"text": text if reply_seat == seat else self._copy_text(text, seat, seats, picture_copy),
                             "re": re_id if reply_seat is None or reply_seat == seat else ""} for seat in seats} if via == "mac" else None
            self.state.request_begin(request_id, seats, text=text, re_id=re_id, via=via, retry_of=retry_of, copies=copies)
            results = []
            stopped = None
            for seat in seats:
                if seat in blocked_seats:
                    result = {"to": seat, "status": "not_attempted", "reason": "picture_access"}
                elif stopped:
                    result = {"to": seat, "status": "not_attempted", "reason": stopped}
                else:
                    self.state.request_seat_start(request_id, seat)
                    copy = text if reply_seat == seat else self._copy_text(text, seat, seats, picture_copy)
                    copy_re = re_id if reply_seat is None or reply_seat == seat else ""
                    outcome = self.helper.send(seat, copy, re_id=copy_re) if copy_re else self.helper.send(seat, copy)
                    result = self._seat_result(seat, outcome)
                    if result["status"] == "not_attempted":
                        stopped = result["reason"]
                if via == "mac":
                    result.update({"re": re_id if reply_seat is None or reply_seat == seat else "",
                                   "text": text if reply_seat == seat else self._copy_text(text, seat, seats, picture_copy)})
                self.state.request_seat_done(request_id, seat, result)
                results.append(result)
            summary = summarize(request_id, results)
            self.state.request_finish(request_id, summary)
        for result in results:
            ring = result.get("ring", {})
            self.log(f"dictation {request_id} desk -> {result['to']}: {result['status']} "
                     f"{result.get('id', result.get('reason', ''))} {ring.get('outcome', '')} {ring.get('reason', '')}".rstrip())
        return 200, summary


class Handler(BaseHTTPRequestHandler):
    server_version = "bell-desk/1"
    sys_version = ""
    timeout = 30

    def log_message(self, fmt, *args):
        return

    @property
    def app(self) -> App:
        return self.server.app

    def _json(self, code: int, obj: dict, auth: dict = None):
        """An answer to a request that passed authentication is signed for that request; the phone believes no other."""
        data = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if auth:
            self.send_header("X-Belldesk-Response-Sig",
                             crypto.sign_response(auth["key"], self.command, self.path, auth["nonce"], code, data))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = self.headers.get("Content-Length", "0")
        if not length.isdigit() or int(length) > MAX_BODY:
            return None
        return self.rfile.read(int(length)) if int(length) else b""

    def do_GET(self):
        if not self.app.phone_active:
            return self._json(503, {"error": "phone_off"})
        path = self.path
        if path == "/v1/hello":
            return self._json(200, self.app.hello())
        if path.startswith("/v1/pair/"):
            pair_id = path[len("/v1/pair/"):]
            status = self.app.pair_status(pair_id) if PAIR_ID_RE.match(pair_id) else None
            return self._json(200, status) if status else self._json(404, {"error": "unknown_pairing"})
        if path == "/v1/events":
            auth = self.app.authenticate(self.headers, "GET", path, b"")
            if not auth:
                return self._json(401, {"error": "unauthorized"})
            return self._events(auth)
        if path.startswith("/v1/dictation/"):
            auth = self.app.authenticate(self.headers, "GET", path, b"")
            if not auth:
                return self._json(401, {"error": "unauthorized"})
            request_id = path[len("/v1/dictation/"):]
            result = self.app.state.request_result(request_id) if UUID_RE.match(request_id) else None
            return self._json(200, result, auth) if result else self._json(404, {"error": "unknown_request"}, auth)
        return self._json(404, {"error": "not_found"})

    def do_POST(self):
        if not self.app.phone_active:
            return self._json(503, {"error": "phone_off"})
        body = self._body()
        if body is None:
            return self._json(413, {"error": "too_large"})
        if self.path == "/v1/pair":
            return self._pair(body)
        nonce_route = PAIR_NONCE_RE.match(self.path)
        if nonce_route:
            try:
                device_nonce = crypto.b64d(json.loads(body)["device_nonce"])
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": "bad_request", "reason": "bad_json"})
            if len(device_nonce) != crypto.NONCE_LEN:
                return self._json(400, {"error": "bad_request", "reason": "bad_nonce"})
            return self._json(*self.app.pair_nonce(nonce_route.group(1), device_nonce))
        if self.path == "/v1/dictation":
            auth = self.app.authenticate(self.headers, "POST", self.path, body)
            if not auth:
                return self._json(401, {"error": "unauthorized"})
            code, result = self.app.dictation(body)
            return self._json(code, result, auth)
        return self._json(404, {"error": "not_found"})

    def _pair(self, body: bytes):
        try:
            payload = json.loads(body)
            device_pub = crypto.b64d(payload["device_pub"])
        except (ValueError, KeyError, TypeError):
            return self._json(400, {"error": "bad_request", "reason": "bad_json"})
        if len(device_pub) != 32 or not crypto.usable_peer(self.app.server_key, device_pub):
            return self._json(400, {"error": "bad_request", "reason": "bad_key"})
        if not self.app.pairing_open():
            return self._json(403, {"error": "pairing_closed"})
        started = self.app.pair_request(device_pub, clean_name(payload.get("device_name")))
        if started is None:
            return self._json(429, {"error": "too_many_pairings"})
        pair_id, commitment = started
        return self._json(200, {"pair_id": pair_id, "server_pub": crypto.b64e(self.app.server_pub),
                                "commitment": crypto.b64e(commitment)})

    def _events(self, auth: dict):
        q = queue.Queue(maxsize=64)
        self.app.subscribe(q, auth["device_id"])
        seq = 0

        def emit(event: dict):
            nonlocal seq
            data = json.dumps({**event, "stream": auth["nonce"], "seq": seq}, separators=(",", ":"), ensure_ascii=False).encode()
            seq += 1
            self.wfile.write(b"data: " + crypto.sign_event(auth["key"], data).encode() + b" " + data + b"\n\n")
            self.wfile.flush()

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            emit({"type": "hello", "name": self.app.name, "seats": self.app.seats, "last": self.app.last_mail,
                  "now": int(time.time())})
            while not self.app.stopping.is_set():
                try:
                    event = q.get(timeout=self.app.ping_interval)
                except queue.Empty:
                    event = {"type": "ping", "now": int(time.time())}
                if event is None or not self.app.device_active(auth["device_id"]):
                    break
                emit(event)
        except OSError:
            pass
        finally:
            self.app.unsubscribe(q)


class DeskHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def __init__(self, address, app: App):
        super().__init__(address, Handler)
        self.app = app
