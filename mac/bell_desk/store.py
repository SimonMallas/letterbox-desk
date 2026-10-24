"""Listener state on disk: the server key, paired devices, and the dictation request record."""

import fcntl
import json
import os
import secrets
import threading
import time

from . import crypto

LOG_MAX = 1_000_000  # bytes; the log then moves to bell-desk.log.1, replacing the older one


def summarize(request_id: str, results: list) -> dict:
    """Top-level status from per-seat results; a single seat also carries its fields at the top, as in v1."""
    published = [r for r in results if r["status"] == "published"]
    attempted = [r for r in results if r["status"] != "not_attempted"]
    if results and len(published) == len(results):
        status = "sent"
    elif published:
        status = "partial"
    elif attempted:
        status = "not_confirmed"
    else:
        status = "not_sent"
    summary = {"status": status, "request_id": request_id, "results": results}
    if len(results) == 1:
        for key in ("id", "ring", "reason"):
            if key in results[0]:
                summary[key] = results[0][key]
    return summary


def _write_private(path: str, data: bytes):
    """A whole file only this user can read: written to a new temporary file (never an existing one or a link),
    synced, renamed over the old one, and the folder synced so the rename survives a crash."""
    tmp = f"{path}.tmp-{os.getpid()}-{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except OSError:
        os.unlink(tmp)
        raise
    folder = os.open(os.path.dirname(path), os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


class State:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
        self.lock = threading.RLock()
        self._requests = {}
        self._in_flight = set()
        self._instance_fd = None
        self._load_requests()

    def lock_instance(self, wait: bool = False, waiting=None):
        """One listener per state directory, so request dedupe and the nonce record are never split.
        With wait, block until the other listener stops (calling waiting() once first) instead of refusing."""
        fd = os.open(self.path("listener.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if not wait:
                os.close(fd)
                raise RuntimeError(f"another bell-desk listener is already using {self.root}")
            if waiting:
                waiting()
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
            os.fsync(fd)
        except OSError:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            raise
        self._instance_fd = fd

    def unlock_instance(self):
        if self._instance_fd is not None:
            fcntl.flock(self._instance_fd, fcntl.LOCK_UN)
            os.close(self._instance_fd)
            self._instance_fd = None

    def path(self, name: str) -> str:
        return os.path.join(self.root, name)

    def server_key(self):
        path = self.path("server_key")
        with self.lock:
            if not os.path.exists(path):
                _write_private(path, crypto.private_bytes(crypto.new_private_key()))
            with open(path, "rb") as handle:
                return crypto.load_private_key(handle.read())

    def config(self) -> dict:
        try:
            with open(self.path("config.json")) as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    def save_config(self, config: dict):
        _write_private(self.path("config.json"), (json.dumps(config, indent=2) + "\n").encode())

    def update_config(self, update):
        """Merge against current disk state under a cross-process lock (CLI and listener)."""
        with self.lock:
            fd = os.open(self.path("config.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                current = self.config()
                config = update(current) if callable(update) else {**current, **update}
                self.save_config(config)
            finally:
                os.close(fd)

    def devices(self) -> dict:
        try:
            with open(self.path("devices.json")) as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    def add_device(self, device_id: str, record: dict):
        with self.lock:
            devices = self.devices()
            devices[device_id] = record
            _write_private(self.path("devices.json"), (json.dumps(devices, indent=2) + "\n").encode())

    def remove_device(self, device_id: str) -> bool:
        with self.lock:
            devices = self.devices()
            if devices.pop(device_id, None) is None:
                return False
            _write_private(self.path("devices.json"), (json.dumps(devices, indent=2) + "\n").encode())
            return True

    def read_ids(self) -> list:
        try:
            with open(self.path("read.json")) as handle:
                ids = json.load(handle)
        except (OSError, ValueError):
            return []
        return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []

    def save_read_ids(self, ids: list):
        with self.lock:
            _write_private(self.path("read.json"), (json.dumps(ids) + "\n").encode())

    def _load_requests(self):
        try:
            with open(self.path("requests.jsonl")) as handle:
                for line in handle:
                    try:
                        self._apply(json.loads(line))
                    except (ValueError, KeyError):
                        continue
        except OSError:
            pass

    def _apply(self, row: dict):
        entry = self._requests.setdefault(row["request_id"], {"seats": [], "parts": {}, "final": None})
        state = row.get("state")
        if state == "pending":
            entry["seats"] = row.get("seats") or ([row["to"]] if row.get("to") else [])
            for key in ("at", "text", "re", "via", "retry_of", "copies"):
                if key in row:
                    entry[key] = row[key]
        elif state == "part":
            entry["parts"][row["seat"]] = {"phase": row["phase"], "result": row.get("result")}
        elif state == "final":
            entry["final"] = row["result"]

    def _append_request(self, row: dict):
        fd = os.open(self.path("requests.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            data = memoryview((json.dumps(row) + "\n").encode())
            while data:
                written = os.write(fd, data)
                if written <= 0:
                    raise OSError("request journal write failed")
                data = data[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        with self.lock:
            self._apply(row)

    def request_result(self, request_id: str):
        """The stored outcome. A group cut short reads per seat: started but unrecorded is not
        confirmed (it may have landed); never started is not attempted."""
        entry = self._requests.get(request_id)
        if entry is None:
            return None
        if entry["final"] is not None:
            return entry["final"]
        results = []
        for seat in entry["seats"]:
            part = entry["parts"].get(seat)
            if part and part["phase"] == "final":
                results.append(part["result"])
            elif part:
                results.append({"to": seat, "status": "not_confirmed", "reason": "interrupted"})
            else:
                results.append({"to": seat, "status": "not_attempted", "reason": "interrupted"})
        for result in results:
            plan = (entry.get("copies") or {}).get(result["to"])
            if plan:
                result.update(plan)
        return summarize(request_id, results)

    def sent_letters(self, limit: int = 100) -> list:
        """What the person sent, newest first, with each seat's stored outcome, for the Desk's Sent view."""
        with self.lock:
            items = [self.sent_letter(rid) for rid, entry in self._requests.items() if entry["seats"]]
        order = sorted(range(len(items)), key=lambda i: (items[i]["at"], i), reverse=True)
        return [items[i] for i in order[:limit]]

    def awaiting_choice(self, sent=None) -> set:
        """(request_id, seat) copies the person still has to decide about: the Mac couldn't confirm them or never
        tried, and nothing was sent again for them since. They stay on screen whatever Keep and the Sent limit say.
        sent is sent_letters(limit=None) when the caller already has it."""
        seen, waiting = set(), set()
        for item in self.sent_letters(limit=None) if sent is None else sent:  # newest first, so each chain's latest attempt comes first
            for result in item["results"]:
                chain = item.get("retry_of") or f"{item['request_id']}/{result.get('to')}"
                if chain in seen:
                    continue
                seen.add(chain)
                if result.get("status") in ("not_confirmed", "not_attempted"):
                    waiting.add((item["request_id"], result.get("to")))
        return waiting

    def sent_letter(self, request_id: str):
        with self.lock:
            entry = self._requests.get(request_id)
            if entry is None or not entry["seats"]:
                return None
            if request_id in self._in_flight:
                done = {seat: part["result"] for seat, part in entry["parts"].items() if part["phase"] == "final"}
                result = {"status": "sending",
                          "results": [done.get(seat) or {"to": seat, "status": "sending", **(entry.get("copies") or {}).get(seat, {})} for seat in entry["seats"]]}
            else:
                result = self.request_result(request_id) or {}
            return {"request_id": request_id, "at": entry.get("at", 0), "via": entry.get("via", ""),
                    "re": entry.get("re", ""), "text": entry.get("text"), "retry_of": entry.get("retry_of", ""),
                    "status": result.get("status", ""), "results": result.get("results", [])}

    def request_begin(self, request_id: str, seats: list, text: str = None, re_id: str = "", via: str = "", retry_of: str = "", copies=None):
        row = {"request_id": request_id, "state": "pending", "seats": seats, "at": int(time.time())}
        if text is not None:
            row.update({"text": text, "re": re_id, "via": via, "retry_of": retry_of})
        if copies is not None:
            row["copies"] = copies
        with self.lock:
            self._in_flight.add(request_id)
        self._append_request(row)

    def request_seat_start(self, request_id: str, seat: str):
        self._append_request({"request_id": request_id, "state": "part", "seat": seat, "phase": "pending"})

    def request_seat_done(self, request_id: str, seat: str, result: dict):
        self._append_request({"request_id": request_id, "state": "part", "seat": seat, "phase": "final", "result": result})

    def request_finish(self, request_id: str, result: dict):
        self._append_request({"request_id": request_id, "state": "final", "result": result, "at": int(time.time())})
        with self.lock:
            self._in_flight.discard(request_id)

    def log(self, message: str):
        line = time.strftime("%Y-%m-%dT%H:%M:%S") + " " + message + "\n"
        path = self.path("bell-desk.log")
        with self.lock:
            try:
                if os.stat(path, follow_symlinks=False).st_size >= LOG_MAX:
                    os.replace(path, path + ".1")
            except FileNotFoundError:
                pass
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode())
            finally:
                os.close(fd)

    def desk_view(self):
        try:
            with open(self.path("desk-view.json")) as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_desk_view(self, data):
        with self.lock:
            _write_private(self.path("desk-view.json"), (json.dumps(data) + "\n").encode())
