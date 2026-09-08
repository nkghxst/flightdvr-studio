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
from PySide6.QtGui import QBrush, QColor, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QSizePolicy, QSpinBox, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTableWidget, QVBoxLayout, QWidget,
)

from .classic_layout import BrowserMode, UNSET, bound_text
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


def _count_label(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


def review_state_text(state: str, range_count: int,
                      flight_count: int | None = None) -> str:
    """The compact State-column summary for a clip."""
    lines = [REVIEW_KEYS[state]]
    if range_count:
        lines.append(_count_label(range_count, "range"))
    if flight_count is not None:
        lines.append("no flying" if flight_count == 0
                     else _count_label(flight_count, "flight"))
    return "\n".join(lines)


def review_state_tooltip(state: str, range_count: int,
                         flight_count: int | None = None) -> str:
    """Spell out the compact marker without making the column wider."""
    details = [REVIEW_LABELS[state]]
    if range_count:
        details.append(f"{_count_label(range_count, 'saved range')}")
    if flight_count is not None:
        estimate = ("no flying detected" if flight_count == 0 else
                    f"{_count_label(flight_count, 'flight')} detected")
        details.append(f"Motion estimate: {estimate}")
    return " · ".join(details)


# The name item uses UserRole for its path, SortItem uses the next role, and
# MainWindow uses the following one for its exported marker.
REVIEW_ROLE = Qt.ItemDataRole.UserRole + 3

# These carry the meaning; the surface colour does not come from them alone.
# `review_tint` blends each one toward the table's real palette so the same
# state remains a faint wash in both the light and dark Windows themes.
_REVIEW_HUES = {
    KEEP: QColor(34, 139, 34),
    MAYBE: QColor(218, 165, 32),
    REJECT: QColor(200, 45, 45),
}


def review_tint(palette: QPalette, state: str,
                strength: float = 0.14) -> QColor | None:
    """A faint state hue blended from the row's actual background.

    Returning ``None`` for Unreviewed matters: it leaves the native style in
    charge rather than painting what happens to be the current Base colour.
    Native Windows reports AlternateBase as black in light mode and white in
    dark mode, and this table does not enable alternating rows. Base is the
    only surface the tint genuinely has to coexist with.
    """
    hue = _REVIEW_HUES.get(state)
    if hue is None:
        return None
    base = palette.color(QPalette.ColorRole.Base)
    return QColor(
        round(base.red() * (1 - strength) + hue.red() * strength),
        round(base.green() * (1 - strength) + hue.green() * strength),
        round(base.blue() * (1 - strength) + hue.blue() * strength),
    )


class ReviewTintDelegate(QStyledItemDelegate):
    """Reinforce the State letter with a faint wash across its whole row."""

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:
        super().initStyleOption(option, index)
        # On native Qt 6.11, Highlight is a rounded rectangle per cell. Leaving
        # selected cells completely to the style also keeps the gaps between
        # those rectangles on Base instead of leaking the review colour.
        if option.state & QStyle.StateFlag.State_Selected:
            return
        state = index.data(REVIEW_ROLE)
        tint = review_tint(option.palette, str(state or ""))
        if tint is not None:
            option.backgroundBrush = QBrush(tint)


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
    length_filter_changed = Signal()
    mode_requested = Signal(object)

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

        # The browser's own control. The View menu mirrors it rather than
        # replacing it: the everyday route should be where the list is.
        header.addSpacing(8)
        header.addWidget(QLabel("List:"))
        self.mode_buttons: dict[BrowserMode, QPushButton] = {}
        for mode in BrowserMode:
            button = QPushButton(mode.label)
            button.setCheckable(True)
            button.setChecked(mode is BrowserMode.NORMAL)
            button.setMaximumWidth(78)
            button.setToolTip({
                BrowserMode.COLLAPSED:
                    "Put the list away and give the picture the room",
                BrowserMode.NORMAL: "The usual arrangement",
                BrowserMode.EXPANDED:
                    "Trade picture size for a taller list. The list scrolls; "
                    "nothing is left out of it.",
            }[mode])
            button.clicked.connect(
                lambda *_, chosen=mode: self.mode_requested.emit(chosen))
            header.addWidget(button)
            self.mode_buttons[mode] = button
        layout.addLayout(header)

        layout.addWidget(self._build_summary_bar())

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

        layout.addLayout(self._build_length_row())
        self.hidden_label = dim(QLabel(""))
        self.hidden_label.setMinimumWidth(0)
        self.hidden_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Preferred)
        self.hidden_label.setToolTip(
            "Filtering hides rows. It never unticks a clip, changes a review "
            "state, touches a saved range or affects anything already queued."
        )
        layout.addWidget(self.hidden_label)

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
            "U Unreviewed · K Keep · M Maybe · R Reject\n"
            "Saved ranges and motion-estimated flights appear below the letter"
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setIconSize(QSize(120, 68))
        self.table.setItemDelegate(ReviewTintDelegate(self.table))
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

    def _build_summary_bar(self) -> QWidget:
        """What stands in for the list while it is collapsed.

        The clip you are working on, its review state, what the filters are
        doing, and an obvious way back. A collapsed list that forgets which
        clip you were on is not collapsed, it is closed.
        """
        bar = self.summary_bar = QWidget()
        bar.hide()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)

        self.reopen_button = QPushButton("Show clips")
        self.reopen_button.setToolTip("Bring the clip list back")
        self.reopen_button.clicked.connect(
            lambda *_: self.mode_requested.emit(BrowserMode.NORMAL))
        row.addWidget(self.reopen_button)

        self.summary_thumb = QLabel()
        self.summary_thumb.setFixedSize(MIN_THUMB_WIDTH,
                                        round(MIN_THUMB_WIDTH * 9 / 16))
        self.summary_thumb.setScaledContents(True)
        row.addWidget(self.summary_thumb)

        self.summary_label = QLabel("No clip selected")
        row.addWidget(self.summary_label)
        row.addStretch(1)
        return bar

    def _build_length_row(self) -> QHBoxLayout:
        """The duration filter from #96, as two bounds and one escape hatch.

        Both bounds are spin boxes whose minimum reads as "off", so there is no
        sentinel to type and no third tick box to explain. Zero-length and
        unreadable clips are the same thing to `ClipInfo.duration`, so they get
        their own visible choice rather than being quietly counted as short.
        """
        row = QHBoxLayout()
        row.addWidget(QLabel("Length:"))

        self.min_length = QSpinBox()
        self.min_length.setRange(UNSET, 7200)
        self.min_length.setSuffix(" s")
        self.min_length.setSpecialValueText("off")
        self.min_length.setMaximumWidth(88)
        self.min_length.setToolTip(
            "Hide clips shorter than this. The bound is inclusive.")
        row.addWidget(QLabel("at least"))
        row.addWidget(self.min_length)

        self.max_length = QSpinBox()
        self.max_length.setRange(UNSET, 7200)
        self.max_length.setSuffix(" s")
        self.max_length.setSpecialValueText("off")
        self.max_length.setMaximumWidth(88)
        self.max_length.setToolTip(
            "Hide clips longer than this. The bound is inclusive.")
        row.addWidget(QLabel("at most"))
        row.addWidget(self.max_length)

        self.show_unknown = QCheckBox("Show unknown")
        self.show_unknown.setChecked(True)
        self.show_unknown.setToolTip(
            "A clip whose length could not be read shows ? in the Length "
            "column. Untick to hide those as well."
        )
        row.addWidget(self.show_unknown)

        self.reset_length = QPushButton("Reset")
        self.reset_length.setMaximumWidth(64)
        self.reset_length.setToolTip("Clear both bounds and show every length")
        self.reset_length.clicked.connect(lambda *_: self.reset_length_filter())
        row.addWidget(self.reset_length)

        for control in (self.min_length, self.max_length):
            control.valueChanged.connect(self._on_length_changed)
        self.show_unknown.toggled.connect(self._on_length_changed)

        self.length_label = dim(QLabel(""))
        # Elides rather than widening the row: it is a description of the two
        # boxes beside it, not a reason for the window to have a wider floor.
        self.length_label.setMinimumWidth(0)
        self.length_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Preferred)
        row.addWidget(self.length_label, 1)
        return row

    def _on_length_changed(self, *_args) -> None:
        """Keep the pair coherent, then tell the window to re-filter.

        Dragging the minimum past an active maximum would otherwise leave two
        boxes that each look reasonable describing a range nothing can be in.
        The maximum moves visibly instead.
        """
        if (self.max_length.value() != UNSET
                and self.max_length.value() < self.min_length.value()):
            blocked = self.max_length.blockSignals(True)
            self.max_length.setValue(self.min_length.value())
            self.max_length.blockSignals(blocked)
        self.length_label.setText(bound_text(
            self.min_length.value(), self.max_length.value(),
            self.show_unknown.isChecked()))
        self.length_filter_changed.emit()

    def reset_length_filter(self) -> None:
        """Back to the empty filter: every clip, whatever its length."""
        for control in (self.min_length, self.max_length):
            blocked = control.blockSignals(True)
            control.setValue(UNSET)
            control.blockSignals(blocked)
        blocked = self.show_unknown.blockSignals(True)
        self.show_unknown.setChecked(True)
        self.show_unknown.blockSignals(blocked)
        self._on_length_changed()

    def show_mode(self, mode: BrowserMode) -> None:
        """Show the list or the one-line summary, and agree with the menu."""
        for candidate, button in self.mode_buttons.items():
            blocked = button.blockSignals(True)
            button.setChecked(candidate is mode)
            button.blockSignals(blocked)
        collapsed = mode is BrowserMode.COLLAPSED
        self.table.setVisible(not collapsed)
        self.summary_bar.setVisible(collapsed)

    def set_summary(self, text: str, thumbnail=None) -> None:
        """The collapsed line's contents, supplied by the window."""
        self.summary_label.setText(text)
        if thumbnail is not None and not thumbnail.isNull():
            self.summary_thumb.setPixmap(thumbnail)
            self.summary_thumb.show()
        else:
            self.summary_thumb.clear()
            self.summary_thumb.hide()

    def set_hidden_summary(self, text: str) -> None:
        self.hidden_label.setText(text)

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
