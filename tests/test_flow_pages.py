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

"""Switching how the window is arranged, without touching what it holds.

The load-bearing claim of this slice is that Flow is a *presentation*: the same
panels, the same player, the same session, the same queue. So most of what is
here is a round trip with a literal snapshot taken on both sides, and a count
of the things there must still be exactly one of.

Nothing here settles whether Flow is pleasant to use, or readable at a compact
size. Offscreen geometry cannot say either, and this slice is a foundation
rather than six finished pages.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from flightdvr.flow_layout import Mode, Stage
from flightdvr.media import ClipInfo, Select


def a_clip(folder: Path, name: str, duration: float = 30.0) -> ClipInfo:
    return ClipInfo(
        path=folder / name, size=599_189_652,
        modified=datetime(2025, 10, 8, 18, 39), duration=duration,
        width=1280, height=720, fps=60.0, video_codec="hevc",
        audio_codec="aac", pix_fmt="yuvj420p", color_range="pc",
    )


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    """A hermetic window. Every isolation is in place before it is built.

    Settings, home and output all point somewhere disposable *before*
    construction, because the window reads its stored mode while it is being
    built — isolating afterwards would be isolating the wrong run.
    """
    import tests.test_music_wiring as wiring

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr("flightdvr.ui.ScanWorker", wiring._NoScan)
    monkeypatch.setattr("flightdvr.ui.HardwareProbe", wiring._NoProbe)
    monkeypatch.setattr("flightdvr.ui.FilmstripLoader", wiring._NoStrip)
    monkeypatch.setattr("flightdvr.updates.should_check", lambda *a, **k: False)
    monkeypatch.setattr("flightdvr.ui.MusicAssetProbe", wiring._FakeProbe,
                        raising=False)

    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow

    card = tmp_path / "card"
    card.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    made = MainWindow(find_tools())
    made.source_combo.insertItem(0, str(card), str(card))
    made.source_combo.setCurrentIndex(0)
    monkeypatch.setattr(made.thumbs, "request", lambda *_: None)
    monkeypatch.setattr(made.player, "load", lambda *a, **k: None)
    for name in ("hdz_001.ts", "hdz_002.ts"):
        made._add_clip(made._scan_generation, a_clip(card, name))
    made._scan_done(made._scan_generation, 2)
    made.export_panel.out_edit.setCurrentText(str(out))
    app.processEvents()
    yield made
    made.close()
    app.processEvents()
    left = [t for t in made.findChildren(QThread) if t.isRunning()]
    assert not left, [type(t).__name__ for t in left]


def snapshot(window) -> dict:
    """Literal values, not widget states. What a session actually is."""
    return {
        "clips": [str(clip.path) for clip in window.clips],
        "reviews": [clip.review for clip in window.clips],
        "ranges": [[(s.start, s.end, s.name, s.sid) for s in clip.selects]
                   for clip in window.clips],
        "ticked": [c.path.name for c in window.selected_clips()],
        "assembly": [(item.fingerprint, item.sid)
                     for item in window.export_panel.assembly_panel.items()],
        "targets": [(t.items[0].fingerprint, t.items[0].sid)
                    for t in window.output_plan.targets],
        "music": [str(window.output_plan.get(t).music.mode)
                  for t in window.output_plan.targets],
        "preset": window._preset_key(),
        "template": window.export_panel.template(),
        "settings": window.export_panel.capture(),
        "jobs": [(str(j.out_path), j.preset_key, str(j.audio.mode))
                 for j in window.jobs],
    }


def counts(window) -> dict:
    """How many there are of the things there must be exactly one of."""
    from flightdvr.live_preview import LivePreview
    from flightdvr.music_panel import MusicPanel
    from flightdvr.player import PreviewPlayer
    from flightdvr.queue_panel import QueuePanel

    return {
        "players": len(window.findChildren(PreviewPlayer)),
        "music_panels": len(window.findChildren(MusicPanel)),
        "queues": len(window.findChildren(QueuePanel)),
        "transports": 1 if window.live_preview is not None else 0,
        "live_previews": sum(
            1 for value in vars(window).values()
            if isinstance(value, LivePreview)),
    }


# -- Classic is the default ----------------------------------------------------

def test_a_window_opens_in_classic(window):
    assert window.view_mode is Mode.CLASSIC
    assert window._flow_host.isHidden()


def test_an_unreadable_stored_mode_opens_classic(window, app):
    window.settings_store.setValue("view_mode", "docked-panels")
    window._restore()
    app.processEvents()
    assert window.view_mode is Mode.CLASSIC


def test_a_stored_flow_mode_is_reopened(window, app):
    window.settings_store.setValue("view_mode", "flow")
    window._restore()
    app.processEvents()
    assert window.view_mode is Mode.FLOW
    window.set_view_mode(Mode.CLASSIC)


# -- the round trip ------------------------------------------------------------

def decide_something(window) -> None:
    """Make the kind of decisions a switch must not disturb."""
    from flightdvr.session import KEEP

    window.clips[0].selects = [Select(2.0, 9.0, "run in", sid="r-1")]
    window.clips[0].current = 0
    window.clips[0].review = KEEP
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    window.export_panel.template_edit.setText("{clip}_{preset}")
    window.export_panel.preset_buttons["master"].setChecked(True)


def test_a_round_trip_changes_nothing_a_session_is_made_of(window, app):
    """The whole claim of this slice, asserted on values rather than widgets."""
    decide_something(window)
    app.processEvents()
    before = snapshot(window)

    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()

    assert snapshot(window) == before


def test_a_round_trip_creates_no_second_anything(window, app):
    """#84's named risk: reparenting must not duplicate a worker, a player or
    a connection."""
    before = counts(window)

    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()

    assert counts(window) == before
    assert before["players"] == 1 and before["queues"] == 1
    assert before["music_panels"] == 1 and before["live_previews"] == 1


def test_a_decision_after_a_round_trip_is_recorded_once(window, app):
    """A duplicated signal connection shows up as one click doing two things,
    and a count of widgets would never see it."""
    from flightdvr.session import KEEP

    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()

    touched = []
    window._touch_session = lambda: touched.append(True)
    window.table.setCurrentCell(0, 0)
    window._set_review(KEEP)

    assert touched == [True], f"the decision was recorded {len(touched)} times"


def test_the_panels_are_the_same_objects_not_copies(window, app):
    before = (window.queue_panel, window.export_panel, window.browser_panel,
              window.preview_view.music_band, window.music_panel)

    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()

    assert (window.queue_panel, window.export_panel, window.browser_panel,
            window.preview_view.music_band, window.music_panel) == before


def test_classic_gets_its_panels_back_where_they_were(window, app):
    """Put back by remembered index. One place too far is not something a
    value snapshot would notice."""
    column = window._left_column
    splitter = window.splitter
    index = splitter.indexOf(column)
    sizes = list(splitter.sizes())
    queue_home = window.queue_panel.parentWidget().layout()
    queue_index = queue_home.indexOf(window.queue_panel)
    assert index >= 0, "the column was not in the splitter to begin with"

    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()

    # Asked of the splitter itself. Going through `layout()` is what a
    # splitter does not answer, so an index checked that way passes whatever
    # position the widget came back in — a mutation putting it at 0 went
    # straight through an earlier version of this.
    assert splitter.indexOf(column) == index, "the column came back elsewhere"
    assert list(splitter.sizes()) == sizes, "the split was not the one it had"
    assert queue_home.indexOf(window.queue_panel) == queue_index
    assert not splitter.isHidden()


# -- navigation ----------------------------------------------------------------

def test_only_stages_with_something_behind_them_are_offered(window):
    """Assemble lives inside ExportPanel and lifting it out would change that
    panel's internals, so it is left out rather than shown blank."""
    assert Stage.ASSEMBLE not in window._offered_stages
    assert Stage.BROWSE in window._offered_stages
    assert Stage.QUEUE in window._offered_stages
    assert set(window.flow_stage_buttons) == set(window._offered_stages)


