import json
import os
import pathlib
import queue
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from bell_desk import service  # noqa: E402
from bell_desk.desk import DeskBridge, DeskMail, letter_time, parse_team  # noqa: E402
from bell_desk import cli  # noqa: E402
from bell_desk.letterbox import doorbell, find_root, pointer_path, roster  # noqa: E402
from bell_desk.phone import PhoneSide  # noqa: E402
from bell_desk.sender import PinnedHelper, file_sha256  # noqa: E402
from bell_desk.server import App, DeskHTTPServer  # noqa: E402
from bell_desk.store import State  # noqa: E402
from bell_desk.watcher import read_header  # noqa: E402
from tests.test_listener import FAKE_HELPER, ISOLATED_PATH, letter, make_bus  # noqa: E402

TEAM_TEXT = """── team health (read-only; queue counts exclude retained ack/nack mail)
   alice: queue[unacked=0 accepted=0] oldest=none owner=alice status=working:22d ring=stale:send_failed pin=live acr=1d
   desk: queue[unacked=0 accepted=0 info=11] oldest=none owner=desk status=missing ring=stale:surface_not_found pin=unknown acr=n/a
   dave: queue[unacked=14 accepted=5 info=17] oldest=88d/close owner=dave status=DORMANT ring=unknown pin=legacy acr=1d
   carol: queue[unacked=0 accepted=0] oldest=none owner=carol status=idle:0d ring=unknown pin=live acr=1d
   erin: queue[unacked=3 accepted=3 info=12] oldest=42d/ack owner=erin status=in_progress:0d ring=unknown pin=live acr=1d
"""


class DeskSetup(unittest.TestCase):
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
        self.mode("ok")
        helper = PinnedHelper(script, file_sha256(script), bus_dir=self.bus, env_path=ISOLATED_PATH)
        self.state = State(os.path.join(root, "state"))
        self.app = App(self.state, helper, self.bus, ["alice", "bob"], console=lambda m: None)
        self.mail = DeskMail(self.bus, self.state, ["alice", "bob"])
        self.sent = []
        self.bridge = DeskBridge(self.app, self.mail, self.sent.append)

    def tearDown(self):
        self.app.stop()
        self.tmp.cleanup()

    def mode(self, mode):
        pathlib.Path(self.root, "mode").write_text(mode)

    def helper_calls(self):
        return len(list(pathlib.Path(self.calls).glob("args.*")))

    def to_desk(self, letter_id, sender="bob", folder="inbox", **extra):
        path = os.path.join(self.bus, "desk", folder, letter_id + ".md")
        letter(path, id=letter_id, **{"from": sender}, to="desk", type="info", priority="now", **extra)
        return path

    def wait_for(self, kind):
        deadline = time.time() + 10
        while time.time() < deadline:
            found = [m for m in self.sent if m.get("type") == "result" and m.get("kind") == kind]
            if found:
                return found[-1]
            time.sleep(0.02)
        self.fail(f"no {kind} result")


class InboxTests(DeskSetup):
    def test_lists_only_letters_to_desk_whose_header_matches_the_file(self):
        self.to_desk("2026-10-07T090000-bob-info-a-1111")
        self.to_desk("2026-10-07T100000-alice-info-b-2222", sender="alice", folder="processed")
        self.to_desk("2026-10-07T110000-bob-info-c-3333", sender="telegram-bridge")
        letter(os.path.join(self.bus, "desk", "inbox", "2026-10-07T120000-bob-info-d-4444.md"),
               id="something-else", **{"from": "bob"}, to="desk")
        letter(os.path.join(self.bus, "desk", "inbox", "2026-10-07T130000-bob-info-e-5555.md"),
               id="2026-10-07T130000-bob-info-e-5555", **{"from": "bob"}, to="alice")
        inbox = self.mail.inbox()
        self.assertEqual([(m["id"], m["from"], m["can_reply"]) for m in inbox], [
            ("2026-10-07T110000-bob-info-c-3333", "telegram-bridge", False),
            ("2026-10-07T100000-alice-info-b-2222", "alice", True),
            ("2026-10-07T090000-bob-info-a-1111", "bob", True),
        ])
        self.assertEqual(inbox[0]["preview"], "body text that must never leave the Mac")
        self.assertEqual(inbox[0]["ts"], "2026-10-07T11:00:00Z")
        self.assertFalse(any(m["read"] for m in inbox))

    def test_a_symlinked_letter_is_not_followed(self):
        target = os.path.join(self.root, "secret.md")
        letter(target, id="2026-10-07T090000-bob-info-link-1", **{"from": "bob"}, to="desk")
        os.symlink(target, os.path.join(self.bus, "desk", "inbox", "2026-10-07T090000-bob-info-link-1.md"))
        self.assertEqual(self.mail.inbox(), [])
        self.assertIsNone(self.mail.open("2026-10-07T090000-bob-info-link-1"))

    def test_opening_marks_read_here_and_leaves_the_letter_where_it_is(self):
        path = self.to_desk("2026-10-07T090000-bob-info-a-1111")
        opened = self.mail.open("2026-10-07T090000-bob-info-a-1111")
        self.assertEqual(opened["text"], "body text that must never leave the Mac")
        self.assertTrue(os.path.exists(path))
        again = DeskMail(self.bus, self.state, ["alice", "bob"]).inbox()
        self.assertTrue(again[0]["read"])

    def test_bad_ids_open_nothing(self):
        for bad in ("../bob/inbox/x", "-rf", "", None, 7, "a" * 129):
            self.assertIsNone(self.mail.open(bad))
            self.assertIsNone(self.mail.letter(bad))

    def test_time_falls_back_to_the_id(self):
        self.assertEqual(letter_time({"sent": "2026-10-07T17:54:14Z"}, "x"), "2026-10-07T17:54:14Z")
        self.assertEqual(letter_time({}, "2026-10-07T174752-alice-info-x"), "2026-10-07T17:47:52Z")
        self.assertEqual(letter_time({"sent": "<script>"}, "no-time"), "")


