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

import time
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QThread, Signal

from flightdvr.widgets import MIN_LIST_HEIGHT

from flightdvr.classic_layout import (
    BrowserMode, ClassicLayout, COLLAPSED_LEFT_SHARE, EXPANDED_LEFT_SHARE,
    NORMAL_LEFT_SHARE, split_sizes,
)


# Every window built here would otherwise start a real `HardwareProbe`, which
# runs test encodes through ffmpeg. These tests are about layout and have no
# opinion about encoders, and a probe still running when the interpreter exits
# destroys a live QThread — the assertions all pass and the process then dies,
# which is exactly what happened: 44 passed, exit 1.
#
# So the windows here never start one. Nothing in production changes; the
# window still owns the same attribute and still stops it on close.


class _NoProbe(QObject):
    """Stands in for `HardwareProbe` without starting a thread."""

    result = Signal(object)

    def __init__(self, tools, parent=None):
        super().__init__(parent)
        self.tools = tools
        self.started = False

    def start(self, *_args) -> None:
        self.started = True

    def isRunning(self) -> bool:  # noqa: N802 (Qt naming)
        return False

    def stop(self) -> None:
        pass

    def wait(self, *_args) -> bool:
        return True


@pytest.fixture(scope="module", autouse=True)
def no_background_work():
    """Hermetic: these windows start no threads and touch no network.

    Two of them were being started. The encoder probe runs test encodes, and
    the update check asks the releases API — neither has any bearing on where
    a splitter sits, and both were still running when the interpreter exited.
    That destroys a live QThread, which is why the assertions all passed and
    the process then died with 44 passed, exit 1.

    The update check is turned off through the gate the window already honours
    rather than through a stand-in, so this switches a real decision off
    instead of pretending the class is something else.

    Module-scoped on purpose. As a function-scoped fixture it was still not
    applied when the module-scoped window was built — pytest sets the wider
    scope up first — so the very first window in the file started both threads
    anyway, and the leak came back one run in three.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("flightdvr.ui.HardwareProbe", _NoProbe)
        patch.setattr("flightdvr.updates.should_check", lambda *a, **k: False)
        yield


def assert_no_threads_left(window) -> None:
    """Teardown is verified, not assumed.

    A window that leaves a QThread running takes the process down at exit, and
    it does it after the last assertion has already passed.
    """
    from PySide6.QtCore import QThread
    running = [t for t in window.findChildren(QThread) if t.isRunning()]
    assert not running, [type(t).__name__ for t in running]


# -- the arithmetic, with no window in sight -----------------------------------


def test_no_mode_takes_width_from_the_export_column():
    """Collapsing used to widen the left column, which does make the picture
    bigger — and cut the export panel's help text off at 386 px. The picture
    is not worth the explanation of the preset being truncated."""
    total = 1220
    collapsed, collapsed_right = split_sizes(BrowserMode.COLLAPSED, total)
    normal, normal_right = split_sizes(BrowserMode.NORMAL, total)
    expanded, expanded_right = split_sizes(BrowserMode.EXPANDED, total)

    assert collapsed == normal == expanded
    assert collapsed_right == normal_right == expanded_right


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


def test_no_mode_moves_the_split():
    """Expanded buys its height from the picture, not from the export column."""
    assert EXPANDED_LEFT_SHARE == NORMAL_LEFT_SHARE == COLLAPSED_LEFT_SHARE


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
    assert_no_threads_left(made)


def settle(qt_app, widget, limit: int = 12) -> int:
    """Let the layout finish, and say when it has.

    A fixed number of `processEvents()` calls is a guess: the panel sets its
    own height, which delivers a resize, which can change the answer once more.
    This waits for the height to stop moving instead, and returns it.
    """
    last = None
    for _ in range(limit):
        qt_app.processEvents()
        if widget.height() == last:
            return last
        last = widget.height()
    return last


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


def test_changing_mode_does_not_call_setSizes_at_all(window, qt_app):
    """The correction behind the one-pixel CI failure, stated as a rule.

    Comparing sizes was not enough: on Windows the computed split happened to
    equal the one the layout had settled on, so re-imposing it looked harmless
    and the drift only appeared on Linux and macOS. The oracle was exact —
    `round(1216 * 0.59)` is 717, and 717/499 is precisely what CI reported
    where the layout had 718/498, so it was this code moving the splitter and
    not Qt rounding or layout timing. A mode has no business calling setSizes,
    so the test asserts the call is never made rather than that its result
    happens to match.
    """
    calls = []
    original = window.splitter.setSizes
    window.splitter.setSizes = lambda sizes: calls.append(list(sizes))
    try:
        for mode in (BrowserMode.EXPANDED, BrowserMode.COLLAPSED,
                     BrowserMode.NORMAL, BrowserMode.NORMAL):
            window.set_browser_mode(mode)
            qt_app.processEvents()
    finally:
        window.splitter.setSizes = original
    assert calls == []


def test_a_dragged_split_survives_every_mode(window, qt_app):
    """Preserved by not being touched, which is why it holds to the pixel."""
    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()
    total = sum(window.splitter.sizes())
    dragged = [total - 460, 460]
    window.splitter.setSizes(dragged)
    qt_app.processEvents()
    settled = window.splitter.sizes()

    for mode in (BrowserMode.EXPANDED, BrowserMode.COLLAPSED,
                 BrowserMode.NORMAL):
        window.set_browser_mode(mode)
        qt_app.processEvents()
        assert window.splitter.sizes() == settled, mode


def test_collapsing_does_not_disturb_the_split(window, qt_app):
    """Putting the list away is not an excuse to squeeze the export column."""
    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()
    before = window.splitter.sizes()

    window.set_browser_mode(BrowserMode.COLLAPSED)
    qt_app.processEvents()
    assert window.splitter.sizes() == before

    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()


def test_an_overflowing_list_scrolls_to_its_last_filtered_row(own_window,
                                                              qt_app):
    """The whole filtered list is reachable, not the first screenful of it.

    The previous version of this test asserted `verticalScrollBarPolicy() != 0
    or True`, which is true whatever the widget does, over three rows that
    never overflowed. This loads enough rows to overflow, checks the scrollbar
    genuinely has somewhere to go, and then checks the last row that survives
    the filter can actually be brought into view.
    """
    window = own_window
    for index in range(40):
        window._add_clip(window._scan_generation,
                         a_clip(f"hdz_{index:03d}.ts", 60.0 + index))
    window.set_browser_mode(BrowserMode.EXPANDED)
    qt_app.processEvents()
    table = window.browser_panel.table

    bar = table.verticalScrollBar()
    assert bar.maximum() > bar.minimum(), "the list did not overflow"

    window.browser_panel.min_length.setValue(80)
    qt_app.processEvents()
    shown = [row for row in range(table.rowCount())
             if not table.isRowHidden(row)]
    assert shown, "the filter hid everything, so this proves nothing"
    assert len(shown) < table.rowCount(), "the filter hid nothing"

    last = shown[-1]
    table.scrollToItem(table.item(last, 0))
    qt_app.processEvents()
    viewport = table.viewport().rect()
    assert viewport.intersects(table.visualItemRect(table.item(last, 0)))

    window.browser_panel.reset_length_filter()
    qt_app.processEvents()


def test_a_collapsed_summary_keeps_the_active_clip_thumbnail(own_window,
                                                             qt_app):
    """The collapsed line promises the clip's thumbnail, so prove it carries
    one when the row has one. The real icon arrives from a worker thread; this
    supplies the icon the worker would have set and checks the summary keeps
    it rather than waiting on that thread."""
    from PySide6.QtGui import QColor, QIcon, QPixmap

    window = own_window
    table = window.browser_panel.table
    pixmap = QPixmap(table.iconSize())
    pixmap.fill(QColor("#3366cc"))
    table.item(0, 0).setIcon(QIcon(pixmap))
    table.setCurrentCell(0, 0)
    qt_app.processEvents()

    window.set_browser_mode(BrowserMode.COLLAPSED)
    qt_app.processEvents()
    try:
        shown = window.browser_panel.summary_thumb.pixmap()
        assert not shown.isNull()
        assert not window.browser_panel.summary_thumb.isHidden()
    finally:
        window.set_browser_mode(BrowserMode.NORMAL)
        qt_app.processEvents()


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
    # Music exists now (W4), and its entry is the band's own toggle.
    assert "Music" in actions


def test_the_view_menu_music_entry_and_the_band_agree_both_ways(window,
                                                                  qt_app):
    band = window.preview_view.music_band
    action = window.music_action
    assert not band.isChecked() and not action.isChecked()
    action.trigger()
    qt_app.processEvents()
    assert band.isChecked() and action.isChecked()
    band.setChecked(False)
    qt_app.processEvents()
    assert not action.isChecked()
    band.setChecked(True)
    qt_app.processEvents()
    assert action.isChecked()
    band.setChecked(False)


def test_collapsed_gives_the_list_reserve_back_to_the_picture(window, qt_app):
    """R12: the one-line summary is all that stays under the picture."""
    box = window.preview_view.preview_box
    window.set_browser_mode(BrowserMode.COLLAPSED)
    qt_app.processEvents()
    collapsed = box._list_room
    assert collapsed == window.browser_panel.summary_bar.sizeHint().height()
    assert collapsed < MIN_LIST_HEIGHT
    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()
    # The list's own rows above its table plus three whole clips, and never
    # less than the old flat reserve (Nk's tested candidate, 9 October).
    assert box._list_room == window._classic_list_room()
    assert box._list_room >= MIN_LIST_HEIGHT


def test_the_classic_band_is_the_shallow_one(window, qt_app):
    """Classic's band gives way down to its track row instead of growing
    the window; Flow's keeps its working minimum."""
    from flightdvr.preview_panel import CLASSIC_MUSIC_MAXIMUM, MUSIC_BAND_MINIMUM
    view = window.preview_view
    band = view.music_band
    band.setChecked(True)
    qt_app.processEvents()
    body = view.music_body
    assert body.minimumHeight() == max(
        24, view.track_button.sizeHint().height())
    assert body.maximumHeight() == CLASSIC_MUSIC_MAXIMUM
    assert MUSIC_BAND_MINIMUM > body.minimumHeight()
    band.setChecked(False)
    qt_app.processEvents()


