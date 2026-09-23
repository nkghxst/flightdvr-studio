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

"""The page shell: navigation at the top, regions in the body, actions pinned.

It owns arrangement and nothing else. Every widget it shows belongs to the
window and is *lent* into a region, which is what keeps there being exactly one
picture, one transport and one of each panel however many pages borrow them.

Two things here are load-bearing rather than cosmetic.

**The stage bar is above the body**, so the picture stops being a full-width
band the whole app sits under. That single move is what lets Browse put a tall
recordings list *beside* a narrower picture column instead of beneath one.

**The actions are pinned to the bottom of the sidebar** and do not scroll away.
Somebody working at a compact size still has to be able to reach the thing that
starts the work, and a Commit button that has scrolled out of the window is the
same as no Commit button.

The shell asks `flow_layout` which regions a page has and builds only those, so
"Queue has no picture" is arranged here and *decided* there — and can be checked
without building a window.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .flow_layout import Region, Stage, regions_for, title
from .widgets import INNER, TIGHT

# The sidebar is a working list, not a strip of decoration: below this it stops
# being readable and the cards start wrapping into nonsense. Measured natively,
# 280 held Queue at 1088px wide, past the 1060px compact size; Queue's own
# header needs 782px of its own. With the 3:1 share the sidebar gets about
# 258px at the compact width anyway, so this floor only binds on Queue.
SIDEBAR_MINIMUM = 240

# Browse's list is the page's subject. It gets the space; the picture column
# beside it stays narrow enough that the list is still the thing you read.
LIST_STRETCH = 3
VIEWPORT_STRETCH = 2


class OneLineNote(QLabel):
    """A caption that keeps to one line and says the rest on hover.

    The picture's caption wrapped, and a wrapped label hands its height-for-
    width up the layout: Qt then sized the picture's whole region from it and
    ignored the region's minimum. One line, cut with an ellipsis where it
    must be, with every word still in the tooltip and in `text()`.
    """

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        super().setText(text)
        self.setToolTip(text)
        self.update()

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt naming)
        # One line at any width. A label claims height-for-width for rich
        # text too, and laid out at no width at all that claimed 68px.
        return False

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt naming)
        return -1

    def _line(self) -> int:
        margins = self.contentsMargins()
        return self.fontMetrics().height() + margins.top() + margins.bottom()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(0, self._line())

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(0, self._line())

    def elided(self) -> str:
        return self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, max(0, self.width()))

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        self.style().drawItemText(
            painter, self.contentsRect(),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self.palette(), self.isEnabled(), self.elided(),
            QPalette.ColorRole.WindowText)


class PictureFrame(QWidget):
    """Holds the one picture in a page without letting its shape size the page.

    The picture sets its own height from its width. Put straight into a page,
    that shape travelled up the layout and Qt gave its region less than it
    asked for, so the picture spilled over the caption beneath it — measured
    natively, 347px of picture in a 275px region. This frame answers for it
    instead: it asks the page for the picture's floor and no more, takes the
    page's slack, clips what it holds, and says how tall it came out so the
    picture can be capped to fit.
    """

    resized = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._floor = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Expanding)

    def hold(self, widget: QWidget) -> None:
        layout = self.layout()
        while layout.count():
            layout.takeAt(0)
        layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)

    def set_floor(self, height: int) -> None:
        """The least the picture can be without clipping its own controls."""
        height = max(0, int(height))
        if height != self._floor:
            self._floor = height
            self.updateGeometry()

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt naming)
        # The picture's own height-for-width stops here.
        return False

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(self.layout().minimumSize().width(), self._floor)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(self.layout().sizeHint().width(), self._floor)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self.resized.emit()


class FlowShell(QWidget):
    """Navigation, one page of regions at a time, and the fixed actions."""

    stage_chosen = Signal(str)        # a Stage value
    back_requested = Signal()
    next_requested = Signal()
    primary_activated = Signal()      # Commit to render
    secondary_activated = Signal()    # Cancel / Cancel this render

    def __init__(self, stages, parent=None) -> None:
        super().__init__(parent)
        self._stages: tuple[Stage, ...] = tuple(Stage(one) for one in stages)
        self._hosts: dict[tuple[Stage, Region], QWidget] = {}
        self._pages: dict[Stage, QWidget] = {}
        self._stage: Stage | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(INNER)

        outer.addWidget(self._build_nav())

        body = QHBoxLayout()
        body.setSpacing(INNER)
        self.pages = QStackedWidget()
        for stage in self._stages:
            page = self._build_page(stage)
            self._pages[stage] = page
            self.pages.addWidget(page)
        body.addWidget(self.pages, 3)
        body.addWidget(self._build_sidebar_column(), 1)
        outer.addLayout(body, 1)

    # -- building -------------------------------------------------------------

    def _build_nav(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(TIGHT)
        self.stage_buttons: dict[Stage, QPushButton] = {}
        for stage in self._stages:
            button = QPushButton(title(stage))
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, chosen=stage:
                self.stage_chosen.emit(chosen.value))
            row.addWidget(button)
            self.stage_buttons[stage] = button
        row.addStretch(1)

        self.back_button = QPushButton("‹ Back")
        self.back_button.clicked.connect(self.back_requested.emit)
        self.next_button = QPushButton("Next ›")
        self.next_button.clicked.connect(self.next_requested.emit)
        row.addWidget(self.back_button)
        row.addWidget(self.next_button)
        return bar

    def _build_page(self, stage: Stage) -> QWidget:
        """One page, holding only the regions this stage actually has."""
        page = QWidget()
        regions = regions_for(stage)
        if Region.LIST in regions:
            # Beside, not above: a row, with the list given the weight.
            layout = QHBoxLayout(page)
            beside = QVBoxLayout()
            beside.setContentsMargins(0, 0, 0, 0)
            beside.setSpacing(INNER)
            layout.addWidget(self._host(stage, Region.LIST), LIST_STRETCH)
            for region in (Region.VIEWPORT, Region.PANEL):
                if region in regions:
                    beside.addWidget(self._host(stage, region))
            layout.addLayout(beside, VIEWPORT_STRETCH)
        else:
            layout = QVBoxLayout(page)
            for region in regions:
                host = self._host(stage, region)
                # The picture takes the slack on a page that has one; on Queue
                # the panel does, which is how the jobs get the body.
                stretch = 1 if region is Region.VIEWPORT else 0
                layout.addWidget(host, stretch)
            if Region.VIEWPORT not in regions:
                layout.setStretch(layout.count() - 1, 1)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(INNER)
        return page

    def _host(self, stage: Stage, region: Region) -> QWidget:
        host = QWidget()
        inner = QVBoxLayout(host)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(TIGHT)
        host.setObjectName(f"{stage.value}-{region.value}")
        self._hosts[(stage, region)] = host
        return host

    def _build_sidebar_column(self) -> QWidget:
        column = QWidget()
        column.setMinimumWidth(SIDEBAR_MINIMUM)
        column.setSizePolicy(QSizePolicy.Policy.Preferred,
                             QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(INNER)

        # Scrolls, because at a compact size the cards will not all fit; the
        # actions below deliberately sit outside it.
        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.sidebar_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar_body = QWidget()
        self._sidebar_layout = QVBoxLayout(self.sidebar_body)
        self._sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self._sidebar_layout.setSpacing(INNER)
        self.sidebar_scroll.setWidget(self.sidebar_body)
        layout.addWidget(self.sidebar_scroll, 1)

        # One above the other. Side by side, at the compact width, "Queue all
        # planned (24)" was cut to "Queue all planned (2" — a count that reads
        # as the wrong number is worse than no count.
        actions = QVBoxLayout()
        actions.setSpacing(TIGHT)
        self.primary_button = QPushButton("Commit to render")
        self.primary_button.clicked.connect(self.primary_activated.emit)
        self.secondary_button = QPushButton("Cancel")
        self.secondary_button.clicked.connect(self.secondary_activated.emit)
        actions.addWidget(self.primary_button)
        actions.addWidget(self.secondary_button)
        layout.addLayout(actions)
        return column

    # -- what the window asks for ---------------------------------------------

    def host(self, stage, region) -> QWidget | None:
        """Where a lent widget goes, or None when this page has no such part."""
        return self._hosts.get((Stage(stage), Region(region)))

    def adopt_sidebar(self, widget: QWidget) -> None:
        """Put the window's one sidebar inside the scrolling column."""
        self._sidebar_layout.addWidget(widget, 1)

    def set_stage(self, stage) -> None:
        chosen = Stage(stage)
        if chosen not in self._pages:
            return
        self._stage = chosen
        # A stack is as large as its largest page, shown or not. Measured
        # natively, a visited Assemble held every page at 780px and Output's
        # controls held Flow at 1088px wide, past the compact size. Pages not
        # on show stop counting.
        for stage_key, page in self._pages.items():
            policy = (QSizePolicy.Policy.Preferred if stage_key is chosen
                      else QSizePolicy.Policy.Ignored)
            page.setSizePolicy(policy, policy)
        self.pages.setCurrentWidget(self._pages[chosen])
        self.pages.updateGeometry()
        for stage_key, button in self.stage_buttons.items():
            blocked = button.blockSignals(True)
            button.setChecked(stage_key is chosen)
            button.blockSignals(blocked)

    @property
    def stage(self) -> Stage | None:
        return self._stage

    def set_steps(self, back: bool, forward: bool) -> None:
        self.back_button.setEnabled(bool(back))
        self.next_button.setEnabled(bool(forward))

    def set_actions(self, *, primary: str, primary_enabled: bool,
                    secondary: str, secondary_enabled: bool = True) -> None:
        """Name and enable the two fixed actions for the page showing now.

        Queue turns the primary off and renames the secondary: from there the
        thing you can do is stop the render in front of you, not start another.
        """
        self.primary_button.setText(primary)
        self.primary_button.setEnabled(bool(primary_enabled))
        self.secondary_button.setText(secondary)
        self.secondary_button.setEnabled(bool(secondary_enabled))
