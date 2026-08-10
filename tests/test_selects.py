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
from PySide6.QtWidgets import QApplication

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


# -- editing them in the window -----------------------------------------------

def loaded(window, flight):
    """A clip loaded into the preview the way selecting a row would."""
    window._add_clip(window._scan_generation, flight)
    window._trim_clip = flight
    window.trim_bar.set_clip(flight.duration, flight.trim_in, flight.out_point)
    window._show_selects()
    return flight


def test_adding_a_select_leaves_the_first_one_alone(window):
    flight = loaded(window, clip())
    flight.trim_in, flight.trim_out = 10.0, 40.0
    window.trim_bar.playhead = 90.0

    window._add_select()
    assert [(s.start, s.end) for s in flight.selects] == [(10.0, 40.0),
                                                          (90.0, 92.0)]
    assert flight.current == 1, "the new one is not the one being edited"


def test_a_new_select_is_a_range_not_a_point(window):
    """A zero-length select is not a range, and would be dropped the moment it
    was written — so adding one would look like nothing happened."""
    flight = loaded(window, clip())
    window.trim_bar.playhead = 100.0
    window._add_select()
    assert flight.real_selects, "the new select vanishes as soon as it is saved"


def test_adding_at_the_very_end_stays_inside_the_clip(window):
    flight = loaded(window, clip(duration=240.0))
    window.trim_bar.playhead = 239.5
    window._add_select()
    added = flight.selects[flight.current]
    assert added.end <= 240.0, "a select ran past the end of the recording"
    assert added.end > added.start


def test_removing_the_last_select_is_refused(window):
    """Down to one, Reset is the way back to the whole clip — removing it would
    leave the interface pointing at nothing."""
    flight = loaded(window, clip())
    flight.trim_in, flight.trim_out = 10.0, 40.0
    window._show_selects()
    window._remove_select()
    assert len(flight.selects) == 1


def test_removing_one_moves_the_editing_position_somewhere_real(window):
    flight = loaded(window, clip())
    flight.selects = [Select(10, 40), Select(90, 120), Select(200, 230)]
    flight.current = 2
    window._show_selects()

    window._remove_select()
    assert len(flight.selects) == 2
    assert 0 <= flight.current < len(flight.selects)
    assert flight.trim_in == 90, "left editing a select that no longer exists"


def test_reset_clears_every_range_not_just_the_edited_one(window):
    flight = loaded(window, clip())
    flight.selects = [Select(10, 40), Select(90, 120), Select(200, 230)]
    window._reset_trim()
    assert flight.real_selects == [], "Reset left the other selects behind"
    assert not flight.is_trimmed


def test_picking_a_range_on_the_filmstrip_starts_editing_it(window):
    flight = loaded(window, clip())
    flight.selects = [Select(10, 40, "one"), Select(90, 120, "two")]
    window._show_selects()

    window.preview_view.select_picked.emit(1)
    assert flight.current == 1
    assert window.trim_bar.in_point == 90
    assert window.trim_bar.out_point == 120


def test_the_row_stays_hidden_until_there_is_more_than_one(window):
    """A card reviewed the way every earlier version reviewed it should not
    grow a control it has no use for."""
    window.show()                       # isVisible is False for everything
    QApplication.processEvents()        # in a window that was never shown

    flight = loaded(window, clip())
    view = window.preview_view
    assert not view.select_remove.isVisible(), (
        "Remove offered on a clip with one range")
    assert not view.select_name.isVisible(), (
        "a name field that would not reach the filename")
    assert view.select_add.isVisible(), (
        "no way to make a second range except a key nobody knows about")

    flight.selects = [Select(10, 40), Select(90, 120)]
    window._show_selects()
    QApplication.processEvents()
    assert view.select_remove.isVisible()
    assert view.select_name.isVisible()
    assert view.select_label.text() == "Range 1 of 2"


def test_multi_range_controls_do_not_expose_internal_select_jargon(window):
    """The model and session key are still called selects, but that editing
    jargon must not leak into the controls a pilot reads."""
    view = window.preview_view
    assert view.select_add.text() == "Add range"
    assert "range" in view.select_add.toolTip().lower()
    assert "(n)" in view.select_add.toolTip().lower(), (
        "renaming the button dropped the only visible shortcut hint")
    assert "range" in view.select_name.placeholderText().lower()
    assert "range" in view.select_name.toolTip().lower()
    assert "range" in view.select_remove.toolTip().lower()

    flight = loaded(window, clip())
    flight.selects = [Select(10, 40), Select(90, 120)]
    window._show_selects()
    assert view.select_label.text() == "Range 1 of 2"


