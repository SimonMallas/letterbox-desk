"""Posts one confirmed dictation through the pinned letterbox helper, as `desk`."""

import hashlib
import importlib
import os
import re
import subprocess

SENT_RE = re.compile(r"^sent: (.+)$", re.M)
OUTCOME_RE = re.compile(r"^doorbell-outcome v=1 outcome=(\S+) reason=(\S+)", re.M)
SLUG = "desk-dictation"
DEFAULT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
PASS_ENV = ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG")


def file_sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class HelperConfigurationError(ValueError):
    pass


def from_config(config, root):
    return PinnedHelper(config['helper'], config['helper_sha256'], bus_dir=root,
                        kind=config.get('helper_kind'), doorbell=config.get('doorbell'),
                        doorbell_sha256=config.get('doorbell_sha256', ''))


class PinnedHelper:
    """Pinned public Letterbox helper, with compatibility for older saved configurations.
    A changed doorbell adapter is omitted: letters can still be published without a ring.
    """

    def __init__(self, path: str, sha256: str, bus_dir: str = "", env_path: str = DEFAULT_PATH, timeout: float = 30,
                 kind: str = "letterbox", doorbell: dict = None, doorbell_sha256: str = ""):
        self.path = path
        self.sha256 = sha256.lower()
        self.bus_dir = bus_dir
        self.env_path = env_path
        self.timeout = timeout
        self.kind = kind
        self.compatibility = None
        if kind != "letterbox":
            if kind not in (None, "bus"):
                raise HelperConfigurationError("Run bell-desk setup with the Letterbox helper.")
            # The compatibility module is intentionally absent from the public tree.
            try:
                self.compatibility = importlib.import_module('.legacy_helper', __package__)
            except ModuleNotFoundError as exc:
                if exc.name != __package__ + '.legacy_helper':
                    raise
                raise HelperConfigurationError("Run bell-desk setup with the Letterbox helper.") from None
        self.doorbell = dict(doorbell or {})
        self.doorbell_sha256 = doorbell_sha256.lower()

    def doorbell_ok(self) -> bool:
        adapter = self.doorbell.get("LETTERBOX_DOORBELL", "")
        try:
            return bool(adapter) and file_sha256(adapter) == self.doorbell_sha256
        except OSError:
            return False

    def pin_ok(self) -> bool:
        try:
            return file_sha256(self.path) == self.sha256
        except OSError:
            return False

    def _env(self) -> dict:
        env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
        env["PATH"] = self.env_path
        if self.kind == "letterbox":
            env["LETTERBOX_AGENT"] = "desk"
            env["LETTERBOX_DIR"] = self.bus_dir
            if self.doorbell_ok():
                env.update(self.doorbell)
            return env
        return self.compatibility.environment(env, self.bus_dir, self.env_path == DEFAULT_PATH)

    def team(self, timeout: float = 10):
        return self.compatibility.team(self, timeout) if self.compatibility else None

    def send(self, to: str, text: str, re_id: str = "") -> dict:
        """One attempt, never retried. `sent` only when the helper reports the written letter."""
        if not self.pin_ok():
            return {"status": "not_confirmed", "reason": "helper_changed"}
        try:
            done = subprocess.run(
                [self.path, "send", to, "info", SLUG, "--now"] + (["--re", re_id] if re_id else []),
                input=text.encode("utf-8"),
                capture_output=True,
                env=self._env(),
                timeout=self.timeout,
                cwd=os.environ.get("HOME", "/"),
            )
        except subprocess.TimeoutExpired:
            return {"status": "not_confirmed", "reason": "helper_timeout"}
        except OSError:
            return {"status": "not_confirmed", "reason": "helper_unavailable"}
        out = done.stdout.decode("utf-8", errors="replace")
        err = done.stderr.decode("utf-8", errors="replace")
        sent = SENT_RE.search(out)
        if not sent:
            return {"status": "not_confirmed", "reason": f"helper_exit_{done.returncode}"}
        envelope = os.path.basename(sent.group(1).strip())
        if envelope.endswith(".md"):
            envelope = envelope[:-3]
        ring = OUTCOME_RE.search(out) or OUTCOME_RE.search(err)
        return {
            "status": "sent",
            "id": envelope,
            "ring": {"outcome": ring.group(1), "reason": ring.group(2)} if ring else {"outcome": "unknown", "reason": "-"},
        }
