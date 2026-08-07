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

"""Preview, transport and filmstrip widgets as one composed view."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    QWidget,
)

from .player import FrameView
from .trim import TrimBar
from .widgets import INNER, TIGHT, PreviewPanel as AspectPreviewBox, dim


class PreviewView(QObject):
    """Build the two preview boxes and expose only user-action signals.

    The picture and filmstrip live in different parent layouts because the
    filmstrip needs the full window width. A QObject composes both without
    inventing a hidden container that would break that geometry.
    """

    frame_clicked = Signal()
    play_requested = Signal()
    set_in_requested = Signal()
    set_out_requested = Signal()
    reset_requested = Signal()
    playhead_moved = Signal(float)
    trim_changed = Signal(float, float)
    select_picked = Signal(int)
    select_added = Signal()
    select_removed = Signal()
    select_renamed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.preview_box = self._build_preview_box()
        self.trim_band = self._build_trim_band()

    def _build_preview_box(self) -> QWidget:
        """The video and its transport, permanently visible."""
        box = AspectPreviewBox("Preview and trim")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(INNER, TIGHT, INNER, INNER)
        layout.setSpacing(INNER)

        self.frame_view = FrameView()
        self.frame_view.clicked.connect(lambda: self.frame_clicked.emit())
        layout.addWidget(self.frame_view, 1)

        # Beside the picture rather than under it. A 16:9 frame in a wide,
        # short box leaves this space without taking height from the clip list.
        layout.addWidget(self._build_sidebar())
        box.view = self.frame_view
        box.sidebar = self.sidebar
        return box

    def _build_sidebar(self) -> QWidget:
        side = self.sidebar = QWidget()
        side.setFixedWidth(190)
        column = QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(TIGHT)
        column.addStretch(1)

        # The position relays thirty times a second; the title changes once per
        # clip. Keeping them separate avoids relaying a long label every frame.
        self.trim_title = QLabel("Select a clip")
        self.trim_title.setWordWrap(True)
        column.addWidget(self.trim_title)

        self.trim_position = dim(QLabel(""))
        column.addWidget(self.trim_position)
        self.trim_summary = dim(QLabel(""))
        column.addWidget(self.trim_summary)
        column.addSpacing(TIGHT)

        # These change once per clip and deliberately stay out of the 30 Hz
        # playhead label update.
        self.clip_format = dim(QLabel(""))
        self.clip_format.setToolTip("Resolution, frame rate, codec and size")
        column.addWidget(self.clip_format)

        self.clip_date = dim(QLabel(""))
        self.clip_date.setToolTip(
            "The timestamp on the card, not when you flew. The Box Pro has no "
            "clock battery, so these are unreliable."
        )
        column.addWidget(self.clip_date)
        column.addSpacing(INNER)

        self.play_button = QPushButton("Play")
        self.play_button.setToolTip(
            "Play the highlighted clip here in the window.\n"
            "Space does the same once the picture has focus."
        )
        self.play_button.clicked.connect(lambda *_: self.play_requested.emit())
        column.addWidget(self.play_button)

        trim_row = QHBoxLayout()
        trim_row.setContentsMargins(0, 0, 0, 0)
        trim_row.setSpacing(TIGHT)
        for text, requested, tip in (
            ("In", self.set_in_requested,
             "Start the export at the playhead  (I)"),
            ("Out", self.set_out_requested,
             "End the export at the playhead  (O)"),
            ("Reset", self.reset_requested, "Use the whole clip again"),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda *_, signal=requested: signal.emit())
            trim_row.addWidget(button)
        column.addLayout(trim_row)
        column.addStretch(1)

        keys = dim(QLabel("Silent · click the picture, then Space plays"))
        keys.setToolTip(
            "With the picture focused:\n"
            "Space or K — play or pause\n"
            "I / O — set the in / out point at the playhead\n"
            "Left / Right — move a second, with Shift five\n"
            "Home / End — jump to the in / out point\n"
            "Esc — stop"
        )
        column.addWidget(keys)

        self.trim_note = dim(QLabel(
            "Remux cuts at keyframes, so a trimmed rewrap can be a second out. "
            "The re-encoding presets are exact."
        ))
        self.trim_note.hide()
        column.addWidget(self.trim_note)
        return side

    def _build_selects_row(self) -> QHBoxLayout:
        """Which range is being edited, and how to add or drop one.

        Here rather than in the sidebar because it is about the ranges drawn
        directly below it, and because the sidebar is 190px and already full.
        Hidden entirely while a clip has one range or none: a card reviewed the
        way every version until now reviewed it should not grow a control it
        has no use for.
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(TIGHT)

        self.select_label = dim(QLabel(""))
        self.select_label.setMinimumWidth(96)
        row.addWidget(self.select_label)

        self.select_name = QLineEdit()
        self.select_name.setPlaceholderText("Name this one — launch, tree dive…")
        self.select_name.setMaximumWidth(280)
        self.select_name.setToolTip(
            "Used in the filename when a clip has more than one select")
        self.select_name.textEdited.connect(
            lambda text: self.select_renamed.emit(text))
        row.addWidget(self.select_name)
        row.addStretch(1)

        add = QPushButton("Add select")
        add.setToolTip("Keep another range out of this clip  (N)")
        add.clicked.connect(lambda *_: self.select_added.emit())
        row.addWidget(add)

        self.select_remove = QPushButton("Remove")
        self.select_remove.setToolTip("Drop the range being edited")
        self.select_remove.clicked.connect(
            lambda *_: self.select_removed.emit())
        row.addWidget(self.select_remove)

        self.select_add = add
        # Everything except Add is about *which* of several ranges you are
        # editing, so none of it means anything until there are several.
        self._only_when_several = [self.select_label, self.select_name,
                                   self.select_remove]
        return row

    def show_selects(self, count: int, index: int, name: str) -> None:
        """Say which range is being edited, and hide what does not apply yet.

        Add stays whatever happens: it is how a second range comes to exist,
        and hiding it would leave the N key as the only way to reach a feature
        nobody would know was there. The rest — which one of several, its name,
        and dropping it — appears once there is more than one.
        """
        several = count > 1
        for widget in self._only_when_several:
            widget.setVisible(several)
        self.select_label.setText(
            f"Select {index + 1} of {count}" if several else "")
        if self.select_name.text() != name:
            self.select_name.setText(name)

    def _build_trim_band(self) -> QWidget:
        """The full-width filmstrip directly under the preview it scrubs."""
        band = QGroupBox("Filmstrip")
        layout = QVBoxLayout(band)
        layout.setContentsMargins(INNER, TIGHT, INNER, INNER)
        layout.addLayout(self._build_selects_row())

        self.trim_bar = TrimBar()
        self.trim_bar.setToolTip(
            "Every keyframe in the clip, a second apart. Click to move the "
            "playhead, drag either end to set where the export starts and ends."
        )
        self.trim_bar.playhead_moved.connect(
            lambda seconds: self.playhead_moved.emit(seconds)
        )
        self.trim_bar.trim_changed.connect(
            lambda in_point, out_point: self.trim_changed.emit(
                in_point, out_point
            )
        )
        self.trim_bar.select_picked.connect(
            lambda index: self.select_picked.emit(index)
        )
        layout.addWidget(self.trim_bar)
        return band
