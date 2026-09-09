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

"""The length filter (#96) as the browser drives it.

`tests/test_clip_filter.py` covers the pure policy. This covers the controls
and the one promise that is easy to break and expensive to discover: filtering
hides rows and does nothing else. A filter that quietly unticked what it hid
would change what the next export contains, and nobody would see it happen.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Qt, Signal

from flightdvr.classic_layout import bound_text, bounds, hidden_summary, length_filter
from flightdvr.media import ClipInfo


def a_clip(name: str, duration: float) -> ClipInfo:
    return ClipInfo(
        path=Path(name), size=599_189_652,
        modified=datetime(2025, 10, 8, 18, 39),
        duration=duration, width=1280, height=720, fps=60.0,
        video_codec="hevc", audio_codec="aac",
        pix_fmt="yuvj420p", color_range="pc",
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


# -- the bounds, before any widget is involved ---------------------------------


def test_zero_is_how_a_spin_box_says_the_bound_is_off():
    assert bounds(0, 0) == (None, None)
    assert bounds(10, 0) == (10, None)
    assert bounds(0, 30) == (None, 30)
    assert bounds(10, 30) == (10, 30)


def test_a_maximum_under_the_minimum_is_raised_rather_than_hiding_everything():
    """Two boxes that each look reasonable can describe a range nothing is in.
    The pair is one control, so the maximum moves to meet the minimum."""
    assert bounds(30, 10) == (30, 30)


def test_both_bounds_are_inclusive():
    exactly = length_filter(10, 30, True)
    assert exactly.matches(a_clip("low.ts", 10.0))
    assert exactly.matches(a_clip("high.ts", 30.0))
    assert not exactly.matches(a_clip("under.ts", 9.999))
    assert not exactly.matches(a_clip("over.ts", 30.001))


def test_unknown_lengths_are_shown_until_that_is_explicitly_turned_off():
    """`ClipInfo.duration` is zero for a probe that read nothing, and the
    Length column shows ?. Those clips are not short, they are unread."""
    unread = a_clip("unread.ts", 0.0)
    assert length_filter(0, 0, True).matches(unread)
    assert length_filter(10, 0, True).matches(unread)
    assert not length_filter(10, 0, False).matches(unread)
    # No numeric bound at all, but the choice still has to bite.
    assert not length_filter(0, 0, False).matches(unread)


def test_the_reset_state_keeps_every_clip():
    reset = length_filter(0, 0, True)
    for duration in (0.0, 1.0, 10.0, 7200.0):
        assert reset.matches(a_clip("c.ts", duration)), duration


def test_length_and_review_compose_so_a_clip_has_to_pass_both():
    short_keep = a_clip("short_keep.ts", 4.0)
    long_keep = a_clip("long_keep.ts", 40.0)
    long_reject = a_clip("long_reject.ts", 40.0)
    long_reject.review = "reject"

    keeps = length_filter(10, 0, True,
                          review_filter=lambda c: c.review != "reject")
    assert keeps.matches(long_keep)
    assert not keeps.matches(short_keep)     # fails length, passes review
    assert not keeps.matches(long_reject)    # passes length, fails review


def test_the_wording_says_what_the_rule_is_and_nothing_when_it_is_off():
    assert bound_text(0, 0, True) == ""
    assert bound_text(10, 0, True) == "0:10 or longer"
    assert bound_text(0, 90, True) == "1:30 or shorter"
    assert bound_text(10, 90, True) == "0:10 to 1:30"
    assert "unknown lengths hidden" in bound_text(0, 0, False)


def test_the_hidden_count_says_how_much_of_it_the_length_rule_did():
    assert hidden_summary(10, 10, 0) == "10 shown"
    assert hidden_summary(10, 6, 4) == "6 of 10 shown · 4 hidden by length"
    assert hidden_summary(10, 6, 1) == "6 of 10 shown · 4 hidden, 1 of them by length"
    assert hidden_summary(10, 6, 0) == "6 of 10 shown · 4 hidden"


# -- the browser doing it ------------------------------------------------------


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(qt_app):
    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow
    made = MainWindow(find_tools())
    made.resize(1402, 900)
    made.show()
    qt_app.processEvents()
    for name, duration in (("hdz_001.ts", 4.0), ("hdz_002.ts", 40.0),
                           ("hdz_003.ts", 400.0), ("hdz_004.ts", 0.0)):
        made._add_clip(made._scan_generation, a_clip(name, duration))
    qt_app.processEvents()
    made.browser_panel.reset_length_filter()
    qt_app.processEvents()
    yield made
    made.close()
    assert_no_threads_left(made)


def visible_names(window) -> list[str]:
    table = window.browser_panel.table
    return [table.item(row, 0).text() for row in range(table.rowCount())
            if not table.isRowHidden(row)]


def test_nothing_is_hidden_until_a_bound_is_set(window):
    assert len(visible_names(window)) == 4


def test_a_minimum_hides_the_short_one_and_keeps_the_unread_one(window, qt_app):
    window.browser_panel.min_length.setValue(10)
    qt_app.processEvents()
    shown = visible_names(window)
    assert "hdz_001.ts" not in shown          # 4 s, genuinely too short
    assert "hdz_004.ts" in shown              # unread, not short
    assert {"hdz_002.ts", "hdz_003.ts"} <= set(shown)


def test_turning_off_unknown_lengths_hides_only_those(window, qt_app):
    window.browser_panel.show_unknown.setChecked(False)
    qt_app.processEvents()
    assert "hdz_004.ts" not in visible_names(window)
    assert "hdz_001.ts" in visible_names(window)


def test_the_hidden_count_is_shown_and_matches_the_rows(window, qt_app):
    window.browser_panel.min_length.setValue(50)
    qt_app.processEvents()
    text = window.browser_panel.hidden_label.text()
    assert f"{len(visible_names(window))} of 4 shown" in text
    assert "by length" in text


def test_reset_brings_everything_back(window, qt_app):
    window.browser_panel.min_length.setValue(100)
    window.browser_panel.show_unknown.setChecked(False)
    qt_app.processEvents()
    assert len(visible_names(window)) < 4

    window.browser_panel.reset_length_filter()
    qt_app.processEvents()

    assert len(visible_names(window)) == 4
    assert window.browser_panel.min_length.value() == 0
    assert window.browser_panel.max_length.value() == 0
    assert window.browser_panel.show_unknown.isChecked()


def test_the_length_filter_composes_with_the_show_box(window, qt_app):
    from flightdvr.browser_panel import FILTER_ALL
    from flightdvr.session import KEEP

    window.clips[1].review = KEEP          # hdz_002.ts, 40 s
    window.clips[2].review = KEEP          # hdz_003.ts, 400 s
    index = window.browser_panel.review_filter.findData(KEEP)
    window.browser_panel.review_filter.setCurrentIndex(index)
    window.browser_panel.min_length.setValue(100)
    qt_app.processEvents()

    shown = visible_names(window)
    assert shown == ["hdz_003.ts"], shown   # Keep and long enough

    window.browser_panel.review_filter.setCurrentIndex(
        window.browser_panel.review_filter.findData(FILTER_ALL))
    window.browser_panel.reset_length_filter()
    qt_app.processEvents()


def test_a_hidden_clip_stays_ticked_and_still_gets_exported(window, qt_app):
    """The promise that costs the most if it is broken.

    Filtering is a view. A clip you ticked and then filtered out of sight is
    still going to be exported, and hiding it must not quietly change that in
    either direction.
    """
    table = window.browser_panel.table
    for row in range(table.rowCount()):
        if table.item(row, 0).text() == "hdz_001.ts":
            table.item(row, 0).setCheckState(Qt.CheckState.Checked)
            break
    qt_app.processEvents()
    assert [c.path.name for c in window.selected_clips()] == ["hdz_001.ts"]

    window.browser_panel.min_length.setValue(10)   # hides hdz_001.ts
    qt_app.processEvents()

    assert "hdz_001.ts" not in visible_names(window)
    still = [c.path.name for c in window.selected_clips()]
    assert still == ["hdz_001.ts"], still

    window.browser_panel.reset_length_filter()
    qt_app.processEvents()
    assert "hdz_001.ts" in visible_names(window)


def test_filtering_changes_no_clip_review_range_or_job(window, qt_app):
    before = [(c.path, c.review, len(c.real_selects)) for c in window.clips]
    jobs_before = list(window.jobs)

    window.browser_panel.min_length.setValue(60)
    window.browser_panel.show_unknown.setChecked(False)
    qt_app.processEvents()
    window.browser_panel.reset_length_filter()
    qt_app.processEvents()

    after = [(c.path, c.review, len(c.real_selects)) for c in window.clips]
    assert after == before
    assert window.jobs == jobs_before