def test_the_estimate_counts_the_files_the_queue_will_make(window):
    from PySide6.QtCore import Qt
    flight = loaded(window, clip())
    flight.selects = [Select(10, 40), Select(90, 120), Select(200, 230)]
    window.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    window._update_estimate()

    said = window.export_panel.estimate_label.text()
    assert "3 files" in said, said


def test_clicking_inside_another_range_picks_it_up(app):
    """The only way to reach a select with the mouse. Emitting the signal by
    hand, as the test above does, skips the part that decides which range a
    click landed in."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from flightdvr.trim import TrimBar

    bar = TrimBar()
    bar.resize(400, 60)
    bar.set_clip(200.0, 90.0, 120.0,
                 ranges=[(10, 40), (90, 120), (160, 190)], selected=1)

    picked = []
    bar.select_picked.connect(picked.append)

    def click(seconds):
        x = bar._x_for(seconds)
        bar.mousePressEvent(QMouseEvent(
            QMouseEvent.Type.MouseButtonPress, QPointF(x, 30),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier))

    click(25.0)
    assert picked == [0], f"clicking the first range picked {picked}"

    picked.clear()
    click(175.0)
    assert picked == [2]

    # Inside the range already being edited, a click is a playhead move.
    picked.clear()
    moved = []
    bar.playhead_moved.connect(moved.append)
    click(105.0)
    assert picked == [], "re-picked the range already being edited"
    assert moved, "the playhead did not move"


def test_a_click_outside_every_range_just_moves_the_playhead(app):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from flightdvr.trim import TrimBar

    bar = TrimBar()
    bar.resize(400, 60)
    bar.set_clip(200.0, 90.0, 120.0, ranges=[(90, 120)], selected=0)
    picked, moved = [], []
    bar.select_picked.connect(picked.append)
    bar.playhead_moved.connect(moved.append)

    x = bar._x_for(60.0)
    bar.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(x, 30),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    assert picked == []
    assert moved


# -- what Codex's review of #34 found -----------------------------------------

def test_a_select_covering_the_whole_clip_is_still_exported(window):
    """A lone trim spanning everything is not a decision and clears. One range
    of several that happens to span everything is still a range — normalising it
    away left a row the filmstrip drew and the queue refused, so two selects on
    screen produced one file with no complaint."""
    flight = loaded(window, clip())
    flight.selects = [Select(10, 40), Select(90, 120)]
    flight.current = 1
    window.trim_bar.set_clip(flight.duration, 90, 120,
                             ranges=[(10, 40), (90, 120)], selected=1)
    window._show_selects()

    # what a drag does: move the bar's own points, then announce them
    window.trim_bar.in_point, window.trim_bar.out_point = 0.0, flight.duration
    window._on_trim_changed(0.0, flight.duration)

    drawn = sorted(window.trim_bar._kept())
    exported = sorted((p.trim_in, p.out_point) for p in flight.for_export())
    assert drawn == exported, (
        f"the filmstrip draws {drawn} and the queue exports {exported}")
    assert window.preview_view.select_label.text() == "Range 2 of 2"


def test_a_lone_trim_covering_everything_still_clears(window):
    """The other half of that rule, or it is just a special case nobody asked
    for: with one select, whole-clip means untrimmed exactly as before."""
    flight = loaded(window, clip())
    flight.trim_in, flight.trim_out = 10.0, 40.0
    window._show_selects()

    window.trim_bar.in_point, window.trim_bar.out_point = 0.0, flight.duration
    window._on_trim_changed(0.0, flight.duration)
    assert not flight.is_trimmed
    assert flight.real_selects == []


def test_picking_a_select_moves_the_player_too(window):
    """The picture said one moment and Play resumed from another."""
    flight = loaded(window, clip())
    flight.selects = [Select(10, 40), Select(90, 120)]
    window._show_selects()
    window.player.seek(33.0)

    window._pick_select(1)
    assert window.trim_bar.playhead == 90
    assert window.player.position == 90, (
        f"the filmstrip moved to 90 and the player is still at "
        f"{window.player.position}")


# -- a settled seek gets a real frame (#35) -----------------------------------

def test_dragging_the_filmstrip_does_not_decode_per_move(window):
    """Each precise decode is about a second of ffmpeg. A drag emits
    continuously, so asking on every move would queue one per pixel."""
    flight = loaded(window, clip())
    asked = []
    window.player.show_frame_at = lambda seconds: asked.append(seconds)

    for tenth in range(40):
        window.trim_bar.playhead = tenth
        window._on_playhead(float(tenth))
    assert asked == [], "decoded while the drag was still going"
    assert window._sharpen_timer.isActive(), "nothing was scheduled"

    window._sharpen()                      # what the timer does when it fires
    assert asked == [39.0], f"asked for {asked}"


def test_a_settled_seek_is_not_sharpened_during_playback(window):
    """The precise decoder releases the playback stream, so this would stop the
    video to sharpen a frame nobody is looking at."""
    flight = loaded(window, clip())
    asked = []
    window.player.show_frame_at = lambda seconds: asked.append(seconds)

    window.trim_bar.playhead = 30.0
    window._on_playhead(30.0)
    window.player.is_playing = True
    window._sharpen()
    assert asked == [], f"asked for {asked} while playing"

    window.player.is_playing = False
    window._sharpen()
    assert asked == [30.0], "paused again and it still did nothing"
# -- what the recording looks like it is doing (#17) --------------------------

def an_activity(**overrides):
    from flightdvr.motion import Activity
    fields = dict(duration=300.0, still=[(40.0, 90.0)],
                  flying=[(0.0, 40.0), (90.0, 250.0)], quietest=1.0)
    fields.update(overrides)
    return Activity(**fields)


def test_a_reading_offers_a_trim_but_does_not_take_it(window):
    """The whole point: it says what it sees and waits to be asked."""
    flight = loaded(window, clip(duration=300.0))
    window._activity_ready(window._strip_generation, str(flight.path),
                           an_activity())

    assert not flight.is_trimmed, "trimmed the clip without being asked"
    assert window.preview_view.activity_button.text(), "offered nothing"
    assert "2 flights" in window.preview_view.activity_note.text()


def test_pressing_the_button_applies_the_longest_flight(window):
    flight = loaded(window, clip(duration=300.0))
    window._activity_ready(window._strip_generation, str(flight.path),
                           an_activity())
    window.preview_view.activity_accepted.emit()

    assert flight.trim_in == pytest.approx(90.0)
    assert flight.out_point == pytest.approx(250.0)


def test_a_clip_already_trimmed_is_not_offered_a_guess(window):
    """Overwriting a decision somebody already made with a guess is the one
    thing this must not do."""
    flight = loaded(window, clip(duration=300.0))
    flight.trim_in, flight.trim_out = 12.0, 30.0
    window._activity_ready(window._strip_generation, str(flight.path),
                           an_activity())

    assert window.preview_view.activity_button.text() == "", (
        "offered to overwrite a trim that was already set")
    assert window.preview_view.activity_note.text(), (
        "said nothing at all, when the reading is still worth showing")


def test_a_reading_for_a_clip_no_longer_selected_is_dropped(window):
    """Browsing the list starts one of these per clip. The path is not enough:
    select A, then B, then A again, and the first reading of A can land after
    the second has started."""
    flight = loaded(window, clip(duration=300.0))
    window._activity_ready(window._strip_generation - 1, str(flight.path),
                           an_activity())
    assert window.preview_view.activity_note.text() == ""

    window._activity_ready(window._strip_generation, "some/other/clip.ts",
                           an_activity())
    assert window.preview_view.activity_note.text() == ""


def test_an_unreadable_feed_says_so_rather_than_guessing(window):
    flight = loaded(window, clip(duration=300.0))
    window._activity_ready(window._strip_generation, str(flight.path),
                           an_activity(readable=False))

    assert "no reading" in window.preview_view.activity_note.text()
    assert window.preview_view.activity_button.text() == ""


def test_accepting_the_suggestion_moves_the_player_too(window):
    """The picture and the label went to the suggested start while Play resumed
    from wherever it had been — the same fault as picking a select, written
    again in a second handler."""
    from flightdvr.motion import Activity

    flight = loaded(window, clip(duration=300.0))
    window._activity = Activity(duration=300.0, still=[(40.0, 90.0)],
                                flying=[(0.0, 40.0), (90.0, 250.0)],
                                quietest=1.0)
    window.player.seek(33.0)
    window._accept_activity()

    assert window.trim_bar.playhead == 90.0
    assert window.player.position == 90.0, (
        f"the filmstrip moved to 90 and the player is at "
        f"{window.player.position}")
