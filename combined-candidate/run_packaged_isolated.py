"""Run a packaged FlightDVR Studio bundle in an isolated profile and grab its window.

    python run_packaged_isolated.py BUNDLE_DIR OUT_DIR

Never installs, never sends input to the desktop. Records the real registry
key and ~/.flightdvr before and after and says whether they changed.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

BUNDLE = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
HOME = OUT / "home"
for sub in ("", "AppData/Roaming", "AppData/Local"):
    (HOME / sub).mkdir(parents=True, exist_ok=True)
INI = OUT / "candidate-settings.ini"
INI.write_text("[General]\nsource_dir=D:/Dev/test-output/flightdvr-screenshots-20261009/"
               "fixtures/card14\nview_mode=classic\ncheck_for_updates=false\n", encoding="ascii")
LOG = []


def say(line: str) -> None:
    LOG.append(line)
    print(line)


def snapshot(tag: str) -> tuple[str, str]:
    reg = OUT / f"hkcu-{tag}.reg"
    subprocess.run(["reg", "export", r"HKCU\Software\FlightDVR Studio", str(reg), "/y"],
                   check=True, capture_output=True)
    real = Path(os.environ["USERPROFILE"]) / ".flightdvr"
    lines = []
    for path in sorted(real.rglob("*")):
        st = path.stat()
        lines.append(f"{str(path)[len(str(real)):]}|{st.st_size}|{st.st_mtime_ns}")
    listing = OUT / f"dotflightdvr-{tag}.txt"
    listing.write_text("\n".join(lines), encoding="utf-8")
    return (hashlib.sha256(reg.read_bytes()).hexdigest(),
            hashlib.sha256(listing.read_bytes()).hexdigest())


def isolated_env() -> dict:
    env = dict(os.environ)
    env.update(USERPROFILE=str(HOME), HOME=str(HOME),
               APPDATA=str(HOME / "AppData/Roaming"),
               LOCALAPPDATA=str(HOME / "AppData/Local"),
               FLIGHTDVR_SETTINGS_FILE=str(INI))
    env.pop("QT_QPA_PLATFORM", None)
    return env


user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def window_of(pid: int):
    found = []

    def visit(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            if "FlightDVR" in title.value:
                found.append((hwnd, title.value))
        return True
    user32.EnumWindows(EnumWindowsProc(visit), 0)
    return found[0] if found else (None, "")


before = snapshot("before")
exe = BUNDLE / "FlightDVRStudio.exe"
check = subprocess.run([str(exe), "--check"], env=isolated_env(), capture_output=True,
                       text=True, timeout=120)
say(f"--check exit {check.returncode}")
(OUT / "check-output.txt").write_text(check.stdout + check.stderr, encoding="utf-8")

proc = subprocess.Popen([str(exe)], env=isolated_env())
hwnd, title = None, ""
deadline = time.monotonic() + 60
while time.monotonic() < deadline and not hwnd:
    time.sleep(0.5)
    hwnd, title = window_of(proc.pid)
time.sleep(6)
say(f"window found: {bool(hwnd)} title={title!r} still running: {proc.poll() is None}")

if hwnd:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    shot = app.primaryScreen().grabWindow(int(hwnd))
    ok = shot.save(str(OUT / "packaged-window.png"))
    say(f"grab saved={ok} window rect={rect.right - rect.left}x{rect.bottom - rect.top} "
        f"image={shot.width()}x{shot.height()}")
    user32.PostMessageW(hwnd, 0x0010, 0, 0)          # WM_CLOSE: the app's own close
try:
    proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
    say("killed after 20 s without closing")
say(f"exit {proc.returncode}")

after = snapshot("after")
say(f"real registry unchanged: {before[0] == after[0]}; real ~/.flightdvr unchanged: "
    f"{before[1] == after[1]}")
ini_text = INI.read_text(encoding="utf-8", errors="replace")
say(f"isolated INI now {len(ini_text.splitlines())} lines; isolated home files: "
    f"{sum(1 for p in HOME.rglob('*') if p.is_file())}")
(OUT / "run.log").write_text("\n".join(LOG) + "\n", encoding="utf-8")