def test_collapsed_releases_the_list_reserve_and_normal_restores_it(
        window, qt_app):
    panel = window.browser_panel
    window.set_browser_mode(BrowserMode.COLLAPSED)
    qt_app.processEvents()
    assert panel.minimumHeight() == 0
    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()
    assert panel.minimumHeight() == MIN_LIST_HEIGHT


def test_the_view_menu_follows_the_mode_chosen_in_the_browser(window, qt_app):
    """Two controls for one setting have to agree, whichever was used."""
    window.set_browser_mode(BrowserMode.EXPANDED)
    qt_app.processEvents()
    checked = {action.text() for action in window.view_menu.actions()
               if action.isCheckable() and action.isChecked()}
    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()
    assert any("Expanded" in text for text in checked)


# -- the preview height cap ----------------------------------------------------
#
# Authorized as a bounded delta to `widgets.PreviewPanel` after the splitter
# alone was measured giving Expanded nothing: at the sizes this window opens
# at, the left column is already at its own minimum width.


def _settled_cap_state(window) -> str:
    box = window.preview_box
    return (f"cap={box._height_cap} fit={window._classic_fit} "
            f"room={box._list_room} box={box.height()} "
            f"column={box.parentWidget().height()} "
            f"list={window.browser_panel.height()} floor={box.content_floor()}")


def test_no_cap_is_exactly_todays_sizing(own_window, qt_app):
    """Normal sets no cap: the picture is what its width earns, short only
    of the list's own room (its rows and three clips) and never under its
    floor. In a window of its own with room to spare, measured once the
    deferred layout has finished (one pump was not enough on macOS CI,
    where a safety cap from a transient overflow was still set; Sol R3)."""
    window = own_window
    window.set_browser_mode(BrowserMode.NORMAL)
    _wait(qt_app)
    box = window.preview_box
    assert box._height_cap is None, _settled_cap_state(window)
    expected = min(box.useful_height(box.width()),
                   box.parentWidget().height() - box._list_room)
    assert box.height() == max(expected, box.content_floor()), _settled_cap_state(window)


def test_a_safety_cap_is_given_back_once_the_column_has_room(own_window, qt_app):
    """A cap taken in a transient overflow comes off when the room returns,
    not only on the next mode change. With everything fitting, the picture
    has no clearance under it (the list above it stretches), so a release
    that waited for a positive clearance never came (Sol R3, 9 October)."""
    window = own_window
    window.set_browser_mode(BrowserMode.NORMAL)
    _wait(qt_app)
    box = window.preview_box
    assert box._height_cap is None, _settled_cap_state(window)
    # What an overflow leaves behind: the picture held at its floor.
    window._classic_fit = box.content_floor()
    box.set_height_cap(window._classic_height_cap())
    _wait(qt_app, 0.3)
    assert box._height_cap is not None
    window._relayout()
    _wait(qt_app)
    assert window._classic_fit is None and box._height_cap is None, (
        _settled_cap_state(window))
    expected = min(box.useful_height(box.width()),
                   box.parentWidget().height() - box._list_room)
    assert box.height() == max(expected, box.content_floor())


def test_the_cap_applies_without_waiting_for_a_width_change(window, qt_app):
    """The reason the cap exists at all.

    Once the splitter is at its minimum the left column stops changing width,
    so a cap that only took effect on the next resize would never take effect.
    """
    box = window.preview_box
    width_before = box.width()
    tall = box.height()

    box.set_height_cap(box.content_floor())
    qt_app.processEvents()

    assert box.width() == width_before
    assert box.height() < tall
    box.set_height_cap(None)
    qt_app.processEvents()


def test_releasing_the_cap_returns_the_exact_height(window, qt_app):
    box = window.preview_box
    before = box.height()
    box.set_height_cap(box.content_floor())
    qt_app.processEvents()
    box.set_height_cap(None)
    qt_app.processEvents()
    assert box.height() == before


def test_the_cap_never_goes_through_the_content_floor(window, qt_app):
    """A ceiling must not clip the buttons it is sitting above."""
    box = window.preview_box
    box.set_height_cap(1)
    qt_app.processEvents()
    try:
        assert box.height() >= box.content_floor()
        assert box.sidebar.height() >= box.sidebar.sizeHint().height()
        assert box.sidebar.isVisible()
        assert window.still_button.isVisible()
    finally:
        box.set_height_cap(None)
        qt_app.processEvents()


def test_expanding_buys_list_height_from_the_picture(window, qt_app):
    """The measurable claim, at the size the window actually opens at."""
    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()
    normal_list = window.browser_panel.table.height()
    normal_picture = window.preview_box.height()
    sidebar_width = window.splitter.sizes()[1]

    window.set_browser_mode(BrowserMode.EXPANDED)
    qt_app.processEvents()
    expanded_list = window.browser_panel.table.height()
    expanded_picture = window.preview_box.height()

    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()

    assert expanded_list > normal_list
    assert expanded_picture < normal_picture
    # Not by quietly widening the export column, which would be simulating an
    # expansion rather than performing one.
    assert window.splitter.sizes()[1] == sidebar_width


def test_resizing_while_expanded_gives_the_new_height_to_the_list(own_window,
                                                                  qt_app):
    """A taller window while expanded is list, not picture.

    Its own window, and settled rather than counted: the shared one carries the
    splitter drag and selection the tests before it leave behind, and the panel
    sets its own height, which delivers a resize that can move the answer once
    more.
    """
    window = own_window
    window.set_browser_mode(BrowserMode.EXPANDED)
    picture = settle(qt_app, window.preview_box)
    listed = window.browser_panel.table.height()

    window.resize(window.width(), window.height() + 150)
    assert settle(qt_app, window.preview_box) == picture
    assert window.browser_panel.table.height() > listed


@pytest.fixture
def own_window(qt_app):
    """A window of its own, for the questions that measure a settled state.

    The module-scoped window is shared, and the tests before this one drag the
    splitter, resize and select clips. All three legitimately move the preview
    floor, so a value read from it is not evidence about oscillation.
    """
    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow
    made = MainWindow(find_tools())
    made.resize(1240, 900)
    made.show()
    qt_app.processEvents()
    for index, name in enumerate(("hdz_001.ts", "hdz_002.ts", "hdz_003.ts")):
        made._add_clip(made._scan_generation, a_clip(name, 100.0 + index))
    qt_app.processEvents()
    yield made
    made.close()
    assert_no_threads_left(made)


def test_repeated_toggles_settle_rather_than_drifting(own_window, qt_app):
    """`setFixedHeight` delivers a resize that arrives back at the same
    handler, so the two could otherwise take turns."""
    window = own_window
    # Settle to the first answer each mode gives in this window, rather than
    # to a number written here: the floor follows the sidebar's own size hint,
    # which is allowed to differ between windows and after a resize. What must
    # not happen is the height moving while nothing else does.
    window.set_browser_mode(BrowserMode.EXPANDED)
    qt_app.processEvents()
    qt_app.processEvents()
    settled_expanded = window.preview_box.height()
    # Expanded's controls are compact, so its floor is measured there.
    expanded_floor = window.preview_box.content_floor()
    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()
    qt_app.processEvents()
    settled_normal = window.preview_box.height()

    for _ in range(4):
        window.set_browser_mode(BrowserMode.EXPANDED)
        qt_app.processEvents()
        qt_app.processEvents()
        assert window.preview_box.height() == settled_expanded
        window.set_browser_mode(BrowserMode.NORMAL)
        qt_app.processEvents()
        qt_app.processEvents()
        assert window.preview_box.height() == settled_normal

    assert settled_expanded < settled_normal
    assert settled_expanded >= expanded_floor


def test_the_cap_does_not_raise_the_window_minimum(window, qt_app):
    """A mode is not allowed to make the window harder to fit on a screen."""
    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()
    normal_minimum = window.minimumSizeHint().width()

    window.set_browser_mode(BrowserMode.EXPANDED)
    qt_app.processEvents()
    assert window.minimumSizeHint().width() <= normal_minimum

    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()


