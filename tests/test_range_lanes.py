"""One lane per range, each on its own clock (W4, R03).

The mapping is pure and tested literally. The window's half — which page shows
the lanes, which range a press chooses, and that nothing is decoded or edited
by them — is tested through real pointer and key events on a shown window.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from flightdvr.assembly import Item
from flightdvr.flow_layout import Mode, Stage
from flightdvr.media import Select
from flightdvr.range_lanes import (
    LaneRange, RangeLanes, clock, local_to_source, source_to_local)
from flightdvr.trim import Filmstrip

# The music wiring harness: no probes, no scans, no decoders, and a disposable
# home. Imported rather than copied so the two cannot drift apart.
from test_music_wiring import (  # noqa: F401  (fixtures)
    app, probes, sessions_home, tick, window)

A = LaneRange("range-a", "A", 96.0, 132.0)
B = LaneRange("range-b", "B", 180.0, 204.0)


# -- the clocks -----------------------------------------------------------------


def test_local_fourteen_is_source_one_hundred_and_ten():
    assert local_to_source(A, 14.0) == 110.0
    assert source_to_local(A, 110.0) == 14.0


def test_a_second_nonzero_range_has_its_own_clock():
    assert local_to_source(B, 14.0) == 194.0
    assert source_to_local(B, 194.0) == 14.0
    assert source_to_local(A, 194.0) is None


def test_the_end_is_exclusive_and_the_start_is_zero():
    assert source_to_local(A, 96.0) == 0.0
    assert source_to_local(A, 132.0) is None
    assert source_to_local(A, 131.999) == pytest.approx(35.999)
    assert local_to_source(A, 99.0) == 132.0      # clamped to the range
    assert local_to_source(A, -3.0) == 96.0


def test_the_clock_reads_in_minutes_seconds_and_tenths():
    assert clock(0) == "0:00.0" and clock(36) == "0:36.0"
    assert clock(96) == "1:36.0" and clock(14.05) == "0:14.0"


# -- the widget -----------------------------------------------------------------


@pytest.fixture
def lanes(app):
    made = RangeLanes()
    made.set_wanted(True)
    made.resize(600, 200)
    made.show()
    yield made
    made.close()


def test_rows_follow_identity_and_the_active_lane_is_taller(lanes, app):
    lanes.set_ranges([A, B], "range-b")
    app.processEvents()
    assert lanes.lane_for("range-b").height() > lanes.lane_for(
        "range-a").height()
    assert lanes.label_for("range-a").text() == (
        "1  A · source 1:36.0–2:12.0 · range 0:00.0–0:36.0")
    renamed = LaneRange("range-a", "Dive", 96.0, 132.0)
    before = lanes.lane_for("range-a")
    lanes.set_ranges([renamed, B], "range-b")
    assert lanes.lane_for("range-a") is before, "a rename rebuilt the row"
    assert lanes.label_for("range-a").text().startswith("1  Dive ·")


def test_a_press_names_its_range_and_the_moment_in_the_recording(lanes, app):
    lanes.set_ranges([A, B], "range-a")
    app.processEvents()
    pressed = []
    lanes.range_clicked.connect(lambda sid, s: pressed.append((sid, s)))
    lane = lanes.lane_for("range-b")
    x = round(14.0 / 24.0 * (lane.width() - 1))
    QTest.mouseClick(lane, Qt.MouseButton.LeftButton, pos=QPoint(x, 5))
    sid, source = pressed[0]
    assert sid == "range-b"
    assert source == pytest.approx(194.0, abs=24.0 / lane.width())


def test_up_and_down_step_by_identity(lanes, app):
    lanes.set_ranges([A, B], "range-a")
    stepped = []
    lanes.range_stepped.connect(stepped.append)
    lanes.setFocus()
    QTest.keyClick(lanes, Qt.Key.Key_Down)
    QTest.keyClick(lanes, Qt.Key.Key_Up)            # already the first
    assert stepped == ["range-b"]


def test_twenty_ranges_scroll_in_a_bounded_height(lanes, app):
    many = [LaneRange(f"r{i}", "", i * 10.0, i * 10.0 + 5) for i in range(20)]
    lanes.set_ranges(many[:3], "r0")
    three = lanes.scroll.height()
    lanes.set_ranges(many, "r19")
    for _ in range(3):
        app.processEvents()
    assert lanes.scroll.height() == three
    assert lanes.scroll.verticalScrollBar().maximum() > 0
    # A long title never widens the lanes past their room.
    assert lanes._body.width() <= lanes.scroll.viewport().width()


def test_frames_are_loaded_once_for_every_lane(lanes, app, tmp_path,
                                               monkeypatch):
    frames = []
    for i in range(4):
        frame = tmp_path / f"f_{i:04d}.jpg"
        frame.write_bytes(b"")
        frames.append(frame)
    loaded = []
    real = __import__("flightdvr.range_lanes", fromlist=["QPixmap"]).QPixmap
    monkeypatch.setattr("flightdvr.range_lanes.QPixmap",
                        lambda path: loaded.append(path) or real())
    lanes.set_strip(Filmstrip(frames, [0.0, 100.0, 190.0, 200.0]))
    lanes.set_ranges([A, B], "range-a")
    app.processEvents()
    lanes.grab()
    lanes.grab()
    assert len(loaded) == len(frames), "frames were loaded per lane or paint"


# -- in the window ----------------------------------------------------------------


def trimming(window, app):
    """Recording 1 with ranges A and B, on Flow's Trim page, shown."""
    window.show()
    clip = window.clips[0]
    clip.selects = [Select(96.0, 132.0, "A", sid="range-a"),
                    Select(180.0, 204.0, "B", sid="range-b")]
    clip.current = 0
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    window.set_view_mode(Mode.FLOW)
    window._show_stage(Stage.TRIM)
    app.processEvents()
    return clip, window.preview_view.range_lanes


