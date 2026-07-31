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

"""Filmstrip extraction and the scrubbing bar used to set in and out points.

Seeking into an MPEG-TS costs the best part of a second, so dragging a handle
cannot seek live. Instead every keyframe is pulled out once, up front, in a
single decode pass. On a Box Pro recording the keyframes land exactly a second
apart, and a three minute clip yields about 200 frames in four and a half
seconds, so scrubbing afterwards is instant.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QWidget

from .media import NO_WINDOW, ClipInfo, Tools, frame_rate_mode

FRAME_WIDTH = 160
PTS = re.compile(r"pts_time:([0-9.]+)")


def cache_root() -> Path:
    base = Path.home() / ".flightdvr" / "strips"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _key(clip: ClipInfo) -> str:
    raw = f"{clip.path}|{clip.size}|{clip.modified.timestamp()}|{FRAME_WIDTH}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


@dataclass
class Filmstrip:
    """Frames from one clip, with the time each was taken at."""

    frames: list[Path] = field(default_factory=list)
    times: list[float] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.frames)

    def index_at(self, seconds: float) -> int:
        """The frame nearest a point in time."""
        if not self.times:
            return 0
        best, gap = 0, abs(self.times[0] - seconds)
        for i, t in enumerate(self.times):
            if abs(t - seconds) < gap:
                best, gap = i, abs(t - seconds)
        return best

    def frame_at(self, seconds: float) -> Path | None:
        if not self.frames:
            return None
        return self.frames[self.index_at(seconds)]


def extract(tools: Tools, clip: ClipInfo) -> Filmstrip:
    """Pull every keyframe out of a clip, reusing the cache when present."""
    folder = cache_root() / _key(clip)
    times_file = folder / "times.txt"

    if times_file.exists():
        frames = sorted(folder.glob("f_*.jpg"))
        times = [float(t) for t in times_file.read_text().split()]
        if frames and len(frames) == len(times):
            return Filmstrip(frames, times)

    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)

    filters = []
    if clip.is_full_range:
        filters.append("scale=in_range=full:out_range=limited")
    filters += [f"scale={FRAME_WIDTH}:-2", "showinfo"]

    command = [
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-y", "-loglevel", "info",
        # Decoding keyframes alone is five times quicker than decoding
        # everything, and a keyframe a second is finer than anyone needs.
        "-skip_frame", "nokey", "-i", str(clip.path),
        # Asked for rather than assumed: -fps_mode replaced -vsync in ffmpeg
        # 5.1, and on 4.4 this call failed, so the filmstrip stayed empty.
        *frame_rate_mode(tools, "passthrough"),
        "-vf", ",".join(filters), "-q:v", "6",
        str(folder / "f_%04d.jpg"),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=600,
            creationflags=NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, OSError):
        return Filmstrip()

    # showinfo reports each frame it passed through, in order.
    times = [float(m) for m in PTS.findall(result.stderr)]
    frames = sorted(folder.glob("f_*.jpg"))
    if not frames:
        return Filmstrip()

    times = times[:len(frames)]
    while len(times) < len(frames):
        times.append(times[-1] + 1.0 if times else 0.0)

    times_file.write_text("\n".join(f"{t:.3f}" for t in times))
    return Filmstrip(frames, times)


class FilmstripLoader(QThread):
    """Builds one clip's filmstrip off the UI thread."""

    ready = Signal(str, object)

    def __init__(self, tools: Tools, clip: ClipInfo, parent=None):
        super().__init__(parent)
        self.tools = tools
        self.clip = clip

    def run(self) -> None:
        try:
            strip = extract(self.tools, self.clip)
        except Exception:  # pragma: no cover - never take the window down
            strip = Filmstrip()
        self.ready.emit(str(self.clip.path), strip)


