"""Native macOS pop-ups for pairing. Text reaches AppleScript as arguments, never as script source."""

import json
import os
import subprocess
import threading

TITLE = "Letterbox Desk"
WINDOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build", "Letterbox Desk.app", "Contents", "MacOS", "bell-pair")


def window_available():
    build = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(WINDOW))))
    return os.access(WINDOW, os.X_OK) and not os.path.exists(os.path.join(build, "window-unavailable"))

ASK = """on run argv
  activate
  try
    set reply to display dialog (item 2 of argv) default answer "" buttons {"Cancel", "Pair"} default button "Pair" cancel button "Cancel" with title (item 1 of argv) giving up after 110
    if gave up of reply then return ""
    return text returned of reply
  on error number -128
    return ""
  end try
end run"""

TELL = """on run argv
  activate
  display dialog (item 2 of argv) buttons {"OK"} default button "OK" with title (item 1 of argv) giving up after 30
end run"""


def ask_for_code(device_name: str) -> str:
    """What the person typed, or "" for Cancel, timeout, or no display. Six-box window if built, else a dialog."""
    if window_available():
        try:
            done = subprocess.run([WINDOW, "--phone", device_name], capture_output=True, text=True, timeout=130)
            return done.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    text = (f"A phone called “{device_name}” wants to pair with Letterbox Desk.\n\n"
            "Type the 6-digit code shown on the phone:")
    try:
        done = subprocess.run(["osascript", "-", TITLE, text], input=ASK, capture_output=True, text=True, timeout=130)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip()


def paired(device_name: str):
    """A moment of confirmation in the pairing window's style; closes itself."""
    if window_available():
        try:
            subprocess.Popen([WINDOW, "--result", "done", "--phone", device_name],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


def wrong_code(device_name: str):
    if window_available():
        try:
            subprocess.Popen([WINDOW, "--result", "wrong", "--phone", device_name],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    tell("That code didn't match the phone, so nothing was paired. Start again on the phone.")


class MenuBar:
    """The menu-bar bell and the Letterbox Desk window: a child process spoken to over its own pipes,
    never the network. Each line it writes is one JSON object, handed to on_message."""

    def __init__(self, on_message, hint: bool = False):
        self.on_message = on_message
        self.hint = hint
        self.lock = threading.Lock()
        self.proc = None

    def start(self) -> bool:
        if not window_available():
            return False
        try:
            self.proc = subprocess.Popen([WINDOW, "--menubar"] + (["--hint"] if self.hint else []),
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except OSError:
            self.proc = None
            return False
        threading.Thread(target=self._read, name="menubar", daemon=True).start()
        return True

    def _read(self):
        for line in self.proc.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict):
                self.on_message(message)

    def send(self, message: dict):
        if not self.proc or self.proc.poll() is not None:
            return
        with self.lock:
            try:
                self.proc.stdin.write(json.dumps(message) + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError):
                pass

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def tell(text: str):
    try:
        subprocess.run(["osascript", "-", TITLE, text], input=TELL, capture_output=True, text=True, timeout=40)
    except (OSError, subprocess.TimeoutExpired):
        pass
