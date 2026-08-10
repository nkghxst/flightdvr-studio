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
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QWidget

from .media import (
    NO_WINDOW, ClipInfo, Tools, frame_rate_mode, request_stop, stop_process,
)

FRAME_WIDTH = 160
PTS = re.compile(r"pts_time:([0-9.]+)")


def cache_root() -> Path:
    base = Path.home() / ".flightdvr" / "strips"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _key(clip: ClipInfo) -> str:
    """Where this clip's filmstrip lives.

    Built on ClipInfo.fingerprint so there is one idea of what identifies a
    recording, plus the frame width, because changing that has to invalidate
    what is already cached.
    """
    raw = f"{clip.fingerprint}|{FRAME_WIDTH}"
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


def _read_cached(folder: Path) -> Filmstrip:
    times_file = folder / "times.txt"
    if not times_file.exists():
        return Filmstrip()
    frames = sorted(folder.glob("f_*.jpg"))
    try:
        times = [float(t) for t in times_file.read_text().split()]
    except (OSError, ValueError):
        return Filmstrip()
    if frames and len(frames) == len(times):
        return Filmstrip(frames, times)
    return Filmstrip()


def cached_filmstrip(clip: ClipInfo) -> Filmstrip:
    """Return this clip's complete cached filmstrip without decoding it."""
    return _read_cached(cache_root() / _key(clip))


def extract(tools: Tools, clip: ClipInfo, register=None,
            cancelled=None) -> Filmstrip:
    """Pull every keyframe out of a clip, reusing the cache when present.

    Frames are written to a staging directory of this extraction's own and
    moved into place at the end. Selecting a clip, then another, then the first
    again starts a second extraction of it while the first is still running,
    and both used to delete and rewrite the one cache directory underneath each
    other. Whoever finishes first now publishes; the loser throws its own work
    away and reads what is already there.

    `register` is handed the running ffmpeg so the caller can stop it. Without
    it this blocked for up to ten minutes and could not be cancelled, which on
    a slow card meant a window that would not close.

    `cancelled` says whether the stop was asked for. Terminating ffmpeg makes
    communicate() return normally, so without this a cancelled extraction
    published the frames it had got to as though they were the whole clip —
    and the cache check only counts frames against times, so that truncated
    filmstrip would be believed from then on. A genuine decode failure still
    publishes what it managed: half a filmstrip of a damaged recording is
    worth having, half of one nobody waited for is not.
    """
    folder = cache_root() / _key(clip)

    cached = cached_filmstrip(clip)
    if cached:
        return cached

    staging = Path(tempfile.mkdtemp(dir=cache_root(), prefix="building-"))

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
        str(staging / "f_%04d.jpg"),
    ]
    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, creationflags=NO_WINDOW,
        )
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        return Filmstrip()

    if register is not None:
        register(proc)
    try:
        _out, errors = proc.communicate(timeout=600)
    except subprocess.TimeoutExpired:
        stop_process(proc)
        shutil.rmtree(staging, ignore_errors=True)
        return Filmstrip()

    if cancelled is not None and cancelled():
        shutil.rmtree(staging, ignore_errors=True)
        return Filmstrip()

    # showinfo reports each frame it passed through, in order.
    times = [float(m) for m in PTS.findall(errors or "")]
    frames = sorted(staging.glob("f_*.jpg"))
    if not frames:
        shutil.rmtree(staging, ignore_errors=True)
        return Filmstrip()

    times = times[:len(frames)]
    while len(times) < len(frames):
        times.append(times[-1] + 1.0 if times else 0.0)
    (staging / "times.txt").write_text("\n".join(f"{t:.3f}" for t in times))

    try:
        staging.rename(folder)
    except OSError:
        # Another extraction of this clip published first. Theirs is complete,
        # so use it rather than fighting over the directory.
        shutil.rmtree(staging, ignore_errors=True)
        return _read_cached(folder)

    return Filmstrip(sorted(folder.glob("f_*.jpg")), times)


class FilmstripLoader(QThread):
    """Builds one clip's filmstrip off the UI thread.

    Carries a generation because browsing the list starts one of these per clip
    and they finish in whatever order they finish. Matching on the clip's path
    alone is not enough: select A, then B, then A again, and the first
    extraction of A can land after the second has started and be accepted as
    the current one.
    """

    ready = Signal(int, str, object)          # generation, clip path, strip
    # generation, clip path, Activity — read from the strip this thread just
    # built, because it already has every frame and the UI thread does not want
    # the wait. Measured at 70–100 ms on a three-minute recording and about
    # half a second on a fifteen-minute one, which is a visible freeze.
    activity_ready = Signal(int, str, object)

    def __init__(self, tools: Tools, clip: ClipInfo, generation: int = 0,
                 parent=None):
        super().__init__(parent)
        self.tools = tools
        self.clip = clip
        self.generation = generation
        self._process: subprocess.Popen | None = None
        self._cancelled = False

    def stop(self) -> None:
        """Ask the extraction to stop. Asks without waiting: called from the
        UI thread when the selection moves on, and when the window closes."""
        self._cancelled = True
        request_stop(self._process)

    def _register(self, proc: subprocess.Popen) -> None:
        self._process = proc
        # Cancelled between starting the thread and ffmpeg starting, in which
        # case nothing had been created yet for stop() to have found.
        if self._cancelled:
            request_stop(proc)

    def run(self) -> None:
        try:
            strip = extract(self.tools, self.clip,
                            register=self._register,
                            cancelled=lambda: self._cancelled)
        except Exception:  # pragma: no cover - never take the window down
            strip = Filmstrip()
        finally:
            stop_process(self._process)
            self._process = None
        if self._cancelled:
            return
        self.ready.emit(self.generation, str(self.clip.path), strip)

        # After the strip, so the filmstrip appears the moment it is ready and
        # the reading of it arrives a fraction later rather than holding it up.
        try:
            from .motion import activity
            found = activity(strip, self.clip.duration)
        except Exception:  # pragma: no cover - a guess must never cost a clip
            found = None
        if not self._cancelled and found is not None:
            self.activity_ready.emit(self.generation, str(self.clip.path), found)


