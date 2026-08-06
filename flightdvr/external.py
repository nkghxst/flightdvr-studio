# FlightDVR Studio - browse, trim and convert HDZero goggle DVR footage.
# Copyright (C) 2026 Isadu Nkemi
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.

"""Handing a file to another program.

Windows associates .ts with Media Player, which opens the file and then
often cannot decode what is inside it, so a player known to cope is
preferred wherever one is installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PLAYER_PATHS = [
    Path(r"C:\Program Files\VideoLAN\VLC\vlc.exe"),
    Path(r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"),
    Path(r"C:\Program Files\mpv\mpv.exe"),
    # macOS keeps the real executable inside the app bundle; nothing lands on
    # PATH when these are installed by dragging them to Applications.
    Path("/Applications/VLC.app/Contents/MacOS/VLC"),
    Path("/Applications/IINA.app/Contents/MacOS/IINA"),
    Path("/Applications/mpv.app/Contents/MacOS/mpv"),
]

# macOS has no xdg-open. `open` is the equivalent and resolves app bundles.
DESKTOP_OPEN = "open" if sys.platform == "darwin" else "xdg-open"
def find_player() -> Path | None:
    """A player known to cope with HEVC inside MPEG-TS.

    Windows associates `.ts` with Media Player, which opens the file and then
    often cannot decode it, so a player that definitely works is preferred when
    one is installed.

    On Linux the usual players are on PATH, and if neither is installed the
    caller falls back to xdg-open, which reaches a Flatpak player through the
    desktop association where exec'ing a binary would not. macOS installs them
    as app bundles instead, so the paths above are checked first there.
    """
    for path in PLAYER_PATHS:
        if path.exists():
            return path
    for name in ("vlc", "mpv", "mplayer", "totem"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def reveal(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen([DESKTOP_OPEN, str(path)])
    except OSError:
        pass