def test_every_preview_control_stays_inside_the_panel_at_a_short_window(qt_app):
    """The browser's extra rows must not push the preview's controls out.

    Asserted rather than eyeballed. A review of the first captures read the
    In / Out / Reset row as clipped at the compact size; in the same run the
    geometry put its lowest edge 97 px inside the sidebar, and the same image
    also cut the export panel's right edge, which nothing in this change can
    reach. The grab was rendering a frame the layout had already moved past.
    Geometry is the evidence; this keeps it honest whichever way a capture
    happens to land.
    """
    from PySide6.QtWidgets import QPushButton
    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow

    window = MainWindow(find_tools())
    window.resize(1402, 706)          # the window's own minimum height
    window.show()
    qt_app.processEvents()
    try:
        for index, seconds in enumerate((8.0, 191.0, 184.0, 0.0, 212.0)):
            window._add_clip(window._scan_generation,
                             a_clip(f"hdz_{index:03d}.ts", seconds))
        window.browser_panel.min_length.setValue(30)
        window.browser_panel.table.setCurrentCell(1, 0)
        window._load_selected_clip()
        settle(qt_app, window.preview_box)

        for mode in BrowserMode:
            window.set_browser_mode(mode)
            settle(qt_app, window.preview_box)
            sidebar = window.preview_box.sidebar
            buttons = [b for b in sidebar.findChildren(QPushButton)
                       if b.isVisible()]
            assert buttons, mode
            lowest = max(b.geometry().bottom() for b in buttons)
            assert lowest <= sidebar.height(), (mode, lowest, sidebar.height())
            assert sidebar.height() <= window.preview_box.height(), mode
    finally:
        window.close()
        assert_no_threads_left(window)


# The base at `11c8b288`, measured with five clips loaded, wants 1402x712.
# The width is unchanged here. The height is not: the length filter is a real
# row and a real row costs real pixels.
#
# The bound is one clip row rather than an exact number. An exact number is
# what made the splitter assertions fail CI by a pixel on two platforms — a
# figure that precise is measuring the style's metrics, not this change. One
# row of the list is the largest cost that can honestly be called "a row", and
# it is small enough that a second row appearing would fail.
BASE_MINIMUM = (1402, 712)
ONE_CLIP_ROW = 24


def compact_header_cost(spacing: int) -> int:
    """What adding one collapsed, checkable band to a column actually costs.

    Measured by doing it, on the platform running this, rather than by adding
    a header's size hint to a spacing and hoping those are the only two things
    a layout charges for. An empty checkable group box, flat and unpadded, is
    exactly what the music band shows when it is collapsed — so a style whose
    group boxes are taller gets a taller allowance, and nobody has to edit a
    constant to make a particular platform pass.
    """
    from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget

    holder = QWidget()
    column = QVBoxLayout(holder)
    column.setSpacing(spacing)
    column.addWidget(QLabel("x"))
    without = holder.minimumSizeHint().height()

    reference = QGroupBox("Music")
    reference.setCheckable(True)
    reference.setChecked(False)
    reference.setFlat(True)
    inner = QVBoxLayout(reference)
    inner.setContentsMargins(0, 0, 0, 0)
    column.addWidget(reference)
    cost = holder.minimumSizeHint().height() - without
    holder.deleteLater()
    return cost


def loaded_window(qt_app):
    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow

    window = MainWindow(find_tools())
    window.resize(*BASE_MINIMUM)
    window.show()
    qt_app.processEvents()
    for index, seconds in enumerate((8.0, 191.0, 184.0, 0.0, 212.0)):
        window._add_clip(window._scan_generation,
                         a_clip(f"hdz_{index:03d}.ts", seconds))
    qt_app.processEvents()
    return window


def test_the_browser_rows_cost_no_more_than_one_row(qt_app):
    """A new control may cost height. It may not cost width, or a second row.

    Tightening the gap in front of the row was tried and bought nothing back,
    so the cost is the row itself rather than spacing. Measured between 6 and
    12 px depending on what is loaded, which is why the bound is a row rather
    than a number.

    The music band is taken out of the layout first. This budget is about the
    browser, and charging a second, unrelated addition to it would turn one
    number into a pot that anything can be paid out of — which is how a bound
    stops meaning anything. The band is bounded on its own below.
    """
    window = loaded_window(qt_app)
    try:
        window.music_band.hide()          # a hidden widget is not laid out
        qt_app.processEvents()
        smallest = window.minimumSizeHint()
        assert smallest.width() <= BASE_MINIMUM[0], smallest.width()
        assert smallest.height() <= BASE_MINIMUM[1] + ONE_CLIP_ROW, (
            smallest.height())
    finally:
        window.close()
        assert_no_threads_left(window)


def test_the_collapsed_music_band_costs_one_header_and_the_spacing(qt_app):
    """Bounded against what a header actually is here, not against 744.

    The allowance is derived: an empty checkable group box plus the spacing
    the window's own layout puts between its rows. Nothing is chosen to make a
    particular platform's number pass, and a style with taller group boxes
    gets a taller allowance because the reference is measured the same way.
    """
    window = loaded_window(qt_app)
    try:
        window.music_band.hide()
        qt_app.processEvents()
        browser_only = window.minimumSizeHint().height()

        window.music_band.show()
        qt_app.processEvents()
        with_band = window.minimumSizeHint().height()

        allowance = compact_header_cost(
            window.centralWidget().layout().spacing())
        assert with_band - browser_only <= allowance, (
            f"collapsed band cost {with_band - browser_only}px, "
            f"one header plus spacing is {allowance}px")
    finally:
        window.close()
        assert_no_threads_left(window)


def test_a_second_row_in_the_collapsed_band_would_still_fail(qt_app):
    """The bound above has to be tight enough to notice content appearing.

    Expanding the band is the cheapest honest way to prove that: if a header's
    worth of allowance also covered the body, it would cover anything, and the
    check would be decoration.
    """
    window = loaded_window(qt_app)
    try:
        window.music_band.hide()
        qt_app.processEvents()
        browser_only = window.minimumSizeHint().height()

        window.music_band.show()
        window.music_band.setChecked(True)
        qt_app.processEvents()
        expanded = window.minimumSizeHint().height()

        allowance = compact_header_cost(
            window.centralWidget().layout().spacing())
        assert expanded - browser_only > allowance, (
            "the collapsed-band allowance is loose enough to hide a row")
    finally:
        window.close()
        assert_no_threads_left(window)


# -- Expanded shows more rows (W4, R12) -------------------------------------------


def many_clips_window(qt_app, mode, size=(1440, 913)):
    """Twelve clips, the mode chosen before anything is measured: no earlier
    Normal visit for Expanded to borrow a size from."""
    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow

    window = MainWindow(find_tools())
    window.set_browser_mode(mode)
    window.resize(*size)
    window.show()
    qt_app.processEvents()
    for index in range(12):
        window._add_clip(window._scan_generation,
                         a_clip(f"hdz_{index:03d}.ts", 60.0 + index))
    for _ in range(4):
        qt_app.processEvents()
        window._sync_thumbnail_size()
    return window


def fully_visible_rows(window) -> int:
    table = window.browser_panel.table
    viewport = table.viewport().rect()
    return sum(1 for row in range(table.rowCount())
               if viewport.contains(table.visualRect(
                   table.model().index(row, 0)).adjusted(0, 0, -1, -1)))


def test_expanded_from_the_start_shows_more_rows_than_normal(qt_app):
    normal = many_clips_window(qt_app, BrowserMode.NORMAL)
    expanded = many_clips_window(qt_app, BrowserMode.EXPANDED)
    try:
        assert expanded.size() == normal.size()
        assert fully_visible_rows(expanded) > fully_visible_rows(normal)
        assert (expanded.browser_panel.table.iconSize().height()
                < normal.browser_panel.table.iconSize().height())
    finally:
        for window in (normal, expanded):
            window.close()
            assert_no_threads_left(window)


def test_switching_modes_repeatedly_lands_on_the_same_rows(qt_app):
    window = many_clips_window(qt_app, BrowserMode.NORMAL)
    table = window.browser_panel.table
    try:
        table.selectRow(5)
        table.scrollToItem(table.item(5, 0))
        qt_app.processEvents()
        seen = {}
        for _round in range(3):
            for mode in (BrowserMode.EXPANDED, BrowserMode.NORMAL):
                window.set_browser_mode(mode)
                # Measured once the deferred layout (40 and 60 ms retries)
                # has finished: four bare pumps sometimes sampled before it,
                # 43 against 41 px in long combined runs (9-10 October).
                _wait(qt_app, 0.5)
                for _ in range(4):
                    qt_app.processEvents()
                    window._sync_thumbnail_size()
                size = table.iconSize().height()
                assert seen.setdefault(mode, size) == size, mode
                # The selection, and the row it is on, survive the switch.
                assert table.currentRow() == 5
                assert table.selectionModel().isRowSelected(5)
        assert seen[BrowserMode.EXPANDED] < seen[BrowserMode.NORMAL]
    finally:
        window.close()
        assert_no_threads_left(window)


