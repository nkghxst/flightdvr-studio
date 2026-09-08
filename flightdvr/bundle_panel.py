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

"""The confirmation that shows a delivery bundle before any of it is queued.

The point of the dialog is the middle column: the exact filename each preset
would write, its estimated size and the runtime of the finished file. A
checklist that only named presets would leave the user to discover at the queue
that two of them write the same file, or that the vertical one was never
possible on this source.

A preset the app already knows would fail is shown greyed out with the reason
next to it rather than left out. "Why can I not have a vertical of this" is a
question that should be answered where it is asked.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QScrollArea, QVBoxLayout, QWidget,
)

from .bundle import Member, collisions
from .format import human_duration, human_size
from .widgets import dim

# Enough names to see what is happening without a dialog as tall as the card.
# The rest are in the tooltip, which is where somebody checking all of them
# will look anyway.
NAMES_SHOWN = 4


def _describe(member: Member) -> tuple[str, str]:
    """The lines under one member's tickbox, and the tooltip behind them."""
    if member.problem:
        return member.problem, member.problem

    names = [job.target.name for job in member.jobs]
    shown = names[:NAMES_SHOWN]
    if len(names) > NAMES_SHOWN:
        shown.append(f"and {len(names) - NAMES_SHOWN} more")
    total = (f"{len(names)} files  ·  " if len(names) > 1 else "")
    total += f"{human_size(member.size)}  ·  {human_duration(member.runtime)}"
    return "\n".join(shown + [total]), "\n".join(names)


class BundleDialog(QDialog):
    """Choose which presets to deliver, and see what each one would write."""

    def __init__(self, members: list[Member], already: set[str],
                 chosen: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add a delivery bundle")
        self._members = members
        self._already = already
        self._boxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Each preset uses the settings it is showing now, and is queued as "
            "its own job. Nothing is queued until you press Add."))

        # Scrolled, because seven presets over several ranges is taller than a
        # laptop screen, and a dialog that cannot be resized down cannot be
        # dismissed on one either.
        body = QWidget()
        rows = QVBoxLayout(body)
        rows.setContentsMargins(0, 0, 0, 0)
        for member in members:
            rows.addWidget(self._row(member, chosen or []))
        rows.addStretch(1)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(body)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setMinimumHeight(240)
        layout.addWidget(area, 1)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel)
        self.add_button = self.buttons.addButton(
            "Add to queue", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._refresh()

    def _row(self, member: Member, chosen: list[str]) -> QWidget:
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)

        box = QCheckBox(member.label)
        box.setEnabled(member.usable)
        # Only a member that can actually be produced starts ticked, so
        # restoring a remembered selection onto material that cannot take it
        # offers what is possible instead of an unqueueable dialog.
        box.setChecked(member.usable and member.key in chosen)
        box.toggled.connect(lambda *_: self._refresh())
        box.setMinimumWidth(120)
        self._boxes[member.key] = box
        line.addWidget(box, 0, Qt.AlignmentFlag.AlignTop)

        text, tip = _describe(member)
        detail = dim(QLabel(text))
        detail.setWordWrap(True)
        detail.setToolTip(tip)
        # A long template on a long recording produces names wider than any
        # sensible dialog. Wrapping keeps the whole name readable rather than
        # widening the window until it leaves the screen.
        detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        line.addWidget(detail, 1)
        return row

    # -- what has been chosen -------------------------------------------------

    def chosen(self) -> list[str]:
        """The ticked presets, in the order the panel offers them."""
        return [m.key for m in self._members
                if m.usable and self._boxes[m.key].isChecked()]

    def selected_members(self) -> list[Member]:
        picked = set(self.chosen())
        return [m for m in self._members if m.key in picked]

    def _refresh(self) -> None:
        """Say what the current ticks add up to, and whether they can be added.

        The collision check runs on every tick rather than only on Add: a
        template with no `{preset}` field makes two ordinary choices clash, and
        finding that out only after pressing the button teaches nothing about
        which pair caused it.
        """
        members = self.selected_members()
        clashes = collisions(members, self._already)

        if not members:
            self.summary.setText("Nothing chosen yet.")
        elif clashes:
            self.summary.setText(
                "Nothing can be added yet:\n• " + "\n• ".join(clashes)
                + "\nAdd {preset} to the name template, or put each preset in "
                  "its own subfolder.")
        else:
            jobs = sum(len(m.jobs) for m in members)
            size = sum(m.size for m in members)
            runtime = sum(m.runtime for m in members)
            self.summary.setText(
                f"{jobs} job{'' if jobs == 1 else 's'}  ·  about "
                f"{human_size(size)}  ·  {human_duration(runtime)} of finished "
                f"video")

        self.add_button.setEnabled(bool(members) and not clashes)