class TrimBar(QWidget):
    """A filmstrip with draggable in and out handles.

    Clicking anywhere moves the playhead; dragging either end moves that point.
    """

    playhead_moved = Signal(float)
    trim_changed = Signal(float, float)

    HANDLE = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(56)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._strip = Filmstrip()
        self._pixmaps: list[QPixmap] = []
        self.duration = 0.0
        self.in_point = 0.0
        self.out_point = 0.0
        self.playhead = 0.0
        self._dragging: str | None = None

    def set_clip(self, duration: float, in_point: float, out_point: float) -> None:
        self.duration = max(0.0, duration)
        self.in_point = in_point
        self.out_point = out_point or self.duration
        self.playhead = in_point
        self.update()

    def set_strip(self, strip: Filmstrip) -> None:
        self._strip = strip
        self._pixmaps = []
        self.update()

    def has_strip(self) -> bool:
        return bool(self._strip)

    # -- geometry -------------------------------------------------------------

    def _x_for(self, seconds: float) -> int:
        if self.duration <= 0:
            return 0
        return round(seconds / self.duration * max(1, self.width() - 1))

    def _time_for(self, x: int) -> float:
        if self.duration <= 0:
            return 0.0
        fraction = min(1.0, max(0.0, x / max(1, self.width() - 1)))
        return fraction * self.duration

    # -- painting -------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        text = palette.color(QPalette.ColorRole.WindowText)
        accent = palette.color(QPalette.ColorRole.Highlight)

        rect = self.rect()
        painter.fillRect(rect, base)

        if self._strip and not self._pixmaps:
            self._pixmaps = [QPixmap(str(p)) for p in self._strip.frames]

        if self._pixmaps and self.duration > 0:
            # Tile frames across the width, choosing the nearest in time.
            step = max(24, rect.height() * 16 // 9)
            for x in range(0, rect.width(), step):
                seconds = self._time_for(x)
                pixmap = self._pixmaps[self._strip.index_at(seconds)]
                if pixmap.isNull():
                    continue
                painter.drawPixmap(
                    QRect(x, 0, step, rect.height()),
                    pixmap.scaled(QSize(step, rect.height()),
                                  Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                  Qt.TransformationMode.SmoothTransformation),
                )

        # Shade whatever is being cut away, heavily enough that the kept region
        # reads at a glance rather than on close inspection.
        shade = QColor(base)
        shade.setAlpha(225)
        left = self._x_for(self.in_point)
        right = self._x_for(self.out_point)
        if left > 0:
            painter.fillRect(QRect(0, 0, left, rect.height()), shade)
        if right < rect.width():
            painter.fillRect(QRect(right, 0, rect.width() - right, rect.height()), shade)

        painter.setPen(accent)
        painter.setBrush(accent)
        for x in (left, right):
            painter.drawRect(QRect(x - self.HANDLE // 2, 0, self.HANDLE, rect.height()))

        head = self._x_for(self.playhead)
        painter.setPen(text)
        painter.drawLine(head, 0, head, rect.height())
        painter.end()

    # -- interaction ----------------------------------------------------------

    def _near(self, x: int, seconds: float) -> bool:
        return abs(x - self._x_for(seconds)) <= self.HANDLE

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.duration <= 0:
            return
        x = event.position().toPoint().x()
        if self._near(x, self.in_point):
            self._dragging = "in"
        elif self._near(x, self.out_point):
            self._dragging = "out"
        else:
            self._dragging = "head"
        self._apply(x)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        x = event.position().toPoint().x()
        if self._dragging:
            self._apply(x)
            return
        near = self._near(x, self.in_point) or self._near(x, self.out_point)
        self.setCursor(Qt.CursorShape.SplitHCursor if near
                       else Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging = None

    def _apply(self, x: int) -> None:
        seconds = self._time_for(x)
        if self._dragging == "in":
            self.in_point = min(seconds, self.out_point - 0.5)
            self.playhead = self.in_point
            self.trim_changed.emit(self.in_point, self.out_point)
        elif self._dragging == "out":
            self.out_point = max(seconds, self.in_point + 0.5)
            self.playhead = self.out_point
            self.trim_changed.emit(self.in_point, self.out_point)
        else:
            self.playhead = seconds
        self.playhead_moved.emit(self.playhead)
        self.update()