class SentTests(DeskSetup):
    def test_what_was_typed_is_kept_with_each_seats_outcome(self):
        self.app.compose_from_mac(["alice", "bob"], "Morning both.")
        body = json.dumps({"request_id": str(uuid.uuid4()), "to": "bob", "text": "From the phone."}).encode()
        self.app.dictation(body)
        sent = self.state.sent_letters()
        self.assertEqual([(s["text"], s["via"], s["status"]) for s in sent],
                         [("From the phone.", "phone", "sent"), ("Morning both.", "mac", "sent")])
        self.assertEqual([r["to"] for r in sent[1]["results"]], ["alice", "bob"])

    def test_the_record_survives_a_restart(self):
        self.app.reply_from_mac({"from": "bob", "id": "2026-10-07T170000-bob-result-x-abcd"}, "Thanks.")
        self.app.stop()
        reloaded = State(self.state.root).sent_letters()
        self.assertEqual((reloaded[0]["text"], reloaded[0]["re"], reloaded[0]["via"]),
                         ("Thanks.", "2026-10-07T170000-bob-result-x-abcd", "mac"))

    def test_records_from_before_the_desk_read_back_without_text(self):
        self.state.request_begin("old", ["bob"])
        self.state.request_finish("old", {"status": "sent", "request_id": "old", "results": []})
        self.assertIsNone(self.state.sent_letters()[0]["text"])
        self.assertEqual(self.app.resend_from_mac("old", "bob"), {"status": "not_sent", "reason": "unknown_letter"})

    def test_resend_goes_only_to_a_seat_the_first_send_did_not_reach(self):
        self.mode("split")
        first = self.app.compose_from_mac(["alice", "bob"], "Split test.")
        self.assertEqual(first["status"], "partial")
        rid = first["request_id"]
        self.assertEqual(self.app.resend_from_mac(rid, "alice"), {"status": "not_sent", "reason": "already_delivered"})
        self.assertEqual(self.app.resend_from_mac(rid, "frank"), {"status": "not_sent", "reason": "not_a_recipient"})
        self.assertEqual(self.app.resend_from_mac(str(uuid.uuid4()), "bob"), {"status": "not_sent", "reason": "unknown_letter"})
        calls = self.helper_calls()
        self.mode("ok")
        again = self.app.resend_from_mac(rid, "bob")
        self.assertEqual((again["status"], [r["to"] for r in again["results"]]), ("sent", ["bob"]))
        self.assertEqual(self.helper_calls(), calls + 1)
        self.assertEqual(self.state.sent_letter(rid)["results"][1]["status"], "not_confirmed")

    def test_a_resent_reply_stays_a_reply(self):
        self.mode("silent")
        first = self.app.reply_from_mac({"from": "bob", "id": "2026-10-07T170000-bob-result-x-abcd"}, "Got it.")
        self.mode("ok")
        self.app.resend_from_mac(first["request_id"], "bob")
        newest = sorted(pathlib.Path(self.calls).glob("args.*"), key=os.path.getmtime)[-1]
        self.assertEqual(newest.read_text().split("\n")[5:7], ["--re", "2026-10-07T170000-bob-result-x-abcd"])

    def test_a_send_still_running_shows_as_sending_and_cannot_be_resent(self):
        self.mode("slow")
        done = {}
        worker = threading.Thread(target=lambda: done.update(r=self.app.compose_from_mac(["bob"], "slow one")))
        worker.start()
        deadline = time.time() + 5
        while self.helper_calls() == 0 and time.time() < deadline:
            time.sleep(0.02)
        running = self.state.sent_letters()[0]
        self.assertEqual((running["status"], running["results"][0]["status"]), ("sending", "sending"))
        self.assertEqual(self.app.resend_from_mac(running["request_id"], "bob"), {"status": "not_sent", "reason": "still_sending"})
        worker.join()
        self.assertEqual(self.state.sent_letters()[0]["status"], "sent")
        self.assertEqual(self.helper_calls(), 1)


    def test_two_resends_of_the_same_copy_at_once_send_one_letter(self):
        self.mode("silent")
        first = self.app.compose_from_mac(["bob"], "Once only.")
        calls = self.helper_calls()
        self.mode("slow")
        results = []
        workers = [threading.Thread(target=lambda: results.append(self.app.resend_from_mac(first["request_id"], "bob")))
                   for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(sorted(r["status"] for r in results), ["not_sent", "sent"])
        self.assertEqual(self.helper_calls(), calls + 1)


class BridgeTests(DeskSetup):
    def test_a_reply_goes_to_whoever_the_letter_on_disk_is_from(self):
        self.to_desk("2026-10-07T090000-bob-info-a-1111")
        self.bridge.handle({"reply": {"id": "2026-10-07T090000-bob-info-a-1111", "text": "On it.", "to": "alice"}})
        result = self.wait_for("reply")
        self.assertEqual((result["ref"], result["summary"]["status"]), ("2026-10-07T090000-bob-info-a-1111", "sent"))
        args = next(pathlib.Path(self.calls).glob("args.*")).read_text().split("\n")[:7]
        self.assertEqual(args, ["send", "bob", "info", "desk-dictation", "--now", "--re", "2026-10-07T090000-bob-info-a-1111"])

    def test_a_reply_to_a_letter_that_is_not_there_runs_nothing(self):
        self.bridge.handle({"reply": {"id": "2026-10-07T090000-bob-info-gone", "text": "hello"}})
        self.assertEqual(self.wait_for("reply")["summary"], {"status": "not_sent", "reason": "unknown_letter"})
        self.assertEqual(self.helper_calls(), 0)

    def test_open_sends_the_letter_and_the_inbox_only_when_it_changes(self):
        self.to_desk("2026-10-07T090000-bob-info-a-1111")
        self.bridge.push()
        self.bridge.push()
        self.assertEqual(len([m for m in self.sent if m["type"] == "mail"]), 1)
        self.bridge.handle({"open": "2026-10-07T090000-bob-info-a-1111"})
        opened = [m for m in self.sent if m["type"] == "letter"][-1]
        self.assertEqual(opened["letter"]["text"], "body text that must never leave the Mac")
        self.assertTrue(self.sent[-1]["inbox"][0]["read"])

    def test_compose_from_the_window_is_recorded_for_sent(self):
        self.bridge.handle({"compose": {"to": ["bob"], "text": "Hello from the window."}})
        self.assertEqual(self.wait_for("compose")["summary"]["status"], "sent")
        mail = [m for m in self.sent if m["type"] == "mail"][-1]
        self.assertEqual(mail["sent"][0]["text"], "Hello from the window.")


class TeamTests(unittest.TestCase):
    def test_only_queue_counts_and_a_status_from_today_are_kept(self):
        self.assertEqual(parse_team(TEAM_TEXT, ["alice", "bob", "carol", "dave", "erin"]), [
            {"seat": "alice", "unacked": 0, "accepted": 0, "reported": "", "has_status": True},
            {"seat": "carol", "unacked": 0, "accepted": 0, "reported": "idle", "has_status": True},
            {"seat": "dave", "unacked": 14, "accepted": 5, "reported": "", "has_status": True},
            {"seat": "erin", "unacked": 3, "accepted": 3, "reported": "in progress", "has_status": True},
        ])

    def test_garbage_yields_nothing(self):
        self.assertEqual(parse_team("bob: status=working:0d\n<html>", ["bob"]), [])


class FindLetterboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.box = os.path.join(self.home, "team-box")
        make_bus(self.box, ["alice", "bob", "desk"])
        self.env = {"HOME": self.home}

    def tearDown(self):
        self.tmp.cleanup()

    def point_at(self, text):
        os.makedirs(os.path.dirname(pointer_path(self.env)), exist_ok=True)
        pathlib.Path(pointer_path(self.env)).write_text(text)

    def test_an_explicit_directory_wins_and_must_be_absolute_and_real(self):
        self.assertEqual(find_root(self.box, self.env), (os.path.realpath(self.box), "given"))
        self.assertIsNone(find_root("team-box", self.env)[0])
        self.assertIsNone(find_root(os.path.join(self.home, "missing"), self.env)[0])

    def test_letterbox_dir_comes_after_an_explicit_folder_and_before_the_pointer(self):
        other = os.path.join(self.home, "other-box")
        os.makedirs(other)
        self.point_at(other + "\n")
        self.assertEqual(find_root("", {**self.env, "LETTERBOX_DIR": self.box}), (os.path.realpath(self.box), "LETTERBOX_DIR"))
        self.assertEqual(find_root(other, {**self.env, "LETTERBOX_DIR": self.box})[0], os.path.realpath(other))
        self.assertIsNone(find_root("", {**self.env, "LETTERBOX_DIR": "relative-box"})[0])

    def test_the_install_pointer_is_read_as_data(self):
        self.point_at(self.box + "\n$(touch pwned)\n")
        root, how = find_root("", self.env)
        self.assertEqual(root, os.path.realpath(self.box))
        self.assertIn("install pointer", how)
        self.assertFalse(os.path.exists("pwned"))
        self.assertEqual(pointer_path({"HOME": self.home, "XDG_CONFIG_HOME": "/x"}), "/x/agent-letterbox/default-dir")

    def test_no_pointer_or_a_relative_one_finds_nothing_rather_than_the_current_directory(self):
        make_bus(os.path.join(self.home, ".letterbox"), ["alice"])
        here = os.getcwd()
        os.chdir(self.home)
        try:
            self.assertIsNone(find_root("", self.env)[0])
            self.point_at(".letterbox\n")
            self.assertIsNone(find_root("", self.env)[0])
            self.assertIsNone(find_root(".letterbox", self.env)[0])
        finally:
            os.chdir(here)

    def test_the_roster_is_folders_with_both_boxes_and_never_desk(self):
        make_bus(self.box, ["carol"])
        os.makedirs(os.path.join(self.box, "half", "inbox"))
        os.makedirs(os.path.join(self.box, "Upper", "inbox"))
        os.makedirs(os.path.join(self.box, "Upper", "processed"))
        os.makedirs(os.path.join(self.box, "locks"))
        os.symlink(os.path.join(self.box, "alice"), os.path.join(self.box, "linked"))
        os.makedirs(os.path.join(self.box, "sneaky", "processed"))
        os.symlink(os.path.join(self.box, "alice", "inbox"), os.path.join(self.box, "sneaky", "inbox"))
        pathlib.Path(self.box, "notes.md").write_text("x")
        self.assertEqual(roster(self.box), ["alice", "bob", "carol"])


PUBLIC_HELPER = os.path.expanduser(os.environ.get("BELL_DESK_TEST_PUBLIC_HELPER", ""))


class DoorbellSettingsTests(unittest.TestCase):
    def test_only_known_keys_and_an_adapter_inside_the_helpers_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = os.path.join(tmp, "agent-letterbox-cmux")
            os.makedirs(os.path.join(install, "bin"))
            os.makedirs(os.path.join(install, "adapters"))
            helper = os.path.join(install, "bin", "letterbox")
            adapter = os.path.join(install, "adapters", "cmux.sh")
            pathlib.Path(adapter).write_text("#!/bin/sh\n")
            root = os.path.join(tmp, "box")
            os.makedirs(root)
            env_sh = pathlib.Path(root, "env.sh")
            env_sh.write_text(f"export LETTERBOX_DIR='{root}'\nexport LETTERBOX_DOORBELL='{adapter}'\n"
                              "export LETTERBOX_CMUX_REGISTRY='/r.tsv'\nexport PATH='/evil'\n"
                              'export LETTERBOX_CMUX_SUBMIT="$(touch pwned)"\n')
            self.assertEqual(doorbell(root, helper),
                             {"LETTERBOX_DOORBELL": os.path.realpath(adapter), "LETTERBOX_CMUX_REGISTRY": "/r.tsv"})
            env_sh.write_text(f"export LETTERBOX_DOORBELL='{os.path.join(tmp, 'elsewhere.sh')}'\n")
            pathlib.Path(tmp, "elsewhere.sh").write_text("#!/bin/sh\n")
            self.assertEqual(doorbell(root, helper), {})

    def test_a_changed_adapter_is_left_out_so_letters_go_without_ringing(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = os.path.join(tmp, "cmux.sh")
            pathlib.Path(adapter).write_text("#!/bin/sh\n")
            helper = PinnedHelper("/x", "0" * 64, bus_dir=tmp, kind="letterbox",
                                  doorbell={"LETTERBOX_DOORBELL": adapter}, doorbell_sha256=file_sha256(adapter))
            self.assertEqual(helper._env()["LETTERBOX_DOORBELL"], adapter)
            pathlib.Path(adapter).write_text("#!/bin/sh\necho changed\n")
            self.assertNotIn("LETTERBOX_DOORBELL", helper._env())
            self.assertEqual((helper._env()["LETTERBOX_AGENT"], helper._env()["LETTERBOX_DIR"]), ("desk", tmp))


@unittest.skipUnless(PUBLIC_HELPER and os.access(PUBLIC_HELPER, os.X_OK), "set BELL_DESK_TEST_PUBLIC_HELPER")
class PublicLetterboxTests(unittest.TestCase):
    """The real public helper on a scratch letterbox, with no doorbell configured: nothing can ring."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "box")
        make_bus(self.root, ["alice", "bob"])
        self.state = os.path.join(self.tmp.name, "state")

    def tearDown(self):
        self.tmp.cleanup()

    def setup_cli(self, *extra):
        return cli.main(["--state", self.state, "setup", "--letterbox-dir", self.root, "--helper", PUBLIC_HELPER, *extra])

    def test_setup_then_a_letter_as_desk(self):
        self.assertEqual(self.setup_cli("--yes"), 0)
        config = State(self.state).config()
        self.assertIs(config["phone"], False)
        self.assertEqual((config["helper_kind"], config["seats"], config["bus"]), ("letterbox", ["alice", "bob"], os.path.realpath(self.root)))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "desk", "inbox")))
        helper = PinnedHelper(config["helper"], config["helper_sha256"], bus_dir=config["bus"], env_path=ISOLATED_PATH,
                              kind="letterbox")
        result = helper.send("alice", "Hello from the desk.", re_id="2026-10-07T120000-alice-info-x-1234abcd")
        self.assertEqual(result["status"], "sent", result)
        self.assertEqual(result["ring"], {"outcome": "no_live_surface", "reason": "adapter_unavailable"})
        fields = read_header(os.path.join(config["bus"], "alice", "inbox", result["id"] + ".md"))
        self.assertEqual((fields["from"], fields["to"], fields["type"], fields["priority"], fields["re"]),
                         ("desk", "alice", "info", "now", "2026-10-07T120000-alice-info-x-1234abcd"))

    def test_setup_refuses_agents_that_are_not_in_the_letterbox_and_an_old_helper(self):
        self.assertEqual(self.setup_cli("--yes", "--agents", "alice,mallory"), 2)
        old = os.path.join(self.tmp.name, "old-letterbox")
        pathlib.Path(old).write_text("#!/bin/sh\necho 'letterbox 0.2.0'\n")
        os.chmod(old, stat.S_IRWXU)
        self.assertEqual(cli.main(["--state", self.state, "setup", "--letterbox-dir", self.root, "--helper", old, "--yes"]), 2)
        self.assertEqual(State(self.state).config(), {})


EDITIONS = {e: os.path.expanduser(os.environ.get(f"BELL_DESK_TEST_{e.upper()}_REPO", ""))
            for e in ("cmux", "tmux", "zellij", "herdr")}


@unittest.skipUnless(all(os.path.isdir(d) for d in EDITIONS.values()), "set BELL_DESK_TEST_{CMUX,TMUX,ZELLIJ,HERDR}_REPO")
class EveryPublicEditionTests(unittest.TestCase):
    """Each edition's published main branch (via git archive; the checkouts are not touched), on a scratch letterbox.
    No multiplexer is on PATH and the pane registry is empty, so nothing can ring."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.installs = {}
        for edition, repo in EDITIONS.items():
            install = os.path.join(cls.tmp.name, f"agent-letterbox-{edition}")
            os.makedirs(install)
            archive = subprocess.run(["git", "-C", repo, "archive", "origin/main"], capture_output=True, check=True)
            subprocess.run(["tar", "-x", "-C", install], input=archive.stdout, check=True)
            cls.installs[edition] = install
        cls.fake_bin = os.path.join(cls.tmp.name, "fake-bin")
        os.makedirs(cls.fake_bin)
        for name in EDITIONS:
            pathlib.Path(cls.fake_bin, name).write_text("#!/bin/sh\nexit 1\n")
            os.chmod(os.path.join(cls.fake_bin, name), stat.S_IRWXU)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def letterbox_for(self, edition, with_doorbell):
        root = os.path.join(self.tmp.name, f"box-{edition}-{with_doorbell}")
        make_bus(root, ["alice"])
        if with_doorbell:
            adapter = os.path.join(self.installs[edition], "adapters", f"{edition}.sh")
            registry = os.path.join(root, f"{edition}-agents.tsv")
            pathlib.Path(registry).write_text("")
            pathlib.Path(root, "env.sh").write_text(
                f"export LETTERBOX_DIR='{root}'\nexport LETTERBOX_DOORBELL='{adapter}'\n"
                f"export LETTERBOX_{edition.upper()}_REGISTRY='{registry}'\n")
        return root

    def test_setup_and_a_letter_as_desk_on_each_edition(self):
        for edition, install in self.installs.items():
            for with_doorbell in (False, True):
                with self.subTest(edition=edition, doorbell=with_doorbell):
                    root = self.letterbox_for(edition, with_doorbell)
                    state = os.path.join(self.tmp.name, f"state-{edition}-{with_doorbell}")
                    helper_path = os.path.join(install, "bin", "letterbox")
                    self.assertEqual(cli.main(["--state", state, "setup", "--letterbox-dir", root, "--helper", helper_path, "--yes"]), 0)
                    config = State(state).config()
                    self.assertEqual(bool(config["doorbell"]), with_doorbell)
                    helper = PinnedHelper(config["helper"], config["helper_sha256"], bus_dir=config["bus"],
                                          env_path=f"{self.fake_bin}:{ISOLATED_PATH}", kind="letterbox",
                                          doorbell=config["doorbell"], doorbell_sha256=config["doorbell_sha256"])
                    result = helper.send("alice", f"Hello {edition}.", re_id="2026-10-07T120000-alice-info-x-1234abcd")
                    self.assertEqual(result["status"], "sent", result)
                    self.assertEqual(result["ring"]["outcome"], "no_live_surface", result)
                    if with_doorbell:
                        self.assertNotEqual(result["ring"]["reason"], "adapter_unavailable", "the adapter never ran")
                    else:
                        self.assertEqual(result["ring"]["reason"], "adapter_unavailable")
                    path = os.path.join(config["bus"], "alice", "inbox", result["id"] + ".md")
                    fields = read_header(path)
                    self.assertEqual((fields["from"], fields["priority"], fields["re"]),
                                     ("desk", "now", "2026-10-07T120000-alice-info-x-1234abcd"))
                    self.assertTrue(pathlib.Path(path).read_text().rstrip().endswith(f"Hello {edition}."))


class HTTPServerStartupTests(unittest.TestCase):
    def test_accepts_connections_without_waiting_for_reverse_dns(self):
        release_dns = threading.Event()
        ready = threading.Event()
        servers = []
        errors = []

        def blocked_dns(*args):
            release_dns.wait(10)
            return "slow.example"

        def start():
            try:
                servers.append(DeskHTTPServer(("127.0.0.1", 0), None))
            except Exception as error:
                errors.append(error)
            finally:
                ready.set()

        with patch("socket.getfqdn", side_effect=blocked_dns):
            worker = threading.Thread(target=start, daemon=True)
            worker.start()
            try:
                self.assertTrue(ready.wait(1), "server startup waited for reverse DNS")
                self.assertEqual(errors, [])
                server = servers[0]
                with socket.create_connection(server.server_address, timeout=1):
                    pass
                self.assertEqual(server.server_name, "127.0.0.1")
                self.assertEqual(server.server_port, server.server_address[1])
            finally:
                release_dns.set()
                worker.join(2)
                for server in servers:
                    server.server_close()


class PhoneSideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        bus = os.path.join(root, "bus")
        make_bus(bus, ["desk", "alice"])
        os.makedirs(os.path.join(root, "calls"))
        self.mode_file = pathlib.Path(root, "mode")
        self.mode_file.write_text("ok")
        helper_path = os.path.join(root, "helper.sh")
        pathlib.Path(helper_path).write_text(FAKE_HELPER)
        os.chmod(helper_path, stat.S_IRWXU)
        helper = PinnedHelper(helper_path, file_sha256(helper_path), bus_dir=bus, env_path=ISOLATED_PATH)
        self.state = State(os.path.join(root, "state"))
        self.app = App(self.state, helper, bus, ["alice"], console=lambda _: None, phone=False)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.phone = PhoneSide(self.app, "127.0.0.1", self.port)
        self.calls = pathlib.Path(root, "calls")

    def tearDown(self):
        self.phone.set_enabled(False)
        self.app.stop()
        self.tmp.cleanup()

    def connectable(self):
        with socket.socket() as probe:
            probe.settimeout(1)
            return probe.connect_ex(("127.0.0.1", self.port)) == 0

    def test_live_switch_closes_the_port_discovery_and_streams_then_reopens(self):
        class Bonjour:
            stopped = False
            def terminate(self):
                self.stopped = True

        first = Bonjour()
        self.state.add_device("fixture-phone", {"name": "iPhone"})
        with patch("bell_desk.phone.subprocess.Popen", return_value=first):
            self.assertEqual(self.phone.set_enabled(True), (True, ""))
        self.assertTrue(self.connectable())
        stream = queue.Queue()
        self.app.subscribe(stream, "fixture-phone")
        self.app.pairings["pending"] = {"state": "pending"}
        self.assertEqual(self.phone.set_enabled(False), (False, ""))
        self.assertFalse(self.connectable())
        self.assertTrue(first.stopped)
        self.assertIsNone(stream.get_nowait())
        self.assertEqual(self.app.subscribers, {})
        self.assertEqual(self.app.pairings["pending"]["state"], "expired")
        self.assertIn("fixture-phone", self.state.devices())
        second = Bonjour()
        with patch("bell_desk.phone.subprocess.Popen", return_value=second):
            self.assertEqual(self.phone.set_enabled(True), (True, ""))
        self.assertTrue(self.connectable())

    def test_live_disable_waits_for_a_slow_send_to_be_recorded(self):
        self.phone.no_bonjour = True
        self.assertEqual(self.phone.set_enabled(True), (True, ""))
        self.mode_file.write_text("slow")
        sent = {}
        worker = threading.Thread(target=lambda: sent.update(result=self.app.compose_from_mac(["alice"], "Slow letter")))
        worker.start()
        deadline = time.time() + 5
        while not list(self.calls.glob("args.*")) and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(list(self.calls.glob("args.*")))
        switch = threading.Thread(target=lambda: self.phone.set_enabled(False))
        switch.start()
        self.assertTrue(switch.is_alive())
        worker.join(5)
        switch.join(5)
        self.assertEqual(sent["result"]["status"], "sent")
        self.assertEqual(self.state.sent_letters()[0]["status"], "sent")
        self.assertFalse(self.connectable())


class ServiceTests(unittest.TestCase):
    def test_serve_requires_a_letterbox_folder_and_agents(self):
        with tempfile.TemporaryDirectory() as root:
            state = State(root)
            bus = os.path.join(root, "box")
            for config in ({}, {"bus": bus}, {"seats": ["alice"]},
                           {"bus": bus, "seats": "alice,bob"},
                           {"bus": bus, "seats": ["alice", 7]},
                           {"bus": bus, "seats": ["Alice"]}):
                state.save_config(config)
                error = StringIO()
                with redirect_stderr(error):
                    self.assertEqual(cli.main(["--state", root, "serve"]), 2)
                self.assertIn("run `bell-desk setup`", error.getvalue())
            state.save_config({})
            error = StringIO()
            with redirect_stderr(error):
                self.assertEqual(cli.main(["--state", root, "serve", "--bus", root, "--seats", "alice"]), 2)
            self.assertIn("no pinned helper", error.getvalue())

    def test_readiness_requires_the_login_item_to_own_the_lock(self):
        with tempfile.TemporaryDirectory() as root:
            holder = State(root)
            self.assertIsNone(service.lock_holder_pid(root))
            holder.lock_instance()
            try:
                self.assertEqual(service.lock_holder_pid(root), os.getpid())
                self.assertTrue(service.login_item_owns_lock(root, f"running, pid {os.getpid()}"))
                self.assertFalse(service.login_item_owns_lock(root, f"running, pid {os.getpid() + 1}"))
                self.assertFalse(service.login_item_owns_lock(root, "running"))
            finally:
                holder.unlock_instance()
            self.assertIsNone(service.lock_holder_pid(root))

    def test_phone_switch_keeps_legacy_pairings_and_the_cli_keeps_them_on_disable(self):
        with tempfile.TemporaryDirectory() as root:
            state = State(root)
            self.assertFalse(cli.phone_enabled(state.config(), state))
            state.add_device("old-phone", {"name": "iPhone"})
            self.assertTrue(cli.phone_enabled(state.config(), state))
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(cli.main(["--state", root, "phone", "disable"]), 0)
            self.assertIn("running Desk applies this shortly", out.getvalue())
            self.assertFalse(state.config()["phone"])
            self.assertIn("old-phone", state.devices())
            with redirect_stdout(StringIO()):
                self.assertEqual(cli.main(["--state", root, "phone", "enable"]), 0)
            self.assertTrue(state.config()["phone"])

    def test_phone_off_has_no_socket_or_bonjour_and_phone_on_has_both(self):
        with tempfile.TemporaryDirectory() as root:
            bus = os.path.join(root, "bus")
            make_bus(bus, ["desk", "alice"])
            helper = os.path.join(root, "helper.sh")
            pathlib.Path(helper).write_text("#!/bin/sh\n")
            marker = os.path.join(root, "dns-sd-attempted")
            state_dir = os.path.join(root, "state")
            state = State(state_dir)
            child_code = """import pathlib, subprocess, sys
from bell_desk import cli, macui
macui.WINDOW = '/nonexistent/bell-pair'
original = subprocess.Popen
def start(args, *a, **kw):
    if args[0] == 'dns-sd':
        pathlib.Path(sys.argv[1]).write_text('started')
        class Bonjour:
            def terminate(self): pathlib.Path(sys.argv[1] + '.stopped').write_text('stopped')
        return Bonjour()
    return original(args, *a, **kw)
subprocess.Popen = start
sys.exit(cli.main(sys.argv[2:]))
"""
            for phone in (False, True):
                with self.subTest(phone=phone):
                    pathlib.Path(marker).unlink(missing_ok=True)
                    state.save_config({"helper": helper, "helper_sha256": file_sha256(helper), "helper_kind": "letterbox",
                                       "bus": bus, "seats": ["alice"], "phone": phone})
                    with socket.socket() as probe:
                        probe.bind(("127.0.0.1", 0))
                        port = probe.getsockname()[1]
                    env = {**os.environ, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
                    env.pop("XPC_SERVICE_NAME", None)
                    process = subprocess.Popen([sys.executable, "-c", child_code, marker, "--state", state_dir,
                                                "serve", "--host", "127.0.0.1", "--port", str(port)],
                                               cwd=str(pathlib.Path(__file__).resolve().parents[1]), env=env,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                    try:
                        lock_deadline = time.monotonic() + 15
                        while service.lock_free(state_dir) and process.poll() is None and time.monotonic() < lock_deadline:
                            time.sleep(0.02)
                        self.assertIsNone(process.poll(), process.stderr.read() if process.poll() is not None else "")
                        self.assertFalse(service.lock_free(state_dir), "listener did not take its state lock")
                        connect_deadline = time.monotonic() + 15
                        connected = False
                        while True:
                            with socket.socket() as probe:
                                probe.settimeout(0.2)
                                connected = probe.connect_ex(("127.0.0.1", port)) == 0
                            if not phone or (connected and os.path.exists(marker)) or time.monotonic() >= connect_deadline:
                                break
                            time.sleep(0.02)
                        self.assertEqual(connected, phone)
                        self.assertEqual(os.path.exists(marker), phone)
                        if phone:
                            state.save_config({**state.config(), "phone": False})
                            stopped = marker + ".stopped"
                            until = time.time() + 5
                            while not os.path.exists(stopped) and time.time() < until:
                                time.sleep(0.05)
                            self.assertTrue(os.path.exists(stopped), "CLI config change did not stop Bonjour")
                            with socket.socket() as probe:
                                self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
                            state.save_config({**state.config(), "phone": True})
                            until = time.time() + 5
                            while time.time() < until:
                                with socket.socket() as probe:
                                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                                        break
                                time.sleep(0.05)
                            else:
                                self.fail("CLI config change did not reopen the phone port")
                    finally:
                        process.terminate()
                        process.wait(timeout=5)

    def test_a_login_item_without_the_flag_still_waits_rather_than_standing_down(self):
        self.assertTrue(service.launched_by_login_item({"XPC_SERVICE_NAME": service.LABEL}))
        self.assertFalse(service.launched_by_login_item({"XPC_SERVICE_NAME": "com.example.other"}))
        with tempfile.TemporaryDirectory() as root:
            helper = os.path.join(root, "helper.sh")
            pathlib.Path(helper).write_text("#!/bin/sh\n")
            bus = os.path.join(root, "bus")
            make_bus(bus, ["desk", "alice"])
            holder = State(root)
            holder.save_config({"helper": helper, "helper_sha256": file_sha256(helper), "helper_kind": "letterbox",
                                "bus": bus, "seats": ["alice"]})
            holder.lock_instance()
            try:
                old_plist = subprocess.Popen([sys.executable, "-m", "bell_desk.cli", "--state", root, "serve", "--no-bonjour", "--port", "0"],
                                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                             env={**os.environ, "XPC_SERVICE_NAME": service.LABEL},
                                             cwd=str(pathlib.Path(__file__).resolve().parents[1]))
                self.assertIn("waiting for it to stop", old_plist.stdout.readline())
                time.sleep(1)
                self.assertIsNone(old_plist.poll(), "an old login item stood down instead of waiting")
            finally:
                old_plist.kill()
                old_plist.wait()
                holder.unlock_instance()

    def test_the_login_item_waits_for_a_running_listener_then_takes_over(self):
        self.assertIn("--wait-for-lock", service.program("/venv/bin", "/s"))
        with tempfile.TemporaryDirectory() as root:
            holder = State(root)
            holder.lock_instance()
            took_over, waited = threading.Event(), threading.Event()
            threading.Thread(target=lambda: (State(root).lock_instance(wait=True, waiting=waited.set), took_over.set()),
                             daemon=True).start()
            self.assertTrue(waited.wait(5))
            self.assertFalse(took_over.wait(1))
            holder.unlock_instance()
            self.assertTrue(took_over.wait(5))

    def test_a_second_listener_stands_down_without_a_crash_and_install_sees_the_lock(self):
        with tempfile.TemporaryDirectory() as root:
            helper = os.path.join(root, "helper.sh")
            pathlib.Path(helper).write_text("#!/bin/sh\n")
            bus = os.path.join(root, "bus")
            make_bus(bus, ["desk", "alice"])
            holder = State(root)
            holder.save_config({"helper": helper, "helper_sha256": file_sha256(helper), "helper_kind": "letterbox",
                                "bus": bus, "seats": ["alice"]})
            self.assertTrue(service.lock_free(root))
            holder.lock_instance()
            try:
                self.assertFalse(service.lock_free(root))
                second = subprocess.run([sys.executable, "-m", "bell_desk.cli", "--state", root, "serve", "--no-bonjour",
                                         "--port", "0"], capture_output=True, text=True, timeout=20,
                                        cwd=str(pathlib.Path(__file__).resolve().parents[1]))
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertIn("standing down", second.stderr)
            finally:
                holder.unlock_instance()
            self.assertTrue(service.lock_free(root))

    def test_the_login_item_restarts_after_a_crash_but_not_after_quit(self):
        spec = service.plist(["/venv/bin/bell-desk", "--state", "/s", "serve"], "/s")
        self.assertEqual(spec["ProgramArguments"], ["/venv/bin/bell-desk", "--state", "/s", "serve"])
        self.assertEqual((spec["RunAtLoad"], spec["KeepAlive"]), (True, {"SuccessfulExit": False}))
        self.assertEqual((spec["StandardOutPath"], spec["StandardErrorPath"]), ("/s/listener.log", "/s/listener.log"))


class ConversationTests(DeskSetup):
    def setUp(self):
        super().setUp()
        self.view = self.bridge.conversations

    def test_join_includes_phone_and_mac(self):
        self.to_desk('2026-10-07T090000-bob-info-one')
        self.app.dictation(json.dumps({'request_id':str(uuid.uuid4()),'to':['bob'],'text':'Phone'}).encode())
        self.app.compose_from_mac(['bob'],'Mac')
        self.assertEqual([r['via'] for r in self.view.rows('bob')],['','phone','mac'])

    def test_mixed_routing_and_retry_only_failed_copy_once(self):
        self.to_desk('2026-10-07T090000-alice-info-one',sender='alice')
        latest='2026-10-07T100000-alice-info-two'
        self.to_desk(latest,sender='alice')
        self.mode('split')
        result=self.view.send({'agent':'alice','to':['alice','bob'],'text':'Typed'})
        a,b=result['results']
        self.assertEqual((a['re'],a['text']),(latest,'Typed'))
        self.assertEqual(b['re'],'')
        self.assertIn('Also addressed to: alice',b['text'])
        self.assertEqual(self.app.resend_from_mac(result['request_id'],'alice')['reason'],'already_delivered')
        self.mode('ok')
        self.assertEqual(self.app.resend_from_mac(result['request_id'],'bob')['status'],'sent')
        count=self.helper_calls()
        self.assertEqual(self.app.resend_from_mac(result['request_id'],'bob')['reason'],'already_delivered')
        self.assertEqual(self.helper_calls(),count)

    def test_removing_agent_sends_new_only(self):
        self.to_desk('2026-10-07T090000-alice-info-one',sender='alice')
        result=self.view.send({'agent':'alice','to':['bob'],'text':'New'})
        self.assertEqual(result['results'][0]['re'],'')

    def test_done_keep_and_new_mail_never_change_letter_files(self):
        first=self.to_desk('2026-10-07T090000-bob-info-one')
        self.view.update({'action':'done','seat':'bob'})
        def row():return next(r for r in self.view.snapshot()['conversations'] if r['seat']=='bob')
        self.assertTrue(row()['done'])
        self.assertTrue(os.path.exists(first))
        self.to_desk('2026-10-08T090000-bob-info-two')
        self.assertFalse(row()['done'])
        self.assertEqual(row()['unread'],1)
        self.assertEqual(len(row()['messages']),1) # Unread mail is never hidden by Keep.
        self.view.update({'action':'read','seat':'bob'})
        self.assertEqual(len(row()['messages']),1) # Opening it must not remove it mid-read.
        self.view.update({'action':'close'})
        self.assertEqual(row()['messages'],[])
        self.view.update({'action':'settings','keep':3650})
        self.assertEqual(len(row()['messages']),2)
        self.assertTrue(os.path.exists(first))

    def test_ring_switch_preserves_phone_and_connection(self):
        q=queue.Queue();self.app.subscribe(q,'fixture')
        self.view.update({'action':'settings','rings':False})
        event={'from':'bob','to':'desk','letter_type':'info','id':'fixture'}
        self.app.publish_mail(event)
        self.assertTrue(q.empty());self.assertTrue(self.app.phone_active);self.assertIn(q,self.app.subscribers)
        self.view.update({'action':'settings','rings':True})
        self.app.publish_mail(event);self.assertEqual(q.get_nowait()['id'],'fixture')

    def test_cards_are_removed(self):
        from bell_desk import macui
        self.assertFalse(hasattr(macui,'show_letter'));self.assertFalse(hasattr(macui,'reply_sent'))

    def test_picture_validation_access_and_missing_changed(self):
        from bell_desk import attachments
        import base64,hashlib
        root=pathlib.Path(self.root,'pictures');root.mkdir()
        probe=root/'probe-fixture';probe.write_bytes(b'probe')
        digest=hashlib.sha256(b'probe').hexdigest()
        self.state.save_config({'attachments':{'dir':str(root.resolve()),'readers':{'alice':digest},'probes':{'alice':{'name':probe.name,'sha256':digest}}}})
        self.assertTrue(attachments.verified(self.state.config(),self.bus,'alice'))
        self.assertFalse(attachments.verified(self.state.config(),self.bus,'bob'))
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        rec=attachments.copy_picture(self.state.config(),self.bus,{'data':base64.b64encode(png).decode()})
        self.assertEqual(attachments.inspect_copy(self.state.config(),self.bus,rec),'ok')
        pathlib.Path(rec['path']).write_bytes(b'changed')
        self.assertEqual(attachments.inspect_copy(self.state.config(),self.bus,rec),'changed')
        pathlib.Path(rec['path']).unlink()
        self.assertEqual(attachments.inspect_copy(self.state.config(),self.bus,rec),'missing')
        source=pathlib.Path(self.root,'source.png');source.write_bytes(png)
        link=pathlib.Path(self.root,'link.png');link.symlink_to(source)
        with self.assertRaises(attachments.PictureError):attachments.copy_picture(self.state.config(),self.bus,{'path':str(link)})
        with self.assertRaises(attachments.PictureError):attachments.read_regular(source, expected=probe.stat())
        parent=pathlib.Path(self.root,'parent-link');parent.symlink_to(source.parent, target_is_directory=True)
        parent_rec=attachments.copy_picture(self.state.config(),self.bus,{'path':str(parent/source.name)})
        self.assertEqual(attachments.inspect_copy(self.state.config(),self.bus,parent_rec),'ok')
        captured={'data':base64.b64encode(attachments.read_regular(source)).decode()}
        source.unlink()
        sent=self.view.send({'to':['alice'],'text':'temporary screenshot','picture':captured})
        self.assertEqual(sent['status'],'sent')
        self.assertEqual(sent['results'][0]['status'],'published')
        with self.assertRaises(attachments.PictureError):attachments.validate(b'x'*(attachments.MAX_BYTES+1))
        probe.write_bytes(b'changed');self.assertFalse(attachments.verified(self.state.config(),self.bus,'alice'))

    def test_picture_failure_is_per_copy_and_metadata_is_last(self):
        from bell_desk import attachments
        import base64,hashlib
        root=pathlib.Path(self.root,'pictures');root.mkdir()
        probe=root/'probe-fixture';probe.write_bytes(b'probe');digest=hashlib.sha256(b'probe').hexdigest()
        self.state.save_config({'attachments':{'dir':str(root.resolve()),'readers':{'alice':digest},'probes':{'alice':{'name':probe.name,'sha256':digest}}}})
        picture={'data':base64.b64encode(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')).decode()}
        result=self.view.send({'to':['alice','bob'],'text':'x'*2000,'picture':picture})
        self.assertEqual(result['status'],'partial')
        self.assertEqual([r['status'] for r in result['results']],['published','not_attempted'])
        self.assertEqual(result['results'][1]['reason'],'picture_access')
        self.assertTrue(result['results'][0]['text'].splitlines()[-1].startswith('Desk picture: '))
        self.assertEqual(self.helper_calls(),1)
        self.assertEqual(self.app.resend_from_mac(result['request_id'],'bob')['reason'],'picture_access')
        self.assertEqual(self.helper_calls(),1)

    def test_attachment_verification_requires_reply_from_requested_seat(self):
        from bell_desk import attachments
        config={'bus':self.bus,'seats':['alice','bob'],'helper':self.app.helper.path,'helper_sha256':self.app.helper.sha256,'helper_kind':'letterbox'}
        self.state.save_config(config)
        attachments.command(self.state,'configure',os.path.join(self.root,'pictures'))
        attachments.command(self.state,'verify','alice')
        probe=self.state.config()['attachments']['probes']['alice']
        rid='2026-10-19T120000-alice-result-proof'
        path=self.to_desk(rid,sender='alice',re=probe['letter'])
        text=pathlib.Path(path).read_text().replace('body text that must never leave the Mac',probe['sha256'])
        pathlib.Path(path).write_text(text)
        attachments.command(self.state,'accept','alice',rid)
        self.assertTrue(attachments.verified(self.state.config(),self.bus,'alice'))
        self.assertFalse(attachments.verified(self.state.config(),self.bus,'bob'))

    def picture_setup(self, folder=''):
        from bell_desk import attachments
        self.state.save_config({'bus':self.bus,'seats':['alice','bob'],'helper':self.app.helper.path,
                                'helper_sha256':self.app.helper.sha256,'helper_kind':'letterbox'})
        return attachments.command(self.state, 'setup', folder)

    def picture_reply(self, seat='alice', **changes):
        probe=self.state.config()['attachments']['probes'][seat]
        rid='2026-10-19T120000-'+seat+'-result-'+uuid.uuid4().hex[:8]
        path=self.to_desk(rid,sender=changes.get('sender',seat),re=changes.get('re',probe['letter']))
        text=pathlib.Path(path).read_text().replace('body text that must never leave the Mac',changes.get('body',probe['sha256']))
        pathlib.Path(path).write_text(text)
        return rid,path

    def test_picture_setup_default_all_seats_and_idempotency(self):
        from bell_desk import attachments
        output=self.picture_setup()
        cfg=self.state.config();root=pathlib.Path(cfg['attachments']['dir'])
        self.assertEqual(root,pathlib.Path(self.bus).resolve().parent/'desk-attachments')
        self.assertNotIn(pathlib.Path(self.bus).resolve(),root.parents)
        self.assertEqual(stat.S_IMODE(root.stat().st_mode),0o700)
        self.assertIn(str(root),output)
        self.assertEqual(set(cfg['attachments']['probes']),{'alice','bob'})
        self.assertEqual(self.helper_calls(),2)
        attachments.command(self.state,'setup')
        self.assertEqual(self.state.config(),cfg)
        self.assertEqual(self.helper_calls(),2) # Valid outstanding probes are reused.
        self.picture_reply('alice');self.picture_reply('bob')
        logs=[];self.assertEqual(attachments.accept_replies(self.state,logs.append),['alice','bob'])
        accepted=self.state.config()
        attachments.command(self.state,'setup')
        self.assertEqual(self.state.config(),accepted)
        self.assertEqual(self.helper_calls(),2)
        self.assertEqual(attachments.accept_replies(self.state,logs.append),[])
        self.assertEqual(logs,['pictures: alice verified','pictures: bob verified'])
        self.assertEqual(self.view.snapshot()['picture_readers'],['alice','bob'])

    def test_picture_setup_resolves_root_and_refuses_unsafe_destinations(self):
        from bell_desk import attachments
        self.picture_setup()
        link=pathlib.Path(self.root,'bus-link');link.symlink_to(self.bus,target_is_directory=True)
        self.state.save_config({**self.state.config(),'bus':str(link)})
        self.assertIn(str(pathlib.Path(self.bus).resolve().parent/'desk-attachments'),attachments.command(self.state,'setup'))
        for destination in [self.bus,os.path.join(self.bus,'pictures')]:
            with self.assertRaises(attachments.PictureError):attachments.command(self.state,'setup',destination)
        collision=pathlib.Path(self.root,'collision');collision.write_text('leave me')
        with self.assertRaises(attachments.PictureError):attachments.command(self.state,'setup',str(collision))
        unsafe=pathlib.Path(self.root,'unsafe');unsafe.mkdir(mode=0o777);unsafe.chmod(0o777)
        with self.assertRaises(attachments.PictureError):attachments.command(self.state,'setup',str(unsafe))
        dedicated=pathlib.Path(self.root,'not-empty');dedicated.mkdir(mode=0o700);(dedicated/'unrelated').write_text('keep')
        with self.assertRaises(attachments.PictureError):attachments.command(self.state,'setup',str(dedicated))
        alias=pathlib.Path(self.root,'folder-link');alias.symlink_to(dedicated,target_is_directory=True)
        with self.assertRaises(attachments.PictureError):attachments.command(self.state,'setup',str(alias))
        with patch('bell_desk.attachments.os.access',return_value=False):
            with self.assertRaises(attachments.PictureError):attachments.command(self.state,'setup',os.path.join(self.root,'no-write'))
        self.assertEqual(collision.read_text(),'leave me')
        self.assertEqual((dedicated/'unrelated').read_text(),'keep')

    def test_picture_auto_accept_rejects_wrong_and_unsolicited_proofs(self):
        from bell_desk import attachments
        self.picture_setup()
        probe=self.state.config()['attachments']['probes']['alice']
        for change in [{'sender':'bob'},{'re':'unrelated'},{'body':'0'*64},
                       {'body':probe['sha256'].upper()},{'body':probe['sha256']+' extra'},
                       {'body':probe['sha256'][:4]+'\u200e'+probe['sha256'][4:]}]:
            self.picture_reply(**change)
        _,wrong_to=self.picture_reply()
        pathlib.Path(wrong_to).write_text(pathlib.Path(wrong_to).read_text().replace('to: desk','to: bob'))
        rid,linked_reply=self.picture_reply()
        moved=pathlib.Path(self.root,'linked-reply-source');pathlib.Path(linked_reply).rename(moved)
        pathlib.Path(linked_reply).symlink_to(moved)
        self.assertEqual(attachments.accept_replies(self.state),[])
        self.assertFalse(attachments.verified(self.state.config(),self.bus,'alice'))
        rid,path=self.picture_reply()
        # New State simulates restart: no watcher baseline or in-memory pending set is needed.
        self.assertEqual(attachments.accept_replies(State(self.state.root)),['alice'])
        self.assertTrue(attachments.verified(self.state.config(),self.bus,'alice'))
        with self.assertRaises(attachments.PictureError):attachments.command(self.state,'accept','alice',rid)
        cfg=self.state.config();cfg['attachments']['readers'].pop('alice');self.state.save_config(cfg)
        self.assertEqual(attachments.accept_replies(self.state),[]) # Consumed proof cannot grant twice.

    def test_picture_auto_accept_rejects_changed_expired_stale_and_reconfigured_proofs(self):
        from bell_desk import attachments
        self.picture_setup();rid,path=self.picture_reply()
        cfg=self.state.config();probe=cfg['attachments']['probes']['alice']
        source=pathlib.Path(cfg['attachments']['dir'],probe['name']);original=source.read_bytes()
        source.write_bytes(b'changed')
        self.assertEqual(attachments.accept_replies(self.state),[])
        source.unlink();replacement=source.with_name('replacement');replacement.write_bytes(original);source.symlink_to(replacement)
        self.assertEqual(attachments.accept_replies(self.state),[])
        source.unlink();source.write_bytes(original)
        with patch('bell_desk.attachments.time.time',return_value=probe['expires_at']+1):
            self.assertEqual(attachments.accept_replies(self.state),[])
        attachments.command(self.state,'verify','alice')
        self.assertEqual(attachments.accept_replies(self.state),[]) # Previous request id is stale.
        self.picture_reply()
        cfg=self.state.config();cfg['attachments']['probes']['alice']['dir']=str(pathlib.Path(self.root,'different'))
        self.state.save_config(cfg)
        self.assertEqual(attachments.accept_replies(self.state),[])
        attachments.command(self.state,'configure',os.path.join(self.root,'new-pictures'))
        self.assertEqual(attachments.accept_replies(self.state),[])

    def test_picture_reprobe_failure_disables_old_proof_and_setup_tries_every_seat(self):
        from bell_desk import attachments
        self.picture_setup();self.picture_reply();attachments.accept_replies(self.state)
        self.assertTrue(attachments.verified(self.state.config(),self.bus,'alice'))
        with patch('bell_desk.sender.PinnedHelper.send',return_value={'status':'not_sent'}):
            with self.assertRaises(attachments.PictureError):attachments.command(self.state,'verify','alice')
        self.assertFalse(attachments.verified(self.state.config(),self.bus,'alice'))
        attachments.command(self.state,'configure',os.path.join(self.root,'new-pictures'))
        calls=[]
        def send(seat,text):
            calls.append(seat)
            return {'status':'not_sent'} if seat=='alice' else {'status':'sent','id':'proof-bob'}
        with patch('bell_desk.sender.PinnedHelper.send',side_effect=send):
            with self.assertRaises(attachments.PictureError):attachments.command(self.state,'setup')
        self.assertEqual(calls,['alice','bob'])
        self.assertIn('bob',self.state.config()['attachments']['probes'])

    def test_picture_setup_window_can_initialize_default_for_one_seat(self):
        self.state.save_config({'bus':self.bus,'seats':['alice','bob'],'helper':self.app.helper.path,
                                'helper_sha256':self.app.helper.sha256,'helper_kind':'letterbox'})
        self.bridge.handle({'picture_setup':{'seat':'bob'}})
        self.assertEqual(self.wait_for('picture_setup')['summary']['status'],'sent')
        self.assertEqual(set(self.state.config()['attachments']['probes']),{'bob'})
        self.assertEqual(self.helper_calls(),1)

    def test_picture_setup_cli_and_concurrent_config_updates(self):
        output=StringIO()
        self.state.save_config({'bus':self.bus,'seats':['alice','bob'],'helper':self.app.helper.path,
                                'helper_sha256':self.app.helper.sha256,'helper_kind':'letterbox'})
        with redirect_stdout(output):
            self.assertEqual(cli.main(['--state',self.state.root,'attachments','setup']),0)
        self.assertIn('Picture folder:',output.getvalue())
        self.state.update_config({'counter':0})
        code="from bell_desk.store import State;import sys;s=State(sys.argv[1]);[(s.update_config(lambda cfg:{**cfg,'counter':cfg['counter']+1})) for _ in range(20)]"
        processes=[subprocess.Popen([sys.executable,'-c',code,self.state.root],cwd=str(pathlib.Path(__file__).resolve().parents[1]),stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
        for process in processes:
            _,error=process.communicate(timeout=10)
            self.assertEqual(process.returncode,0,error.decode())
        self.assertEqual(self.state.config()['counter'],40)
        self.assertEqual(set(self.state.config()['attachments']['probes']),{'alice','bob'})

    def test_picture_setup_window_action_reprobes_only_roster_seat(self):
        self.picture_setup();old=self.state.config()['attachments']['probes']['bob']['name']
        self.bridge.handle({'picture_setup':{'seat':'bob'}})
        self.assertEqual(self.wait_for('picture_setup')['summary']['status'],'sent')
        self.assertNotEqual(self.state.config()['attachments']['probes']['bob']['name'],old)
        calls=self.helper_calls()
        self.bridge.handle({'picture_setup':{'seat':'outsider'}})
        self.assertEqual(self.helper_calls(),calls)

    def test_retry_aliases_share_one_copy_lock(self):
        self.mode('silent')
        first=self.app.compose_from_mac(['bob'],'Original')
        second=self.app.resend_from_mac(first['request_id'],'bob')
        started,release=threading.Event(),threading.Event()
        def send(*args,**kwargs):
            started.set();release.wait(5)
            return {'status':'sent','id':'fixture-published','ring':{'outcome':'submitted'}}
        with patch.object(self.app.helper,'send',side_effect=send):
            results=[]
            worker=threading.Thread(target=lambda:results.append(self.app.resend_from_mac(first['request_id'],'bob')))
            worker.start();self.assertTrue(started.wait(2))
            try:
                self.assertEqual(self.app.resend_from_mac(second['request_id'],'bob')['reason'],'still_sending')
            finally:
                release.set();worker.join(5)
            self.assertEqual(results[0]['status'],'sent')
        self.assertEqual(self.app.resend_from_mac(second['request_id'],'bob')['reason'],'already_delivered')

    def test_incoming_picture_path_never_becomes_file_access(self):
        path=self.to_desk('2026-10-19T120000-bob-info-path')
        text=pathlib.Path(path).read_text().replace('body text that must never leave the Mac','Desk picture: {"path":"/etc/passwd","size":1,"sha256":"bad"}')
        pathlib.Path(path).write_text(text)
        with patch('bell_desk.attachments.inspect_copy',side_effect=AssertionError('incoming file access')):
            self.assertIn('/etc/passwd',self.view.rows('bob')[0]['text'])

    def _interrupted_mixed_restart(self, successful_before_stop):
        latest='2026-10-19T120000-alice-info-source'
        self.to_desk(latest,sender='alice')
        send=self.app.helper.send
        attempts=[]
        class Stopped(Exception):
            pass
        def interrupt(*args,**kwargs):
            if len(attempts) == successful_before_stop:
                raise Stopped()
            attempts.append(args)
            return send(*args,**kwargs)
        with patch.object(self.app.helper,'send',side_effect=interrupt):
            with self.assertRaises(Stopped):
                self.view.send({'agent':'alice','to':['alice','bob'],'text':'The exact typed text'})
        rid=self.state.sent_letters()[0]['request_id']
        helper=self.app.helper
        self.app.stop()
        self.state=State(self.state.root)
        self.app=App(self.state,helper,self.bus,['alice','bob'],console=lambda m:None)
        rows=self.state.sent_letter(rid)['results']
        self.assertEqual(rows[1]['status'],'not_attempted' if successful_before_stop == 0 else 'not_confirmed')
        # Verify actual helper arguments and body after reloading the journal.
        result=self.app.resend_from_mac(rid,'bob')
        self.assertEqual(result['status'],'sent')
        newest=max(pathlib.Path(self.calls).glob('args.*'),key=os.path.getmtime)
        self.assertNotIn('--re',newest.read_text())
        body=newest.with_name(newest.name.replace('args.','stdin.')).read_text()
        self.assertIn('Also addressed to: alice',body)
        self.assertTrue(body.startswith('The exact typed text'))
        if successful_before_stop == 0:
            self.assertEqual(self.app.resend_from_mac(rid,'alice')['status'],'sent')
            newest=max(pathlib.Path(self.calls).glob('args.*'),key=os.path.getmtime)
            self.assertEqual(newest.read_text().splitlines()[-2:],['--re',latest])
            self.assertEqual(newest.with_name(newest.name.replace('args.','stdin.')).read_text(),'The exact typed text')
        else:
            self.assertEqual(self.app.resend_from_mac(rid,'alice')['reason'],'already_delivered')

    def test_restart_before_first_copy_preserves_both_copy_plans(self):
        self._interrupted_mixed_restart(0)

    def test_restart_during_extra_copy_preserves_its_plan(self):
        self._interrupted_mixed_restart(1)

    def test_activity_for_opening_survives_read_history_keep_filter(self):
        latest='2026-10-07T090000-bob-info-one'
        self.to_desk(latest)
        self.mail.mark_read(latest)
        row=next(r for r in self.view.snapshot()['conversations'] if r['seat']=='bob')
        self.assertEqual(row['messages'],[])
        self.assertGreater(row['last_at'],0)
        self.assertEqual(row['latest'],latest)

    def test_conversation_pushes_are_deduplicated_and_force_refresh_still_sends(self):
        self.to_desk('2026-10-19T120000-bob-info-projection')
        self.bridge.push();self.bridge.push()
        self.assertEqual(len([m for m in self.sent if m['type']=='conversations']),1)
        self.assertEqual(len([m for m in self.sent if m['type']=='mail']),1)
        self.view.update({'action':'settings','rings':False})
        self.bridge.push()
        self.assertEqual(len([m for m in self.sent if m['type']=='conversations']),2)
        self.assertEqual(len([m for m in self.sent if m['type']=='mail']),1)
        self.bridge.push(force=True)
        self.assertEqual(len([m for m in self.sent if m['type']=='conversations']),3)
        self.assertEqual(len([m for m in self.sent if m['type']=='mail']),2)

    def test_native_window_uses_bundle_and_build_failure_keeps_plain_fallback(self):
        from bell_desk import macui
        self.assertIn('Letterbox Desk.app/Contents/MacOS/bell-pair',macui.WINDOW)
        build=pathlib.Path(self.root,'build');exe=build/'Letterbox Desk.app/Contents/MacOS/bell-pair'
        exe.parent.mkdir(parents=True);exe.write_text('fixture');exe.chmod(0o700)
        with patch.object(macui,'WINDOW',str(exe)):
            self.assertTrue(macui.window_available())
            (build/'window-unavailable').touch()
            self.assertFalse(macui.window_available())
            with patch.object(macui.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'123456','')) as run:
                self.assertEqual(macui.ask_for_code('fixture'),'123456')
                self.assertEqual(run.call_args.args[0][0],'osascript')


if __name__ == "__main__":
    unittest.main()


class PublicIdentityTests(unittest.TestCase):
    def test_old_launchd_identity_is_recognized_and_status_is_readable(self):
        self.assertTrue(service.launched_by_login_item({"XPC_SERVICE_NAME":service.LEGACY_LABEL}))
        missing=subprocess.CompletedProcess([],113,'','not found')
        running=subprocess.CompletedProcess([],0,'state = running\n pid = 4321\n','')
        with patch('bell_desk.service._launchctl',side_effect=[missing,running]) as launch:
            self.assertEqual(service.status(),'running, pid 4321')
            self.assertEqual(launch.call_args_list[-1].args,('print',f'gui/{os.getuid()}/{service.LEGACY_LABEL}'))

    def test_explicit_service_install_migrates_only_known_old_login_item(self):
        with tempfile.TemporaryDirectory() as scratch:
            def path(label=service.LABEL):return os.path.join(scratch,label+'.plist')
            pathlib.Path(path(service.LEGACY_LABEL)).write_text('old fixture')
            ok=subprocess.CompletedProcess([],0,'','')
            with patch('bell_desk.service.plist_path',side_effect=path),patch('bell_desk.service._launchctl',return_value=ok) as launch,patch('bell_desk.service.lock_free',return_value=True),patch('bell_desk.service.status',return_value='running, pid 4321'),patch('bell_desk.service.login_item_owns_lock',return_value=True),patch('bell_desk.service.time.sleep'):
                result,_=service.install(['/fixture/bell-desk','serve'],scratch,phone=False)
                self.assertTrue(result)
                self.assertFalse(os.path.exists(path(service.LEGACY_LABEL)))
                import plistlib
                self.assertEqual(plistlib.loads(pathlib.Path(path()).read_bytes())['Label'],'dev.aitortoise.letterboxdesk.listener')
                self.assertIn(unittest.mock.call('bootout',f'gui/{os.getuid()}/{service.LEGACY_LABEL}'),launch.call_args_list)

class PublicHelperConfigurationTests(unittest.TestCase):
    def test_missing_private_module_refuses_old_and_unknown_config(self):
        from bell_desk.sender import from_config, HelperConfigurationError
        missing=ModuleNotFoundError(name='bell_desk.legacy_helper')
        for kind in [None,'bus','unsupported']:
            cfg={'helper':'/fixture/helper','helper_sha256':'0'*64}
            if kind is not None:cfg['helper_kind']=kind
            with patch('bell_desk.sender.importlib.import_module',side_effect=missing):
                with self.assertRaisesRegex(HelperConfigurationError,'bell-desk setup with the Letterbox helper'):
                    from_config(cfg,'/fixture/box')

    def test_serve_reports_setup_error_before_listener_starts(self):
        with tempfile.TemporaryDirectory() as root:
            state=State(root)
            state.save_config({'bus':root,'seats':['alice'],'helper':'/fixture/helper','helper_sha256':'0'*64})
            error=StringIO()
            with patch('bell_desk.sender.importlib.import_module',side_effect=ModuleNotFoundError(name='bell_desk.legacy_helper')),redirect_stderr(error):
                self.assertEqual(cli.main(['--state',root,'serve']),2)
            self.assertIn('bell-desk setup with the Letterbox helper',error.getvalue())
            self.assertFalse(os.path.exists(state.path('listener.lock')))


class PictureInstallerPrompt(unittest.TestCase):
    def run_prompt(self, answer=None):
        import pty
        script=pathlib.Path(__file__).resolve().parents[1]/'Tools'/'setup-pictures.sh'
        with tempfile.TemporaryDirectory() as scratch:
            executable=pathlib.Path(scratch,'fake-bell')
            log=pathlib.Path(scratch,'calls.json')
            executable.write_text('#!'+sys.executable+'\nimport json,sys\nfrom pathlib import Path\nPath('+repr(str(log))+').write_text(json.dumps(sys.argv[1:]))\n')
            executable.chmod(0o700)
            if answer is None:
                result=subprocess.run([str(script),str(executable)],stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=5)
            else:
                master,slave=pty.openpty()
                try:
                    process=subprocess.Popen([str(script),str(executable)],stdin=slave,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                    os.write(master,answer.encode())
                    stdout,_=process.communicate(timeout=5)
                    result=subprocess.CompletedProcess(process.args,process.returncode,stdout,'')
                finally:
                    os.close(master);os.close(slave)
            return result,json.loads(log.read_text()) if log.exists() else None

    def test_without_tty_skips_and_prints_command(self):
        result,calls=self.run_prompt()
        self.assertEqual(result.returncode,0)
        self.assertIsNone(calls)
        self.assertIn('bell-desk attachments setup',result.stdout)

    def test_tty_default_and_yes_run_setup_once(self):
        for answer in ['\n','y\n']:
            with self.subTest(answer=answer):
                result,calls=self.run_prompt(answer)
                self.assertEqual(result.returncode,0)
                self.assertEqual(calls,['attachments','setup'])

    def test_tty_no_skips_setup(self):
        result,calls=self.run_prompt('n\n')
        self.assertEqual(result.returncode,0)
        self.assertIsNone(calls)
        self.assertIn('bell-desk attachments setup',result.stdout)