def test_flow_s_stacked_list_ignores_classic_s_expanded_rows(qt_app):
    """Flow's Browse stacks the list and keeps its own sizing: Expanded left
    over from Classic must not shrink its thumbnails."""
    window = many_clips_window(qt_app, BrowserMode.EXPANDED)
    panel = window.browser_panel
    table = panel.table
    try:
        panel.set_stacked(True)
        sizes = {}
        for mode in (BrowserMode.EXPANDED, BrowserMode.NORMAL):
            panel.show_mode(mode)
            table.setIconSize(table.iconSize() * 0)     # never the cached size
            panel.sync_thumbnail_size()
            sizes[mode] = table.iconSize().height()
        assert sizes[BrowserMode.EXPANDED] == sizes[BrowserMode.NORMAL]
    finally:
        panel.set_stacked(False)
        window.close()
        assert_no_threads_left(window)


def test_the_list_panel_does_not_answer_height_for_width(qt_app):
    """Its one-line labels wrapped, so the panel asked its column for its
    full preferred height at any window size (48px overflow at 1120x760,
    measured natively)."""
    window = many_clips_window(qt_app, BrowserMode.NORMAL, (1120, 760))
    panel = window.browser_panel
    try:
        assert not panel.length_label.wordWrap()
        assert not panel.hidden_label.wordWrap()
        assert not panel.hasHeightForWidth()
        panel.set_hidden_summary("3 hidden by length")
        assert panel.hidden_label.toolTip().startswith("3 hidden by length")
    finally:
        window.close()
        assert_no_threads_left(window)


def test_the_window_minimum_counts_the_picture_at_its_floor(qt_app,
                                                           isolated_controls):
    """A narrower-and-shorter move is not held to the old picture's height."""
    window = many_clips_window(qt_app, BrowserMode.NORMAL, (1460, 1000))
    try:
        for _ in range(6):
            qt_app.processEvents()
        box = window.preview_view.preview_box
        slack = box.height() - box.content_floor()
        assert slack > 0
        assert window.minimumHeight() == (
            window.minimumSizeHint().height() - slack)
    finally:
        window.close()
        assert_no_threads_left(window)


# -- the approved compact fold, and wrapped presets (24 September) --------------


def test_a_fold_shows_the_summary_and_keeps_the_chosen_mode(qt_app):
    window = many_clips_window(qt_app, BrowserMode.EXPANDED, (1120, 760))
    panel = window.browser_panel
    table = panel.table
    try:
        table.selectRow(5)
        table.setCurrentCell(5, 0)
        window.preview_view.music_band.setChecked(True)
        qt_app.processEvents()
        # Folded directly: whether this offscreen size needs it is the
        # native harness's question; this is about what a fold is.
        window._set_list_folded(True)
        assert panel.folded and not table.isVisible()
        assert panel.summary_bar.isVisible()
        # The person's choice is untouched: buttons, menu and state.
        assert panel.mode_buttons[BrowserMode.EXPANDED].isChecked()
        assert window.browser_mode_actions[BrowserMode.EXPANDED].isChecked()
        assert window._layout_state.browser is BrowserMode.EXPANDED
        assert panel.minimumHeight() == 0
        # "Show clips" brings the list back, on its row, and keeps it back.
        panel.reopen_button.click()
        qt_app.processEvents()
        assert not panel.folded and table.isVisible()
        assert table.currentRow() == 5 and window._fold_suppressed
        window._check_list_fold()
        assert not panel.folded
        # Closing Music forgets the explicit restore.
        window.preview_view.music_band.setChecked(False)
        qt_app.processEvents()
        assert not window._fold_suppressed
    finally:
        window.close()
        assert_no_threads_left(window)


def test_a_mode_choice_while_folded_unfolds_and_is_kept(qt_app):
    window = many_clips_window(qt_app, BrowserMode.NORMAL, (1120, 760))
    panel = window.browser_panel
    try:
        window._set_list_folded(True)
        window.set_browser_mode(BrowserMode.COLLAPSED)
        qt_app.processEvents()
        assert not panel.folded
        assert window._layout_state.browser is BrowserMode.COLLAPSED
        assert panel.summary_bar.isVisible()
    finally:
        window.set_browser_mode(BrowserMode.NORMAL)
        window.close()
        assert_no_threads_left(window)


def test_the_fold_is_never_carried_into_flow(qt_app):
    from flightdvr.flow_layout import Mode
    window = many_clips_window(qt_app, BrowserMode.NORMAL, (1120, 760))
    try:
        window._set_list_folded(True)
        window.set_view_mode(Mode.FLOW)
        qt_app.processEvents()
        assert not window.browser_panel.folded
        window.set_view_mode(Mode.CLASSIC)
    finally:
        window.close()
        assert_no_threads_left(window)


def test_the_summary_names_the_range_being_worked_on(qt_app):
    from flightdvr.media import Select
    window = many_clips_window(qt_app, BrowserMode.NORMAL)
    try:
        window.table.setCurrentCell(2, 0)
        window._load_selected_clip()
        clip = window._trim_clip
        clip.selects = [Select(1.0, 2.0, "", sid="s-1"),
                        Select(3.0, 5.0, "Launch", sid="s-2")]
        clip.current = 1
        window._refresh_browser_summary()
        text = window.browser_panel.summary_label.text()
        assert clip.path.name in text and "range 2 of 2 (Launch)" in text
        assert window.browser_panel.summary_label.toolTip() == text
    finally:
        window.close()
        assert_no_threads_left(window)


def test_the_preset_buttons_wrap_to_the_width_they_have(qt_app):
    from flightdvr.export_panel import ExportPanel
    from flightdvr.presets import PRESET_ORDER
    panel = ExportPanel()
    try:
        panel.resize(900, 700)
        panel.show()
        qt_app.processEvents()
        assert panel._preset_columns == len(PRESET_ORDER)
        panel.resize(360, 700)
        for _ in range(3):
            qt_app.processEvents()
        assert panel._preset_columns < len(PRESET_ORDER)
        viewport = panel.scroller.viewport()
        for button in panel.preset_buttons.values():
            right = button.mapTo(viewport, button.rect().topRight()).x()
            assert right < viewport.width(), button.text()
        # Same buttons, same group, same order.
        assert list(panel.preset_buttons) == list(PRESET_ORDER)
        assert all(button.group() is panel.preset_group
                   for button in panel.preset_buttons.values())
        panel.resize(900, 700)
        for _ in range(3):
            qt_app.processEvents()
        assert panel._preset_columns == len(PRESET_ORDER)
    finally:
        panel.close()


def test_a_flow_round_trip_puts_classic_s_list_back_where_it_was(qt_app):
    """Sol's review of 9553790: 6 came back as 5. Flow's list moves the
    scroll (done explicitly here, as offscreen geometry may not); Classic
    puts it back when the selection is the same, and respects a different
    one chosen in Flow."""
    from flightdvr.flow_layout import Mode
    window = many_clips_window(qt_app, BrowserMode.NORMAL, (1120, 760))
    table = window.browser_panel.table
    bar = table.verticalScrollBar()
    try:
        table.setCurrentCell(8, 0)
        table.selectRow(8)
        qt_app.processEvents()
        bar.setValue(min(bar.maximum(), 6))
        before = bar.value()
        assert before > 0
        window.set_view_mode(Mode.FLOW)
        qt_app.processEvents()
        bar.setValue(0)                          # Flow's own geometry
        window.set_view_mode(Mode.CLASSIC)
        for _ in range(4):
            qt_app.processEvents()
        assert table.currentRow() == 8 and bar.value() == before

        window.set_view_mode(Mode.FLOW)
        qt_app.processEvents()
        table.setCurrentCell(1, 0)               # a different choice in Flow
        bar.setValue(0)
        window.set_view_mode(Mode.CLASSIC)
        for _ in range(4):
            qt_app.processEvents()
        assert table.currentRow() == 1 and bar.value() != before
    finally:
        window.close()
        assert_no_threads_left(window)


# -- P1: what folding for Music sets aside, and brings back (29 September) -----


def _filter_widgets(window):
    panel = window.browser_panel
    return [panel.review_filter, *panel.review_buttons.values(),
            panel.review_count_label, panel.min_length, panel.max_length,
            panel.show_unknown, panel.reset_length]


def test_folding_for_music_sets_the_filter_rows_and_secondary_lines_aside(
        qt_app):
    window = many_clips_window(qt_app, BrowserMode.NORMAL, (1120, 760))
    panel, view = window.browser_panel, window.preview_view
    try:
        window.show()
        view.music_band.setChecked(True)
        qt_app.processEvents()
        panel.review_filter.setCurrentIndex(1)       # a filter the person set
        panel.min_length.setValue(3)
        state = (panel.review_filter.currentIndex(), panel.min_length.value())
        before = [w.isVisible() for w in _filter_widgets(window)]
        assert all(before)
        window._set_list_folded(True)
        # Held folded for the check: with the compact controls (9 October)
        # this window has room, and the next pass would unfold it.
        window._fold_need = 10 ** 6
        qt_app.processEvents()
        assert not any(w.isVisible() for w in _filter_widgets(window))
        assert not view.clip_format.isVisible()
        assert not view.clip_date.isVisible()
        # Every control stays: Play and Grab still side by side, In/Out/Reset.
        assert view.play_button.isVisible() and view.still_button.isVisible()
        assert all(b.isVisible() for b, _tip in view._source_edits)
        assert (view.play_button.mapTo(window, view.play_button.rect().topLeft()).y()
                == view.still_button.mapTo(window, view.still_button.rect().topLeft()).y())
        # The summary still says what is selected; restore is one press away.
        assert panel.summary_bar.isVisible() and panel.reopen_button.isVisible()
        assert window._music_disclosed > 0
        panel.reopen_button.click()
        qt_app.processEvents()
        assert not panel.folded
        assert [w.isVisible() for w in _filter_widgets(window)] == before
        # Music is still open, so the controls stay compact (format and date
        # in the list); closing it brings the lines back.
        assert not view.clip_format.isVisible()
        view.music_band.setChecked(False)
        qt_app.processEvents()
        assert view.clip_format.isVisible() and view.clip_date.isVisible()
        assert (panel.review_filter.currentIndex(),
                panel.min_length.value()) == state, "filter state kept"
        assert window._music_disclosed == 0
    finally:
        panel.min_length.setValue(0)
        panel.review_filter.setCurrentIndex(0)
        view.music_band.setChecked(False)
        window.close()
        assert_no_threads_left(window)


