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

"""Finding the SD card, listing recordings on it, and copying them off."""

from __future__ import annotations

import ctypes
import os
import shutil
import string
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

# The Box Pro writes .ts by default; the DVR can be switched to .mp4 in the menus.
CLIP_EXTENSIONS = {".ts", ".mp4", ".mov", ".mkv"}

# Top-level folders that never hold DVR footage. Cards get reused in phones and
# car stereos, and walking those trees is wasted time on a slow reader.
SKIP_DIRS = {
    "$recycle.bin", ".$recycle_bin$", "system volume information", "lost.dir",
    "android", "music", "pictures", "podcasts", "ringtones", "alarms",
    "notifications", "audiobooks", "documents", "download", "downloads",
    "pioneer", "fsck", "log", ".trashed", ".thumbnails",
}

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3


@dataclass(frozen=True)
class Drive:
    path: Path
    letter: str
    label: str
    removable: bool

    @property
    def description(self) -> str:
        name = self.label or "Untitled"
        # Only removable media gets a tag. Marking every internal disk "(fixed)"
        # is noise: what matters is spotting the card.
        return f"{self.letter}  {name}" + ("   — removable" if self.removable else "")


def _volume_label(root: str) -> str:
    if not hasattr(ctypes, "windll"):
        return ""
    buf = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    try:
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), buf, 261, None, None, None, fs, 261
        )
    except OSError:
        return ""
    return buf.value if ok else ""


def list_drives(removable_only: bool = False) -> list[Drive]:
    """Mounted drives, removable ones first. Windows-specific, degrades gracefully."""
    if not hasattr(ctypes, "windll"):
        return []
    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    drives: list[Drive] = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not (mask >> i) & 1:
            continue
        root = f"{letter}:\\"
        try:
            kind = kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        except OSError:
            continue
        if kind not in (DRIVE_REMOVABLE, DRIVE_FIXED):
            continue
        removable = kind == DRIVE_REMOVABLE
        if removable_only and not removable:
            continue
        drives.append(Drive(Path(root), f"{letter}:", _volume_label(root), removable))

    # A card is what people are usually reaching for, so float it to the top.
    drives.sort(key=lambda d: (not d.removable, d.letter))
    return drives


def find_clips(folder: Path, recursive: bool = True) -> list[Path]:
    """Video files under `folder`, skipping system and non-video directories.

    Uses os.walk so each file is stat-ed once. The previous version stat-ed
    every candidate twice, once to filter and again to sort.
    """
    if not folder.exists():
        return []

    found: list[tuple[float, Path]] = []
    if not recursive:
        entries: Iterable[os.DirEntry] = []
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return []
        for entry in entries:
            _consider(entry, found)
        return [path for _, path in sorted(found, key=lambda pair: -pair[0])]

    for root, dirnames, _ in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
        try:
            for entry in os.scandir(root):
                _consider(entry, found)
        except OSError:
            continue

    return [path for _, path in sorted(found, key=lambda pair: -pair[0])]


def _consider(entry, found: list[tuple[float, Path]]) -> None:
    try:
        if not entry.is_file(follow_symlinks=False):
            return
        if Path(entry.name).suffix.lower() not in CLIP_EXTENSIONS:
            return
        stat = entry.stat()
        if stat.st_size < 128 * 1024:
            return
        found.append((stat.st_mtime, Path(entry.path)))
    except OSError:
        return


def looks_like_dvr_card(folder: Path) -> bool:
    """True when a drive holds files named the way the HDZero DVR names them."""
    for candidate in (folder / "movies", folder):
        try:
            if not candidate.is_dir():
                continue
            for entry in os.scandir(candidate):
                name = entry.name.lower()
                if name.startswith("hdz") and Path(name).suffix in CLIP_EXTENSIONS:
                    return True
        except OSError:
            continue
    return False


