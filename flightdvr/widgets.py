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

from PySide6.QtCore import QEvent, Qt, Signal
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
        # An optional ceiling, for when the clip list is worth more than the
        # last of the picture. None is the shipped behaviour and costs nothing.
        self._height_cap: int | None = None
        # Room kept under the picture for the clip list that shares its column
        # in Classic. A frame that holds only the picture has no list to keep
        # room for, and reserving it there left a blank band under the picture.
        self._list_room = MIN_LIST_HEIGHT
        # Where the controls column sits: beside the picture (Classic, and
        # every Flow page but one), or below it, for a column too narrow to
        # hold both side by side.
        self._controls_below = False
        self._sizing = False
        # What the group box's title and frame cost, measured while nothing is
        # capping the height. Remembered rather than re-measured, because once
        # a cap is on, the picture has shrunk and measuring off it would give a
        # different floor every time — which made the height settle at two
        # values depending on which mode it had been in before.
        self._chrome: int | None = None

    def useful_height(self, width: int) -> int:
        """How tall this is worth being at a given width.

        Measured off the picture where possible rather than derived from the
        margins: a group box's title and frame cost more height than
        contentsMargins reports, and deriving it left the picture twenty
        pixels short of filling the width.
        """
        if self.view is None or self.sidebar is None:
            return self.minimumHeight()
        if self._controls_below:
            return self._stacked_height(width, picture_floor=False)
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

    def content_floor(self) -> int:
        """The shortest this can be without clipping one of its own controls.

        `useful_height` already refuses to go below the sidebar, because
        clipping the buttons off the bottom is a worse trade than a black bar.
        This is that same floor, named, so a ceiling cannot be set through it.
        """
        if self.sidebar is None:
            return self.minimumHeight()
        if self._controls_below:
            return self._stacked_height(self.width(), picture_floor=True)
        margins = self.contentsMargins()
        chrome = self._chrome
        if chrome is None:
            chrome = margins.top() + margins.bottom()
        # `sizeHint` alone under-reports: the sidebar's last line wraps, and a
        # wrapped label's height depends on the width it is given. Capping to
        # the hint cut "then Space plays" off the bottom, which is the clipping
        # this floor exists to prevent.
        needed = self.sidebar.sizeHint().height()
        needed = max(needed, self.sidebar.minimumSizeHint().height())
        if self.sidebar.width() > 0:
            layout = self.sidebar.layout()
            if layout is not None and layout.hasHeightForWidth():
                needed = max(needed,
                             layout.heightForWidth(self.sidebar.width()))
        return needed + chrome

    def set_controls_below(self, below: bool) -> None:
        """Say the controls sit under the picture rather than beside it.

        The layout itself is the owner's to rearrange; this is the height
        arithmetic that goes with it. Beside, the taller of picture and
        controls sets the height. Below, they add up.
        """
        below = bool(below)
        if below == self._controls_below:
            return
        self._controls_below = below
        self._apply_height()

    def _stacked_height(self, width: int, picture_floor: bool) -> int:
        """Picture, gap, controls and the box's own chrome, one above another.

        With `picture_floor`, the least the picture may be (the floor a cap
        cannot go through); otherwise what the width earns at its aspect.
        """
        margins = self.contentsMargins()
        spacing = self.layout().spacing() if self.layout() else 0
        # Across, the picture spans the box less its insets, measured off the
        # picture once there is one, as `useful_height` does.
        inset = (self.width() - self.view.width()
                 if self.view.width() > 0 and self.width() > 0
                 else margins.left() + margins.right())
        inner = max(1, width - inset)
        chrome = self._chrome
        if chrome is None:
            chrome = margins.top() + margins.bottom()
        if picture_floor:
            picture = self.view.minimumHeight()
        else:
            picture = max(round(inner / self.view.aspect),
                          self.view.minimumHeight())
        controls = self.sidebar.sizeHint().height()
        layout = self.sidebar.layout()
        if layout is not None and layout.hasHeightForWidth():
            controls = max(controls, layout.heightForWidth(inner))
        return picture + spacing + controls + chrome

    def set_list_room(self, pixels: int) -> None:
        """How much of the parent's height to leave for a list under this.

        Classic's column holds the list, and keeps `MIN_LIST_HEIGHT` for it.
        """
        pixels = max(0, int(pixels))
        if pixels == self._list_room:
            return
        self._list_room = pixels
        self._apply_height()

    def set_height_cap(self, cap: int | None) -> None:
        """Cap the height, or pass None to go back to what the width earns.

        Applied here rather than waiting for a resize: the left column stops
        changing width once the splitter is at its minimum, so a cap that only
        took effect on the next resize would never take effect at all.
        """
        cap = None if cap is None else max(1, int(cap))
        if cap == self._height_cap:
            return
        self._height_cap = cap
        self._apply_height()

    def _wanted_height(self) -> int:
        wanted = self.useful_height(self.width())
        # Never at the cost of the clip list disappearing entirely.
        parent = self.parentWidget()
        if parent is not None:
            wanted = min(wanted, max(1, parent.height() - self._list_room))
        if self._height_cap is not None:
            # A ceiling, but never through the floor: the controls stay usable
            # and the picture keeps its aspect by letterboxing, which is the
            # trade this class already makes at small sizes.
            wanted = min(wanted, self._height_cap)
        # The floor is not a rule about caps, it is a rule about this panel:
        # below it the sidebar's own buttons go under the bottom edge. The
        # parent clamp above could already push through it on a short window,
        # and the browser's extra rows made that reachable — In / Out / Reset
        # were cut in half at 1402x790. Clipping the controls is the worse
        # trade, and this class already says so about the black bar.
        return max(wanted, self.content_floor())

    def _apply_height(self) -> None:
        """One owner for the height, and one place that can change it.

        Guarded because `setFixedHeight` delivers a resize, which arrives back
        here: without this the two would take turns for as long as the answer
        kept moving.
        """
        if self._sizing:
            return
        self._sizing = True
        try:
            # Measured only with the controls beside the picture: below it, the
            # box's height less the picture's is the controls as well, and
            # counting them as chrome inflated every floor that followed —
            # measured natively, 320px where the controls needed 206.
            if (self._height_cap is None and not self._controls_below
                    and self.view is not None and self.view.height() > 0):
                self._chrome = self.height() - self.view.height()
            wanted = self._wanted_height()
            if self.height() != wanted:
                self.setFixedHeight(wanted)
        finally:
            self._sizing = False

    def event(self, event) -> bool:
        """Re-apply the height when the sidebar's own size hint changes.

        A ceiling set while the sidebar wanted 210 px would clip it once the
        sidebar wanted 230 — the range name field appears, and the buttons go
        under the bottom edge. A resize is not delivered for that, because the
        panel's width has not changed; a layout request is.
        """
        handled = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest:
            self._apply_height()
        return handled

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Take the height its width has earned, and no more.

        Driven by this widget's own width because that is the only input:
        setting the height cannot change it, so this settles in one pass. Doing
        it from the window's resize handler instead made the answer depend on
        which resize Qt happened to deliver first, and the picture came out
        two thirds of the width it could have had.
        """
        super().resizeEvent(event)
        self._apply_height()
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