def test_closing_music_or_leaving_classic_brings_the_rows_back(
        qt_app, isolated_controls):
    from flightdvr.flow_layout import Mode
    window = many_clips_window(qt_app, BrowserMode.NORMAL, (1120, 760))
    panel, view = window.browser_panel, window.preview_view
    try:
        window.show()
        view.music_band.setChecked(True)
        qt_app.processEvents()
        window._set_list_folded(True)
        view.music_band.setChecked(False)
        qt_app.processEvents()
        window._check_list_fold()
        assert not panel.folded
        assert panel.review_filter.isVisible() and view.clip_format.isVisible()
        view.music_band.setChecked(True)
        qt_app.processEvents()
        window._set_list_folded(True)
        window.set_view_mode(Mode.FLOW)
        qt_app.processEvents()
        window.set_view_mode(Mode.CLASSIC)
        qt_app.processEvents()
        assert window._music_hidden is None
        assert panel.review_filter.isVisible()
        # Compact while Music is open: every control, format in the list.
        assert not view.clip_format.isVisible() and view.still_button.isVisible()
        # And a list mode chosen while folded, which also unfolds.
        view.music_band.setChecked(True)
        qt_app.processEvents()
        window._set_list_folded(True)
        window.set_browser_mode(BrowserMode.EXPANDED)
        qt_app.processEvents()
        assert window._music_hidden is None
        assert panel.review_filter.isVisible() and view.still_button.isVisible()
        assert not view.clip_format.isVisible()
        window.set_browser_mode(BrowserMode.NORMAL)
    finally:
        view.music_band.setChecked(False)
        window.close()
        assert_no_threads_left(window)


def test_unfolding_counts_what_the_fold_set_aside(qt_app):
    """Room that only exists because the rows are set aside is not room to
    unfold into: unfolding brings them back, and the fold would come again."""
    window = many_clips_window(qt_app, BrowserMode.NORMAL, (1120, 760))
    panel, view = window.browser_panel, window.preview_view
    try:
        window.show()
        view.music_band.setChecked(True)
        qt_app.processEvents()
        window._set_list_folded(True)
        # Held folded for the check: with the compact controls (9 October)
        # this window has room, and the next pass would unfold it.
        window._fold_need = 10 ** 6
        qt_app.processEvents()
        window._fold_need = 1
        box, body = view.preview_box, view.music_body
        spare = (max(0, box.height() - box.content_floor())
                 + max(0, body.height() - body.minimumHeight()))
        # Enough for the list alone, not for the list and what came back.
        window._fold_need = max(1, spare)
        window._music_disclosed = 1
        window._check_list_fold()
        assert panel.folded, "unfolded into room that the rows would retake"
        window._music_disclosed = 0
        window._check_list_fold()
        assert not panel.folded
    finally:
        view.music_band.setChecked(False)
        window.close()
        assert_no_threads_left(window)


# -- The Remux caveat beside the picture gets the height its text needs --------

# The windows below are isolated as the music-wiring ones are, before they
# exist: settings (conftest), a disposable home for the sessions and the
# thumbnail and strip caches, a disposable card and output folder, no encoder
# probe or release check (no_background_work), no scan thread, no strip decode
# and no thumbnail requests. A window built by hand outside these wrote to the
# real settings and caches; offscreen is a platform, not a guard.


class _NoStrip(QThread):
    """A FilmstripLoader that decodes nothing (as in test_music_wiring)."""

    ready = Signal(object)
    activity_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, *args, **kwargs):
        parent = args[3] if len(args) > 3 else kwargs.get("parent")
        super().__init__(parent)

    def start(self, *_args) -> None:
        self.finished.emit()

    def isRunning(self) -> bool:  # noqa: N802 (Qt naming)
        return False

    def stop(self) -> None:
        pass

    def wait(self, *_args) -> bool:
        return True


class _NoScan(QObject):
    """A ScanWorker that starts no thread (as in test_music_wiring)."""

    counted = Signal(int, int)
    found = Signal(int, object)
    done = Signal(int, int)

    def __init__(self, tools, folder, recursive, generation, parent=None):
        super().__init__(parent)

    def start(self) -> None:
        pass

    def isRunning(self) -> bool:  # noqa: N802 (Qt naming)
        return False

    def stop(self) -> None:
        pass

    def wait(self, *_args) -> bool:
        return True


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


@pytest.fixture
def isolated_controls(qt_app, isolated_home, monkeypatch):
    """The isolation for controls that build their own window through
    `many_clips_window`, which adds clips straight after construction: so
    everything, thumbnails included, is in place before the window exists.
    Settings (conftest), home (isolated_home), no encoder probe or release
    check (no_background_work), no scan thread, no strip decode, and no
    thumbnail request, patched on the class because there is no window yet
    to patch it on.

    It also owns the windows those controls build. The helper constructs the
    window and then goes on to size, show and fill it before the test's own
    try/finally begins, so a failure in between left a window nobody closed.
    Each window is registered the moment its constructor returns, and one the
    test did not close is closed here, and checked for threads, while every
    guard above is still in place: this runs before monkeypatch undoes them.
    """
    from flightdvr.ui import MainWindow

    monkeypatch.setattr("flightdvr.ui.ScanWorker", _NoScan)
    monkeypatch.setattr("flightdvr.ui.FilmstripLoader", _NoStrip)
    monkeypatch.setattr("flightdvr.thumbs.ThumbnailLoader.request",
                        lambda self, *_a, **_k: None)
    built = []
    construct = MainWindow.__init__

    def registered(self, *args, **kwargs):
        construct(self, *args, **kwargs)
        built.append(self)

    monkeypatch.setattr(MainWindow, "__init__", registered)
    try:
        yield isolated_home
    finally:
        # Every window is attempted, whatever an earlier one does; the first
        # failure is raised once they all have been.
        failures = []
        for window in built:
            try:
                if not window._closing:
                    window.close()
            except Exception as exc:
                failures.append(exc)
        qt_app.processEvents()
        for window in built:
            try:
                assert_no_threads_left(window)
            except AssertionError as exc:
                failures.append(exc)
        if failures:
            raise failures[0]


@pytest.fixture
def isolated_window(qt_app, tmp_path, isolated_home, monkeypatch):
    """A window whose every write lands under `tmp_path`, closed however the
    test ends, with the guards still in place until it has."""
    from dataclasses import replace

    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow

    monkeypatch.setattr("flightdvr.ui.ScanWorker", _NoScan)
    monkeypatch.setattr("flightdvr.ui.FilmstripLoader", _NoStrip)
    card = tmp_path / "card"
    card.mkdir()
    out = tmp_path / "out"
    out.mkdir()

    made = MainWindow(find_tools())
    try:
        monkeypatch.setattr(made.thumbs, "request", lambda *_: None)
        monkeypatch.setattr(made.player, "load", lambda *a, **k: None)
        made.source_combo.insertItem(0, str(card), str(card))
        made.source_combo.setCurrentIndex(0)
        made.export_panel.out_edit.setCurrentText(str(out))
        made.resize(1240, 900)
        made.show()
        qt_app.processEvents()
        for index, name in enumerate(("hdz_001.ts", "hdz_002.ts", "hdz_003.ts")):
            made._add_clip(made._scan_generation,
                           replace(a_clip(name, 100.0 + index), path=card / name))
        qt_app.processEvents()
        yield made
    finally:
        made.close()
        qt_app.processEvents()
        assert_no_threads_left(made)



