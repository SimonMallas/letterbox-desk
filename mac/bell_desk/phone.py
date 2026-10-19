"""The optional phone transport attached to the running Letterbox Desk."""

import subprocess
import threading

from . import crypto
from .server import DeskHTTPServer


class PhoneSide:
    def __init__(self, app, host: str, port: int, no_bonjour: bool = False):
        self.app = app
        self.host = host
        self.port = port
        self.no_bonjour = no_bonjour
        self.server = None
        self.bonjour = None

    @property
    def active(self) -> bool:
        return self.app.phone_active

    def set_enabled(self, enabled: bool) -> tuple[bool, str]:
        """A switch waits for any send to finish before changing the phone transport."""
        with self.app.send_lock:
            if enabled == self.active:
                return self.active, ""
            if not enabled:
                self.app.phone_active = False
                self.app.close_phone_streams()
                if self.server:
                    self.server.shutdown()
                    self.server.server_close()
                    self.server = None
                if self.bonjour:
                    self.bonjour.terminate()
                    self.bonjour = None
                self.app.server_key = None
                self.app.server_pub = None
                return False, ""

            key = self.app.state.server_key()
            public_key = crypto.public_bytes(key)
            try:
                server = DeskHTTPServer((self.host, self.port), self.app)
            except OSError as exc:
                return False, f"phone server could not start: {exc}"
            try:
                if not self.no_bonjour:
                    self.bonjour = subprocess.Popen(
                        ["dns-sd", "-R", "Letterbox Desk", "_belldesk._tcp", "local", str(self.port), "v=1"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                server.server_close()
                return False, f"phone discovery could not start: {exc}"
            self.app.server_key = key
            self.app.server_pub = public_key
            self.server = server
            self.app.phone_active = True
            threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
            return True, ""