class TrimBar(QWidget):
    """A filmstrip with draggable in and out handles.

    Clicking anywhere moves the playhead; dragging either end moves that point.
    """

    playhead_moved = Signal(float)
    trim_changed = Signal(float, float)
    select_picked = Signal(int)

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

        # The other ranges on this clip, drawn but not edited. in_point and
        # out_point remain the one being worked on, so dragging, I, O and Reset
        # all mean what they meant when a clip had only one.
        self.ranges: list[tuple[float, float]] = []
        self.selected = 0

    def set_clip(self, duration: float, in_point: float, out_point: float,
                 ranges=None, selected: int = 0) -> None:
        self.duration = max(0.0, duration)
        self.in_point = in_point
        self.out_point = out_point or self.duration
        self.playhead = in_point
        self.ranges = [(a, b or self.duration) for a, b in (ranges or [])]
        self.selected = selected
        self.update()

    def _kept(self) -> list[tuple[float, float]]:
        """Every range that will be exported, the edited one included.

        The edited range comes from in_point/out_point rather than from the
        list, because dragging a handle moves those and the list is only
        refreshed when the selection changes.
        """
        if not self.ranges:
            return [(self.in_point, self.out_point)]
        kept = list(self.ranges)
        if 0 <= self.selected < len(kept):
            kept[self.selected] = (self.in_point, self.out_point)
        return kept

    def set_playhead(self, seconds: float) -> None:
        """Move the marker without telling anyone it moved.

        playhead_moved is deliberately not emitted. The player is what moves
        the playhead while a clip runs, and echoing the move back would send
        the window off to paint a filmstrip still over the live video.

        Repainting only when the marker changes pixel matters more than it
        looks: paintEvent rescales every visible tile with a smooth transform,
        so at thirty updates a second this widget would cost more than the
        decoder does, and the decoder would get the blame.
        """
        seconds = max(0.0, seconds)
        if self.duration > 0:
            seconds = min(seconds, self.duration)
        moved = self._x_for(seconds) != self._x_for(self.playhead)
        self.playhead = seconds
        if moved:
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

        # Shade whatever is being cut away, heavily enough that the kept
        # regions read at a glance rather than on close inspection. Painted as
        # the gaps between ranges rather than per range, so two selects that
        # touch do not shade each other's footage.
        shade = QColor(base)
        shade.setAlpha(225)
        kept = sorted(self._kept())
        edge = 0
        for start, end in kept:
            left = self._x_for(start)
            if left > edge:
                painter.fillRect(QRect(edge, 0, left - edge, rect.height()), shade)
            edge = max(edge, self._x_for(end))
        if edge < rect.width():
            painter.fillRect(
                QRect(edge, 0, rect.width() - edge, rect.height()), shade)

        # The ranges nobody is editing get a thin line rather than a handle:
        # visible enough to show there is something there, quiet enough that
        # the pair being dragged is obvious.
        quiet = QColor(accent)
        quiet.setAlpha(120)
        painter.setPen(quiet)
        painter.setBrush(quiet)
        for index, (start, end) in enumerate(self.ranges):
            if index == self.selected:
                continue
            for seconds in (start, end):
                x = self._x_for(seconds)
                painter.drawRect(QRect(x - 1, 0, 2, rect.height()))

        left = self._x_for(self.in_point)
        right = self._x_for(self.out_point)
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
            # Clicking inside another range picks it up, which is the only way
            # to reach a select with the mouse. The handles are checked first,
            # so overlapping ranges never steal a drag that had already begun.
            picked = self._range_at(self._time_for(x))
            if picked is not None and picked != self.selected:
                self.select_picked.emit(picked)
                return
            self._dragging = "head"
        self._apply(x)

    def _range_at(self, seconds: float) -> int | None:
        """Which range covers this moment, latest first.

        Later selects win where two overlap: they are the ones drawn on top, so
        they are the ones a click looks like it landed on.
        """
        for index in range(len(self.ranges) - 1, -1, -1):
            start, end = self.ranges[index]
            if start - 0.01 <= seconds <= end + 0.01:
                return index
        return None

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
