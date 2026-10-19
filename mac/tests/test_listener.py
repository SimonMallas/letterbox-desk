import http.client
import json
import os
import pathlib
import secrets
import stat
import sys
import tempfile
import threading
import time
import unittest
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from bell_desk import crypto  # noqa: E402
from bell_desk.sender import PinnedHelper, file_sha256  # noqa: E402
from bell_desk.server import App, DeskHTTPServer  # noqa: E402
from bell_desk.store import State  # noqa: E402
from bell_desk.watcher import LETTER_MAX, InboxWatcher, read_header, read_letter_text  # noqa: E402

VECTORS = json.loads(pathlib.Path(__file__).with_name("vectors.json").read_text())
ISOLATED_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def letter(path, **fields):
    head = "\n".join(f"{k}: {v}" for k, v in fields.items())
    pathlib.Path(path).write_text(f"---\n{head}\n---\nbody text that must never leave the Mac\n")


def make_bus(root, seats):
    for seat in seats:
        for sub in ("inbox", "processed"):
            os.makedirs(os.path.join(root, seat, sub), exist_ok=True)


FAKE_HELPER = """#!/bin/bash
here="$(dirname "$0")"
printf '%s\\n' "$@" > "$here/calls/args.$$"
printf 'LETTERBOX_AGENT=%s LETTERBOX_DIR=%s MODE_ENV=%s\\n' "$LETTERBOX_AGENT" "$LETTERBOX_DIR" "${MODE:-}" > "$here/calls/env.$$"
cat > "$here/calls/stdin.$$"
case "$(cat "$here/mode")" in
  ok) echo "sent: $LETTERBOX_DIR/$2/inbox/2026-10-07T090000-desk-info-desk-dictation-abcd1234.md"
      echo "doorbell-outcome v=1 outcome=no_live_surface reason=session_missing target=-" ;;
  slow) sleep 1
        echo "sent: $LETTERBOX_DIR/$2/inbox/2026-10-07T090000-desk-info-desk-dictation-slow0001.md"
        echo "doorbell-outcome v=1 outcome=submitted reason=- target=x" ;;
  split) if [ "$2" = "bob" ]; then exit 3; fi
         echo "sent: $LETTERBOX_DIR/$2/inbox/2026-10-07T090000-desk-info-desk-dictation-$2.md"
         echo "doorbell-outcome v=1 outcome=submitted reason=- target=x" ;;
  silent) exit 3 ;;
  hang) sleep 5 ;;
esac
"""


