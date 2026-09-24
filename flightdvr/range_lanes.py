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

"""One lane per range, each on its own clock that starts at zero.

The whole-recording filmstrip above says where a range sits in the source.
These lanes say what each range *is*: 0:00 to its length, drawn from the same
frames the filmstrip already extracted. Nothing here decodes, and nothing here
edits a range — the active range is edited on the filmstrip, as it always was.

Rows are keyed by the range's stable id, never by its position. A lane that is
clicked names the range it was drawn for, so a reorder or a rename between the
drawing and the click cannot select a neighbour.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

ACTIVE_HEIGHT = 44
QUIET_HEIGHT = 22
LABEL_GAP = 2
ROW_GAP = 6
# Room for the active lane and two more before the list scrolls. A card with
# twenty ranges must not push the picture off a compact page.
VISIBLE_ROWS = 3


def clock(seconds: float) -> str:
    """Minutes and seconds to a tenth: a range is short, and 0:14.0 into it
    is the unit a person trimming is thinking in."""
    tenths = round(max(0.0, seconds) * 10)
    minutes, rest = divmod(tenths, 600)
    return f"{minutes}:{rest // 10:02d}.{rest % 10}"


@dataclass(frozen=True)
class LaneRange:
    """One range as a lane draws it: identity, name and source interval."""

    sid: str
    name: str
    start: float
    end: float

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start)


def _title(number: int, lane: LaneRange) -> str:
    name = lane.name or f"Range {number}"
    return (f"{number}  {name} · source {clock(lane.start)}–{clock(lane.end)}"
            f" · range 0:00.0–{clock(lane.length)}")


def local_to_source(lane: LaneRange, local: float) -> float:
    """A moment on the range's own clock, as a moment in the recording."""
    return lane.start + max(0.0, min(local, lane.length))


def source_to_local(lane: LaneRange, source: float) -> float | None:
    """A moment in the recording on the range's own clock, or None when the
    range does not contain it. The end is exclusive: the out point is where
    the range stops, not a frame in it."""
    if lane.start <= source < lane.end:
        return source - lane.start
    return None


