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

"""Several ranges out of one recording, and one job for each.

The design claim this has to hold up is that nothing downstream changes: the
presets, the jobs and the export path go on believing a recording has exactly
one in point and one out point, because at queueing a clip with three selects
becomes three clips with one each.

So most of what is worth testing is that the fan-out produces ordinary clips,
and that trim_in still means what it always meant.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from flightdvr.format import safe_name, select_stem
from flightdvr.media import ClipInfo, Select


def clip(name="hdz_047.ts", duration=240.0) -> ClipInfo:
    return ClipInfo(
        path=Path(name), size=599_189_652,
        modified=datetime(2025, 10, 8, 18, 39),
        duration=duration, width=1280, height=720, fps=60.0,
        video_codec="hevc", audio_codec="aac",
        pix_fmt="yuvj420p", color_range="pc",
    )


# -- trim_in still means what it meant ----------------------------------------

def test_a_clip_nobody_touched_is_untrimmed():
    fresh = clip()
    assert fresh.trim_in == 0.0 and fresh.trim_out == 0.0
    assert not fresh.is_trimmed
    assert fresh.selects == []


def test_setting_a_trim_makes_the_first_select():
    edited = clip()
    edited.trim_in, edited.trim_out = 12.0, 30.0
    assert edited.is_trimmed
    assert [(s.start, s.end) for s in edited.selects] == [(12.0, 30.0)]


def test_trim_in_reads_the_select_being_edited():
    """The pair is a view onto one row of the list, not a second copy of it —
    which is the whole reason there is nothing to keep in step."""
    several = clip()
    several.selects = [Select(10.0, 40.0), Select(90.0, 120.0)]

    several.current = 0
    assert several.trim_in == 10.0 and several.trim_out == 40.0
    several.current = 1
    assert several.trim_in == 90.0 and several.trim_out == 120.0

    several.trim_in = 95.0
    assert several.selects[1].start == 95.0, "edited the wrong select"
    assert several.selects[0].start == 10.0, "edited a select nobody asked for"


def test_clearing_a_trim_is_not_a_range_covering_everything():
    """A whole-clip select is not a decision, and storing one would make a
    reset look like a choice somebody made."""
    edited = clip()
    edited.trim_in, edited.trim_out = 12.0, 30.0
    edited.trim_in = edited.trim_out = 0.0
    assert not edited.is_trimmed
    assert edited.real_selects == []


# -- the fan-out --------------------------------------------------------------

def test_three_selects_become_three_clips_with_the_right_points():
    flight = clip()
    flight.selects = [Select(10, 40, "Launch"), Select(90, 120, "Tree dive"),
                      Select(200, 230, "Landing")]

    pieces = flight.for_export()
    assert [(p.trim_in, p.out_point) for p in pieces] == [
        (10, 40), (90, 120), (200, 230)]
    assert all(len(p.selects) == 1 for p in pieces), (
        "a piece carried more than the one range it is for")


def test_a_clip_with_no_selects_exports_whole():
    whole = clip()
    pieces = whole.for_export()
    assert len(pieces) == 1
    assert pieces[0].trim_in == 0.0
    assert pieces[0].out_point == 240.0


def test_the_pieces_do_not_share_state_with_the_clip_they_came_from():
    """They are handed to jobs that outlive the selection, so an edit made
    afterwards must not reach into a queued export."""
    flight = clip()
    flight.selects = [Select(10, 40), Select(90, 120)]
    first, second = flight.for_export()

    flight.current = 0
    flight.trim_in = 999.0
    assert first.trim_in == 10, "editing the clip changed a queued piece"
    second.trim_in = 111.0
    assert flight.selects[1].start == 90, "editing a piece changed the clip"


def test_an_empty_select_does_not_produce_an_empty_export():
    """Clearing a trim leaves the row in place so the interface has something
    to point at. It must not become a zero-length job."""
    flight = clip()
    flight.selects = [Select(0.0, 0.0)]
    pieces = flight.for_export()
    assert len(pieces) == 1
    assert pieces[0].out_point == 240.0


# -- what the files get called ------------------------------------------------

def test_one_select_keeps_the_name_the_recording_always_had():
    """A clip trimmed the way every version until now trimmed it has to export
    to the filename it always did."""
    flight = clip()
    flight.selects = [Select(10, 40)]
    piece, = flight.for_export()
    assert select_stem(piece, 0, 1) == "hdz_047"


def test_several_selects_are_told_apart_by_number_and_name():
    flight = clip()
    flight.selects = [Select(10, 40, "Launch"), Select(90, 120, "Tree dive")]
    pieces = flight.for_export()
    names = [select_stem(p, i, len(pieces)) for i, p in enumerate(pieces)]
    assert names == ["hdz_047_1_Launch", "hdz_047_2_Tree-dive"]
    assert len(set(names)) == 2, "two selects would overwrite each other"


def test_unnamed_selects_still_get_distinct_files():
    flight = clip()
    flight.selects = [Select(10, 40), Select(90, 120), Select(200, 230)]
    pieces = flight.for_export()
    names = [select_stem(p, i, len(pieces)) for i, p in enumerate(pieces)]
    assert names == ["hdz_047_1", "hdz_047_2", "hdz_047_3"]


@pytest.mark.parametrize("typed, expected", [
    ("Tree dive!", "Tree-dive!"),
    ("gap #2", "gap-#2"),
    ('a/b\\c:d*e?f"g<h>i|j', "abcdefghij"),
    ("  spaces  everywhere  ", "spaces-everywhere"),
    ("trailing dot.", "trailing-dot"),
    ("", ""),
])
def test_a_typed_name_is_reduced_to_something_a_filesystem_accepts(typed,
                                                                   expected):
    """Names are typed by hand and go straight into a filename. Windows refuses
    several of these outright and silently strips a trailing dot."""
    assert safe_name(typed) == expected


def test_a_very_long_name_is_cut_rather_than_breaking_the_path():
    assert len(safe_name("x" * 300)) == 48


# -- what actually reaches the queue ------------------------------------------

@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    """A window with somewhere to write and nothing in the way of queueing."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)

    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow

    made = MainWindow(find_tools())
    made.export_panel.out_edit.setCurrentText(str(tmp_path / "out"))
    yield made
    made.close()


