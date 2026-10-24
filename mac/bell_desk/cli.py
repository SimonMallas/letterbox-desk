"""`bell-desk`: run the listener in a terminal pane, pin the helper, list paired phones."""

import argparse
import json
import os
import secrets
import stat
import sys
import threading
import time

from . import letterbox, macui, service
from .desk import DeskBridge, DeskMail
from .phone import PhoneSide
from .sender import from_config, HelperConfigurationError, file_sha256
from .server import App
from .store import State, _write_private
from .watcher import InboxWatcher, SEAT_RE

HOME = os.path.expanduser("~")
HELP = """Pairing: type pair (or choose Pair a phone… in the menu bar), then open Bell on the phone.
Type the code the phone shows into the pop-up; the Mac then shows a code to type on the phone.
commands:
  pair              open pairing for 3 minutes
  approve <code>    approve a waiting phone here instead, with the 6 digits it shows
  deny [id]         refuse it
  devices           list paired phones
  unpair <device>   remove a phone; it must pair again
  status            helper pin, seats, phone switch
  quit              stop the listener"""


def phone_enabled(config: dict, state: State) -> bool:
    """An older paired install stays on; a new or unpaired install defaults off."""
    return config["phone"] is True if "phone" in config else bool(state.devices())


def set_phone(state: State, enabled: bool) -> str:
    state.update_config({"phone": enabled})
    return f"Phone: {'on' if enabled else 'off'} requested. A running Desk applies this shortly. Pairings are kept."


def cmd_pin(args) -> int:
    if not os.path.isabs(args.helper):
        print("bell-desk: --helper must be an absolute path", file=sys.stderr)
        return 2
    actual = file_sha256(args.helper)
    if actual != args.sha256.lower():
        print(f"bell-desk: {args.helper} hashes to {actual}, not the pin you gave; nothing saved", file=sys.stderr)
        return 1
    state = State(args.state)
    state.update_config({"helper": args.helper, "helper_sha256": actual})
    print(f"pinned {args.helper} sha256 {actual}")
    return 0


def cmd_setup(args) -> int:
    """Point Desk at the person's letterbox: show its agents and pin the helper."""
    root, how = letterbox.find_root(args.letterbox_dir)
    if not root:
        print(f"bell-desk: {how}. Pass --letterbox-dir with your letterbox folder.", file=sys.stderr)
        return 2
    helper, helper_how = letterbox.find_helper(args.helper)
    if not helper:
        print(f"bell-desk: {helper_how}. Install Agent Letterbox first, or pass --helper.", file=sys.stderr)
        return 2
    version = letterbox.helper_version(helper)
    if not version or version < letterbox.MIN_VERSION:
        found = ".".join(map(str, version)) if version else "no version"
        print(f"bell-desk: {helper} reports {found}; Letterbox Desk needs letterbox 0.5.0 or newer.", file=sys.stderr)
        return 2
    agents = letterbox.roster(root)
    if not agents:
        print(f"bell-desk: no agents in {root}. Set up your letterbox and its agents first.", file=sys.stderr)
        return 2
    door = letterbox.doorbell(root, helper)
    print(f"Letterbox:  {root}  (from the {how})")
    print(f"Helper:     {helper}  (letterbox {'.'.join(map(str, version))}, {letterbox.edition(helper) or 'unknown'} edition)")
    print(f"Doorbell:   {door['LETTERBOX_DOORBELL'] if door else 'none found'}")
    if not door:
        print(f"WARNING: no doorbell settings in {os.path.join(root, 'env.sh')} (run your edition's `letterbox <edition> setup`)."
              " Letters will still arrive, but agents will not be rung.")
    print(f"Agents:     {', '.join(agents)}")
    chosen = agents
    if args.agents:
        chosen = [a for a in args.agents.split(",") if a]
    elif not args.yes:
        typed = input("Agents the Desk may write to (Enter for all of them, or names separated by commas): ").strip()
        chosen = [a.strip() for a in typed.split(",") if a.strip()] if typed else agents
    unknown = [a for a in chosen if a not in agents]
    if unknown or not chosen:
        print(f"bell-desk: not agents in this letterbox: {', '.join(unknown) or 'none chosen'}", file=sys.stderr)
        return 2
    if not args.yes and input(f"Set up Letterbox Desk with {', '.join(chosen)}? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing changed.")
        return 1
    for sub in ("inbox", "processed"):
        os.makedirs(os.path.join(root, "desk", sub), mode=0o700, exist_ok=True)
    state = State(args.state)
    previous = state.config()
    phone = phone_enabled(previous, state)
    if "bus" not in previous and not args.yes:
        phone = input("Use the iPhone app (Letterbox Bell)? [y/N] ").strip().lower() in ("y", "yes")
    config = {"bus": root, "seats": chosen, "helper": helper, "helper_kind": "letterbox", "phone": phone,
              "helper_sha256": file_sha256(helper), "doorbell": door,
              "doorbell_sha256": file_sha256(door["LETTERBOX_DOORBELL"]) if door else ""}
    def merge(latest):
        access = latest.get("attachments")
        if access:
            same_bus = os.path.realpath(latest.get("bus", "")) == os.path.realpath(root)
            access = {**access, **{key: {seat: proof for seat, proof in access.get(key, {}).items()
                                       if same_bus and seat in chosen} for key in ("readers", "probes")}}
        return {**latest, **config, **({"attachments": access} if access else {})}
    from .attachments import access_lock
    with access_lock(state):
        state.update_config(merge)
    print(f"Letterbox Desk will write as \"desk\" to {', '.join(chosen)}. Phone: {'on' if phone else 'off'}. Helper pinned at sha256 {config['helper_sha256'][:12]}.")
    return 0