class VectorTests(unittest.TestCase):
    def test_vectors_reproduce(self):
        server = crypto.load_private_key(crypto.b64d(VECTORS["server_private"]))
        device = crypto.load_private_key(crypto.b64d(VECTORS["device_private"]))
        server_pub, device_pub = crypto.public_bytes(server), crypto.public_bytes(device)
        self.assertEqual(crypto.b64e(server_pub), VECTORS["server_public"])
        self.assertEqual(crypto.b64e(device_pub), VECTORS["device_public"])
        device_nonce, server_nonce = crypto.b64d(VECTORS["device_nonce"]), crypto.b64d(VECTORS["server_nonce"])
        self.assertEqual(crypto.b64e(crypto.commitment(server_pub, device_pub, server_nonce)), VECTORS["commitment"])
        self.assertEqual(crypto.pairing_code(server_pub, device_pub, device_nonce, server_nonce), VECTORS["pairing_code"])
        self.assertEqual(crypto.device_id(device_pub), VECTORS["device_id"])
        key = crypto.derive_key(device, server_pub, server_pub, device_pub, device_nonce, server_nonce)
        self.assertEqual(crypto.b64e(key), VECTORS["auth_key"])
        r = VECTORS["request"]
        self.assertEqual(crypto.sign_request(key, r["method"], r["path"], r["ts"], r["nonce"], r["body"].encode()), r["sig"])
        g = VECTORS["empty_body_get"]
        self.assertEqual(crypto.sign_request(key, g["method"], g["path"], g["ts"], g["nonce"], b""), g["sig"])
        self.assertEqual(crypto.sign_event(key, VECTORS["event"]["json"].encode()), VECTORS["event"]["sig"])


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bus = self.tmp.name
        make_bus(self.bus, ["alice", "bob", "desk"])
        self.events = []
        self.watcher = InboxWatcher(self.bus, self.events.append)

    def tearDown(self):
        self.tmp.cleanup()

    def drop(self, seat, name, **fields):
        letter(os.path.join(self.bus, seat, "inbox", name + ".md"), **fields)

    def test_existing_letters_never_ring(self):
        self.drop("alice", "old-1", id="old-1", **{"from": "bob"}, to="alice", type="info", priority="now")
        self.watcher.baseline()
        self.watcher.scan_once()
        self.assertEqual(self.events, [])

    def test_new_priority_now_letter_rings_with_header_fields_only(self):
        self.watcher.baseline()
        self.drop("alice", "new-1", id="new-1", **{"from": "bob"}, to="alice", type="result", priority="now", sent="2026-10-07T08:00:00Z")
        self.watcher.scan_once()
        self.assertEqual(self.events, [{"to": "alice", "from": "bob", "letter_type": "result", "id": "new-1",
                                        "priority": "now", "ts": "2026-10-07T08:00:00Z", "for_you": False}])
        self.watcher.scan_once()
        self.assertEqual(len(self.events), 1)

    def test_ineligible_letters_are_silent(self):
        self.watcher.baseline()
        self.drop("alice", "a", id="a", **{"from": "bob"}, to="alice", type="info", priority="normal")
        self.drop("alice", "b", id="b", **{"from": "desk"}, to="alice", type="info", priority="now")
        self.drop("alice", "c", id="not-c", **{"from": "bob"}, to="alice", type="info", priority="now")
        self.drop("alice", "d", id="d", **{"from": "bob"}, to="bob", type="info", priority="now")
        self.drop("alice", "e", id="e", **{"from": "../bob"}, to="alice", type="info", priority="now")
        target = os.path.join(self.bus, "bob", "inbox", "real.md")
        letter(target, id="f", **{"from": "bob"}, to="alice", type="info", priority="now")
        os.symlink(target, os.path.join(self.bus, "alice", "inbox", "f.md"))
        self.watcher.scan_once()
        self.assertEqual(self.events, [])

    def test_unfenced_letter_retried_then_dropped(self):
        self.watcher.baseline()
        path = os.path.join(self.bus, "alice", "inbox", "half.md")
        pathlib.Path(path).write_text("---\nid: half\nfrom: bob\n")
        for _ in range(4):
            self.watcher.scan_once()
        self.assertIn(("alice", "half.md"), self.watcher.tries)
        self.watcher.scan_once()
        self.assertNotIn(("alice", "half.md"), self.watcher.tries)
        self.assertEqual(self.events, [])

    def test_seat_that_appears_later_is_baselined(self):
        self.watcher.baseline()
        make_bus(self.bus, ["carol"])
        self.drop("carol", "k1", id="k1", **{"from": "bob"}, to="carol", type="info", priority="now")
        self.watcher.scan_once()
        self.assertEqual(self.events, [])

    def test_letter_filed_before_the_next_scan_still_rings_once(self):
        self.watcher.baseline()
        self.drop("alice", "fast", id="fast", **{"from": "bob"}, to="alice", type="result", priority="now")
        os.rename(os.path.join(self.bus, "alice", "inbox", "fast.md"), os.path.join(self.bus, "alice", "processed", "fast.md"))
        self.watcher.scan_once()
        self.watcher.scan_once()
        self.assertEqual([e["id"] for e in self.events], ["fast"])

    def test_filing_a_letter_that_already_rang_does_not_ring_again(self):
        self.watcher.baseline()
        self.drop("alice", "seen", id="seen", **{"from": "bob"}, to="alice", type="result", priority="now")
        self.watcher.scan_once()
        os.rename(os.path.join(self.bus, "alice", "inbox", "seen.md"), os.path.join(self.bus, "alice", "processed", "seen.md"))
        self.watcher.scan_once()
        self.assertEqual([e["id"] for e in self.events], ["seen"])

    def test_a_letter_to_the_person_rings_as_for_you(self):
        self.watcher.baseline()
        self.drop("desk", "reply-1", id="reply-1", **{"from": "bob"}, to="desk", type="result", priority="now")
        self.drop("desk", "echo-1", id="echo-1", **{"from": "desk"}, to="desk", type="info", priority="now")
        self.watcher.scan_once()
        self.assertEqual([(e["id"], e["for_you"]) for e in self.events], [("reply-1", True)])

    def test_only_letters_to_the_person_reach_the_mac_card(self):
        cards = []
        watcher = InboxWatcher(self.bus, self.events.append, on_letter_for_person=lambda e, text: cards.append((e["from"], text)))
        watcher.baseline()
        self.drop("desk", "r1", id="r1", **{"from": "bob"}, to="desk", type="result", priority="now")
        self.drop("alice", "t1", id="t1", **{"from": "bob"}, to="alice", type="result", priority="now")
        watcher.scan_once()
        self.assertEqual(cards, [("bob", "body text that must never leave the Mac")])

    def test_letter_text_is_the_body_only_cleaned_and_bounded(self):
        path = os.path.join(self.bus, "desk", "inbox", "b.md")
        pathlib.Path(path).write_text("---\nid: b\nfrom: bob\n---\nHello\x1b[31m there\n" + "x" * (LETTER_MAX + 50))
        text = read_letter_text(path)
        self.assertTrue(text.startswith("Hello[31m there"))
        self.assertNotIn("from: bob", text)
        self.assertLessEqual(len(text), LETTER_MAX)
        link = os.path.join(self.bus, "desk", "inbox", "link.md")
        os.symlink(path, link)
        self.assertEqual(read_letter_text(link), "")

    def test_a_fifo_named_like_a_letter_does_not_hang_the_watcher(self):
        self.watcher.baseline()
        os.mkfifo(os.path.join(self.bus, "desk", "inbox", "trap.md"))
        done = threading.Event()
        threading.Thread(target=lambda: (self.watcher.scan_once(), done.set()), daemon=True).start()
        self.assertTrue(done.wait(3), "the scan hung on a FIFO")
        self.assertEqual(read_letter_text(os.path.join(self.bus, "desk", "inbox", "trap.md")), "")

    def test_direction_marks_are_removed_from_card_text(self):
        path = os.path.join(self.bus, "desk", "inbox", "d.md")
        pathlib.Path(path).write_text("---\nid: d\n---\nabc\u202edef\u2066ghi")
        self.assertEqual(read_letter_text(path), "abcdefghi")

    def test_body_is_never_read_past_the_header(self):
        path = os.path.join(self.bus, "alice", "inbox", "x.md")
        letter(path, id="x", **{"from": "bob"}, to="alice", type="info", priority="now")
        self.assertNotIn("body", json.dumps(read_header(path)))


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = os.path.join(self.tmp.name, "calls")
        os.makedirs(self.calls)
        self.script = os.path.join(self.tmp.name, "helper.sh")
        pathlib.Path(self.script).write_text(FAKE_HELPER)
        os.chmod(self.script, stat.S_IRWXU)

    def tearDown(self):
        self.tmp.cleanup()

    def helper(self, mode, timeout=30):
        pathlib.Path(self.tmp.name, "mode").write_text(mode)
        return PinnedHelper(self.script, file_sha256(self.script), bus_dir="/fixture/bus", env_path=ISOLATED_PATH, timeout=timeout)

    def test_sent_parses_envelope_and_ring(self):
        result = self.helper("ok").send("bob", "hello bob")
        self.assertEqual(result, {"status": "sent", "id": "2026-10-07T090000-desk-info-desk-dictation-abcd1234",
                                  "ring": {"outcome": "no_live_surface", "reason": "session_missing"}})
        args = next(pathlib.Path(self.calls).glob("args.*")).read_text().split("\n")[:5]
        self.assertEqual(args, ["send", "bob", "info", "desk-dictation", "--now"])
        self.assertEqual(next(pathlib.Path(self.calls).glob("env.*")).read_text().strip(), "LETTERBOX_AGENT=desk LETTERBOX_DIR=/fixture/bus MODE_ENV=")
        self.assertEqual(next(pathlib.Path(self.calls).glob("stdin.*")).read_text(), "hello bob")

    def test_no_sent_line_is_not_confirmed(self):
        self.assertEqual(self.helper("silent").send("bob", "x"), {"status": "not_confirmed", "reason": "helper_exit_3"})

    def test_timeout_is_not_confirmed(self):
        self.assertEqual(self.helper("hang", timeout=0.5).send("bob", "x"), {"status": "not_confirmed", "reason": "helper_timeout"})

    def test_changed_helper_is_refused_without_running(self):
        helper = self.helper("ok")
        helper.sha256 = "0" * 64
        self.assertEqual(helper.send("bob", "x"), {"status": "not_confirmed", "reason": "helper_changed"})
        self.assertEqual(list(pathlib.Path(self.calls).glob("args.*")), [])