def queue_up(window, clips, join=False):
    """Tick the clips, add them, and report what landed in the queue."""
    from PySide6.QtCore import Qt

    for clip in clips:
        window._add_clip(window._scan_generation, clip)
    for row in range(window.table.rowCount()):
        window.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
    window.export_panel.join_check.setChecked(join)
    window.jobs.clear()
    window._add_to_queue()
    return window.jobs


def test_three_selects_of_one_clip_become_three_jobs(window):
    flight = clip()
    flight.selects = [Select(10, 40, "Launch"), Select(90, 120, "Tree dive"),
                      Select(200, 230, "Landing")]

    jobs = queue_up(window, [flight])
    assert len(jobs) == 3, [j.out_path.name for j in jobs]
    assert [(j.clips[0].trim_in, j.clips[0].out_point) for j in jobs] == [
        (10, 40), (90, 120), (200, 230)]
    assert len({j.out_path for j in jobs}) == 3, (
        "two jobs aimed at the same file, so one would overwrite the other")


def test_a_clip_with_one_select_queues_exactly_as_it_always_did(window):
    """The common case must not change: one job, and the filename it had
    before selects existed."""
    flight = clip()
    flight.trim_in, flight.trim_out = 30.0, 90.0

    jobs = queue_up(window, [flight])
    assert len(jobs) == 1
    assert jobs[0].out_path.name.startswith("hdz_047")
    assert "_1" not in jobs[0].out_path.stem
    assert (jobs[0].clips[0].trim_in, jobs[0].clips[0].out_point) == (30.0, 90.0)


def test_joined_selects_become_one_job_holding_them_in_order(window):
    flight = clip()
    flight.selects = [Select(200, 230, "Landing"), Select(10, 40, "Launch"),
                      Select(90, 120, "Tree dive")]

    jobs = queue_up(window, [flight], join=True)
    assert len(jobs) == 1, [j.out_path.name for j in jobs]
    assert [(c.trim_in, c.out_point) for c in jobs[0].clips] == [
        (10, 40), (90, 120), (200, 230)], "not in the order they occur"
