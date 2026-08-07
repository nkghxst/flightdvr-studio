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

"""Turning values into the strings and paths the rest of the app uses.

Pure functions with no Qt in them, which is why they are here: several are
the kind of thing that looks obvious and is not, and each one that bit is
documented where it lives.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

def human_size(num_bytes: float) -> str:
    if num_bytes <= 0:
        return "-"
    mb = num_bytes / (1024 * 1024)
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def human_duration(seconds: float) -> str:
    """Runtime in units people actually use, not decimal minutes."""
    total = int(round(seconds))
    if total < 60:
        return f"{total} sec"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} hr {minutes:02d} min"
    return f"{minutes} min {secs:02d} sec"


def natural_key(text: str) -> str:
    """Sort key where digit runs compare numerically (hdz_9 before hdz_112)."""
    return re.sub(r"\d+", lambda m: m.group().zfill(12), text.lower())


def canonical_path(path) -> str:
    """One spelling of a path, so two ideas of identity cannot disagree.

    `G:\\Movies` and `g:\\movies` are the same folder on Windows and different
    strings everywhere. Sessions used `os.path.normcase` for the autosave
    filename and the raw spelling for the clip fingerprints, so opening a card
    through a differently-cased path found the right session and then reported
    every clip in it as missing.

    Resolved as well as case-folded: a session opened through a mapped drive,
    a symlink or a relative path is about the same footage as one opened
    through the real one.
    """
    text = Path(path)
    try:
        text = text.resolve()
    except OSError:
        text = text.absolute()
    return os.path.normcase(str(text))


def folder_label(source: str) -> str:
    """The last component of a path written on any platform.

    A session records the folder it was made from, and that string travels:
    written on Windows, it may well be read on Linux, where
    `Path(r"G:\\movies").name` is the whole string rather than "movies".
    """
    if not source:
        return ""
    text = str(source).replace("\\", "/").rstrip("/")
    tail = text.rsplit("/", 1)[-1]
    return tail if tail and not tail.endswith(":") else text


def output_key(path: Path) -> str:
    """One name per file, for spotting two jobs aimed at the same place.

    Absolute and case-folded, because Windows and macOS treat hdz_001.mp4 and
    HDZ_001.mp4 as one file. Comparing the paths as written meant two jobs from
    differently-cased folders queued happily and the second silently overwrote
    the first, without the overwrite prompt appearing.
    """
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    return os.path.normcase(str(resolved))


def existing_ancestor(path: Path) -> Path:
    """The nearest folder that exists, so free space can be measured.

    Looking only at the immediate parent meant a destination two levels below
    anything that existed skipped the capacity check altogether: disk_usage()
    failed, the failure came back as zero, and zero reads as "no warning".
    """
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def _clip_set_id(clips) -> str:
    """A short identifier for exactly this set of clips and their trims.

    Concat lists were named after the first clip alone, so two different joins
    beginning with the same recording shared one file and overwrote each other.
    """
    material = "|".join(
        f"{c.path}:{c.trim_in:.3f}:{c.trim_out:.3f}" for c in clips
    )
    return hashlib.sha1(material.encode("utf-8", "replace")).hexdigest()[:8]
def work_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "flightdvr"
    path.mkdir(parents=True, exist_ok=True)
    return path
