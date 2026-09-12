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
from PySide6.QtCore import QObject, Signal

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


# -- the preview height cap ----------------------------------------------------
#
# Authorized as a bounded delta to `widgets.PreviewPanel` after the splitter
# alone was measured giving Expanded nothing: at the sizes this window opens
# at, the left column is already at its own minimum width.


def test_no_cap_is_exactly_todays_sizing(window, qt_app):
    """The default has to cost nothing, or every other window changes too."""
    window.set_browser_mode(BrowserMode.NORMAL)
    qt_app.processEvents()
    box = window.preview_box
    assert box._height_cap is None
    assert box.height() == box.useful_height(box.width())


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
    assert settled_expanded >= window.preview_box.content_floor()


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
