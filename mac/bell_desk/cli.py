"""`bell-desk`: run the listener in a terminal pane, pin the helper, list paired phones."""

import argparse
import os
import sys
import threading

from . import letterbox, macui, service
from .desk import DeskBridge, DeskMail
from .phone import PhoneSide
from .sender import from_config, HelperConfigurationError, file_sha256
from .server import App
from .store import State
from .watcher import InboxWatcher, SEAT_RE

HOME = os.path.expanduser("~")
HELP = """Pairing: open Bell on the phone. A pop-up on this Mac asks for the code the phone shows.
commands:
  pair              re-open pairing straight away after a refused attempt
  approve [id]      approve a waiting phone here instead, after comparing both screens
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
        phone = input("Use the iPhone app (Agent Bell)? [y/N] ").strip().lower() in ("y", "yes")
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
    elif args.action == "uninstall":
        print(service.uninstall())
    else:
        print(service.status())
    return 0


def cmd_phone(args) -> int:
    state = State(args.state)
    if args.action == "status":
        print(f"Phone: {'on' if phone_enabled(state.config(), state) else 'off'} (configured)")
    else:
        print(set_phone(state, args.action == "enable"))
    return 0


def console_loop(app: App, stop: threading.Event):
    print(HELP, flush=True)
    for line in sys.stdin:
        words = line.split()
        if not words:
            continue
        verb, rest = words[0].lower(), words[1:]
        if verb == "pair":
            if app.phone_active:
                app.open_pairing()
            else:
                print("Phone is off; enable it in the menu or with `bell-desk phone enable`.", flush=True)
        elif verb in ("approve", "deny"):
            print(app.decide(rest[0] if rest else "", verb == "approve") if app.phone_active else "Phone is off.", flush=True)
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
    try:
        app = App(state, helper, bus, seats, console=lambda m: print(m, flush=True),
                  ask_for_code=macui.ask_for_code, tell=macui.wrong_code, paired=macui.paired, phone=False,
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

    def on_menu(message):
        if message.get("phone_toggle"):
            with switch_lock:
                enabled = not phone_side.active
                active, problem = phone_side.set_enabled(enabled)
                if not problem:
                    set_phone(state, active)
            send_state(problem or f"Phone is now {'on' if active else 'off'}.")
        else:
            bridge.handle(message)

    menubar = macui.MenuBar(on_menu, hint=not os.path.exists(hint_marker))
    mail = DeskMail(bus, state, seats)
    bridge = DeskBridge(app, mail, menubar.send)

    watcher = InboxWatcher(bus, app.publish_mail, interval=args.interval, log=app.log)

    def tell_menubar():
        tick = 0
        while not stop.wait(2):
            tick += 1
            with switch_lock:
                desired = phone_enabled(state.config(), state)
                changed = desired != phone_side.active
                if changed:
                    active, problem = phone_side.set_enabled(desired)
            if changed:
                send_state(problem or f"Phone is now {'on' if active else 'off'}.")
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

    watcher.start()
    if menubar.start() and not os.path.exists(hint_marker):
        open(hint_marker, "w").close()
    send_state(error)
    bridge.push(force=True)
    threading.Thread(target=bridge.refresh_team, name="desk-team", daemon=True).start()
    threading.Thread(target=tell_menubar, name="menubar-state", daemon=True).start()
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
    serve.add_argument("--pair", action="store_true", help="lift any pairing pause at start")
    serve.add_argument("--no-bonjour", action="store_true")
    serve.add_argument("--wait-for-lock", action="store_true",
                       help="if another listener is running, wait for it to stop instead of standing down (the login item)")
    pin = sub.add_parser("pin", help="pin the letterbox helper by sha256")
    pin.add_argument("--helper", required=True)
    pin.add_argument("--sha256", required=True)
    sub.add_parser("devices", help="list paired phones")
    phone_cmd = sub.add_parser("phone", help="turn Agent Bell's iPhone connection on or off")
    phone_cmd.add_argument("action", choices=["enable", "disable", "status"])
    setup = sub.add_parser("setup", help="point Letterbox Desk at your letterbox and choose its agents")
    setup.add_argument("--letterbox-dir", default="", help="your letterbox folder (default: $LETTERBOX_DIR, else the one Letterbox installed)")
    setup.add_argument("--helper", default="", help="the letterbox helper (default: ~/.local/bin/letterbox)")
    setup.add_argument("--agents", default="", help="comma-separated agents the Desk may write to (default: ask)")
    setup.add_argument("--yes", action="store_true", help="accept what was found without asking")
    svc = sub.add_parser("service", help="run the listener as a login item, or stop doing so")
    svc.add_argument("action", choices=["install", "uninstall", "status"])
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
    return {"serve": cmd_serve, "pin": cmd_pin, "devices": cmd_devices, "phone": cmd_phone, "service": cmd_service,
            "setup": cmd_setup}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