def click_local(lanes, sid: str, local: float) -> None:
    lane = lanes.lane_for(sid)
    x = round(local / lane.lane.length * (lane.width() - 1))
    QTest.mouseClick(lane, Qt.MouseButton.LeftButton, pos=QPoint(x, 5))


def test_lanes_show_on_flow_trim_only(window, app):
    _clip, lanes = trimming(window, app)
    assert lanes.isVisible()
    assert [one.sid for one in lanes.ranges] == ["range-a", "range-b"]
    band = window.preview_view.trim_band
    for stage in Stage:
        if stage is Stage.TRIM or stage not in window._offered_stages:
            continue
        window._show_stage(stage)
        app.processEvents()
        assert not lanes.isVisibleTo(window), stage
        # Where this page shows the filmstrip, the filmstrip shows alone.
        if band.isVisibleTo(window):
            assert not lanes.isVisibleTo(band), stage
    window._show_stage(Stage.TRIM)
    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()
    assert not lanes.isVisibleTo(window)


def test_a_press_after_a_reorder_chooses_by_identity_not_row(window, app):
    clip, lanes = trimming(window, app)
    # The list changes order under the drawn lanes, then B's lane is pressed.
    clip.selects.reverse()
    clip.current = 1                                  # still A
    click_local(lanes, "range-b", 2.0)
    app.processEvents()
    assert clip.selects[clip.current].sid == "range-b"


def test_inside_the_active_range_a_press_moves_the_playhead_only(window, app):
    clip, lanes = trimming(window, app)
    before = [(s.start, s.end, s.name, s.sid) for s in clip.selects]
    click_local(lanes, "range-a", 14.0)
    app.processEvents()
    assert window.trim_bar.playhead == pytest.approx(110.0, abs=0.2)
    assert lanes.playhead == window.trim_bar.playhead
    # Pressing at the very end stops short of the exclusive out point.
    click_local(lanes, "range-a", 36.0)
    app.processEvents()
    assert window.trim_bar.playhead < 132.0
    assert window.trim_bar.playhead == pytest.approx(132.0 - 1 / 60, abs=0.01)
    assert [(s.start, s.end, s.name, s.sid) for s in clip.selects] == before
    assert clip.selects[clip.current].sid == "range-a"


def test_keys_step_between_ranges_through_the_window(window, app):
    clip, lanes = trimming(window, app)
    lanes.setFocus()
    QTest.keyClick(lanes, Qt.Key.Key_Down)
    app.processEvents()
    assert clip.selects[clip.current].sid == "range-b"
    assert lanes.active_sid == "range-b"
    assert lanes.lane_for("range-b").height() > lanes.lane_for(
        "range-a").height()


def test_a_rename_updates_its_lane_and_keeps_the_assembly_reference(
        window, app):
    clip, lanes = trimming(window, app)
    window._store_assembly([Item(clip.fingerprint, "range-a")])
    window._rename_select("Dive")
    app.processEvents()
    assert lanes.label_for("range-a").text().startswith("1  Dive ·")
    assert window.session.assembly == [Item(clip.fingerprint, "range-a")]


def test_moving_the_active_range_redraws_its_lane(window, app):
    clip, lanes = trimming(window, app)
    window.trim_bar.trim_changed.emit(100.0, 132.0)
    app.processEvents()
    assert lanes.ranges[0] == LaneRange("range-a", "A", 100.0, 132.0)
    assert lanes.label_for("range-a").text().endswith("range 0:00.0–0:32.0")


def test_lanes_start_no_filmstrip_read(window, app, monkeypatch):
    made = []
    import flightdvr.ui as ui
    real = ui.FilmstripLoader

    def counting(*args, **kwargs):
        made.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(ui, "FilmstripLoader", counting)
    clip, lanes = trimming(window, app)
    before = len(made)
    clip.selects = [Select(i * 10.0, i * 10.0 + 5, "", sid=f"r{i}")
                    for i in range(20)]
    window._show_selects()
    for sid in ("r3", "r7", "r19"):
        lanes.range_stepped.emit(sid)
    app.processEvents()
    assert len(made) == before
