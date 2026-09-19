# FlightDVR Studio - browse, trim and convert HDZero goggle DVR footage.
# Copyright (C) 2026 Isadu Nkemi
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.

"""A small output-clock scrub strip for one compiled sequence."""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .sequence_plan import SequencePlan


class SequenceStrip(QWidget):
    """Draw occurrence seams and emit revision-bound output positions.

    The widget owns no editable sequence data.  Its only timing authority is
    the immutable ``SequencePlan`` supplied by the window, and every request
    carries that plan's revision back to the caller.  A drag begun before an
    Assembly reorder can therefore be refused instead of being interpreted on
    the new order.
    """

    scrub_requested = Signal(str, float)  # sequence revision, output seconds

    def __init__(self, parent=None):
        super().__init__(parent)
        self._plan: SequencePlan | None = None
        self._position = 0.0
        self.setMinimumHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Joined output position")
        self.setToolTip(
            "Drag to inspect the assembled output clock. Seams belong to the "
            "following occurrence; the exact end holds the last frame.")

    @property
    def plan(self) -> SequencePlan | None:
        return self._plan

    @property
    def position(self) -> float:
        return self._position

    @property
    def seams(self) -> tuple[float, ...]:
        if self._plan is None:
            return ()
        starts = [float(item.output.start)
                  for item in self._plan.occurrences]
        return tuple(starts + [float(self._plan.total_duration)])

    def set_plan(self, plan: SequencePlan | None) -> None:
        self._plan = plan
        self._position = 0.0
        self.setEnabled(plan is not None)
        self.update()

    def set_position(self, seconds: float) -> None:
        if self._plan is None:
            return
        total = float(self._plan.total_duration)
        self._position = max(0.0, min(float(seconds), total))
        self.update()

    def request_position(self, seconds: float) -> None:
        """Emit one bounded user-style request against the displayed plan."""
        if self._plan is None:
            return
        try:
            numeric = float(seconds)
        except (TypeError, ValueError, OverflowError):
            return
        if not math.isfinite(numeric):
            return
        total = float(self._plan.total_duration)
        bounded = max(0.0, min(numeric, total))
        self.scrub_requested.emit(self._plan.revision, bounded)

    def _request_at(self, x: float) -> None:
        if self._plan is None:
            return
        width = max(1, self.width() - 2)
        ratio = max(0.0, min(float(x) - 1.0, width)) / width
        self.request_position(ratio * float(self._plan.total_duration))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: D102
        if event.button() == Qt.MouseButton.LeftButton and self._plan is not None:
            self._request_at(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: D102
        if (event.buttons() & Qt.MouseButton.LeftButton
                and self._plan is not None):
            self._request_at(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: D102
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(1.0, 8.0, max(1.0, self.width() - 2.0),
                        max(8.0, self.height() - 16.0))
        palette = self.palette()
        painter.setPen(QPen(palette.mid().color(), 1.0))
        painter.setBrush(palette.base())
        painter.drawRoundedRect(bounds, 4.0, 4.0)

        plan = self._plan
        if plan is None:
            painter.setPen(palette.placeholderText().color())
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter,
                             "Add at least two resolved Assembly rows")
            return

        total = float(plan.total_duration)
        if total <= 0:
            return
        colours = (QColor("#3b82f6"), QColor("#14b8a6"), QColor("#8b5cf6"))
        for index, occurrence in enumerate(plan.occurrences):
            left = bounds.left() + bounds.width() * (
                float(occurrence.output.start) / total)
            right = bounds.left() + bounds.width() * (
                float(occurrence.output.end) / total)
            colour = colours[index % len(colours)]
            colour.setAlpha(145)
            painter.fillRect(QRectF(left, bounds.top(), max(1.0, right - left),
                                    bounds.height()), colour)
            if index:
                painter.setPen(QPen(palette.text().color(), 1.0))
                painter.drawLine(int(left), int(bounds.top()),
                                 int(left), int(bounds.bottom()))

        playhead = bounds.left() + bounds.width() * (self._position / total)
        painter.setPen(QPen(palette.highlight().color(), 3.0))
        painter.drawLine(int(playhead), int(bounds.top() - 4),
                         int(playhead), int(bounds.bottom() + 4))