class _Lane(QWidget):
    """One range's frames on its local clock, with a playhead when inside it."""

    clicked = Signal(str, float)

    def __init__(self, owner: "RangeLanes", lane: LaneRange, number: int,
                 parent=None) -> None:
        super().__init__(parent)
        self.owner = owner
        self.lane = lane
        self.number = number
        self.active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

    def set_active(self, active: bool) -> None:
        self.active = active
        self.setFixedHeight(ACTIVE_HEIGHT if active else QUIET_HEIGHT)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(200, ACTIVE_HEIGHT if self.active else QUIET_HEIGHT)

    def local_at(self, x: float) -> float:
        width = max(1, self.width() - 1)
        return min(1.0, max(0.0, x / width)) * self.lane.length

    def x_for(self, local: float) -> int:
        if self.lane.length <= 0:
            return 0
        return round(local / self.lane.length * max(1, self.width() - 1))

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        text = palette.color(QPalette.ColorRole.WindowText)
        accent = palette.color(QPalette.ColorRole.Highlight)
        rect = self.rect()
        painter.fillRect(rect, base)

        strip, pixmaps = self.owner.strip_frames()
        lane = self.lane
        if pixmaps and lane.length > 0:
            step = max(24, rect.height() * 16 // 9)
            for x in range(0, rect.width(), step):
                seconds = local_to_source(lane, self.local_at(x))
                pixmap = pixmaps[strip.index_at(seconds)]
                if pixmap.isNull():
                    continue
                painter.drawPixmap(
                    QRect(x, 0, step, rect.height()),
                    pixmap.scaled(QSize(step, rect.height()),
                                  Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                  Qt.TransformationMode.SmoothTransformation))
        if not self.active:
            # Quieter, so the lane being worked on reads first.
            shade = QColor(base)
            shade.setAlpha(110)
            painter.fillRect(rect, shade)

        frame = QColor(accent if self.active else text)
        frame.setAlpha(255 if self.active else 90)
        painter.setPen(frame)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        local = source_to_local(lane, self.owner.playhead)
        if local is not None:
            painter.setPen(text)
            head = self.x_for(local)
            painter.drawLine(head, 0, head, rect.height())
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        local = self.local_at(event.position().x())
        self.clicked.emit(self.lane.sid, local_to_source(self.lane, local))


class RangeLanes(QWidget):
    """Every range of the recording being trimmed, one lane each.

    ``range_clicked(sid, source_seconds)`` says which lane was pressed and
    where, as a moment in the recording. The window decides what that means:
    choosing another range, or moving the playhead within the active one.
    """

    range_clicked = Signal(str, float)
    range_stepped = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Ranges, each on its own clock")
        self._ranges: tuple[LaneRange, ...] = ()
        self._active_sid = ""
        self.playhead = 0.0
        self._strip = None
        self._pixmaps: list[QPixmap] = []
        self._rows: list[tuple[QLabel, _Lane]] = []
        self._wanted = False
        self._followed = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._body = QWidget()
        self._column = QVBoxLayout(self._body)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(ROW_GAP)
        self._column.addStretch(1)
        self.scroll.setWidget(self._body)
        outer.addWidget(self.scroll)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)
        self.hide()

    # -- what is shown --------------------------------------------------------

    @property
    def ranges(self) -> tuple[LaneRange, ...]:
        return self._ranges

    @property
    def active_sid(self) -> str:
        return self._active_sid

    def lane_for(self, sid: str) -> _Lane | None:
        return next((lane for _label, lane in self._rows
                     if lane.lane.sid == sid), None)

    def label_for(self, sid: str) -> QLabel | None:
        return next((label for label, lane in self._rows
                     if lane.lane.sid == sid), None)

    def set_ranges(self, ranges, active_sid: str) -> None:
        """The recording's ranges in their current order, and which one the
        filmstrip is editing. Rebuilt only when the ranges themselves change;
        a playhead move repaints."""
        ranges = tuple(ranges)
        if ranges != self._ranges:
            same_rows = ([one.sid for one in ranges]
                         == [one.sid for one in self._ranges])
            self._ranges = ranges
            if same_rows:
                # A handle being dragged, or a rename: the same rows, redrawn.
                # Rebuilding on every mouse move would recreate every lane.
                for number, (one, (label, lane)) in enumerate(
                        zip(ranges, self._rows), start=1):
                    label.setText(_title(number, one))
                    label.setToolTip(label.text())
                    lane.lane = one
                    lane.update()
            else:
                self._rebuild()
        self._active_sid = active_sid
        for _label, lane in self._rows:
            lane.set_active(lane.lane.sid == active_sid)
        self._fit_height()
        self.setVisible(bool(ranges) and self._wanted)

    def set_wanted(self, wanted: bool) -> None:
        """Whether the page this sits on shows lanes at all."""
        self._wanted = bool(wanted)
        self.setVisible(self._wanted and bool(self._ranges))

    def set_strip(self, strip) -> None:
        """The filmstrip's frames, loaded once for every lane."""
        if strip is self._strip:
            return
        self._strip = strip
        self._pixmaps = []
        self._repaint_lanes()

    def strip_frames(self):
        strip = self._strip
        if strip and not self._pixmaps:
            self._pixmaps = [QPixmap(str(path)) for path in strip.frames]
        return strip, self._pixmaps

    def follow(self, bar) -> None:
        """Keep the playhead where the filmstrip draws it.

        The filmstrip repaints whenever its playhead moves a pixel, from
        whichever of the window's many routes moved it. Reading it back on
        those repaints is one connection instead of one at every route, and a
        route added later cannot forget the lanes.
        """
        self._followed = bar
        bar.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt naming)
        if watched is self._followed and event.type() == QEvent.Type.Paint:
            self.set_playhead(watched.playhead)
        return False

    def set_playhead(self, seconds: float) -> None:
        if seconds == self.playhead:
            return
        was, self.playhead = self.playhead, seconds
        for _label, lane in self._rows:
            inside = (source_to_local(lane.lane, was) is not None
                      or source_to_local(lane.lane, seconds) is not None)
            if inside:
                lane.update()

    # -- building -------------------------------------------------------------

    def _rebuild(self) -> None:
        for label, _lane in self._rows:
            label.parentWidget().deleteLater()
        self._rows = []
        for number, one in enumerate(self._ranges, start=1):
            row = QWidget()
            stack = QVBoxLayout(row)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(LABEL_GAP)
            label = QLabel(_title(number, one))
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.NoTextInteraction)
            # Clipped rather than allowed to widen the column: the lanes take
            # the filmstrip's width, never more.
            label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Preferred)
            label.setToolTip(label.text())
            lane = _Lane(self, one, number)
            lane.clicked.connect(self.range_clicked)
            stack.addWidget(label)
            stack.addWidget(lane)
            # Before the trailing stretch, so spare height goes below.
            self._column.insertWidget(self._column.count() - 1, row)
            self._rows.append((label, lane))

    def _fit_height(self) -> None:
        """Room for the active lane and a few quiet ones; the rest scroll."""
        if not self._rows:
            self.scroll.setFixedHeight(0)
            return
        label = self._rows[0][0].sizeHint().height() + LABEL_GAP
        shown = min(VISIBLE_ROWS, len(self._rows))
        height = (ACTIVE_HEIGHT + (shown - 1) * QUIET_HEIGHT
                  + shown * label + (shown - 1) * ROW_GAP)
        self.scroll.setFixedHeight(height)
        active = self.lane_for(self._active_sid)
        if active is not None:
            self.scroll.ensureWidgetVisible(active, 0, 0)

    def _repaint_lanes(self) -> None:
        for _label, lane in self._rows:
            lane.update()

    # -- keys -----------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Up and Down choose the neighbouring range, by its identity."""
        sids = [one.sid for one in self._ranges]
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and sids:
            here = (sids.index(self._active_sid)
                    if self._active_sid in sids else 0)
            step = -1 if event.key() == Qt.Key.Key_Up else 1
            there = max(0, min(len(sids) - 1, here + step))
            if there != here:
                self.range_stepped.emit(sids[there])
            event.accept()
            return
        super().keyPressEvent(event)