class RecordTests(unittest.TestCase):
    def test_a_group_cut_off_mid_send_reads_back_per_seat(self):
        with tempfile.TemporaryDirectory() as root:
            state = State(root)
            state.request_begin("r1", ["alice", "bob", "carol"])
            state.request_seat_start("r1", "alice")
            state.request_seat_done("r1", "alice", {"to": "alice", "status": "published", "id": "a", "ring": {"outcome": "submitted", "reason": "-"}})
            state.request_seat_start("r1", "bob")
            reloaded = State(root).request_result("r1")
            self.assertEqual(reloaded["status"], "partial")
            self.assertEqual([(r["to"], r["status"]) for r in reloaded["results"]],
                             [("alice", "published"), ("bob", "not_confirmed"), ("carol", "not_attempted")])

    def test_a_v1_record_still_reads_back(self):
        with tempfile.TemporaryDirectory() as root:
            pathlib.Path(root, "requests.jsonl").write_text(
                json.dumps({"request_id": "old", "state": "pending", "to": "bob", "at": 1}) + "\n"
                + json.dumps({"request_id": "old", "state": "final", "result": {"status": "sent", "request_id": "old", "id": "x"}}) + "\n")
            self.assertEqual(State(root).request_result("old")["id"], "x")


class Client:
    def __init__(self, port):
        self.port = port
        self.device_id = None
        self.key = None

    def request(self, method, path, body=b"", sign=True, ts=None, nonce=None, sig=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if sign:
            ts = int(time.time()) if ts is None else ts
            nonce = nonce or secrets.token_hex(16)
            headers.update({
                "X-Belldesk-Device": self.device_id,
                "X-Belldesk-Time": str(ts),
                "X-Belldesk-Nonce": nonce,
                "X-Belldesk-Sig": sig or crypto.sign_request(self.key, method, path, ts, nonce, body),
            })
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, (json.loads(data) if data else None)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.root = self.tmp.name
        self.bus = os.path.join(root, "bus")
        make_bus(self.bus, ["alice", "bob", "desk"])
        self.calls = os.path.join(root, "calls")
        os.makedirs(self.calls)
        script = os.path.join(root, "helper.sh")
        pathlib.Path(script).write_text(FAKE_HELPER)
        os.chmod(script, stat.S_IRWXU)
        pathlib.Path(root, "mode").write_text("ok")
        os.environ["MODE"] = "leaks-if-env-is-passed"
        helper = PinnedHelper(script, file_sha256(script), bus_dir=self.bus, env_path=ISOLATED_PATH)
        self.app = App(State(os.path.join(root, "state")), helper, self.bus, ["alice", "bob", "frank"],
                       console=lambda m: None, ping_interval=0.3)
        self.server = DeskHTTPServer(("127.0.0.1", 0), self.app)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = Client(self.server.server_address[1])

    def tearDown(self):
        self.app.stop()
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def start_pairing(self):
        device = crypto.new_private_key()
        device_pub = crypto.public_bytes(device)
        body = json.dumps({"device_pub": crypto.b64e(device_pub), "device_name": "Sample iPhone"}).encode()
        status, reply = self.client.request("POST", "/v1/pair", body, sign=False)
        self.assertEqual(status, 200, reply)
        self.assertNotIn("server_nonce", reply)
        return device, device_pub, reply

    def send_nonce(self, pair_id, device_nonce):
        body = json.dumps({"device_nonce": crypto.b64e(device_nonce)}).encode()
        return self.client.request("POST", f"/v1/pair/{pair_id}/nonce", body, sign=False)

    def pair(self):
        device, device_pub, reply = self.start_pairing()
        server_pub = crypto.b64d(reply["server_pub"])
        self.assertEqual(reply["server_pub"], self.client.request("GET", "/v1/hello", sign=False)[1]["server_pub"])
        device_nonce = secrets.token_bytes(16)
        status, revealed = self.send_nonce(reply["pair_id"], device_nonce)
        self.assertEqual(status, 200, revealed)
        server_nonce = crypto.b64d(revealed["server_nonce"])
        self.assertEqual(crypto.b64d(reply["commitment"]), crypto.commitment(server_pub, device_pub, server_nonce))
        code = crypto.pairing_code(server_pub, device_pub, device_nonce, server_nonce)
        self.assertEqual(self.app.pairings[reply["pair_id"]]["code"], code)
        self.assertEqual(self.client.request("GET", "/v1/pair/" + reply["pair_id"], sign=False)[1], {"state": "pending"})
        self.assertTrue(self.app.decide("", True).startswith("approved"))
        status, state = self.client.request("GET", "/v1/pair/" + reply["pair_id"], sign=False)
        self.assertEqual(state["state"], "approved")
        self.client.device_id = state["device_id"]
        self.client.key = crypto.derive_key(device, server_pub, server_pub, device_pub, device_nonce, server_nonce)
        self.assertEqual(crypto.b64e(self.client.key), self.app.state.devices()[state["device_id"]]["key"])

    def test_no_approval_before_the_phone_commits_its_nonce(self):
        self.app.open_pairing()
        _, _, reply = self.start_pairing()
        self.assertIn("no code yet", self.app.decide("", True))
        self.assertEqual(self.app.state.devices(), {})
        self.assertEqual(self.send_nonce(reply["pair_id"], secrets.token_bytes(16))[0], 200)
        self.assertEqual(self.send_nonce(reply["pair_id"], secrets.token_bytes(16)), (409, {"error": "nonce_already_set"}))

    def test_server_nonce_is_hidden_until_the_phone_nonce_is_fixed(self):
        self.app.open_pairing()
        _, device_pub, reply = self.start_pairing()
        server_nonce = self.app.pairings[reply["pair_id"]]["server_nonce"]
        self.assertNotIn(crypto.b64e(server_nonce), json.dumps(reply))
        self.assertNotIn(crypto.b64e(server_nonce), json.dumps(self.client.request("GET", "/v1/pair/" + reply["pair_id"], sign=False)[1]))

    def test_a_second_listener_on_the_same_state_is_refused(self):
        with self.assertRaises(RuntimeError):
            App(State(self.app.state.root), self.app.helper, self.bus, ["bob"], console=lambda m: None)

    def test_shutdown_waits_for_a_running_send_and_the_next_listener_dedupes(self):
        self.app.open_pairing()
        self.pair()
        pathlib.Path(self.root, "mode").write_text("slow")
        request_id = str(uuid.uuid4())
        body = json.dumps({"request_id": request_id, "to": "bob", "text": "slow one"}).encode()
        sent = {}
        sender = threading.Thread(target=lambda: sent.update(result=self.app.dictation(body)))
        sender.start()
        deadline = time.time() + 5
        while self.helper_calls() == 0 and time.time() < deadline:
            time.sleep(0.02)
        stopper = threading.Thread(target=self.app.stop)
        stopper.start()
        time.sleep(0.3)
        self.assertTrue(stopper.is_alive(), "stop() released the lock while a send was still running")
        sender.join()
        stopper.join()
        self.assertEqual(sent["result"][1]["status"], "sent")
        self.assertEqual(self.app.dictation(body.replace(b"slow one", b"late"))[0], 503)
        successor = App(State(self.app.state.root), self.app.helper, self.bus, ["bob"], console=lambda m: None)
        try:
            code, again = successor.dictation(body)
            self.assertEqual((code, again.get("replayed")), (200, True))
            self.assertEqual(self.helper_calls(), 1)
        finally:
            successor.stop()

    def stdin_texts(self):
        return sorted(f.read_text() for f in pathlib.Path(self.calls).glob("stdin.*"))

    def test_several_seats_get_one_copy_each_naming_the_others(self):
        self.app.open_pairing()
        self.pair()
        status, result = self.client.request("POST", "/v1/dictation", json.dumps(
            {"request_id": str(uuid.uuid4()), "to": ["alice", "bob"], "text": "Both of you, please."}).encode())
        self.assertEqual((status, result["status"]), (200, "sent"))
        self.assertEqual([(r["to"], r["status"]) for r in result["results"]], [("alice", "published"), ("bob", "published")])
        self.assertNotIn("id", result)
        texts = self.stdin_texts()
        self.assertEqual(len(texts), 2)
        self.assertTrue(any(t.endswith("Also addressed to: bob (intended recipients; delivery and reading are not guaranteed).") for t in texts))
        self.assertTrue(any(t.endswith("Also addressed to: alice (intended recipients; delivery and reading are not guaranteed).") for t in texts))

    def test_a_partial_group_reports_each_seat(self):
        self.app.open_pairing()
        self.pair()
        pathlib.Path(self.root, "mode").write_text("split")
        result = self.client.request("POST", "/v1/dictation", json.dumps(
            {"request_id": str(uuid.uuid4()), "to": ["alice", "bob"], "text": "Split test."}).encode())[1]
        self.assertEqual(result["status"], "partial")
        self.assertEqual([(r["to"], r["status"]) for r in result["results"]], [("alice", "published"), ("bob", "not_confirmed")])

    def test_a_changed_helper_stops_the_group_before_anything_runs(self):
        self.app.open_pairing()
        self.pair()
        self.app.helper.sha256 = "0" * 64
        result = self.client.request("POST", "/v1/dictation", json.dumps(
            {"request_id": str(uuid.uuid4()), "to": ["alice", "bob"], "text": "x"}).encode())[1]
        self.assertEqual(result["status"], "not_sent")
        self.assertEqual([(r["to"], r["status"], r["reason"]) for r in result["results"]],
                         [("alice", "not_attempted", "helper_changed"), ("bob", "not_attempted", "helper_changed")])
        self.assertEqual(self.helper_calls(), 0)

    def test_bad_seat_lists_run_nothing(self):
        self.app.open_pairing()
        self.pair()
        for to, reason in ((["bob", "bob"], "duplicate_seat"), ([], "no_seats"), (["bob", "dave"], "unknown_seat"), (["bob", 7], "unknown_seat")):
            status, reply = self.client.request("POST", "/v1/dictation", json.dumps(
                {"request_id": str(uuid.uuid4()), "to": to, "text": "x"}).encode())
            self.assertEqual((status, reply["reason"]), (400, reason))
        self.assertEqual(self.helper_calls(), 0)

    def test_a_group_replay_sends_nothing_more(self):
        self.app.open_pairing()
        self.pair()
        body = json.dumps({"request_id": str(uuid.uuid4()), "to": ["alice", "bob"], "text": "once"}).encode()
        self.client.request("POST", "/v1/dictation", body)
        again = self.client.request("POST", "/v1/dictation", body)[1]
        self.assertTrue(again["replayed"])
        self.assertEqual(self.helper_calls(), 2)

    def test_a_reply_typed_on_the_mac_goes_only_to_the_seat_that_wrote(self):
        result = self.app.reply_from_mac({"from": "bob", "id": "2026-10-07T170000-bob-result-x-abcd"}, "Thanks, carry on.")
        self.assertEqual(result["status"], "sent")
        self.assertEqual([r["to"] for r in result["results"]], ["bob"])
        self.assertEqual(self.stdin_texts(), ["Thanks, carry on."])
        args = next(pathlib.Path(self.calls).glob("args.*")).read_text().split("\n")[:7]
        self.assertEqual(args, ["send", "bob", "info", "desk-dictation", "--now", "--re", "2026-10-07T170000-bob-result-x-abcd"])

    def test_a_letter_written_in_the_menu_bar_takes_the_same_path(self):
        result = self.app.compose_from_mac(["alice", "bob"], "Morning both.")
        self.assertEqual(result["status"], "sent")
        self.assertEqual(self.helper_calls(), 2)
        refused = self.app.compose_from_mac(["dave"], "x")
        self.assertEqual(refused, {"status": "not_sent", "reason": "unknown_seat"})
        self.assertEqual(self.helper_calls(), 2)

    def test_a_reply_with_a_malformed_letter_id_is_refused(self):
        for bad in ("-rf", "", "has space", "../x", "a" * 129):
            self.assertEqual(self.app.reply_from_mac({"from": "bob", "id": bad}, "hi")["reason"], "bad_letter_id")
        self.assertEqual(self.helper_calls(), 0)

    def test_a_reply_to_a_sender_who_is_not_a_seat_is_refused(self):
        result = self.app.reply_from_mac({"from": "telegram-bridge", "id": "x"}, "hello")
        self.assertEqual(result, {"status": "not_sent", "reason": "unknown_seat"})
        self.assertEqual(self.helper_calls(), 0)

    def test_unpair_closes_a_stream_whose_queue_is_full(self):
        self.app.open_pairing()
        self.pair()
        import queue as queue_module
        q = queue_module.Queue(maxsize=64)
        self.app.subscribe(q, self.client.device_id)
        for i in range(64):
            q.put_nowait({"type": "mail", "n": i})
        self.assertTrue(self.app.unpair(self.client.device_id))
        self.assertNotIn(q, self.app.subscribers)
        self.assertIsNone(q.get_nowait())
        self.app.publish_mail({"to": "alice", "from": "bob", "letter_type": "info", "id": "after", "priority": "now", "ts": ""})
        self.assertTrue(q.empty())

    def dictate(self, to="bob", text="Check my relay is still up.", request_id=None):
        body = json.dumps({"request_id": request_id or str(uuid.uuid4()), "to": to, "text": text}).encode()
        return self.client.request("POST", "/v1/dictation", body)

    def helper_calls(self):
        return len(list(pathlib.Path(self.calls).glob("args.*")))

    def current_code(self):
        return next((p["code"] for p in self.app.pairings.values() if p["state"] == "pending" and p["code"]), "")

    def wait_for(self, check, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if check():
                return True
            time.sleep(0.02)
        return False

    def test_typing_the_phones_code_into_the_pop_up_pairs_it(self):
        asked = []
        self.app.ask_for_code = lambda name: asked.append(name) or self.current_code()[:3] + " " + self.current_code()[3:]
        _, _, reply = self.start_pairing()
        self.assertEqual(self.send_nonce(reply["pair_id"], secrets.token_bytes(16))[0], 200)
        self.assertTrue(self.wait_for(lambda: self.app.pairings[reply["pair_id"]]["state"] == "approved"))
        self.assertEqual(asked, ["Sample iPhone"])
        self.assertEqual(len(self.app.state.devices()), 1)

    def test_a_wrong_code_refuses_and_pauses_pairing(self):
        told = []
        self.app.ask_for_code = lambda name: "000000" if self.current_code() != "000000" else "111111"
        self.app.tell = told.append
        _, _, reply = self.start_pairing()
        self.send_nonce(reply["pair_id"], secrets.token_bytes(16))
        self.assertTrue(self.wait_for(lambda: self.app.pairings[reply["pair_id"]]["state"] == "denied"))
        self.assertTrue(self.wait_for(lambda: told == ["Sample iPhone"]))
        self.assertEqual(self.app.state.devices(), {})
        body = json.dumps({"device_pub": crypto.b64e(crypto.public_bytes(crypto.new_private_key()))}).encode()
        self.assertEqual(self.client.request("POST", "/v1/pair", body, sign=False)[0], 403)
        self.app.open_pairing()
        self.assertEqual(self.client.request("POST", "/v1/pair", body, sign=False)[0], 200)

    def test_cancelling_the_pop_up_refuses_without_a_second_dialog(self):
        told = []
        self.app.ask_for_code = lambda name: ""
        self.app.tell = told.append
        _, _, reply = self.start_pairing()
        self.send_nonce(reply["pair_id"], secrets.token_bytes(16))
        self.assertTrue(self.wait_for(lambda: self.app.pairings[reply["pair_id"]]["state"] == "denied"))
        self.assertEqual(told, [])
        self.assertFalse(self.app.pairing_open())

    def test_only_one_phone_can_be_waiting_at_a_time(self):
        self.start_pairing()
        body = json.dumps({"device_pub": crypto.b64e(crypto.public_bytes(crypto.new_private_key()))}).encode()
        self.assertEqual(self.client.request("POST", "/v1/pair", body, sign=False)[0], 429)

    def test_pair_then_dictate_then_replay(self):
        self.app.open_pairing()
        self.pair()
        request_id = str(uuid.uuid4())
        status, result = self.dictate(request_id=request_id)
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["ring"]["outcome"], "no_live_surface")
        status, again = self.dictate(request_id=request_id)
        self.assertTrue(again["replayed"])
        self.assertEqual(self.helper_calls(), 1)
        self.assertEqual(self.client.request("GET", "/v1/dictation/" + request_id)[1]["id"], result["id"])
        self.assertEqual(self.client.request("GET", "/v1/dictation/" + str(uuid.uuid4()))[0], 404)

    def test_bad_dictations_run_nothing(self):
        self.app.open_pairing()
        self.pair()
        cases = [
            (self.dictate(to="dave"), "unknown_seat"),
            (self.dictate(to="../bob"), "unknown_seat"),
            (self.dictate(to="frank"), "inbox_missing"),
            (self.dictate(text="   "), "text_empty"),
            (self.dictate(text="x" * 2001), "text_too_long"),
            (self.dictate(text="hi\x1b[2Jthere"), "text_control_chars"),
            (self.dictate(text="abc‮evil"), "text_control_chars"),
            (self.dictate(request_id="not-a-uuid"), "bad_request_id"),
        ]
        for (status, reply), reason in cases:
            self.assertEqual((status, reply["reason"]), (400, reason))
        self.assertEqual(self.helper_calls(), 0)

    def test_authentication_failures(self):
        self.app.open_pairing()
        self.pair()
        body = json.dumps({"request_id": str(uuid.uuid4()), "to": "bob", "text": "x"}).encode()
        self.assertEqual(self.client.request("POST", "/v1/dictation", body, sig=crypto.b64e(b"0" * 32))[0], 401)
        self.assertEqual(self.client.request("POST", "/v1/dictation", body, ts=int(time.time()) - 600)[0], 401)
        nonce = secrets.token_hex(16)
        self.assertEqual(self.client.request("GET", "/v1/dictation/" + str(uuid.uuid4()), nonce=nonce)[0], 404)
        self.assertEqual(self.client.request("GET", "/v1/dictation/" + str(uuid.uuid4()), nonce=nonce)[0], 401)
        self.client.device_id = "0" * 16
        self.assertEqual(self.client.request("POST", "/v1/dictation", body)[0], 401)
        self.assertEqual(self.helper_calls(), 0)

    def test_signed_event_stream(self):
        self.app.open_pairing()
        self.pair()
        ts, nonce = int(time.time()), secrets.token_hex(16)
        conn = http.client.HTTPConnection("127.0.0.1", self.client.port, timeout=10)
        conn.request("GET", "/v1/events", headers={
            "X-Belldesk-Device": self.client.device_id, "X-Belldesk-Time": str(ts), "X-Belldesk-Nonce": nonce,
            "X-Belldesk-Sig": crypto.sign_request(self.client.key, "GET", "/v1/events", ts, nonce, b"")})
        response = conn.getresponse()
        self.assertEqual(response.status, 200)

        def next_event():
            while True:
                line = response.readline().decode()
                if line.startswith("data: "):
                    sig, _, body = line[len("data: "):].rstrip("\n").partition(" ")
                    self.assertEqual(sig, crypto.sign_event(self.client.key, body.encode()))
                    return json.loads(body)

        hello = next_event()
        self.assertEqual((hello["type"], hello["seats"], hello["stream"], hello["seq"]), ("hello", ["alice", "bob", "frank"], nonce, 0))
        self.assertEqual(hello["name"], self.app.name)
        self.app.publish_mail({"to": "alice", "from": "bob", "letter_type": "result", "id": "x1", "priority": "now", "ts": "t"})
        mail = next_event()
        self.assertEqual((mail["type"], mail["from"], mail["seq"]), ("mail", "bob", 1))
        self.assertEqual(next_event()["type"], "ping")
        conn.close()

    def raw(self, method, path, body=b"", key=None):
        ts, nonce = int(time.time()), secrets.token_hex(16)
        conn = http.client.HTTPConnection("127.0.0.1", self.client.port, timeout=10)
        conn.request(method, path, body=body, headers={
            "X-Belldesk-Device": self.client.device_id, "X-Belldesk-Time": str(ts), "X-Belldesk-Nonce": nonce,
            "X-Belldesk-Sig": crypto.sign_request(key or self.client.key, method, path, ts, nonce, body)})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, data, response.getheader("X-Belldesk-Response-Sig"), nonce

    def test_answers_to_signed_requests_are_signed_for_that_request(self):
        self.app.open_pairing()
        self.pair()
        key = self.client.key
        body = json.dumps({"request_id": str(uuid.uuid4()), "to": "bob", "text": "Signed answer please."}).encode()
        status, data, sig, nonce = self.raw("POST", "/v1/dictation", body)
        self.assertEqual(status, 200)
        self.assertEqual(sig, crypto.sign_response(key, "POST", "/v1/dictation", nonce, 200, data))
        self.assertNotEqual(sig, crypto.sign_response(key, "POST", "/v1/dictation", secrets.token_hex(16), 200, data))
        missing = "/v1/dictation/" + str(uuid.uuid4())
        status, data, sig, nonce = self.raw("GET", missing)
        self.assertEqual((status, sig), (404, crypto.sign_response(key, "GET", missing, nonce, 404, data)))
        bad = json.dumps({"request_id": str(uuid.uuid4()), "to": "nobody", "text": "x"}).encode()
        status, data, sig, nonce = self.raw("POST", "/v1/dictation", bad)
        self.assertEqual((status, sig), (400, crypto.sign_response(key, "POST", "/v1/dictation", nonce, 400, data)))
        status, _, sig, _ = self.raw("POST", "/v1/dictation", body, key=b"\0" * 32)
        self.assertEqual((status, sig), (401, None))

    def test_unknown_routes_and_oversized_bodies(self):
        self.assertEqual(self.client.request("GET", "/v1/nope", sign=False)[0], 404)
        self.assertEqual(self.client.request("POST", "/v1/pair", b"x" * (17 * 1024), sign=False)[0], 413)


if __name__ == "__main__":
    unittest.main()
