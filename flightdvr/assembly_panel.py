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

"""The list of ranges that will become one file, in the order chosen.

The old join checkbox asked for a result and inferred the order. This asks for
the order and shows it, which is the whole point: a delivery is a decision, and
a decision nobody can see before pressing Export is not one they made.

Reordering is by button and by keyboard, never only by dragging. Dragging is a
fine way to move a row and a poor way to be the only way — it needs a pointer,
a steady hand and a visible target, and this list is a thing people will edit
from a laptop trackpad on a field table.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)

from .assembly import Item, Row, summary
from .format import human_duration
from .widgets import dim

ITEM_ROLE = Qt.ItemDataRole.UserRole


class OrderedList(QListWidget):
    """A list that says when a drag actually finished.

    `rowsMoved` is the obvious signal and never fires here: an internal move
    on a list is an insert followed by a remove, not a move, so listening to
    the model reports one drag as two events with a half-finished order in
    between. The drop itself is the only moment the new order is both complete
    and known to be the user's.
    """

    dropped = Signal()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        before = self.count()
        super().dropEvent(event)
        if event.isAccepted() and self.count() == before:
            self.dropped.emit()


class AssemblyPanel(QWidget):
    """A compact ordered list, and the four things you can do to it."""

    fill_requested = Signal()          # take the ticked ranges
    export_requested = Signal()        # queue the list, in the order shown
    order_changed = Signal()           # the list itself changed

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Assembly</b>"))
        header.addStretch(1)
        self.fill_button = QPushButton("Use ticked ranges")
        self.fill_button.setToolTip(
            "Fill the list from the ranges ticked in the browser, in DVR "
            "counter order and then along each recording"
        )
        self.fill_button.clicked.connect(lambda *_: self.fill_requested.emit())
        header.addWidget(self.fill_button)
        layout.addLayout(header)

        self.list = OrderedList()
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setAlternatingRowColors(True)
        self.list.setMinimumHeight(120)
        # A second way to reorder, never the only way. Dragging needs a
        # pointer, a steady hand and a visible target; the buttons and
        # Alt+Arrow keys stay exactly as they were.
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setDropIndicatorShown(True)
        # One signal per completed drop. Qt moves rows by inserting and then
        # removing, so listening to rowsInserted or rowsRemoved would report a
        # single drag twice and capture a half-finished order in between.
        self.list.dropped.connect(self._on_dropped)
        layout.addWidget(self.list)

        self.summary_label = dim(QLabel("Nothing in the assembly yet"))
        layout.addWidget(self.summary_label)

        buttons = QHBoxLayout()
        self.up_button = QPushButton("Move up")
        self.down_button = QPushButton("Move down")
        self.remove_button = QPushButton("Remove")
        self.remove_button.setToolTip(
            "Take it out of this list. The range itself is left alone."
        )
        self.reset_button = QPushButton("Default order")
        for button, slot in (
            (self.up_button, lambda *_: self._move(-1)),
            (self.down_button, lambda *_: self._move(1)),
            (self.remove_button, lambda *_: self._remove()),
            (self.reset_button, lambda *_: self.fill_requested.emit()),
        ):
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.export_button = QPushButton("Add assembly to queue")
        self.export_button.setToolTip(
            "Queue one joined export of exactly this list, in this order"
        )
        self.export_button.clicked.connect(
            lambda *_: self.export_requested.emit())
        layout.addWidget(self.export_button)

        # The keyboard has to reach everything the buttons do. Alt with the
        # arrows is the usual spelling for "move the thing", and leaves plain
        # Up and Down to do what they always do in a list.
        for keys, slot in (
            ("Alt+Up", lambda: self._move(-1)),
            ("Alt+Down", lambda: self._move(1)),
            ("Del", self._remove),
        ):
            shortcut = QShortcut(QKeySequence(keys), self.list)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(slot)

        self.list.itemSelectionChanged.connect(self._update_buttons)
        self._update_buttons()

    # -- what is in it --------------------------------------------------------

    def show_rows(self, rows: list[Row]) -> None:
        """Redraw from the model, in the model's order, keeping the selection.

        One loop over one ordered list. Drawing the resolved rows and then the
        missing ones put an interleaved gap at the bottom, and `items()` then
        handed that altered order back to be saved — so merely opening a card
        with a gap in the middle rewrote the order it was stored in.
        """
        chosen = set(self._selected_rows())
        self.list.clear()

        for row in rows:
            text = row.label()
            if not row.missing:
                text = f"{text}   {human_duration(row.duration)}"
            entry = QListWidgetItem(text)
            entry.setData(ITEM_ROLE, row.item)
            if row.missing:
                # Visible, in place, and neither selectable nor draggable — it
                # is a problem to resolve, not material to arrange. Clearing
                # ItemIsEnabled alone leaves ItemIsDragEnabled set, so the row
                # could still be picked up and moved away from the position
                # that is the only remaining evidence of where it belonged.
                entry.setFlags(entry.flags()
                               & ~Qt.ItemFlag.ItemIsEnabled
                               & ~Qt.ItemFlag.ItemIsDragEnabled
                               & ~Qt.ItemFlag.ItemIsDropEnabled)
            self.list.addItem(entry)

        for index in chosen:
            if index < self.list.count():
                self.list.item(index).setSelected(True)

        self.summary_label.setText(summary(rows))
        self._update_buttons()

    def _on_dropped(self) -> None:
        """A drag finished. Same path as the buttons, so the order is captured
        and scheduled for saving by the code that already does that."""
        self.order_changed.emit()
        self._update_buttons()

    def items(self) -> list[Item]:
        """The list as stored references, in exactly the order displayed."""
        return [self.list.item(row).data(ITEM_ROLE)
                for row in range(self.list.count())]

    def is_empty(self) -> bool:
        return self.list.count() == 0

    # -- editing it -----------------------------------------------------------

    def _selected_rows(self) -> list[int]:
        return sorted(self.list.row(i) for i in self.list.selectedItems())

    def _move(self, step: int) -> None:
        """Move the selection one place, keeping it selected and contiguous.

        Walking the rows from the leading edge is what stops a multi-row
        selection from turning inside out: moving the top one first when going
        up, the bottom one first when going down.
        """
        rows = self._selected_rows()
        if not rows:
            return
        if step < 0 and rows[0] == 0:
            return
        if step > 0 and rows[-1] == self.list.count() - 1:
            return

        for row in (rows if step < 0 else reversed(rows)):
            taken = self.list.takeItem(row)
            self.list.insertItem(row + step, taken)
            taken.setSelected(True)
        self.order_changed.emit()
        self._update_buttons()

    def _remove(self) -> None:
        """Take rows out of the list. The ranges themselves are untouched."""
        rows = self._selected_rows()
        if not rows:
            return
        for row in reversed(rows):
            self.list.takeItem(row)
        self.order_changed.emit()
        self._update_buttons()

    def _update_buttons(self) -> None:
        rows = self._selected_rows()
        count = self.list.count()
        self.up_button.setEnabled(bool(rows) and rows[0] > 0)
        self.down_button.setEnabled(bool(rows) and rows[-1] < count - 1)
        self.remove_button.setEnabled(bool(rows))
        self.reset_button.setEnabled(count > 0)
        # One item is a trim, not an assembly, and the ordinary Add to queue
        # already does that better. Two is where a join begins to mean anything.
        self.export_button.setEnabled(count > 1)
