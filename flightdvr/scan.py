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
import sys
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

# Filesystems worth offering as a source. Everything else in /proc/mounts is
# kernel bookkeeping, snap images, container overlays and the like.
LINUX_FILESYSTEMS = {
    "vfat", "exfat", "msdos", "ntfs", "ntfs3", "fuseblk",
    "ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs",
    "hfsplus", "udf", "iso9660",
}

# Where desktop environments mount removable media.
LINUX_MEDIA_ROOTS = ("/media/", "/run/media/", "/mnt/")

# /proc/mounts escapes these so a path with a space stays one field.
MOUNT_ESCAPES = {"\\040": " ", "\\011": "\t", "\\012": "\n", "\\134": "\\"}

# macOS mounts everything here, including a symlink back to the startup disk.
MACOS_VOLUMES = Path("/Volumes")

# Bookkeeping macOS leaves in /Volumes that is never worth offering as a source.
MACOS_SKIP_VOLUMES = {".timemachine", "com.apple.timemachine.donotpresent"}


@dataclass(frozen=True)
class Drive:
    path: Path
    identifier: str      # "G:" on Windows, the mount point's name on Linux
    label: str
    removable: bool

    @property
    def description(self) -> str:
        name = self.label or "Untitled"
        # Only removable media gets a tag. Marking every internal disk "(fixed)"
        # is noise: what matters is spotting the card.
        tag = "   — removable" if self.removable else ""
        if self.identifier and self.identifier != name:
            return f"{self.identifier}  {name}{tag}"
        return f"{name}{tag}"


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


def _unescape_mount(field: str) -> str:
    """/proc/mounts writes spaces and tabs as octal escapes."""
    for code, char in MOUNT_ESCAPES.items():
        field = field.replace(code, char)
    return field


def _linux_is_removable(device: str) -> bool:
    """Ask sysfs whether the block device behind a mount is removable.

    /dev/sdb1 belongs to sdb, and /sys/block/sdb/removable holds the flag. USB
    card readers sometimes report 0 anyway, so the caller also treats anything
    mounted under /media or /run/media as removable.
    """
    name = device.rsplit("/", 1)[-1]
    if not name:
        return False
    # Strip the partition number: sdb1 -> sdb, mmcblk0p1 -> mmcblk0, nvme0n1p1.
    base = name
    while base and base[-1].isdigit():
        base = base[:-1]
    if base.endswith("p") and any(c.isdigit() for c in name):
        base = base[:-1]
    for candidate in (base, name):
        flag = Path("/sys/block") / candidate / "removable"
        try:
            if flag.read_text().strip() == "1":
                return True
        except OSError:
            continue
    return False


def parse_linux_mounts(text: str) -> list[tuple[str, str, str]]:
    """Pull (device, mount point, filesystem) out of /proc/mounts content."""
    found = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mount, fstype = parts[0], _unescape_mount(parts[1]), parts[2]
        if fstype not in LINUX_FILESYSTEMS or not device.startswith("/dev/"):
            continue
        found.append((device, mount, fstype))
    return found


def _linux_drives(removable_only: bool = False) -> list[Drive]:
    try:
        text = Path("/proc/mounts").read_text()
    except OSError:
        return []

    drives: list[Drive] = []
    seen: set[str] = set()
    for device, mount, _fstype in parse_linux_mounts(text):
        if mount in seen:
            continue
        seen.add(mount)
        removable = (
            _linux_is_removable(device)
            or any(mount.startswith(root) for root in LINUX_MEDIA_ROOTS)
        )
        if removable_only and not removable:
            continue
        # Auto-mounted media takes its folder name from the volume label.
        label = Path(mount).name or mount
        drives.append(Drive(Path(mount), label, label, removable))

    drives.sort(key=lambda d: (not d.removable, str(d.path)))
    return drives


def macos_volumes_to_drives(
    entries: Iterable[tuple[str, bool, int]],
    root_device: int,
    removable_only: bool = False,
) -> list[Drive]:
    """Turn a listing of /Volumes into drives.

    Each entry is (name, is a symlink, device id). macOS represents the startup
    disk as a symlink in /Volumes pointing back at /, so that one is reported as
    the fixed drive and everything else — cards, USB sticks, network shares — is
    treated as removable. Python cannot read the MNT_REMOVABLE flag without
    shelling out to diskutil, so the mount location is the signal, exactly as it
    is on Linux. A second internal APFS volume gets its own device id and so
    would be labelled removable; that costs nothing beyond an extra row.
    """
    drives: list[Drive] = []
    boot_label = ""

    for name, is_symlink, device in entries:
        if name.startswith(".") or name.lower() in MACOS_SKIP_VOLUMES:
            continue
        if is_symlink or device == root_device:
            boot_label = boot_label or name
            continue
        drives.append(Drive(MACOS_VOLUMES / name, name, name, True))

    if not removable_only:
        drives.append(Drive(Path("/"), "/", boot_label or "Macintosh HD", False))

    drives.sort(key=lambda d: (not d.removable, str(d.path)))
    return drives


def _macos_drives(removable_only: bool = False) -> list[Drive]:
    try:
        root_device = Path("/").stat().st_dev
    except OSError:
        root_device = -1

    try:
        listing = sorted(MACOS_VOLUMES.iterdir())
    except OSError:
        listing = []

    entries: list[tuple[str, bool, int]] = []
    for entry in listing:
        try:
            # is_symlink() first: is_dir() follows the link and would hide it.
            is_symlink = entry.is_symlink()
            if not entry.is_dir():
                continue
            entries.append((entry.name, is_symlink, entry.stat().st_dev))
        except OSError:
            continue

    return macos_volumes_to_drives(entries, root_device, removable_only)


def _windows_drives(removable_only: bool = False) -> list[Drive]:
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
    drives.sort(key=lambda d: (not d.removable, d.identifier))
    return drives


def list_drives(removable_only: bool = False) -> list[Drive]:
    """Mounted drives and volumes, removable ones first."""
    if hasattr(ctypes, "windll"):
        return _windows_drives(removable_only)
    if sys.platform == "darwin":
        return _macos_drives(removable_only)
    return _linux_drives(removable_only)


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
