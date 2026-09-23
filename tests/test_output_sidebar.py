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

"""The two halves of the list, and the boundary between them.

No session, card or queue here: the sidebar is given worded rows and hands back
a key, so what it says can be checked without building the thing that knows
what an `OutputTarget` is.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication, QListWidget

from flightdvr.output_sidebar import COMMITTED_NOTE, Card, OutputSidebar


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def sidebar(app):
    made = OutputSidebar()
    yield made
    made.deleteLater()


def planned_card(key="a", title="hdz_047.ts · Launch"):
    return Card(key=key, title=title, detail="Social · 0:12–0:48",
                sound="♪ Nocturne-in-C-sharp.mp3", status="Planned")


def committed_card(key="j1"):
    return Card(key=key, title="hdz_046.ts · Bando pass",
                detail="Social · 0:20–0:50 · committed 16:31",
                sound="♪ Kollektiv-Turmstrasse.mp3", status="Rendering 47%")


def test_the_boundary_is_stated_where_a_person_will_read_it(sidebar):
    """The whole point of the split: editing reaches the ones above and not
    the ones below, said in words rather than discovered by changing a preset
    and watching the old one come out."""
    sidebar.show_cards([planned_card()], [committed_card()])

    assert sidebar.committed_note.text() == COMMITTED_NOTE
    assert not sidebar.committed_note.isHidden()


def test_the_committed_half_is_absent_rather_than_empty(sidebar):
    """An empty heading over an empty list says a thing exists and is broken."""
    sidebar.show_cards([planned_card()], [])

    assert sidebar.committed.isHidden()
    assert sidebar.committed_title.isHidden()
    assert sidebar.committed_note.isHidden()


def test_a_committed_row_cannot_be_selected(sidebar):
    """A row that highlights invites the click that proves it is not editable."""
    sidebar.show_cards([], [committed_card()])

    assert sidebar.committed.selectionMode() is (
        QListWidget.SelectionMode.NoSelection)
    sidebar.committed.item(0).setSelected(True)
    assert sidebar.committed.selectedItems() == []


def test_choosing_a_planned_card_hands_back_its_key_once(sidebar):
    heard = []
    sidebar.chosen.connect(heard.append)
    sidebar.show_cards([planned_card("a"), planned_card("b", "second")], [])

    sidebar.planned.item(1).setSelected(True)

    assert heard == ["b"]
    assert sidebar.selected_key == "b"


def test_selecting_from_outside_is_not_news(sidebar):
    """The window and the sidebar agree about what is selected; only a person
    choosing something is worth announcing."""
    heard = []
    sidebar.chosen.connect(heard.append)
    sidebar.show_cards([planned_card("a"), planned_card("b", "second")], [])

    sidebar.select("b")

    assert sidebar.selected_key == "b"
    assert heard == [], "a programmatic selection was announced as a choice"


def test_a_refresh_keeps_a_surviving_selection_without_announcing_it(sidebar):
    """Writing a list emits selection changes, and a surviving choice must be
    restored without being re-announced as if somebody had just made it.

    Named for what it actually establishes. Called "one refresh is one
    refresh" it overclaimed: removing the `_filling` fence from the choice
    handler leaves this green, because the "same key as already selected"
    guard suppresses the emit on its own. The fence earns its place on the
    `select()` path instead, where the key genuinely differs — which is what
    `test_selecting_from_outside_is_not_news` pins.
    """
    heard = []
    sidebar.chosen.connect(heard.append)
    sidebar.show_cards([planned_card("a")], [])
    sidebar.select("a")
    heard.clear()

    sidebar.show_cards([planned_card("a"), planned_card("b", "second")], [])

    assert heard == [], f"refreshing announced a choice: {heard}"
    assert sidebar.selected_key == "a", "the surviving selection was dropped"


def test_a_selection_that_no_longer_exists_is_dropped_not_moved(sidebar):
    """The dangerous version of keeping a selection: the row index survives
    while the output under it is a different one, so the next edit lands
    somewhere nobody chose."""
    sidebar.show_cards([planned_card("a"), planned_card("b", "second")], [])
    sidebar.select("b")
    assert sidebar.selected_key == "b"

    sidebar.show_cards([planned_card("c", "quite different")], [])

    assert sidebar.selected_key is None
    assert sidebar.planned.selectedItems() == []


def test_a_card_shows_what_it_was_given_and_nothing_invented(sidebar):
    sidebar.show_cards([planned_card()], [])

    said = sidebar.planned.item(0).text()
    assert "hdz_047.ts · Launch" in said
    assert "Social · 0:12–0:48" in said
    assert "Nocturne-in-C-sharp.mp3" in said


def test_a_committed_card_carries_its_status_in_the_row(sidebar):
    sidebar.show_cards([], [committed_card()])

    assert "Rendering 47%" in sidebar.committed.item(0).text()