def test_navigating_queues_nothing(window, app):
    """#84 is explicit: navigation must never start or queue an export."""
    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    before = len(window.jobs)

    for _ in range(len(window._offered_stages)):
        window._step_stage(1)
        app.processEvents()
    for _ in range(len(window._offered_stages)):
        window._step_stage(-1)
        app.processEvents()

    assert len(window.jobs) == before
    window.set_view_mode(Mode.CLASSIC)


def test_back_and_next_stop_at_the_ends(window, app):
    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    window._show_stage(window._offered_stages[0])
    assert not window.flow_back.isEnabled()
    window._show_stage(window._offered_stages[-1])
    assert not window.flow_next.isEnabled()
    window.set_view_mode(Mode.CLASSIC)


def test_revisiting_an_earlier_stage_keeps_what_was_decided(window, app):
    """#84: users must be able to go back without losing selections."""
    decide_something(window)
    app.processEvents()
    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    before = snapshot(window)

    window._show_stage(Stage.OUTPUT)
    app.processEvents()
    window._show_stage(Stage.BROWSE)
    app.processEvents()

    assert snapshot(window) == before
    window.set_view_mode(Mode.CLASSIC)


def test_the_queue_stays_reachable_from_every_stage(window, app):
    """Progress and cancel must not disappear because another page is open."""
    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    for stage in window._offered_stages:
        window._show_stage(stage)
        app.processEvents()
        assert window.queue_panel is not None
        assert window.queue_panel.parentWidget() is not None
        assert window.queue_panel.start_button is not None
    window.set_view_mode(Mode.CLASSIC)


def test_the_queue_keeps_its_controls_in_flow(window, app):
    """A submitted job is an immutable *input* snapshot, not a frozen object:
    status, cancel and the authorized pending retarget stay live, so the Queue
    page keeps its controls rather than becoming a read-only list."""
    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    window._show_stage(Stage.QUEUE)
    app.processEvents()

    assert window.queue_panel.isEnabled()
    for control in ("start_button", "cancel_button"):
        assert hasattr(window.queue_panel, control)
    window.set_view_mode(Mode.CLASSIC)


# -- the transport is not disturbed --------------------------------------------

def test_switching_does_not_start_the_sound(window, app):
    """Quiet startup survives a presentation change."""
    assert window.live_preview.status.muted
    window.set_view_mode(Mode.FLOW)
    app.processEvents()

    assert window.live_preview.status.muted
    assert not window.live_preview.status.playing

    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()
    assert window.live_preview.status.muted
    assert not window.live_preview.status.playing


def test_switching_twice_is_the_same_as_not_switching(window, app):
    before = snapshot(window)
    for _ in range(3):
        window.set_view_mode(Mode.FLOW)
        app.processEvents()
        window.set_view_mode(Mode.CLASSIC)
        app.processEvents()
    assert snapshot(window) == before


def test_asking_for_the_mode_it_is_already_in_does_nothing(window, app):
    window.set_view_mode(Mode.CLASSIC)
    assert window.view_mode is Mode.CLASSIC
    assert window._flow_host.isHidden()