# -- When the layout work a transition queued has finished ---------------------
#
# The caveat has to be readable once the preset change's layout work is done,
# not at whatever moment a helper happens to return. `settle()` returns when
# the preview's height repeats, or when its passes run out, and says nothing
# about the window: measured natively, the caveat was cut at that point and
# whole at the next recorded one, the window having grown in between.
#
# What "done" means here is read off the source, not timed. Everything this
# window defers, it defers through `QTimer.singleShot(ms, callable)`, and only
# two modules do it: `flightdvr.ui` (the relayout's five zero-delay calls, the
# minimum held on every layout request, the picture's fit retried at 40 ms, the
# list fold at 60, the Music room at 120, the list-place restore at 20) and
# `flightdvr.preview_panel` (the note that says what stops the music, kept
# in view). There are no queued
# connections, posted events, animations or basic timers in the package. One
# timer object also matters: the selection timer reloads the clip a quarter
# of a second after a row change.
#
# So the tests below count those deferred calls, by standing a counting
# subclass in for `QTimer` in those two modules (nothing in the product is
# changed, and a call made in any other form is not queued at all and refuses
# completion),
# and the work is finished when all of this holds at once: no counted call is
# still to run, including any a call queued in its turn; the selection timer
# is not pending; and Qt's event loop has just reported that it had nothing
# left to handle. Neither a number of passes nor two equal sizes nor a wait
# is taken for completion. The watchdog only ever fails.
#
# Two things it must never do. A call that raised has run, but it has not done
# its work: its failure is kept for good, and an empty queue after it attests
# nothing. And a call still waiting when a test ends, however it ends, must not
# outlive it: the observer is closed before the window and its guards are let
# go, and a closed observer runs nothing it was handed and queues nothing new.
# What it abandoned is recorded as abandoned, never as finished.


class LayoutNotComplete(AssertionError):
    """The queued layout work did not finish; nothing may be asserted of it."""


class LayoutWork:
    """Counts the window's deferred layout calls, from before it is built,
    and owns them until it is closed."""

    def __init__(self) -> None:
        from PySide6.QtCore import QTimer

        self.outstanding: dict[int, str] = {}
        self.scheduled = 0
        self.untracked: list[str] = []
        self.failed: list[tuple[str, BaseException]] = []
        self.abandoned: list[str] = []
        self.refused: list[str] = []
        self.closed = False
        self._calls: dict[int, object] = {}
        work = self

        class Counted(QTimer):
            """QTimer, with singleShot(ms, callable) counted and owned."""

            @staticmethod
            def singleShot(*args):  # noqa: N802 (Qt naming)
                if work.closed:
                    work.refused.append(repr(args))
                    return
                if len(args) != 2 or not callable(args[1]):
                    # Not a form this can own, so it is not queued at all.
                    work.untracked.append(repr(args))
                    return
                delay, callback = args
                token = work.scheduled
                work.scheduled += 1
                name = (f"#{token} "
                        f"{getattr(callback, '__qualname__', type(callback).__name__)}"
                        f" @{delay}ms")
                work.outstanding[token] = name
                work._calls[token] = callback
                try:
                    # Qt is handed the token, not the callback: closing the
                    # observer leaves it nothing to call.
                    QTimer.singleShot(delay, lambda: work.dispatch(token))
                except Exception as error:
                    del work._calls[token], work.outstanding[token]
                    work.failed.append((f"{name} (could not be queued)", error))
                    raise

        self.timer = Counted

    def dispatch(self, token: int) -> None:
        """What Qt calls when a counted call comes due."""
        callback = self._calls.pop(token, None)
        if callback is None:
            return  # abandoned when the observer closed, and recorded there
        # No longer waiting before it runs, so that whatever it queues in its
        # turn is counted as waiting after it.
        name = self.outstanding.pop(token)
        try:
            callback()
        except Exception as error:
            # Kept here and not left to however Qt reports an error raised
            # under its event loop: see held().
            self.failed.append((name, error))

    def close(self) -> None:
        """Let go of every call still waiting. None of them runs after this,
        and nothing more is queued."""
        if self.closed:
            return
        self.closed = True
        self.abandoned = [self.outstanding[token] for token in sorted(self._calls)]
        self._calls.clear()

    def held(self) -> list[str]:
        """Why this work can never be shown to have finished. Nothing here is
        ever cleared."""
        return ([f"failed: {name}: {error!r}" for name, error in self.failed]
                + [f"abandoned, never run: {name}" for name in self.abandoned]
                + [f"made in a form that is not counted, and not queued: {call}"
                   for call in self.untracked])

    def verdict(self) -> None:
        held = self.held()
        if held:
            raise LayoutNotComplete(
                f"layout work failed or was abandoned: {held}"
            ) from (self.failed[0][1] if self.failed else None)


@pytest.fixture
def layout_work(monkeypatch):
    from flightdvr import ui

    work = LayoutWork()
    monkeypatch.setattr("flightdvr.ui.QTimer", work.timer)
    monkeypatch.setattr("flightdvr.preview_panel.QTimer", work.timer)

    # The selection timer is waited for too, and it calls this from Qt.
    load = ui.MainWindow._load_selected_clip

    def load_recording_failure(window, *args, **kwargs):
        try:
            return load(window, *args, **kwargs)
        except Exception as error:
            work.failed.append(("the selected clip's load", error))
            raise

    monkeypatch.setattr(ui.MainWindow, "_load_selected_clip",
                        load_recording_failure)
    yield work
    # Before monkeypatch gives the real home and settings back.
    work.close()


@pytest.fixture
def spare_work():
    """An observer of its own for the cases below, which break it on purpose:
    closed however the test ends, and before the window is."""
    work = LayoutWork()
    yield work
    work.close()


@pytest.fixture
def tracked_window(layout_work, isolated_window):
    """The isolated window, built after the counting was in place. Its
    deferred calls are let go of before it is closed, and a test that left
    any failed or unrun does not pass."""
    try:
        # Showing a window queues at least the held minimum; none counted
        # would mean the window was built before its calls could be seen.
        assert layout_work.scheduled > 0, (
            "the window's deferred calls are not counted")
        yield isolated_window
    finally:
        layout_work.close()
    layout_work.verdict()


def complete_layout(qt_app, window, work, watchdog_ms: int = 5000) -> int:
    """Run the event loop until the queued layout work has finished.

    Returns the passes it took. Raises `LayoutNotComplete`, with what was
    still waiting, if the watchdog runs out first, and at once, every time,
    for work that has failed, been abandoned or been closed: it never returns
    because time has passed, nor because a queue is empty after a failure.
    """
    from PySide6.QtCore import QEventLoop

    loop = QEventLoop()
    deadline = time.monotonic() + watchdog_ms / 1000
    passes = handled_passes = 0
    waiting: list[str] = []

    def refuse(why: str):
        raise LayoutNotComplete(
            f"{why}: held={work.held()}, closed={work.closed}, "
            f"waiting={waiting}, passes={passes} of which {handled_passes} "
            f"handled events, window={window.size()}, "
            f"minimum={window.minimumHeight()}"
        ) from (work.failed[0][1] if work.failed else None)

    while True:
        if work.closed or work.held():
            refuse("this layout work can not be shown to have finished")
        try:
            handled = loop.processEvents(QEventLoop.ProcessEventsFlag.AllEvents)
        except Exception as error:
            work.failed.append(("raised out of the event loop", error))
            continue
        passes += 1
        handled_passes += bool(handled)
        waiting = list(work.outstanding.values())
        if window._select_timer.isActive():
            waiting.append("the selection timer (reloads the clip)")
        if not handled and not waiting and not work.held():
            return passes
        if time.monotonic() > deadline:
            refuse(f"layout work still queued after {watchdog_ms} ms")


def test_queued_layout_work_is_never_taken_for_finished(tracked_window,
                                                       layout_work,
                                                       spare_work, qt_app):
    """Work that keeps queueing itself is refused, by name, and accepted only
    once it has actually stopped."""
    window = tracked_window
    complete_layout(qt_app, window, layout_work)
    stopped = []

    def queues_itself_again():
        if not stopped:
            spare_work.timer.singleShot(0, queues_itself_again)

    try:
        spare_work.timer.singleShot(0, queues_itself_again)
        with pytest.raises(LayoutNotComplete) as refused:
            complete_layout(qt_app, window, spare_work, watchdog_ms=250)
        assert "queues_itself_again" in str(refused.value)

        stopped.append(True)
        complete_layout(qt_app, window, spare_work)
        assert spare_work.outstanding == {}
    finally:
        stopped.append(True)


def test_a_layout_call_that_fails_is_never_taken_for_finished(tracked_window,
                                                             layout_work,
                                                             spare_work,
                                                             qt_app):
    """A call that raised has left the queue without doing its work. The
    queue being empty afterwards shows nothing, then or later."""
    window = tracked_window
    complete_layout(qt_app, window, layout_work)

    def fails_part_way():
        raise RuntimeError("the layout call failed")

    spare_work.timer.singleShot(0, fails_part_way)
    with pytest.raises(LayoutNotComplete) as refused:
        complete_layout(qt_app, window, spare_work)
    assert "fails_part_way" in str(refused.value)
    assert isinstance(refused.value.__cause__, RuntimeError)

    # Nothing is waiting and the event loop is idle: still not finished.
    assert spare_work.outstanding == {}
    qt_app.processEvents()
    with pytest.raises(LayoutNotComplete) as again:
        complete_layout(qt_app, window, spare_work)
    assert "fails_part_way" in str(again.value)
    with pytest.raises(LayoutNotComplete):
        spare_work.verdict()


