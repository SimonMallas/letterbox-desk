"""Keeping the listener running without a terminal: an opt-in LaunchAgent for this user, started at login and
restarted after a crash. Stopping it is explicit; its log stays in the private state directory."""

import fcntl
import json
import os
import plistlib
import re
import subprocess
import time
import urllib.request

LABEL = "dev.aitortoise.letterboxdesk.listener"
LEGACY_LABEL = "com.agentbell.listener"


def plist_path(label: str = LABEL) -> str:
    return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", label + ".plist")


def launched_by_login_item(env=os.environ) -> bool:
    """launchd names the job in XPC_SERVICE_NAME, so a login item written before --wait-for-lock still waits."""
    return env.get("XPC_SERVICE_NAME") in (LABEL, LEGACY_LABEL)


def program(venv_bin: str, state_dir: str) -> list:
    """The login item waits for a listener that is already running to stop, then takes over; it never exits for it."""
    return [os.path.join(venv_bin, "bell-desk"), "--state", state_dir, "serve", "--wait-for-lock"]


def plist(program: list, state_dir: str) -> dict:
    log = os.path.join(state_dir, "listener.log")
    return {
        "Label": LABEL,
        "ProgramArguments": program,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "ProcessType": "Interactive",
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def _launchctl(*args):
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True)


def install(program: list, state_dir: str, phone: bool = True):
    """(ok, message). ok only once launchd reports the listener running."""
    log = os.path.join(state_dir, "listener.log")
    os.close(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    # Only an explicit service install migrates the old login item.
    _launchctl("bootout", f"gui/{os.getuid()}/{LEGACY_LABEL}")
    for _ in range(10):
        if lock_free(state_dir):
            break
        time.sleep(0.5)
    else:
        return False, ("another Letterbox Desk listener is already running (not the login item). "
                       "Quit it first (type quit in its window), then run: bell-desk service install")
    try:
        os.remove(plist_path(LEGACY_LABEL))
    except FileNotFoundError:
        pass
    path = plist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        plistlib.dump(plist(program, state_dir), handle)
    domain = f"gui/{os.getuid()}"
    done = _launchctl("bootstrap", domain, path)
    if done.returncode != 0:
        return False, (f"could not start the listener: {done.stderr.strip() or done.returncode} (log: {log}). "
                       "Try again with: bell-desk service install, or remove it with: bell-desk service uninstall")
    for _ in range(20):
        first = status()
        if first.startswith("running"):
            time.sleep(2)
            if status() == first and login_item_owns_lock(state_dir, first) and (not phone or answers()):
                return True, f"running now; it starts at login and restarts if it stops unexpectedly (log: {log})"
        time.sleep(0.5)
    return False, (f"launchd accepted it but the listener is not answering ({status()}); see {log}. "
                   "It is left in place so you can look; remove it with: bell-desk service uninstall")


def answers(port: int = 8765) -> bool:
    """The listener's own hello on this Mac, the same thing a phone asks for first."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/hello", timeout=3) as response:
            return json.load(response).get("server") == "bell-desk"
    except (OSError, ValueError):
        return False


def lock_free(state_dir: str) -> bool:
    """True when no listener holds this state's lock right now."""
    try:
        fd = os.open(os.path.join(state_dir, "listener.lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def lock_holder_pid(state_dir: str):
    """Return the PID written by the process holding this lock, never a stale unlocked PID."""
    try:
        fd = os.open(os.path.join(state_dir, "listener.lock"), os.O_RDWR)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            data = os.read(fd, 32).decode("ascii", errors="ignore").strip()
            return int(data) if data.isdigit() else None
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None
    except OSError:
        return None
    finally:
        os.close(fd)


def login_item_owns_lock(state_dir: str, launchd_status: str) -> bool:
    match = re.fullmatch(r"running, pid (\d+)", launchd_status)
    return bool(match and lock_holder_pid(state_dir) == int(match.group(1)))


def uninstall() -> str:
    removed = False
    for label in (LABEL, LEGACY_LABEL):
        _launchctl("bootout", f"gui/{os.getuid()}/{label}")
        try:
            os.remove(plist_path(label))
            removed = True
        except FileNotFoundError:
            pass
    return "stopped, and it will not start at login" if removed else "the listener was not installed as a login item"


def status() -> str:
    for label in (LABEL, LEGACY_LABEL):
        done = _launchctl("print", f"gui/{os.getuid()}/{label}")
        if done.returncode != 0:
            continue
        state = re.search(r"^\s*state = (\S+)", done.stdout, re.M)
        pid = re.search(r"^\s*pid = (\d+)", done.stdout, re.M)
        return f"{state.group(1) if state else 'unknown'}" + (f", pid {pid.group(1)}" if pid else "")
    return "not installed as a login item"
