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

"""The clip browser and the sizing behaviour local to it."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QVBoxLayout, QWidget,
)

from .thumbs import THUMB_WIDTH
from .widgets import MIN_THUMB_WIDTH, MIN_VISIBLE_CLIPS, dim


class BrowserPanel(QWidget):
    """Own the clip table and emit the handful of actions around it."""

    open_external_requested = Signal()
    select_all_requested = Signal()
    select_none_requested = Signal()
    item_changed = Signal(object)
    item_activated = Signal(object)
    selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        self.clip_count_label = QLabel("No clips loaded")
        header.addWidget(self.clip_count_label)
        header.addStretch(1)

        self.preview_button = QPushButton("Open in player…")
        self.preview_button.setToolTip(
            "Hand the highlighted clip to your usual video player.\n"
            "Double-clicking a row plays it here instead."
        )
        self.preview_button.clicked.connect(
            lambda *_: self.open_external_requested.emit()
        )
        header.addWidget(self.preview_button)

        for text, requested in (
            ("All", self.select_all_requested),
            ("None", self.select_none_requested),
        ):
            button = QPushButton(text)
            button.setFixedWidth(58)
            button.clicked.connect(lambda *_, signal=requested: signal.emit())
            header.addWidget(button)
        layout.addLayout(header)

        self.warning_label = dim(QLabel())
        self.warning_label.hide()
        layout.addWidget(self.warning_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Clip", "Length", "Size", "Card date", "Format"]
        )
        self.table.horizontalHeaderItem(3).setToolTip(
            "The timestamp on the card, not when you flew. The Box Pro has no "
            "clock battery, so these are unreliable."
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setIconSize(QSize(120, 68))
        self.table.setSortingEnabled(True)
        head = self.table.horizontalHeader()
        # The name column takes the slack, but the thumbnail grows into it, so
        # extra width buys a bigger preview rather than empty space.
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            head.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        head.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        head.setToolTip("Click a column heading to sort by it")
        self.table.itemChanged.connect(
            lambda item: self.item_changed.emit(item)
        )
        self.table.itemDoubleClicked.connect(
            lambda item, *_: self.item_activated.emit(item)
        )
        self.table.itemSelectionChanged.connect(
            lambda: self.selection_changed.emit()
        )
        layout.addWidget(self.table, 1)

    def sync_thumbnail_size(self) -> None:
        """Fit thumbnails to the width and height the list actually has.

        Width is capped at the generated thumbnail size; height is bounded so
        several clips remain visible. This must be called after Qt's deferred
        layout pass, when the viewport reports the size it will keep.
        """
        if self.table.rowCount() == 0:
            return
        available = self.table.columnWidth(0)
        # Leave room for the tick box, cell padding and filename. The floor is
        # low enough that a narrow window shrinks the image before the name.
        width = max(MIN_THUMB_WIDTH, min(THUMB_WIDTH, available - 150))

        viewport = self.table.viewport().height()
        if viewport > 0:
            by_height = max(48, viewport // MIN_VISIBLE_CLIPS - 6)
            width = max(
                MIN_THUMB_WIDTH,
                min(width, round(by_height * 16 / 9)),
            )

        height = round(width * 9 / 16)
        if self.table.iconSize().width() == width:
            return
        self.table.setIconSize(QSize(width, height))
        for row in range(self.table.rowCount()):
            self.table.setRowHeight(row, height + 6)