def test_layout_work_let_go_of_never_runs_and_is_not_finished(tracked_window,
                                                             layout_work,
                                                             qt_app):
    """Calls still waiting when a test gives up, one delayed and one that
    queues itself again, are recorded as abandoned, run nothing when Qt
    brings them due, queue nothing more, and are never reported finished."""
    window = tracked_window
    complete_layout(qt_app, window, layout_work)
    own = LayoutWork()
    ran = []

    def comes_due_late():
        ran.append("late")

    def queues_itself_again():
        ran.append("again")
        own.timer.singleShot(0, queues_itself_again)

    try:
        own.timer.singleShot(200, comes_due_late)
        own.timer.singleShot(200, queues_itself_again)
        with pytest.raises(LayoutNotComplete):
            complete_layout(qt_app, window, own, watchdog_ms=20)
    finally:
        own.close()
    assert ran == [], "the fixture: a call came due before the watchdog"
    assert len(own.abandoned) == 2
    assert "comes_due_late" in own.abandoned[0]
    assert "queues_itself_again" in own.abandoned[1]

    # What Qt does when each comes due, after the close.
    own.dispatch(0)
    own.dispatch(1)
    assert ran == [] and own.scheduled == 2

    own.timer.singleShot(0, comes_due_late)
    assert own.scheduled == 2 and len(own.refused) == 1
    with pytest.raises(LayoutNotComplete):
        complete_layout(qt_app, window, own)
    with pytest.raises(LayoutNotComplete):
        own.verdict()


REMUX_KEYFRAME_WARNING = ("Remux cuts at keyframes, so a trimmed rewrap can be "
                          "a second out. The re-encoding presets are exact.")


def test_the_remux_caveat_is_sized_by_its_height_for_width(isolated_window):
    """dim() sets a fresh size policy, which drops word wrap's
    height-for-width; the caveat's text is never set again to restore it."""
    note = isolated_window.export_panel.remux_keyframe_note
    assert note.wordWrap()
    assert note.sizePolicy().hasHeightForWidth()


def whole_and_inside(label, side, box) -> list[str]:
    """What is wrong with how `label` is shown, if anything."""
    from PySide6.QtCore import QPoint, QRect

    problems = []
    needed = label.heightForWidth(label.width())
    if label.height() < needed:
        problems.append(f"{label.height()}px given, {needed} needed at "
                        f"{label.width()}px")
    if not side.rect().contains(QRect(label.mapTo(side, QPoint(0, 0)),
                                      label.size())):
        problems.append("outside the side column")
    if not box.contentsRect().contains(QRect(label.mapTo(box, QPoint(0, 0)),
                                             label.size())):
        problems.append("outside the preview box")
    shown = sum(r.width() * r.height() for r in label.visibleRegion())
    if shown != label.width() * label.height():
        problems.append(f"{shown} of {label.width() * label.height()} px² shown")
    return problems


def shown_whole(label) -> list[str]:
    """What is wrong with how `label` is shown, if anything, wherever it is:
    its own full height for its width, and every pixel of it on screen."""
    problems = []
    needed = label.heightForWidth(label.width())
    if label.height() < needed:
        problems.append(f"{label.height()}px given, {needed} needed at "
                        f"{label.width()}px")
    shown = sum(r.width() * r.height() for r in label.visibleRegion())
    if shown != label.width() * label.height():
        problems.append(f"{shown} of {label.width() * label.height()} px² shown")
    return problems


def shown_within_its_viewport(label) -> list[str]:
    """Offscreen only (see its one use): what is wrong with how `label` is
    shown, bounded by the viewport of the scroll area it is in. Its own full
    height for its width, as everywhere; and what is shown of it must be
    exactly its rectangle cut to that viewport, nothing more and no holes,
    not empty, and with every row of it (the cut may only be at the sides).
    This is visibility within the viewport, not the whole text rendered."""
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QRegion
    from PySide6.QtWidgets import QScrollArea

    problems = []
    needed = label.heightForWidth(label.width())
    if label.height() < needed:
        problems.append(f"{label.height()}px given, {needed} needed at "
                        f"{label.width()}px")
    area = label.parentWidget()
    while area is not None and not isinstance(area, QScrollArea):
        area = area.parentWidget()
    if area is None:
        return problems + ["not inside a scroll area"]
    viewport = area.viewport()
    # All in the label's own coordinates.
    own = QRect(QPoint(0, 0), label.size())
    port = QRect(label.mapFrom(viewport, QPoint(0, 0)), viewport.size())
    expected = own.intersected(port)
    if expected.isEmpty():
        problems.append("none of it inside the viewport")
    elif label.visibleRegion() != QRegion(expected):
        shown = [[r.x(), r.y(), r.width(), r.height()]
                 for r in label.visibleRegion()]
        problems.append(f"shown {shown} is not its rectangle within the viewport "
                        f"{[expected.x(), expected.y(), expected.width(), expected.height()]}")
    if expected.top() != 0 or expected.height() != label.height():
        problems.append(f"rows outside the viewport: {expected.y()}+"
                        f"{expected.height()} of {label.height()}")
    return problems


def where_shown(label) -> list:
    """The label and each ancestor up to its window, in window coordinates:
    its rectangle, its minimum size hint, and the rectangles of it that are
    visible. Evidence for a failure message; nothing asserts on it."""
    from PySide6.QtCore import QPoint

    window = label.window()
    rows, widget = [], label
    while widget is not None:
        at = (widget.mapTo(window, QPoint(0, 0)) if widget is not window
              else QPoint(0, 0))
        hint = widget.minimumSizeHint()
        rows.append((type(widget).__name__,
                     [at.x(), at.y(), widget.width(), widget.height()],
                     [hint.width(), hint.height()],
                     [[r.x() + at.x(), r.y() + at.y(), r.width(), r.height()]
                      for r in widget.visibleRegion()]))
        if widget is window:
            break
        widget = widget.parentWidget()
    return rows


@pytest.mark.parametrize("size", [(1120, 760), (1440, 913)])
def test_the_remux_caveat_is_shown_whole_with_the_remux_options(
        tracked_window, layout_work, qt_app, size):
    """Beside the picture the caveat's four lines raised the picture's floor,
    and natively at 1120x760 the window grew to 812 to show them. With the
    Remux options it is shown whole, with all its words, and choosing Remux
    asks nothing more of the window. The note beside the picture stays whole."""
    window = tracked_window
    view = window.preview_view
    window.resize(*size)
    window.browser_panel.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    window.export_panel.preset_buttons["social"].click()
    complete_layout(qt_app, window, layout_work)
    side, box = view.sidebar, view.preview_box
    keys, caveat = view.focus_note, window.export_panel.remux_keyframe_note
    before = (window.minimumHeight(), window.size())
    assert not caveat.isVisible()

    window.export_panel.preset_buttons["remux"].click()
    # What the earlier checkpoint saw, kept for the record and not asserted:
    # `settle()` returning is not a claim that anything has stopped moving.
    settle(qt_app, window.preview_box)
    when_settle_returned = shown_whole(caveat)
    complete_layout(qt_app, window, layout_work)
    assert caveat.isVisible()
    assert caveat.text() == REMUX_KEYFRAME_WARNING
    # On Qt's offscreen platform only, the export column's scroll content can
    # be wider than its viewport, and it does not scroll sideways: measured
    # (R-OFFSCREEN-1) at the offscreen window's minimum width, the whole
    # column was cut on the right, the warning with it, while natively it is
    # shown whole. So offscreen its visibility is held to the viewport, every
    # row and nothing missing inside it; everywhere else, and in the native
    # checks, it must be shown whole. This is not whole-text acceptance.
    from PySide6.QtWidgets import QApplication

    if QApplication.platformName() == "offscreen":
        problems = shown_within_its_viewport(caveat)
    else:
        problems = shown_whole(caveat)
    # One string, so that pytest prints all of it rather than a shortened repr.
    assert problems == [], repr((
        window.size(), QApplication.platformName(),
        {"when settle() returned": when_settle_returned,
         "where shown": where_shown(caveat)}))
    assert whole_and_inside(keys, side, box) == [], window.size()
    assert (window.minimumHeight(), window.size()) == before

    # Control: away from Remux the caveat goes, and the note stays whole.
    window.export_panel.preset_buttons["social"].click()
    complete_layout(qt_app, window, layout_work)
    assert not caveat.isVisible()
    assert whole_and_inside(keys, side, box) == []


# -- After Remux, Classic's column still holds the picture ---------------------


def remux_at(window, qt_app, work, size):
    """Classic at `size`, a clip loaded, Remux chosen: its keyframe warning
    shown with its options, and the layout work each step queued finished."""
    window.resize(*size)
    window.browser_panel.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    complete_layout(qt_app, window, work)
    window.export_panel.preset_buttons["remux"].click()
    complete_layout(qt_app, window, work)


def column_holds_the_picture(window) -> bool:
    column = window._left_column
    box = window.preview_view.preview_box
    return column.height() >= box.geometry().bottom() + 1


@pytest.mark.parametrize("size", [(1120, 760), (1440, 913)])
def test_a_picture_at_its_floor_is_given_the_room_it_needs(tracked_window,
                                                         layout_work,
                                                         qt_app, size):
    """With Remux chosen the picture cannot be shorter than the controls
    beside it. The column must still hold the whole picture, and the window's
    contents must have their minimum."""
    window = tracked_window
    remux_at(window, qt_app, layout_work, size)
    assert column_holds_the_picture(window), (
        window._left_column.height(),
        window.preview_view.preview_box.geometry().bottom() + 1)
    central = window.centralWidget()
    assert central.height() >= central.minimumSizeHint().height()