def cmd_devices(args) -> int:
    devices = State(args.state).devices()
    if not devices:
        print("no paired phones")
    for device_id, record in devices.items():
        print(f"{device_id}  {record['name']}")
    return 0


def cmd_service(args) -> int:
    if args.action == "install":
        state = State(args.state)
        ok, message = service.install(service.program(os.path.dirname(sys.executable), args.state), args.state,
                                      phone=phone_enabled(state.config(), state))
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    elif args.action == "update":
        return update_service(args.state)
    elif args.action == "uninstall":
        print(service.uninstall())
    else:
        print(service.status())
    return 0


def same_program(installed, ours) -> bool:
    """The login item runs this install when its command is ours, comparing the program and state paths for real."""
    def real(argv):
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            return None
        argv = [os.path.realpath(argv[0])] + argv[1:]
        if "--state" in argv[:-1]:
            at = argv.index("--state") + 1
            argv[at] = os.path.realpath(argv[at])
        return argv
    return real(installed) is not None and real(installed) == real(ours)


def update_service(state_dir: str) -> int:
    """After a new version is installed: make sure it is the one running. 0 restarted this install's login item;
    3 a terminal listener must be restarted by hand; 4 nothing was running; 5 another install's login item is
    running and was left alone; 1 the restart failed."""
    ours = service.program(os.path.dirname(sys.executable), state_dir)
    if service.status().startswith("running"):
        if not same_program(service.installed_program(), ours):
            print("The login item running now belongs to another Letterbox Desk install, so this install left it alone.",
                  file=sys.stderr)
            return 5
        state = State(state_dir)
        ok, message = service.install(ours, state_dir, phone=phone_enabled(state.config(), state), migrate_legacy=False)
        print(f"restarted, so this version is the one running: {message}" if ok else message,
              file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    if not service.lock_free(state_dir):
        print("A listener started in a terminal is still running the version that was here before. "
              "In its window, type quit, then start it again.", file=sys.stderr)
        return 3
    print("not running")
    return 4


def cmd_phone(args) -> int:
    state = State(args.state)
    if args.action == "status":
        print(f"Phone: {'on' if phone_enabled(state.config(), state) else 'off'} (configured)")
    else:
        print(set_phone(state, args.action == "enable"))
    return 0


PAIR_REQUEST_PREFIX = "pair-request-"
PAIR_RESULT_PREFIX = "pair-result-"
PAIRING_OPEN = "Pairing is open for 3 minutes: open Letterbox Bell on your iPhone."
PAIR_WAIT = 15  # seconds `bell-desk pair` waits for the listener; turning the phone on waits for a letter being sent


def pair_clock() -> float:
    """The clock a pair request's deadline is on. Both processes share it, nobody can set it, and it keeps
    counting while the Mac sleeps, so a request the CLI gave up on stays late whatever the wall clock says."""
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def write_json_whole(path: str, value):
    """Written the way every state file is, so a reader never sees half of it and it never goes through a link."""
    _write_private(path, json.dumps(value).encode())


def read_regular_json(path: str, limit: int = 4096):
    """Parse the JSON in the regular file at path. Checked on what was actually opened: never through a link,
    never blocking on a pipe, and never more than limit bytes. Raises OSError or ValueError otherwise."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not a regular file")
        data = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        raise ValueError("too large")
    return json.loads(data)


def result_path(state: State, token: str) -> str:
    return state.path(f"{PAIR_RESULT_PREFIX}{token}.json")


def request_pairing(state: State, deadline: float = 0.0, token: str = "") -> str:
    """Ask the running listener to turn the phone on and open pairing; returns the request's own file.
    Each request is its own file, so claiming or withdrawing one can never remove another. With a deadline (on
    pair_clock) the listener answers in pair-result-<token>.json and opens nothing once the deadline has passed:
    whoever asked has already been told pairing did not open. Without one (the pane's pair) there is no answer."""
    token = token or secrets.token_hex(8)
    path = state.path(f"{PAIR_REQUEST_PREFIX}{time.time_ns():020d}-{token}.json")
    write_json_whole(path, {"token": token, "deadline": deadline})
    return path


def made_at(name: str) -> int:
    """The time_ns a request's file name says it was made, or 0 when the name doesn't say."""
    stamp = name[len(PAIR_REQUEST_PREFIX):len(PAIR_REQUEST_PREFIX) + 20]
    return int(stamp) if stamp.isascii() and stamp.isdigit() else 0


def claim_pair_requests(state: State, before: int = 0) -> list:
    """Every waiting request, oldest first, as (token, deadline). A request is claimed by removing its own file, so
    one the asker withdrew first is skipped. A file that isn't a whole request is removed and never acted on.
    With before (a time_ns), only requests made before then are claimed."""
    try:
        with os.scandir(state.root) as entries:  # regular files only: never a directory, never through a link
            names = sorted(entry.name for entry in entries if entry.name.startswith(PAIR_REQUEST_PREFIX)
                           and entry.name.endswith(".json") and entry.is_file(follow_symlinks=False)
                           and not (before and made_at(entry.name) >= before))
    except OSError:
        return []
    claimed = []
    for name in names:
        path = state.path(name)
        try:
            request = read_regular_json(path)
            token, deadline = str(request["token"]), float(request["deadline"])
            whole = name.endswith(f"-{token}.json")
        except FileNotFoundError:
            continue
        except (OSError, ValueError, TypeError, KeyError):
            whole = False
        try:
            os.remove(path)
        except FileNotFoundError:
            continue  # withdrawn before it was claimed
        except OSError:
            continue  # can't claim it, so don't act on it
        if whole:
            claimed.append((token, deadline))
    return claimed


def drop_pair_results(state: State):
    """Answers left from before this listener started have no reader. Regular files only; an odd entry is left
    alone and never stops the listener starting."""
    try:
        with os.scandir(state.root) as entries:
            stale = [entry.path for entry in entries
                     if entry.name.startswith(PAIR_RESULT_PREFIX) and entry.is_file(follow_symlinks=False)]
    except OSError:
        return
    for path in stale:
        try:
            os.remove(path)
        except OSError:
            pass


def cmd_pair(args) -> int:
    """Ask the running listener to open pairing, and report what it actually did."""
    if service.lock_free(args.state):
        print("Letterbox Desk isn't running, so there is nothing to pair with. Start it, then run this again.",
              file=sys.stderr)
        return 1
    state = State(args.state)
    token = secrets.token_hex(8)
    result = result_path(state, token)
    request = request_pairing(state, pair_clock() + PAIR_WAIT, token)
    # One second past the request's deadline, so an answer written right at the deadline still arrives.
    deadline = time.monotonic() + PAIR_WAIT + 1
    while time.monotonic() < deadline:
        try:
            notice = str(read_regular_json(result).get("notice", ""))
        except (OSError, ValueError, AttributeError):
            time.sleep(0.1)
            continue
        try:
            os.remove(result)
        except FileNotFoundError:
            pass
        if notice == PAIRING_OPEN:
            print(notice)
            return 0
        print(f"Pairing did not open: {notice}", file=sys.stderr)
        return 1
    try:
        os.remove(request)  # withdrawn if not yet claimed; a claimed one is past its deadline and opens nothing
    except FileNotFoundError:
        pass
    print(f"The running Letterbox Desk did not answer within {PAIR_WAIT} seconds, so pairing is not open. It may be "
          "busy sending a letter: try again. If this keeps happening it may be an older version: quit it, start it "
          "again, then run this again.", file=sys.stderr)
    return 1


def console_loop(app: App, stop: threading.Event):
    print(HELP, flush=True)
    for line in sys.stdin:
        words = line.split()
        if not words:
            continue
        verb, rest = words[0].lower(), words[1:]
        if verb == "pair":
            request_pairing(app.state)
        elif verb == "approve":
            print(app.approve_code(" ".join(rest)) if app.phone_active else "Phone is off.", flush=True)
        elif verb == "deny":
            print(app.decide(rest[0] if rest else "") if app.phone_active else "Phone is off.", flush=True)
        elif verb == "devices":
            for device_id, record in app.state.devices().items():
                print(f"{device_id}  {record['name']}", flush=True)
        elif verb == "unpair" and rest:
            print("unpaired" if app.unpair(rest[0]) else "no such device", flush=True)
        elif verb == "status":
            print(f"helper pin {'ok' if app.helper.pin_ok() else 'MISMATCH: sends refused'}; "
                  f"seats {', '.join(app.seats)}; phone {'on' if app.phone_active else 'off'}", flush=True)
        elif verb in ("quit", "exit"):
            break
        else:
            print(HELP, flush=True)
    stop.set()


def cmd_serve(args) -> int:
    state = State(args.state)
    config = state.config()
    bus_path = args.bus or config.get("bus")
    seats = [s for s in args.seats.split(",") if s] if args.seats else config.get("seats")
    if not bus_path or not isinstance(seats, list) or not seats or not all(
            isinstance(seat, str) and SEAT_RE.fullmatch(seat) for seat in seats):
        print("bell-desk: letterbox folder and agents are required; run `bell-desk setup` or pass --bus and --seats",
              file=sys.stderr)
        return 2
    phone = phone_enabled(config, state)
    if args.pair and not phone:
        print("bell-desk: phone is off; run `bell-desk phone enable` first", file=sys.stderr)
        return 2
    if not config.get("helper"):
        print("bell-desk: no pinned helper; run `bell-desk pin --helper PATH --sha256 HEX` first", file=sys.stderr)
        return 2
    bus = os.path.realpath(bus_path)
    try:
        helper = from_config(config, bus)
    except HelperConfigurationError as exc:
        print(f"bell-desk: {exc}", file=sys.stderr)
        return 2
    started = time.time_ns()  # a pair request made from now on is for this listener, even while it is starting
    try:
        app = App(state, helper, bus, seats, console=lambda m: print(m, flush=True),
                  ask_for_code=macui.ask_for_code, tell=macui.wrong_code, paired=macui.paired,
                  confirmed=macui.confirmed, phone=False,
                  wait_for_lock=args.wait_for_lock or service.launched_by_login_item())
    except RuntimeError as exc:
        # Another listener already serves this state. Standing down is not a crash, so a login item
        # started alongside it is not restarted every 30 seconds.
        print(f"bell-desk: {exc}; this one is standing down", file=sys.stderr)
        return 0
    phone_side = PhoneSide(app, args.host, args.port, args.no_bonjour)
    active, error = phone_side.set_enabled(phone)
    if error:
        print(f"bell-desk: {error}; Desk remains available", file=sys.stderr)
    stop = threading.Event()
    switch_lock = threading.Lock()
    hint_marker = state.path("menubar-hint-shown")
    def send_state(notice=""):
        message = {"type": "state", "seats": seats, "phones": len(state.devices()), "phone": phone_side.active}
        if notice:
            message["phone_notice"] = notice
        menubar.send(message)

    def switch_locked(enabled: bool, pair: bool = False, deadline: float = 0.0) -> str:
        """Turn the phone side on or off and return the menu's notice; switch_lock must be held. Pairing opens
        only when the person asks: Pair a phone…, `bell-desk pair`, the pane's pair, or turning the phone on with
        none paired."""
        # Switching waits for any letter being sent; when nothing needs to change, don't wait for it.
        active, problem = (enabled, "") if enabled == phone_side.active else phone_side.set_enabled(enabled)
        if not problem:
            set_phone(state, active)
        # Checked after the switch, which may have waited for a letter being sent: a request whose asker has
        # already been told pairing did not open, or one that arrives while the listener stops, opens nothing.
        if pair and not problem and stop.is_set():
            return "Letterbox Desk is stopping, so pairing did not open."
        if pair and not problem and deadline and pair_clock() > deadline:
            return "The phone side was busy past the request's time limit, so pairing did not open."
        opening = active and not problem and (pair or (enabled and not state.devices()))
        if opening:
            app.open_pairing()
        return problem or (PAIRING_OPEN if opening else f"Phone is now {'on' if active else 'off'}.")

    def switch_phone(enabled: bool, pair: bool = False) -> str:
        with switch_lock:
            return switch_locked(enabled, pair)

    def toggle_phone() -> str:
        with switch_lock:
            return switch_locked(not phone_side.active)

    def sync_phone_with_config():
        """Apply `bell-desk phone enable/disable`. Reading the setting and switching happen under one lock, so a
        stale reading can never undo a newer switch, such as a pairing request that just turned the phone on."""
        with switch_lock:
            desired = phone_enabled(state.config(), state)
            return None if desired == phone_side.active else switch_locked(desired)

    def on_menu(message):
        if message.get("pair_phone"):
            send_state(switch_phone(True, pair=True))
        elif message.get("phone_toggle"):
            send_state(toggle_phone())
        else:
            bridge.handle(message)

    menubar = macui.MenuBar(on_menu, hint=not os.path.exists(hint_marker))
    mail = DeskMail(bus, state, seats)
    bridge = DeskBridge(app, mail, menubar.send)

    watcher = InboxWatcher(bus, app.publish_mail, interval=args.interval, log=app.log)

    def pair_now(deadline: float) -> str:
        with switch_lock:
            if stop.is_set():
                return "Letterbox Desk is stopping, so pairing did not open."
            return switch_locked(True, pair=True, deadline=deadline)

    def answer_pair_requests():
        """`bell-desk pair` and the pane's pair, answered within half a second whatever the refresh is doing."""
        while not stop.wait(0.5):
            try:
                answer_waiting_requests()
            except Exception as exc:  # one bad request must never stop pairing requests being answered
                app.log(f"pair request failed: {exc!r}")

    def answer_waiting_requests():
        for token, deadline in claim_pair_requests(state):
            notice = pair_now(deadline)
            if deadline:  # a `bell-desk pair` is waiting for this answer
                write_json_whole(result_path(state, token), {"token": token, "notice": notice})
            send_state(notice)

    def tell_menubar():
        tick = 0
        while not stop.wait(2):
            tick += 1
            notice = sync_phone_with_config()
            if notice:
                send_state(notice)
            from .attachments import accept_replies
            try:
                accept_replies(state, app.log)
            except (OSError, ValueError):
                app.log("pictures: verification unavailable; access remains unchanged")
            bridge.push()
            if tick % 15 == 0:
                send_state()
            if tick % 150 == 0:
                bridge.refresh_team()

    claim_pair_requests(state, before=started)  # requests from before this listener started are stale: stays closed
    drop_pair_results(state)
    watcher.start()
    if menubar.start() and not os.path.exists(hint_marker):
        open(hint_marker, "w").close()
    send_state(error)
    bridge.push(force=True)
    threading.Thread(target=bridge.refresh_team, name="desk-team", daemon=True).start()
    threading.Thread(target=tell_menubar, name="menubar-state", daemon=True).start()
    threading.Thread(target=answer_pair_requests, name="pair-requests", daemon=True).start()
    print(f"bell-desk {'listening on ' + args.host + ':' + str(args.port) if active else 'Desk only'} · bus {bus}", flush=True)
    print(f"seats: {', '.join(seats)} · helper pin {'ok' if helper.pin_ok() else 'MISMATCH: sends refused'}"
          f" · phone {'on' if active else 'off'}", flush=True)
    if args.pair and active:
        app.open_pairing()
    try:
        if sys.stdin and sys.stdin.isatty():
            threading.Thread(target=console_loop, args=(app, stop), name="console", daemon=True).start()
        stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()  # first, so no pairing request is answered "open" while the listener shuts down
        phone_side.set_enabled(False)
        watcher.stop()
        app.stop()
        menubar.stop()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bell-desk", description="Letterbox Desk: Mac conversations for Agent Letterbox")
    parser.add_argument("--state", default=os.path.join(HOME, ".bell-desk"), help="state directory")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the listener")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--bus", help="letterbox root (default: the one chosen at setup)")
    serve.add_argument("--seats", help="comma-separated agents (default: the ones chosen at setup)")
    serve.add_argument("--interval", type=float, default=1.0)
    serve.add_argument("--pair", action="store_true", help="open pairing for 3 minutes at start")
    serve.add_argument("--no-bonjour", action="store_true")
    serve.add_argument("--wait-for-lock", action="store_true",
                       help="if another listener is running, wait for it to stop instead of standing down (the login item)")
    pin = sub.add_parser("pin", help="pin the letterbox helper by sha256")
    pin.add_argument("--helper", required=True)
    pin.add_argument("--sha256", required=True)
    sub.add_parser("devices", help="list paired phones")
    phone_cmd = sub.add_parser("phone", help="turn Letterbox Bell's iPhone connection on or off")
    phone_cmd.add_argument("action", choices=["enable", "disable", "status"])
    sub.add_parser("pair", help="open pairing on the running Letterbox Desk for 3 minutes")
    setup = sub.add_parser("setup", help="point Letterbox Desk at your letterbox and choose its agents")
    setup.add_argument("--letterbox-dir", default="", help="your letterbox folder (default: $LETTERBOX_DIR, else the one Letterbox installed)")
    setup.add_argument("--helper", default="", help="the letterbox helper (default: ~/.local/bin/letterbox)")
    setup.add_argument("--agents", default="", help="comma-separated agents the Desk may write to (default: ask)")
    setup.add_argument("--yes", action="store_true", help="accept what was found without asking")
    svc = sub.add_parser("service", help="run the listener as a login item, or stop doing so")
    svc.add_argument("action", choices=["install", "update", "uninstall", "status"])
    attachment_cmd = sub.add_parser("attachments", help="configure picture storage and verify seat read access")
    attachment_cmd.add_argument("action", choices=["setup", "configure", "verify", "accept", "status"])
    attachment_cmd.add_argument("value", nargs="?", default="")
    attachment_cmd.add_argument("--reply", default="")
    args = parser.parse_args(argv)
    if args.cmd == "attachments":
        from . import attachments
        try:
            print(attachments.command(State(args.state), args.action, args.value, args.reply))
            return 0
        except (OSError, ValueError, KeyError) as exc:
            print(f"bell-desk: {exc}", file=sys.stderr)
            return 1
    return {"serve": cmd_serve, "pin": cmd_pin, "devices": cmd_devices, "phone": cmd_phone, "pair": cmd_pair, "service": cmd_service,
            "setup": cmd_setup}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
