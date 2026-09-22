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

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .flow_layout import Region, Stage, regions_for, title
from .widgets import INNER, TIGHT

# The sidebar is a working list, not a strip of decoration: below this it stops
# being readable and the cards start wrapping into nonsense.
SIDEBAR_MINIMUM = 280

# Browse's list is the page's subject. It gets the space; the picture column
# beside it stays narrow enough that the list is still the thing you read.
LIST_STRETCH = 3
VIEWPORT_STRETCH = 2


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

        actions = QHBoxLayout()
        actions.setSpacing(TIGHT)
        self.primary_button = QPushButton("Commit to render")
        self.primary_button.clicked.connect(self.primary_activated.emit)
        self.secondary_button = QPushButton("Cancel")
        self.secondary_button.clicked.connect(self.secondary_activated.emit)
        actions.addWidget(self.primary_button, 1)
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
        self.pages.setCurrentWidget(self._pages[chosen])
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
