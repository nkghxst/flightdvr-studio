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

"""What you are making: the ones you can still change, then the ones you can't.

The list is in two parts and the boundary between them is the point. Above it
are planned outputs, which editing reaches. Below are committed jobs, which it
does not — and the sidebar says so in words rather than leaving somebody to
discover it by changing a preset and watching the render come out with the old
one.

This holds no model. It is given rows of plain values and hands back the key of
whatever was chosen, so what a card says can be checked without a session, a
card of recordings or a queue — and so the window keeps being the only thing
that knows what an `OutputTarget` is.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
)

from .widgets import INNER, TIGHT, dim

KEY_ROLE = Qt.ItemDataRole.UserRole

COMMITTED_NOTE = "Editing above does not reach these."


@dataclass(frozen=True)
class Card:
    """One line of the list, already worded by whoever knows the model."""

    key: object
    title: str
    detail: str = ""
    sound: str = ""
    status: str = ""

    def lines(self) -> tuple[str, ...]:
        return tuple(part for part in (self.title, self.detail, self.sound)
                     if part)


class OutputSidebar(QWidget):
    """Planned above, committed below, and the line between them stated."""

    chosen = Signal(object)          # the key of a planned card

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._filling = False
        self._selected: object | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(INNER)

        self.heading = QLabel("<b>This export</b>")
        layout.addWidget(self.heading)
        self.summary = dim(QLabel(""))
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.planned_title = QLabel("Planned — you can still change these")
        layout.addWidget(self.planned_title)
        self.planned = QListWidget()
        self.planned.setWordWrap(True)
        self.planned.itemSelectionChanged.connect(self._on_chosen)
        layout.addWidget(self.planned, 1)

        self.committed_title = QLabel("Committed — being made, or made")
        layout.addWidget(self.committed_title)
        self.committed_note = dim(QLabel(COMMITTED_NOTE))
        self.committed_note.setWordWrap(True)
        layout.addWidget(self.committed_note)
        self.committed = QListWidget()
        self.committed.setWordWrap(True)
        # Not selectable on purpose. A committed job is not a thing you edit,
        # and a row that highlights invites the click that proves it.
        self.committed.setSelectionMode(
            QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self.committed)

    # -- what the window gives it ---------------------------------------------

    def show_cards(self, planned, committed) -> None:
        """Redraw both lists, keeping the chosen planned output if it survives.

        Fenced against itself: writing a list emits a selection change, and
        answering that would rebuild while rebuilding. One refresh has to be
        one refresh, which a counter can see and a screen cannot.
        """
        if self._filling:
            return
        self._filling = True
        try:
            keep = self._selected
            self.planned.clear()
            for card in planned:
                item = QListWidgetItem("\n".join(card.lines()))
                item.setData(KEY_ROLE, card.key)
                if card.status:
                    item.setToolTip(card.status)
                self.planned.addItem(item)
                if card.key == keep:
                    item.setSelected(True)
            if self.planned.selectedItems():
                self._selected = keep
            else:
                self._selected = None

            self.committed.clear()
            for card in committed:
                lines = list(card.lines())
                if card.status:
                    lines.append(card.status)
                self.committed.addItem(QListWidgetItem("\n".join(lines)))
            has_committed = bool(committed)
            self.committed_title.setVisible(has_committed)
            self.committed_note.setVisible(has_committed)
            self.committed.setVisible(has_committed)
        finally:
            self._filling = False

    def set_summary(self, text: str) -> None:
        self.summary.setText(text)

    @property
    def selected_key(self):
        return self._selected

    def select(self, key) -> None:
        """Choose a planned card from outside, without re-announcing it.

        The window and the sidebar agree about what is selected; only a person
        choosing something is news.
        """
        self._filling = True
        try:
            for row in range(self.planned.count()):
                item = self.planned.item(row)
                if item.data(KEY_ROLE) == key:
                    item.setSelected(True)
                    self._selected = key
                    return
            self.planned.clearSelection()
            self._selected = None
        finally:
            self._filling = False

    # -- what it hands back ---------------------------------------------------

    def _on_chosen(self) -> None:
        if self._filling:
            return
        items = self.planned.selectedItems()
        if not items:
            return
        key = items[0].data(KEY_ROLE)
        if key is None or key == self._selected:
            return
        self._selected = key
        self.chosen.emit(key)
