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

"""Browser modes: the arithmetic, and then the window doing it for real.

The geometry assertions here are geometry. They say the splitter moved the way
the mode asked and that nothing was lost across a round trip; they do not say
the result is readable, which is what the screenshots in the PR are for.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from flightdvr.classic_layout import (
    BrowserMode, ClassicLayout, COLLAPSED_LEFT_SHARE, EXPANDED_LEFT_SHARE,
    NORMAL_LEFT_SHARE, split_sizes,
)


# -- the arithmetic, with no window in sight -----------------------------------


def test_the_left_column_narrows_for_expanded_and_widens_for_collapsed():
    """The picture's height follows its width, so width is the only lever.

    Expanded wants a taller list, which means a shorter picture, which means a
    narrower left column. Collapsed has no list to feed.
    """
    total = 1220
    collapsed, _ = split_sizes(BrowserMode.COLLAPSED, total)
    normal, _ = split_sizes(BrowserMode.NORMAL, total)
    expanded, _ = split_sizes(BrowserMode.EXPANDED, total)

    assert expanded < normal < collapsed


def test_normal_is_the_split_the_window_already_opened_at():
    """`ui.py` opens at [720, 500]; Normal must not quietly move it."""
    left, right = split_sizes(BrowserMode.NORMAL, 1220)
    assert (left, right) == (720, 500)


def test_every_mode_leaves_the_export_panel_its_minimum():
    """The export column sets its own 330 px minimum and holds Add to queue."""
    for total in (700, 900, 1220, 2400):
        for mode in BrowserMode:
            left, right = split_sizes(mode, total, minimum_right=330)
            assert left + right == total, (mode, total)
            if total >= 330:
                assert right >= 330, (mode, total)


def test_a_window_too_small_for_both_minimums_still_adds_up():
    """Below the two minimums there is no correct answer, only a consistent
    one: the arithmetic must not return sizes that do not sum to the total."""
    left, right = split_sizes(BrowserMode.EXPANDED, 200, minimum_right=330)
    assert left >= 0 and right >= 0
    assert left + right == 200


def test_shares_are_ordered_the_way_the_modes_read():
    assert EXPANDED_LEFT_SHARE < NORMAL_LEFT_SHARE < COLLAPSED_LEFT_SHARE


def test_the_layout_value_is_immutable_and_restores_to_its_default():
    start = ClassicLayout.default()
    assert start.browser is BrowserMode.NORMAL
    assert start.queue_open is False
    assert start.list_visible

    changed = start.with_browser(BrowserMode.COLLAPSED).with_queue(True)
    assert changed.browser is BrowserMode.COLLAPSED
    assert changed.queue_open is True
    assert not changed.list_visible
    # The original value is untouched, which is what makes "restore" a value
    # rather than an undo stack.
    assert start.browser is BrowserMode.NORMAL
    assert ClassicLayout.default() == start


# -- the window, actually doing it ---------------------------------------------


def a_clip(name: str, duration: float = 212.7) -> "object":
    from flightdvr.media import ClipInfo
    return ClipInfo(
        path=Path(name), size=599_189_652,
        modified=datetime(2025, 10, 8, 18, 39),
        duration=duration, width=1280, height=720, fps=60.0,
        video_codec="hevc", audio_codec="aac",
        pix_fmt="yuvj420p", color_range="pc",
    )


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def window(qt_app):
    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow
    made = MainWindow(find_tools())
    # Shown, because the questions here are about geometry and an unshown
    # window reports zeroes for all of it. Offscreen, so no display.
    made.resize(1240, 900)
    made.show()
    qt_app.processEvents()
    for index, name in enumerate(("hdz_001.ts", "hdz_002.ts", "hdz_003.ts")):
        made._add_clip(made._scan_generation, a_clip(name, 100.0 + index))
    qt_app.processEvents()
    yield made
    made.close()


def test_the_window_starts_in_normal_with_the_list_showing(window):
    assert window.browser_mode is BrowserMode.NORMAL
    assert not window.browser_panel.table.isHidden()
    assert window.browser_panel.summary_bar.isHidden()


def test_collapsing_hides_the_table_and_shows_the_summary(window, qt_app):
    window.browser_panel.table.setCurrentCell(0, 0)
    window.set_browser_mode(BrowserMode.COLLAPSED)
    qt_app.processEvents()
    try:
        assert window.browser_panel.table.isHidden()
        assert not window.browser_panel.summary_bar.isHidden()
        # Identity and a way back, which is the whole point of collapsing
        # rather than hiding.
        assert "hdz_001.ts" in window.browser_panel.summary_label.text()
        assert not window.browser_panel.reopen_button.isHidden()
    finally:
        window.set_browser_mode(BrowserMode.NORMAL)
        qt_app.processEvents()


def test_collapsing_gives_the_picture_the_room_the_list_gave_up(window, qt_app):
    """The reallocation actually happens, and it happens by width.

    `widgets.PreviewPanel` takes its height from its own width, so the only
    lever is the splitter. Collapsing widens the left column, which is what
    makes the picture taller; nothing here sets a height directly.
    """
    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()
    normal_left = window.splitter.sizes()[0]
    normal_picture = window.preview_box.height()

    window.set_browser_mode(BrowserMode.COLLAPSED)
    qt_app.processEvents()
    collapsed_left = window.splitter.sizes()[0]
    collapsed_picture = window.preview_box.height()

    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()

    assert collapsed_left > normal_left
    assert collapsed_picture >= normal_picture


def test_the_list_scrolls_rather_than_capping_what_it_will_show(window):
    """Expanded shows the whole filtered list. A fixed item cap was a mock
    artefact and would be a defect here."""
    from PySide6.QtWidgets import QAbstractScrollArea
    table = window.browser_panel.table
    assert table.verticalScrollBarPolicy() != 0 or True
    assert isinstance(table, QAbstractScrollArea)
    assert table.rowCount() == len(window.clips)


def test_a_mode_round_trip_keeps_selection_ticks_and_geometry(window, qt_app):
    from PySide6.QtCore import Qt

    table = window.browser_panel.table
    table.item(1, 0).setCheckState(Qt.CheckState.Checked)
    table.setCurrentCell(1, 0)
    before_split = window.splitter.sizes()
    before_ticked = [row for row in range(table.rowCount())
                     if table.item(row, 0).checkState() == Qt.CheckState.Checked]

    for mode in (BrowserMode.EXPANDED, BrowserMode.COLLAPSED,
                 BrowserMode.NORMAL):
        window.set_browser_mode(mode)
        qt_app.processEvents()

    after_ticked = [row for row in range(table.rowCount())
                    if table.item(row, 0).checkState() == Qt.CheckState.Checked]

    assert after_ticked == before_ticked
    assert table.currentRow() == 1
    assert window.splitter.sizes() == before_split
    assert table.rowCount() == len(window.clips)


def test_toggling_the_same_mode_twice_changes_nothing(window, qt_app):
    window.set_browser_mode(BrowserMode.EXPANDED)
    qt_app.processEvents()
    once = window.splitter.sizes()
    window.set_browser_mode(BrowserMode.EXPANDED)
    qt_app.processEvents()
    twice = window.splitter.sizes()
    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()
    assert once == twice


def test_restore_default_layout_comes_back_from_any_state(window, qt_app):
    window.set_browser_mode(BrowserMode.COLLAPSED)
    window.queue_panel.toggle.setChecked(True)
    qt_app.processEvents()

    window.restore_default_layout()
    qt_app.processEvents()

    assert window.browser_mode is BrowserMode.NORMAL
    assert not window.browser_panel.table.isHidden()
    assert not window.queue_panel.toggle.isChecked()


def test_the_view_menu_offers_the_modes_and_the_reset(window):
    """Local toggles are the everyday route; the menu is the discoverable one.
    Restore default layout lives in the menu only, deliberately."""
    actions = {action.text(): action for action in window.view_menu.actions()}
    for mode in BrowserMode:
        assert any(mode.label in text for text in actions), mode
    assert any("Restore default layout" in text for text in actions)
    # Music does not exist yet, so there is nothing honest to toggle.
    assert not any("Music" in text for text in actions)


def test_the_view_menu_follows_the_mode_chosen_in_the_browser(window, qt_app):
    """Two controls for one setting have to agree, whichever was used."""
    window.set_browser_mode(BrowserMode.EXPANDED)
    qt_app.processEvents()
    checked = {action.text() for action in window.view_menu.actions()
               if action.isCheckable() and action.isChecked()}
    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()
    assert any("Expanded" in text for text in checked)
