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

"""Cached thumbnails for the clip list.

Thumbnails go through the same levels correction the exports use, so the
preview matches what you will actually get rather than the washed-out version.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .media import NO_WINDOW, ClipInfo, Tools

THUMB_WIDTH = 240

# How far past the seek point to decode before keeping a frame. Seeking in an
# MPEG-TS recording lands on an estimated byte offset rather than on a
# keyframe, so the first frames out of the decoder are torn macroblocks or flat
# grey. Decoding through a second and a half lets it resynchronise on a real
# keyframe. Without this, most thumbnails come out as noise.
RESYNC_SECONDS = 1.5


def cache_dir() -> Path:
    base = Path.home() / ".flightdvr" / "thumbs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _cache_key(clip: ClipInfo) -> str:
    raw = f"{clip.path}|{clip.size}|{clip.modified.timestamp()}|{THUMB_WIDTH}|{RESYNC_SECONDS}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def thumbnail_path(clip: ClipInfo) -> Path:
    return cache_dir() / f"{_cache_key(clip)}.jpg"


def build_command(tools: Tools, clip: ClipInfo, target: Path) -> list[str]:
    """ffmpeg arguments for one thumbnail."""
    duration = clip.duration if clip.duration > 0 else 0.0

    filters = []
    if clip.is_full_range:
        filters.append("scale=in_range=full:out_range=limited")
    filters.append(f"scale={THUMB_WIDTH}:-2:flags=bilinear")

    command = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-y", "-v", "error"]

    if duration > RESYNC_SECONDS + 2:
        # Seek fast to roughly a tenth in, then decode forward through the
        # resync window before keeping a frame.
        seek = min(max(1.0, duration * 0.12), duration - RESYNC_SECONDS - 1.0)
        command += ["-ss", f"{seek:.2f}", "-i", str(clip.path),
                    "-ss", f"{RESYNC_SECONDS:.2f}"]
    else:
        # Too short to seek into; decode from the start and take a late frame.
        command += ["-i", str(clip.path)]
        if duration > 1.0:
            command += ["-ss", f"{min(0.5, duration / 3):.2f}"]

    command += ["-frames:v", "1", "-vf", ",".join(filters), "-q:v", "4", str(target)]
    return command


def extract(tools: Tools, clip: ClipInfo) -> Path | None:
    """Grab a representative frame, reusing the cached copy when present."""
    target = thumbnail_path(clip)
    if target.exists() and target.stat().st_size > 0:
        return target

    try:
        subprocess.run(
            build_command(tools, clip, target),
            capture_output=True, timeout=90, creationflags=NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return target if target.exists() and target.stat().st_size > 0 else None


class _Signals(QObject):
    ready = Signal(str, str)  # clip path, thumbnail path


class _ThumbTask(QRunnable):
    def __init__(self, tools: Tools, clip: ClipInfo, signals: _Signals):
        super().__init__()
        self.tools = tools
        self.clip = clip
        self.signals = signals

    def run(self) -> None:
        result = extract(self.tools, self.clip)
        if result:
            self.signals.ready.emit(str(self.clip.path), str(result))


class ThumbnailLoader(QObject):
    """Generates thumbnails on a small background pool.

    Requests are held back while a scan is running. Thumbnail extraction and
    clip probing both read from the same slow card, and letting them compete
    makes the listing crawl.
    """

    ready = Signal(str, str)

    def __init__(self, tools: Tools, parent=None):
        super().__init__(parent)
        self.tools = tools
        self._signals = _Signals()
        self._signals.ready.connect(self.ready)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(3)
        self._queued: set[str] = set()
        self._held: list[ClipInfo] = []
        self._paused = False

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        held, self._held = self._held, []
        for clip in held:
            self._start(clip)

    def request(self, clip: ClipInfo) -> None:
        key = str(clip.path)
        if key in self._queued:
            return
        self._queued.add(key)
        if self._paused:
            self._held.append(clip)
        else:
            self._start(clip)

    def _start(self, clip: ClipInfo) -> None:
        self._pool.start(_ThumbTask(self.tools, clip, self._signals))

    def clear(self) -> None:
        self._pool.clear()
        self._queued.clear()
        self._held.clear()

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(2000)