def test_layout_requests_do_not_ratchet_the_window_up(tracked_window,
                                                     layout_work, qt_app):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    window = tracked_window
    remux_at(window, qt_app, layout_work, (1120, 760))
    before = (window.minimumHeight(), window.size())
    for _ in range(5):
        QApplication.postEvent(window, QEvent(QEvent.Type.LayoutRequest))
        complete_layout(qt_app, window, layout_work)
    assert (window.minimumHeight(), window.size()) == before


def test_remux_and_back_ask_nothing_more_of_the_window(
        tracked_window, layout_work, qt_app):
    """Social, then Remux, then Social again: the same preset, the same list,
    the same clip. Beside the picture Remux's caveat raised the minimum,
    natively from 760 to 812, and the window grew to show it. With the Remux
    options it asks nothing: the minimum and the size stay what Social had,
    and going back leaves exactly the state the baseline was taken in."""
    window = tracked_window
    presets = window.export_panel.preset_buttons
    note = window.export_panel.remux_keyframe_note

    def state():
        return (window.export_panel.preset_key(), window._layout_state.browser,
                window.browser_panel.table.currentRow(),
                note.isVisible(), window.width())

    window.resize(1120, 760)
    window.browser_panel.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    presets["social"].click()
    complete_layout(qt_app, window, layout_work)
    baseline_state, baseline = state(), window.minimumHeight()
    size = window.size()
    assert baseline_state[0] == "social" and not note.isVisible()

    presets["remux"].click()
    complete_layout(qt_app, window, layout_work)
    assert note.isVisible()
    assert (window.minimumHeight(), window.size()) == (baseline, size)
    assert column_holds_the_picture(window)

    presets["social"].click()
    complete_layout(qt_app, window, layout_work)
    assert state() == baseline_state, "not the state the baseline was taken in"
    assert (window.minimumHeight(), window.size()) == (baseline, size)
    assert column_holds_the_picture(window)


# -- Nk's tested candidate, 9 October: room for the list and for Music -------
#
# Each of these failed on the tested candidate (39fe1ee): an open band left at
# its track row with Level below the fold, a controls column whose full height
# set the picture's floor, a four-line card-clock note, an export column as
# tall as its tallest preset page, and a trim drag that silenced playing sound
# whether or not the range changed.


def _wait(qt_app, seconds: float = 0.8) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt_app.processEvents()
        time.sleep(0.01)


def test_open_music_keeps_its_working_rows_in_view(qt_app):
    window = many_clips_window(qt_app, BrowserMode.NORMAL, (1160, 880))
    view = window.preview_view
    try:
        view.music_band.setChecked(True)
        _wait(qt_app)
        body = view.music_body
        viewport = body.viewport().rect()
        for widget in (view.track_button, view.focus_button, view.listen_level):
            corner = widget.mapTo(body.viewport(), widget.rect().bottomRight())
            assert viewport.contains(corner), f"{widget.objectName() or widget} below the fold"
    finally:
        view.music_band.setChecked(False)
        window.close()
        assert_no_threads_left(window)


def test_music_open_compacts_the_controls_and_lowers_the_picture_floor(qt_app):
    window = many_clips_window(qt_app, BrowserMode.NORMAL, (1160, 880))
    view = window.preview_view
    box = view.preview_box
    try:
        _wait(qt_app, 0.4)
        floor_closed = box.content_floor()
        view.music_band.setChecked(True)
        _wait(qt_app)
        assert box.content_floor() < floor_closed
        # Every control stays.
        assert view.play_button.isVisible() and view.sound_button.isVisible()
        assert view.still_button.isVisible()
        assert all(button.isVisible() for button, _tip in view._source_edits)
        view.music_band.setChecked(False)
        _wait(qt_app)
        assert box.content_floor() == floor_closed
    finally:
        view.music_band.setChecked(False)
        window.close()
        assert_no_threads_left(window)


def test_the_card_clock_note_takes_one_line(qt_app):
    window = many_clips_window(qt_app, BrowserMode.NORMAL, (1160, 880))
    note = window.warning_label
    try:
        text = ("All 14 clips are stamped 08 Oct 2025 within 39 minutes of each "
                "other, which is not when they were filmed. These goggles have a "
                "socket for a CR2032 clock battery but none fitted, so the clock "
                "restarts from the same value on every power-up.")
        note.setText(text)
        note.show()
        _wait(qt_app, 0.4)
        assert note.height() <= note.fontMetrics().height() + 6
        assert note.toolTip() == text and note.text() == text
    finally:
        window.close()
        assert_no_threads_left(window)


def test_the_export_options_take_the_height_of_the_page_shown(qt_app):
    window = many_clips_window(qt_app, BrowserMode.NORMAL, (1160, 880))
    stack = window.export_panel.options_stack
    try:
        tallest = max(stack.widget(i).sizeHint().height() for i in range(stack.count()))
        window.export_panel.preset_buttons["master"].click()
        _wait(qt_app, 0.4)
        shown = stack.currentWidget().sizeHint().height()
        assert shown < tallest
        assert stack.height() <= shown + 2
    finally:
        window.close()
        assert_no_threads_left(window)


def test_a_trim_that_changes_nothing_leaves_sound_alone_and_a_change_pauses(
        qt_app, monkeypatch):
    window = many_clips_window(qt_app, BrowserMode.NORMAL, (1160, 880))
    try:
        window.browser_panel.table.setCurrentCell(0, 0)
        window._load_selected_clip()
        _wait(qt_app, 0.3)
        clip = window._trim_clip
        assert clip is not None
        fenced, paused = [], []
        monkeypatch.setattr(window, "_silence_monitoring",
                            lambda reason, **kw: fenced.append(reason))
        monkeypatch.setattr(window.player, "pause", lambda: paused.append(True))
        window.player.is_playing = True
        window._on_trim_changed(0.0, clip.duration)          # the whole clip
        assert fenced == [] and paused == [], "a no-op trim silenced playback"
        window._on_trim_changed(5.0, clip.duration - 5.0)    # a real change
        assert len(fenced) == 1 and paused == [True]
        window.player.is_playing = False
    finally:
        window.close()
        assert_no_threads_left(window)


def test_height_the_band_took_for_an_instant_is_given_back(own_window, qt_app):
    """Natively at 1490x880 with the list collapsed, Qt's last layout pass
    applied a stale minimum as Music opened and the window grew 5 px with
    everything settled at a 650 px minimum (Sol R3 follow-up, 10 October).
    The layout's growth lands exactly on the minimum it applied; that, and
    only that, is given back."""
    window = own_window
    view = window.preview_view
    window.set_browser_mode(BrowserMode.COLLAPSED)
    view.music_band.setChecked(True)
    _wait(qt_app)
    try:
        was = window.size()
        least = window.minimumHeight()
        assert window.minimumSizeHint().height() < was.height()
        window._band_growth = {"grown": None, "moved": False}
        # Growth the way the layout makes it: to the minimum it applies.
        window.setMinimumHeight(was.height() + 5)
        _wait(qt_app, 0.3)
        assert window.height() == was.height() + 5
        window.setMinimumHeight(least)
        window._give_back_band_growth(was)
        _wait(qt_app, 0.3)
        assert window.size() == was
    finally:
        view.music_band.setChecked(False)
        window.set_browser_mode(BrowserMode.NORMAL)


@pytest.mark.parametrize("wider", [False, True], ids=["height-only", "width-and-height"])
def test_a_resize_made_while_music_opens_is_left_alone(own_window, qt_app, wider):
    """Sol R4 (10 October): a height-only resize made before the growth check
    ran (1490x880 to 1490x980) was put back to 880. Someone resizing the
    window is never undone, height-only or not."""
    window = own_window
    view = window.preview_view
    window.set_browser_mode(BrowserMode.COLLAPSED)
    _wait(qt_app)
    try:
        was = window.size()
        view.music_band.setChecked(True)
        qt_app.processEvents()
        wanted = (was.width() + (40 if wider else 0), was.height() + 100)
        window.resize(*wanted)                      # before the pending check
        _wait(qt_app, 0.9)                          # past MUSIC_GROWTH_CHECK_MS
        assert (window.width(), window.height()) == wanted
    finally:
        view.music_band.setChecked(False)
        window.set_browser_mode(BrowserMode.NORMAL)


def test_a_settings_file_override_keeps_a_check_off_the_real_settings(
        qt_app, tmp_path, monkeypatch):
    """FLIGHTDVR_SETTINGS_FILE puts the settings in that INI file, so a
    packaged candidate can be checked without the registry (10 October)."""
    from PySide6.QtCore import QSettings
    import flightdvr.ui as ui
    path = tmp_path / "candidate-settings.ini"
    monkeypatch.setattr(ui, "QSettings", QSettings)    # the real class
    monkeypatch.setenv("FLIGHTDVR_SETTINGS_FILE", str(path))
    store = ui._settings_store()
    assert store.format() == QSettings.Format.IniFormat
    assert Path(store.fileName()) == path
    store.setValue("probe", 1)
    store.sync()
    assert path.is_file()
