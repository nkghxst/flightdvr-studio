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
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QVBoxLayout, QWidget,
)

from .session import KEEP, MAYBE, REJECT, UNREVIEWED
from .thumbs import THUMB_WIDTH
from .widgets import MIN_THUMB_WIDTH, MIN_VISIBLE_CLIPS, dim


FILTER_ALL = "all"
FILTER_EXPORTED = "exported"
REVIEW_LABELS = {
    UNREVIEWED: "Unreviewed",
    KEEP: "Keep",
    MAYBE: "Maybe",
    REJECT: "Reject",
}
REVIEW_KEYS = {
    UNREVIEWED: "U",
    KEEP: "K",
    MAYBE: "M",
    REJECT: "R",
}


class BrowserPanel(QWidget):
    """Own the clip table and emit the handful of actions around it."""

    open_external_requested = Signal()
    select_all_requested = Signal()
    select_none_requested = Signal()
    item_changed = Signal(object)
    item_activated = Signal(object)
    selection_changed = Signal()
    filter_changed = Signal(str)
    review_requested = Signal(str)

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
            button.setToolTip(
                f"{'Tick' if text == 'All' else 'Untick'} every visible clip"
            )
            button.clicked.connect(lambda *_, signal=requested: signal.emit())
            header.addWidget(button)
        layout.addLayout(header)

        review = QHBoxLayout()
        review.addWidget(QLabel("Show:"))
        self.review_filter = QComboBox()
        for label, value in (
            ("All", FILTER_ALL),
            ("Unreviewed", UNREVIEWED),
            ("Keep", KEEP),
            ("Maybe", MAYBE),
            ("Reject", REJECT),
            ("Exported", FILTER_EXPORTED),
        ):
            self.review_filter.addItem(label, value)
        self.review_filter.setToolTip("Show only clips in this review state")
        self.review_filter.currentIndexChanged.connect(
            lambda *_: self.filter_changed.emit(
                str(self.review_filter.currentData()))
        )
        review.addWidget(self.review_filter)

        review.addSpacing(8)
        review.addWidget(QLabel("Mark:"))
        self.review_buttons: dict[str, QPushButton] = {}
        for state, label in REVIEW_LABELS.items():
            key = REVIEW_KEYS[state]
            button = QPushButton(label)
            button.setToolTip(
                f"Mark the highlighted clip {label} ({key})"
            )
            button.clicked.connect(
                lambda *_, chosen=state: self.review_requested.emit(chosen)
            )
            review.addWidget(button)
            self.review_buttons[state] = button

        review.addStretch(1)
        self.review_count_label = QLabel("0 of 0 reviewed")
        self.review_count_label.setToolTip(
            "Keep, Maybe and Reject all count as reviewed"
        )
        review.addWidget(self.review_count_label)
        layout.addLayout(review)

        self.warning_label = dim(QLabel())
        self.warning_label.hide()
        layout.addWidget(self.warning_label)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Clip", "Length", "Size", "Card date", "Format", "State"]
        )
        self.table.horizontalHeaderItem(3).setToolTip(
            "The timestamp on the card, not when you flew. The Box Pro has no "
            "clock battery, so these are unreliable."
        )
        self.table.horizontalHeaderItem(5).setToolTip(
            "U Unreviewed · K Keep · M Maybe · R Reject"
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
        for column in range(1, 6):
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

        # Scoped to the list. K remains Play while the preview has focus, and
        # becomes Keep while the browser has focus; a window-wide shortcut
        # would make both ambiguous and Qt would fire neither.
        self.review_shortcuts: dict[str, QShortcut] = {}
        for state, key in REVIEW_KEYS.items():
            shortcut = QShortcut(
                QKeySequence(key), self.table,
                activated=lambda chosen=state: self.review_requested.emit(
                    chosen),
            )
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.review_shortcuts[state] = shortcut

    def set_review_progress(self, reviewed: int, total: int) -> None:
        self.review_count_label.setText(f"{reviewed} of {total} reviewed")

    def sync_thumbnail_size(self) -> None:
        """Fit thumbnails to the width and height the list actually has.

        Width is capped at the generated thumbnail size; height is bounded so
        several clips remain visible. This must be called after Qt's deferred
        layout pass, when the viewport reports the size it will keep.
        """
        if self.table.rowCount() == 0:
            return
        available = self.table.columnWidth(0)
        # Leave room for the tick box, cell padding and filename. `hdz_000.ts`
        # is 55px in the native Windows UI font; 110px leaves the same again
        # for the tick and padding. The old 150px reserve plus the State column
        # pinned thumbnails at their minimum even when the list grew taller.
        width = max(MIN_THUMB_WIDTH, min(THUMB_WIDTH, available - 110))

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
