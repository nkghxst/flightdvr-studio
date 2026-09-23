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

"""The shell's arrangement, without a window around it.

No `MainWindow` here on purpose: the shell owns arrangement and nothing else,
so if these needed a session, a card and a player to answer "is the list beside
the picture", the separation would already have failed.

These settle geometry and structure. They settle nothing about whether the
result is legible at a real size on a real display — that is the native pass,
and it is not this file.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication, QLabel

from flightdvr.flow_layout import STAGE_ORDER, Region, Stage
from flightdvr.flow_shell import FlowShell


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def shell(app):
    made = FlowShell(STAGE_ORDER)
    yield made
    made.deleteLater()


def test_queue_has_no_viewport_to_hide(shell):
    """Settled, not pending. A page that owned a hidden viewport would keep a
    decoder alive for a page that shows nothing — Queue's space has to be
    earned by not having one, not by hiding it."""
    assert shell.host(Stage.QUEUE, Region.VIEWPORT) is None
    assert shell.host(Stage.QUEUE, Region.LIST) is None
    assert shell.host(Stage.QUEUE, Region.PANEL) is not None


def test_every_other_page_has_the_picture(shell):
    for stage in (Stage.BROWSE, Stage.TRIM, Stage.ASSEMBLE, Stage.MUSIC,
                  Stage.OUTPUT):
        assert shell.host(stage, Region.VIEWPORT) is not None, stage


def test_browse_puts_the_list_beside_the_picture_and_gives_it_the_room(shell):
    """The approved Browse is a tall list you can read *beside* a narrower
    picture column. Stacked, it is the app we already have."""
    shell.set_stage(Stage.BROWSE)
    shell.resize(1440, 940)
    shell.show()
    app = QApplication.instance()
    app.processEvents()

    listing = shell.host(Stage.BROWSE, Region.LIST)
    picture = shell.host(Stage.BROWSE, Region.VIEWPORT)
    left = listing.mapTo(shell, listing.rect().topLeft())
    right = picture.mapTo(shell, picture.rect().topLeft())

    assert left.x() < right.x(), "the picture is not beside the list"
    assert abs(left.y() - right.y()) < 40, "the picture is stacked under it"
    assert listing.width() > picture.width(), (
        f"the list is not the wider column: {listing.width()} vs "
        f"{picture.width()}")
    shell.hide()


def test_the_actions_are_pinned_outside_the_scrolling_sidebar(shell):
    """A Commit button that has scrolled out of the window is the same as no
    Commit button, which is the whole reason the actions are fixed."""
    def inside_scroll(widget) -> bool:
        parent = widget.parentWidget()
        while parent is not None:
            if parent is shell.sidebar_scroll:
                return True
            parent = parent.parentWidget()
        return False

    assert not inside_scroll(shell.primary_button)
    assert not inside_scroll(shell.secondary_button)
    assert inside_scroll(shell.sidebar_body)


def test_a_sidebar_taller_than_the_column_cannot_push_the_actions_out(shell):
    """The structural half of the compact promise, and only that half.

    I first wrote this as "the actions stay visible when the sidebar
    overflows", asserted with `isVisible()` and a mapped corner, then with
    `visibleRegion()`. **Both passed with the actions moved inside the
    scrolling area** — offscreen, a clipped widget still reports itself
    visible, still maps inside the window, and still returns a non-empty
    region. The assertion resembled the guard without being able to reach it,
    so it is not kept in that form.

    What can honestly be checked here is the arrangement: the actions are a
    sibling of the scroll area in the column, so overflow is absorbed by the
    scroll area and not by pushing them down. **Whether they are genuinely
    reachable at a compact size on a real display is a native check**, listed
    as such rather than claimed from this file.
    """
    shell.resize(1060, 700)
    for index in range(40):
        shell.adopt_sidebar(QLabel(f"planned output {index}"))
    shell.show()
    QApplication.instance().processEvents()

    column = shell.primary_button.parentWidget()
    assert shell.sidebar_scroll.parentWidget() is column, (
        "the actions and the scrolling cards are not siblings in one column")
    assert shell.sidebar_body.height() > shell.sidebar_scroll.height(), (
        "the fixture never overflowed, so nothing was under pressure")
    assert shell.primary_button.y() > shell.sidebar_scroll.y(), (
        "the actions are not below the scrolling region")
    shell.hide()


def test_choosing_a_stage_moves_the_page_and_marks_the_button(shell):
    heard = []
    shell.stage_chosen.connect(heard.append)

    shell.set_stage(Stage.OUTPUT)

    assert shell.stage is Stage.OUTPUT
    assert shell.stage_buttons[Stage.OUTPUT].isChecked()
    assert not shell.stage_buttons[Stage.BROWSE].isChecked()
    assert heard == [], "setting the stage re-emitted the choice"


def test_pressing_a_stage_button_asks_rather_than_moves(shell):
    """The shell does not decide navigation. The window does, because only the
    window knows whether moving is allowed."""
    heard = []
    shell.stage_chosen.connect(heard.append)
    shell.set_stage(Stage.BROWSE)

    shell.stage_buttons[Stage.QUEUE].click()

    assert heard == [Stage.QUEUE.value]
    assert shell.stage is Stage.BROWSE, "the shell moved itself"


def test_the_page_actions_are_named_and_disabled_per_page(shell):
    shell.set_actions(primary="Commit to render", primary_enabled=True,
                      secondary="Cancel")
    assert shell.primary_button.isEnabled()

    shell.set_actions(primary="Commit to render", primary_enabled=False,
                      secondary="Cancel this render")

    assert not shell.primary_button.isEnabled()
    assert shell.secondary_button.text() == "Cancel this render"


def test_a_lent_widget_sits_in_its_region_and_can_be_taken_back(shell):
    """Lent, never owned: the window keeps exactly one of each widget."""
    borrowed = QLabel("the one picture")
    host = shell.host(Stage.ASSEMBLE, Region.VIEWPORT)

    host.layout().addWidget(borrowed)
    assert borrowed.parentWidget() is host

    host.layout().removeWidget(borrowed)
    borrowed.setParent(None)
    assert borrowed.parentWidget() is None


def test_steps_disable_at_the_ends(shell):
    shell.set_steps(False, True)
    assert not shell.back_button.isEnabled()
    assert shell.next_button.isEnabled()


def test_a_page_left_behind_does_not_size_the_one_being_shown(shell):
    """Measured natively: once Assemble had been visited, every page kept its
    780px minimum, and Flow could not go below 1088px wide because the stack
    took the widest page nobody was looking at. At the compact size that
    pushed the window past the screen."""
    from PySide6.QtWidgets import QWidget

    def settled():
        QApplication.processEvents()
        return shell.pages.minimumSizeHint()

    shell.show()                      # hints are not kept for an unshown shell
    shell.set_stage(Stage.BROWSE)
    small = settled()
    tall = QWidget()
    tall.setMinimumSize(900, 800)
    shell.host(Stage.ASSEMBLE, Region.PANEL).layout().addWidget(tall)

    shell.set_stage(Stage.ASSEMBLE)
    assert settled().height() >= 800, (
        "the fixture never made Assemble tall")
    shell.set_stage(Stage.BROWSE)

    assert settled() == small, (
        "the page left behind still sets the size of the one on show")


def test_a_page_never_shown_does_not_size_the_shell(shell):
    """Found natively: opening Flow grew a 1440x913 window to 1194 tall.
    Panels are lent into every page before the first page is chosen, and
    until then each page still counted — so the window grew to the tallest
    one, and a window that has grown does not shrink back by itself."""
    from PySide6.QtWidgets import QWidget

    shell.show()
    QApplication.processEvents()
    before = shell.pages.minimumSizeHint()

    tall = QWidget()
    tall.setMinimumSize(900, 800)
    shell.host(Stage.MUSIC, Region.PANEL).layout().addWidget(tall)
    tall.show()                       # as the window does with every panel
    QApplication.processEvents()

    assert shell.pages.minimumSizeHint() == before, (
        "a page nobody has opened is sizing the shell")
