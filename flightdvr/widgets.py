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

"""Small widgets and the measurements that shape the window.

Reusable pieces with no knowledge of the main window: it imports them,
never the other way round.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication, QComboBox, QGroupBox, QLabel, QSizePolicy, QTableWidgetItem,
    QWidget,
)

from pathlib import Path

from .player import FrameView

def resource(name: str) -> Path:
    return Path(__file__).resolve().parent / "resources" / name


def app_icon() -> QIcon:
    path = resource("icon.ico")
    return QIcon(str(path)) if path.exists() else QIcon()


def key_fill(widget, strength: float = 0.14) -> str:
    """A background for a keycap that is visible in either theme.

    Same problem and same answer as `dim` below, from the other direction. A
    grey picked to look right on a light window is a near-white block on a dark
    one: measured, the dark theme's own text on a fixed #d0d0d0 chip comes out
    at 1.54:1. Blending the real window colour toward the real text colour
    gives #3e3e3e there and 10.7:1, and #d1d1d1 with 7.1:1 in the light theme.

    Returned as a hex string because the only caller writes it into rich text,
    where a QColor cannot go.
    """
    palette = widget.palette()
    back = palette.color(QPalette.ColorRole.Window)
    text = palette.color(QPalette.ColorRole.WindowText)
    return QColor(
        round(back.red() * (1 - strength) + text.red() * strength),
        round(back.green() * (1 - strength) + text.green() * strength),
        round(back.blue() * (1 - strength) + text.blue() * strength),
    ).name()


def dim(label: QLabel, strength: float = 0.34) -> QLabel:
    """Mute a label so it reads as secondary text without becoming unreadable.

    The obvious approach, `color: palette(mid)`, is close to invisible against a
    dark theme: on the Windows 11 dark style it resolves to #282828 on a #1e1e1e
    window, a contrast ratio of 1.13:1. Blending the real text colour toward the
    real background colour gives about 7.9:1 and works in either theme.
    """
    palette = label.palette()
    text = palette.color(QPalette.ColorRole.WindowText)
    back = palette.color(QPalette.ColorRole.Window)
    blended = QColor(
        round(text.red() * (1 - strength) + back.red() * strength),
        round(text.green() * (1 - strength) + back.green() * strength),
        round(text.blue() * (1 - strength) + back.blue() * strength),
    )
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                  QPalette.ColorGroup.Disabled):
        palette.setColor(group, QPalette.ColorRole.WindowText, blended)
    label.setPalette(palette)
    label.setWordWrap(True)
    # A wrapped QLabel reports the height it needs for its current width, but a
    # vertical layout will happily give it less and clip the last lines when the
    # panel narrows. MinimumExpanding makes the layout grow the label instead.
    label.setSizePolicy(QSizePolicy.Policy.Preferred,
                        QSizePolicy.Policy.MinimumExpanding)
    return label
# Every gap in the window is one of these four, and which one says how related
# the two things either side of it are. Before this they were a dozen different
# literals chosen a panel at a time, so the spacing varied in ways that meant
# nothing and grouped nothing.
EDGE = 10       # the window's own margin
GAP = 12        # between regions that have nothing to do with each other
INNER = 6       # between parts of one region
TIGHT = 4       # between controls that belong together

# How many clips the list should manage to show before thumbnails start giving
# up size for it. Sized on width alone the rows came out 141 px tall, which is
# two clips on a normal window whatever height the list was given.
MIN_VISIBLE_CLIPS = 4

# Small enough that a narrow window shrinks the picture rather than
# eliding the clip's name, which is the one thing in the row you need.
MIN_THUMB_WIDTH = 72

# The clip list never gives up more than this to the picture, however wide the
# left column is dragged.
MIN_LIST_HEIGHT = 150


def _default_window_size() -> tuple[int, int]:
    """How big to open when there is no remembered size.

    Tall and fairly narrow: the clip list, the picture and the filmstrip are
    stacked, so height is what the window wants and width past the point the
    picture fills is spent on the export column. Clamped to the screen,
    because a default taller than the desktop opens with its bottom edge and
    the Add to queue button off the end of it.
    """
    wanted = (1060, 1300)
    screen = QApplication.primaryScreen()
    if screen is None:
        return wanted
    available = screen.availableGeometry()
    return (min(wanted[0], available.width() - 40),
            min(wanted[1], available.height() - 60))
class PreviewPanel(QGroupBox):
    """The preview, as tall as its picture can fill and no taller.

    A titled group box, and so is the filmstrip beneath it. One frame around
    both was tried twice — as a child widget outside the layout, and as
    something the window's body painted — because the two really are one thing
    and a frame around each says they are two. Neither read as well as the
    plain boxes, and the first one drew in the wrong place. Two frames it is.

    A 16:9 frame in a box of any other shape letterboxes: past `width / aspect`
    every extra pixel of height is a black bar, and short of it every missing
    pixel is black down the sides. There is exactly one right height and it
    follows from the width, so this is `heightForWidth` rather than something
    recomputed in a resize handler — Qt solves it during layout, and the answer
    stops depending on which resize happened to fire first.

    Everything the picture cannot use goes to the clip list above it, which is
    why there is no splitter here: a handle could only choose how much black to
    look at. Widening the left column is what makes the picture bigger.
    """

    def __init__(self, title: str):
        super().__init__(title)
        self.view: FrameView | None = None
        self.sidebar: QWidget | None = None

    def useful_height(self, width: int) -> int:
        """How tall this is worth being at a given width.

        Measured off the picture where possible rather than derived from the
        margins: a group box's title and frame cost more height than
        contentsMargins reports, and deriving it left the picture twenty
        pixels short of filling the width.
        """
        if self.view is None or self.sidebar is None:
            return self.minimumHeight()
        margins = self.contentsMargins()
        spacing = self.layout().spacing() if self.layout() else 0
        # Both measured off the picture once there is one, so the answer holds
        # whatever the style charges for a group box's title and frame.
        inset = (self.width() - self.view.width() if self.view.width() > 0
                 else margins.left() + margins.right()
                 + self.sidebar.sizeHint().width() + spacing)
        chrome = (self.height() - self.view.height() if self.view.height() > 0
                  else margins.top() + margins.bottom())
        picture = round(max(1, width - inset) / self.view.aspect)
        # The sidebar is taller than the picture at small sizes, and clipping
        # the buttons off the bottom is a worse trade than a black bar.
        floor = self.sidebar.sizeHint().height()
        return max(picture, floor) + chrome

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Take the height its width has earned, and no more.

        Driven by this widget's own width because that is the only input:
        setting the height cannot change it, so this settles in one pass. Doing
        it from the window's resize handler instead made the answer depend on
        which resize Qt happened to deliver first, and the picture came out
        two thirds of the width it could have had.
        """
        super().resizeEvent(event)
        wanted = self.useful_height(self.width())
        # Never at the cost of the clip list disappearing entirely.
        parent = self.parentWidget()
        if parent is not None:
            wanted = min(wanted, max(1, parent.height() - MIN_LIST_HEIGHT))
        if self.height() != wanted:
            self.setFixedHeight(wanted)
class SortItem(QTableWidgetItem):
    """Table cell that sorts on a supplied key rather than on its text."""

    SORT_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, text: str, key):
        super().__init__(text)
        self.setData(self.SORT_ROLE, key)

    def __lt__(self, other):
        mine = self.data(self.SORT_ROLE)
        theirs = other.data(self.SORT_ROLE) if isinstance(other, QTableWidgetItem) else None
        if mine is None or theirs is None:
            return super().__lt__(other)
        try:
            return mine < theirs
        except TypeError:
            return str(mine) < str(theirs)


class DriveCombo(QComboBox):
    """Source picker that re-reads the drive list every time it is opened.

    Enumerating once at startup meant a card inserted afterwards never
    appeared, which is exactly when you want to see it.
    """

    about_to_show = Signal()

    def showPopup(self) -> None:  # noqa: N802 (Qt naming)
        self.about_to_show.emit()
        super().showPopup()