def detect_card() -> Path | None:
    """First removable drive that contains HDZero-looking recordings."""
    removable = list_drives(removable_only=True)
    for drive in removable:
        if looks_like_dvr_card(drive.path):
            return drive.path
    for drive in removable:
        if find_clips(drive.path):
            return drive.path
    return None


# -- the clock problem --------------------------------------------------------

def timestamps_are_unreliable(stamps: Sequence[datetime]) -> str:
    """Explain why these file dates cannot be trusted, or return "".

    The Box Pro's own log reports `rtc_init has NOT detected a battery`: there
    is no backup cell, so the clock restarts from the same stored value every
    time the goggles power up. Recordings made weeks apart end up carrying
    near-identical timestamps, and a card that has been through a filesystem
    check loses even those.
    """
    if len(stamps) < 3:
        return ""

    if any(stamp.year < 2000 for stamp in stamps):
        return ("Some clips are dated before 2000, which means the goggles "
                "booted with their clock reset.")

    days = Counter(stamp.date() for stamp in stamps)
    common_day, count = days.most_common(1)[0]
    if count / len(stamps) < 0.8:
        return ""

    span = max(stamps) - min(stamps)
    total_clips = len(stamps)
    if span.total_seconds() < 3600 and total_clips >= 10:
        return (
            f"All {total_clips} clips are stamped {common_day:%d %b %Y} within "
            f"{int(span.total_seconds() // 60)} minutes of each other, which is "
            "not when they were filmed. These goggles have a socket for a CR2032 "
            "clock battery but none fitted, so the clock restarts from the same "
            "value on every power-up. Sort by clip name instead — the DVR's "
            "counter is the real recording order. Fitting a cell fixes it at "
            "source; the Goggle 2 ships with one."
        )
    return ""


# -- copying off the card -----------------------------------------------------

def ingest_destination(
    base: Path,
    source: Path,
    by_date: bool,
    date_prefix: bool,
    flight_date: date | None = None,
) -> Path:
    """Where a copied clip should land in the library.

    `flight_date` overrides the file's own timestamp, which is what you want
    given the goggles cannot keep time.
    """
    stamp = flight_date or datetime.fromtimestamp(source.stat().st_mtime).date()
    folder = base / stamp.strftime("%Y-%m-%d") if by_date else base
    name = source.name
    if date_prefix and not name.startswith(stamp.strftime("%Y-%m-%d")):
        name = f"{stamp.strftime('%Y-%m-%d')}_{name}"
    return folder / name


def copy_clips(
    sources: list[Path],
    base: Path,
    by_date: bool = True,
    date_prefix: bool = True,
    flight_date: date | None = None,
    on_progress: Callable[[int, int, str], bool] | None = None,
) -> tuple[list[Path], list[str]]:
    """Copy recordings into the library, skipping ones already there.

    `on_progress(done, total, name)` may return False to stop early.
    Returns the files written and any human-readable problems.
    """
    written: list[Path] = []
    problems: list[str] = []
    total = len(sources)

    for index, source in enumerate(sources):
        if on_progress and not on_progress(index, total, source.name):
            break
        try:
            target = ingest_destination(base, source, by_date, date_prefix, flight_date)
            target.parent.mkdir(parents=True, exist_ok=True)

            if target.exists() and target.stat().st_size == source.stat().st_size:
                written.append(target)
                continue

            temporary = target.with_suffix(target.suffix + ".part")
            shutil.copy2(source, temporary)

            # Size check is enough here: we are guarding against a half-finished
            # copy, not against a card that is silently corrupting data.
            if temporary.stat().st_size != source.stat().st_size:
                temporary.unlink(missing_ok=True)
                problems.append(f"{source.name}: copy finished at the wrong size")
                continue

            temporary.replace(target)
            written.append(target)
        except OSError as exc:
            problems.append(f"{source.name}: {exc}")

    if on_progress:
        on_progress(total, total, "")
    return written, problems


def free_space(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0
