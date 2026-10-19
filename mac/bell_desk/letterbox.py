"""Finding the person's own letterbox: its root and the agents in it. Nothing about ours is assumed."""

import os
import re
import subprocess

from .watcher import SEAT_RE

NOT_AGENTS = {"desk"}


def pointer_path(env=os.environ) -> str:
    """Where the public Letterbox installer records the default letterbox."""
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(base, "agent-letterbox", "default-dir")


def find_root(explicit: str = "", env=os.environ):
    """(root, how) from an explicit directory, else LETTERBOX_DIR, else the install pointer read as plain data;
    (None, why) if none gives an existing absolute directory. Never the current directory, which is where the helper
    itself falls back."""
    if explicit:
        candidate, how = explicit, "given"
    elif env.get("LETTERBOX_DIR"):
        candidate, how = env["LETTERBOX_DIR"], "LETTERBOX_DIR"
    else:
        try:
            with open(pointer_path(env), encoding="utf-8") as handle:
                candidate = handle.readline().strip()
        except OSError:
            return None, f"no letterbox was given and there is no install pointer at {pointer_path(env)}"
        how = f"install pointer {pointer_path(env)}"
    if not os.path.isabs(candidate):
        return None, f"{candidate!r} is not an absolute path"
    root = os.path.realpath(candidate)
    if not os.path.isdir(root):
        return None, f"{root} is not a directory"
    return root, how


def roster(root: str) -> list:
    """Candidate agents: direct children, not symlinks, named like seats, with both inbox/ and processed/. Never desk.
    The person confirms the list; pane registries are not a roster."""
    try:
        entries = sorted(os.scandir(root), key=lambda entry: entry.name)
    except OSError:
        return []
    seats = []
    for entry in entries:
        if entry.name in NOT_AGENTS or not SEAT_RE.match(entry.name) or not entry.is_dir(follow_symlinks=False):
            continue
        boxes = [os.path.join(entry.path, sub) for sub in ("inbox", "processed")]
        if all(os.path.isdir(box) and not os.path.islink(box) for box in boxes):
            seats.append(entry.name)
    return seats


ENV_LINE = re.compile(r"^export (LETTERBOX_[A-Z0-9_]+)='([^'\n]*)'$")
DOORBELL_KEY = re.compile(r"^LETTERBOX_(DOORBELL|[A-Z]+_REGISTRY|[A-Z]+_PATTERNS|[A-Z]+_SUBMIT)$")
VERSION_LINE = re.compile(r"^letterbox (\d+)\.(\d+)\.(\d+)\s*$")
EDITION_DIR = re.compile(r"^agent-letterbox-([a-z]+)$")
MIN_VERSION = (0, 5, 0)


def find_helper(explicit: str = "", env=os.environ):
    """(real path, how) of the public `letterbox` helper, resolved through its launcher symlink, or (None, why)."""
    home = env.get("HOME") or os.path.expanduser("~")
    candidate = explicit or os.path.join(home, ".local", "bin", "letterbox")
    if not os.path.isabs(candidate):
        return None, f"{candidate!r} is not an absolute path"
    real = os.path.realpath(candidate)
    if not os.path.isfile(real) or not os.access(real, os.X_OK):
        return None, f"no letterbox helper at {candidate}"
    return real, "given" if explicit else "the installed launcher"


def helper_version(path: str):
    """(major, minor, patch) from `letterbox --version`, or None."""
    try:
        done = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10,
                              env={"PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~")})
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = VERSION_LINE.match(done.stdout.strip())
    return tuple(int(part) for part in match.groups()) if match and done.returncode == 0 else None


def edition(helper: str) -> str:
    """cmux, tmux, zellij or herdr, from the install directory the helper lives in."""
    match = EDITION_DIR.match(os.path.basename(os.path.dirname(os.path.dirname(helper))))
    return match.group(1) if match else ""


def doorbell(root: str, helper: str) -> dict:
    """The doorbell settings the edition's setup wrote to <root>/env.sh, read as data and never sourced. Only known
    keys, and only an adapter inside the helper's own install. Empty when there is none."""
    try:
        with open(os.path.join(root, "env.sh"), encoding="utf-8") as handle:
            text = handle.read(16384)
    except OSError:
        return {}
    found = {}
    for line in text.splitlines():
        match = ENV_LINE.match(line.strip())
        if match and DOORBELL_KEY.match(match.group(1)):
            found[match.group(1)] = match.group(2)
    adapter = found.get("LETTERBOX_DOORBELL", "")
    install = os.path.realpath(os.path.dirname(os.path.dirname(helper)))
    real = os.path.realpath(adapter) if os.path.isabs(adapter) else ""
    if not real or not real.startswith(install + os.sep) or not os.path.isfile(real):
        return {}
    found["LETTERBOX_DOORBELL"] = real
    return found
