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

from PySide6.QtCore import QPoint, QThread, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from flightdvr.assembly import Item
from flightdvr.assembly_panel import ITEM_ROLE as ASSEMBLY_ITEM_ROLE
from flightdvr.audio_plan import AudioMode, MusicChoice
from flightdvr.flow_layout import Mode, Region, Stage
from flightdvr.widgets import MIN_LIST_HEIGHT
from flightdvr.jobs import Job, JobStatus
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


def make_aba_assembly(window, app) -> None:
    """Literal A/B/A clock: [10,13), [2,4), [10,13)."""
    first, second = window.clips
    first.selects = [Select(10.0, 13.0, "A", sid="a")]
    second.selects = [Select(2.0, 4.0, "B", sid="b")]
    window._store_assembly([
        Item(first.fingerprint, "a"),
        Item(second.fingerprint, "b"),
        Item(first.fingerprint, "a"),
    ])
    app.processEvents()


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

def test_every_stage_now_has_something_behind_it(window):
    """Assemble was left out while its panel could only be reached by changing
    `ExportPanel`'s internals. It is borrowed whole now, like every other
    panel, so the approved six are all offered and the bar has no gap."""
    for stage in Stage:
        assert stage in window._offered_stages, stage
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


# -- what a save in Flow must not throw away ------------------------------------

def test_saving_while_flow_holds_the_panels_keeps_the_classic_split(window, app):
    """The finding (#123 review).

    Flow lends both splitter children to stages, so the splitter is empty while
    it is showing. `saveState()` on an empty splitter is an empty split, and
    writing that threw away the proportions the person had chosen.
    """
    window.splitter.setSizes([900, 320])
    app.processEvents()
    chosen = bytes(window.splitter.saveState())

    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    assert window.splitter.count() == 0, "Flow did not actually borrow them"

    window._save()

    assert bytes(window.settings_store.value("splitter")) == chosen, (
        "an empty splitter was written over the chosen split")
    window.set_view_mode(Mode.CLASSIC)


def test_the_split_comes_back_after_a_round_trip(window, app):
    window.splitter.setSizes([880, 340])
    app.processEvents()
    before = list(window.splitter.sizes())

    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()

    assert list(window.splitter.sizes()) == before


def test_a_window_reopened_after_saving_in_flow_has_the_same_split(
        window, app, tmp_path, monkeypatch):
    """The restart Sol asked for, end to end: choose a split, save while Flow
    is showing, and open a second window against the same stored settings."""
    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow

    # Room first: a splitter in a window too small to hold the sizes clamps
    # them, and a baseline taken from a clamped split measures nothing.
    window.resize(1402, 900)
    window.show()
    app.processEvents()
    # Deliberately far from the default split. A chosen split that sits near
    # where the window would have put it anyway cannot tell a restored layout
    # from a fresh one — my first tolerance was wide enough to let exactly
    # that through.
    window.splitter.setSizes([1150, 200])
    app.processEvents()
    chosen = list(window.splitter.sizes())
    assert chosen[0] > 2 * chosen[1], chosen

    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    window._save()

    # The first window stays in Flow until the second has been built:
    # returning it to Classic first rewrites the stored mode, and window two
    # then opens in Classic and never exercises the ordering at all.
    #
    # The second window opens in Flow, because that is what was stored. An
    # earlier version of this forced Classic first, which walked straight past
    # the bug: entering Flow during startup captured the *default* split as
    # the Classic one, and only a window that actually reopens in Flow shows
    # it.
    again = MainWindow(find_tools())
    try:
        again.resize(1402, 900)
        again.show()
        app.processEvents()
        assert again.view_mode is Mode.FLOW, "the stored mode was not reopened"
        again.set_view_mode(Mode.CLASSIC)
        app.processEvents()
        assert again.splitter.count() == 2, "the reopened window lost a panel"
        assert sum(again.splitter.sizes()) > 0
        # Proportion rather than pixels: the second window is not guaranteed
        # the first one's exact geometry, and asserting pixels here would be
        # measuring the window manager.
        left = again.splitter.sizes()[0] / max(1, sum(again.splitter.sizes()))
        wanted = chosen[0] / max(1, sum(chosen))
        assert abs(left - wanted) < 0.06, (again.splitter.sizes(), chosen)
    finally:
        again.close()
        window.set_view_mode(Mode.CLASSIC)
        app.processEvents()


# -- the working-output sidebar ------------------------------------------------

def tick(window, index: int) -> None:
    from PySide6.QtCore import Qt
    window.table.item(index, 0).setCheckState(Qt.CheckState.Checked)


def rows(listing) -> list[str]:
    return [listing.item(row).text() for row in range(listing.count())]


def test_a_ticked_clip_nobody_clicked_is_listed(window, app):
    """The finding this slice exists for.

    `OutputPlan.targets` holds outputs whose music panel has been *looked at*,
    because the only place a target is added is the focus handler. A sidebar
    built on it would omit ticked work nobody had clicked — so the list comes
    from what a queue action would resolve instead.
    """
    tick(window, 1)
    app.processEvents()
    window.set_view_mode(Mode.FLOW)
    app.processEvents()

    assert any("hdz_002.ts" in text for text in rows(window.sidebar_working))
    window.set_view_mode(Mode.CLASSIC)


def test_three_ranges_are_three_cards(window, app):
    window.clips[0].selects = [
        Select(1.0, 5.0, "one", sid="r-1"),
        Select(6.0, 9.0, "two", sid="r-2"),
        Select(11.0, 14.0, "three", sid="r-3"),
    ]
    tick(window, 0)
    app.processEvents()
    window.set_view_mode(Mode.FLOW)
    app.processEvents()

    listed = rows(window.sidebar_working)
    assert len(listed) == 3, listed
    assert sum("one" in text for text in listed) == 1
    window.set_view_mode(Mode.CLASSIC)


def test_the_cards_say_the_target_the_preset_and_one_line_about_sound(
        window, app):
    """No percentage, no duration, no invented metadata."""
    tick(window, 0)
    app.processEvents()
    window.set_view_mode(Mode.FLOW)
    app.processEvents()

    text = rows(window.sidebar_working)[0]
    assert "hdz_001.ts" in text
    assert len(text.splitlines()) <= 3
    for absent in ("%", " s,", "fade", "trimmed to fit"):
        assert absent not in text, text
    window.set_view_mode(Mode.CLASSIC)


def test_the_sound_line_is_truthful_about_a_track_still_being_read(
        window, app, monkeypatch, tmp_path):
    import tests.test_music_wiring as wiring

    tick(window, 0)
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    app.processEvents()
    monkeypatch.setattr(
        "flightdvr.ui.QFileDialog.getOpenFileName",
        staticmethod(lambda *a, **k: (str(tmp_path / "song.mp3"), "")))
    window._choose_music_track()
    app.processEvents()
    window.set_view_mode(Mode.FLOW)
    app.processEvents()

    assert any("Reading" in text for text in rows(window.sidebar_working))
    window.set_view_mode(Mode.CLASSIC)


def test_an_unticked_clip_leaves_the_list_but_keeps_its_music(window, app):
    """Nothing is silently discarded, and nothing stale is displayed."""
    from PySide6.QtCore import Qt

    tick(window, 0)
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    app.processEvents()
    target = window._music_target
    assert target in window.output_plan.targets

    window.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    app.processEvents()
    window.set_view_mode(Mode.FLOW)
    app.processEvents()

    assert not any("hdz_001.ts" in text for text in rows(window.sidebar_working))
    assert target in window.output_plan.targets, "the choice was discarded"
    window.set_view_mode(Mode.CLASSIC)


def test_choosing_a_card_focuses_it_through_the_ordinary_handlers(window, app):
    window.clips[0].selects = [
        Select(1.0, 5.0, "one", sid="r-1"),
        Select(6.0, 9.0, "two", sid="r-2"),
    ]
    tick(window, 0)
    app.processEvents()
    window.set_view_mode(Mode.FLOW)
    app.processEvents()

    window.sidebar_working.item(1).setSelected(True)
    app.processEvents()

    assert window._trim_clip is window.clips[0]
    assert window.clips[0].real_selects[window.clips[0].current].sid == "r-2"
    window.set_view_mode(Mode.CLASSIC)


def test_choosing_a_card_starts_no_sound(window, app):
    tick(window, 0)
    app.processEvents()
    window.set_view_mode(Mode.FLOW)
    app.processEvents()

    window.sidebar_working.item(0).setSelected(True)
    app.processEvents()

    assert window.live_preview.status.muted
    assert not window.live_preview.status.playing
    window.set_view_mode(Mode.CLASSIC)


def test_one_action_causes_one_rebuild(window, app):
    """A reentrant refresh looks identical on screen and only a counter sees
    it: writing a list emits selection changes, and answering them would
    rebuild again."""
    tick(window, 0)
    app.processEvents()
    window.set_view_mode(Mode.FLOW)
    app.processEvents()

    before = window._sidebar_rebuilds
    window.sidebar_working.item(0).setSelected(True)
    app.processEvents()

    assert window._sidebar_rebuilds - before <= 1, (
        f"one selection caused {window._sidebar_rebuilds - before} rebuilds")
    window.set_view_mode(Mode.CLASSIC)


def test_a_queued_job_is_listed_as_submitted_and_stays_editable_upstream(
        window, app, monkeypatch):
    """A submitted job is an immutable *input* snapshot, not a frozen object:
    it keeps its status, and the working output it came from stays editable."""
    monkeypatch.setattr("flightdvr.ui.QMessageBox.warning",
                        staticmethod(lambda *a, **k: None))
    tick(window, 0)
    app.processEvents()
    window._add_to_queue()
    app.processEvents()
    assert window.jobs, "nothing queued, so this proves nothing"

    window.set_view_mode(Mode.FLOW)
    app.processEvents()

    assert rows(window.sidebar_submitted), "the queued job was not listed"
    assert any("hdz_001.ts" in text for text in rows(window.sidebar_working)), (
        "the working output disappeared when it was queued")
    assert window.queue_panel.start_button is not None
    window.set_view_mode(Mode.CLASSIC)


def test_the_sidebar_is_flow_only_and_classic_is_unchanged(window, app):
    assert window._sidebar.parentWidget() is not None
    assert not window._sidebar.isVisible()
    assert window.view_mode is Mode.CLASSIC


def test_an_assembly_collapses_the_cards_to_one_joined_output(window, app,
                                                              monkeypatch):
    """One job, so one card — and it names the run rather than a recording."""
    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1")]
    window.clips[1].selects = [Select(2.0, 6.0, "two", sid="r-2")]
    tick(window, 0)
    tick(window, 1)
    app.processEvents()
    window._fill_assembly()
    monkeypatch.setattr(window.export_panel, "join_enabled", lambda: True)
    app.processEvents()

    window.set_view_mode(Mode.FLOW)
    app.processEvents()

    listed = rows(window.sidebar_working)
    assert len(listed) == 1, listed
    assert "joined" in listed[0]
    window.set_view_mode(Mode.CLASSIC)


# -- the sidebar is live, not a snapshot taken on the way in (#124 review) ------

def in_flow(window, app):
    """Open Flow *first*, then change things. That ordering is the finding.

    Every earlier test here set its state and then switched, so it only ever
    inspected the first render — a sidebar that never refreshed again would
    have passed all of them.
    """
    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    return window.sidebar_working


def test_ticking_a_clip_while_flow_is_open_adds_its_card(window, app):
    listing = in_flow(window, app)
    assert not any("hdz_002.ts" in text for text in rows(listing))

    tick(window, 1)
    app.processEvents()

    assert any("hdz_002.ts" in text for text in rows(listing))
    window.set_view_mode(Mode.CLASSIC)


def test_unticking_a_clip_while_flow_is_open_removes_its_card(window, app):
    from PySide6.QtCore import Qt

    tick(window, 0)
    app.processEvents()
    listing = in_flow(window, app)
    assert any("hdz_001.ts" in text for text in rows(listing))

    window.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    app.processEvents()

    assert not any("hdz_001.ts" in text for text in rows(listing))
    window.set_view_mode(Mode.CLASSIC)


def test_changing_the_preset_relabels_only_the_output_being_edited(
        window, app):
    """Was "relabels every card", which described one global preset. With
    each output owning its choices, a change belongs to the output open in the
    panel — the card that follows it is that one, and the others keep theirs.
    Relabelling every card would now be a visible lie about what each renders.
    """
    first, second = two_planned_targets(window, app)
    listing = in_flow(window, app)
    window._select_working_target(first)
    app.processEvents()
    label = {row: listing.item(row).text() for row in range(listing.count())}

    window.export_panel.preset_buttons["upload"].setChecked(True)
    app.processEvents()

    changed = [row for row in range(listing.count())
               if listing.item(row).text() != label[row]]
    assert len(changed) == 1, f"expected one card to change, got {changed}"
    assert listing.item(changed[0]).data(
        __import__("flightdvr.output_sidebar", fromlist=["KEY_ROLE"]
                   ).KEY_ROLE) == first
    window.set_view_mode(Mode.CLASSIC)


def test_filling_the_assembly_while_flow_is_open_collapses_the_cards(
        window, app, monkeypatch):
    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1")]
    window.clips[1].selects = [Select(2.0, 6.0, "two", sid="r-2")]
    tick(window, 0)
    tick(window, 1)
    app.processEvents()
    listing = in_flow(window, app)
    assert len(rows(listing)) == 2

    # One real action, and the join state set before it. Calling
    # `_store_assembly` a second time afterwards is what hid the defect: the
    # first call refreshed against the assembly it was about to replace, and
    # the second one tidied up after it.
    monkeypatch.setattr(window.export_panel, "join_enabled", lambda: True)
    window._fill_assembly()
    app.processEvents()

    listed = rows(listing)
    assert len(listed) == 1 and "joined" in listed[0], listed
    window.set_view_mode(Mode.CLASSIC)


def test_a_rescan_while_flow_is_open_empties_the_cards(window, app,
                                                       monkeypatch):
    """An empty or failed rescan leaves nothing to export, so nothing is
    listed — a stale card would name material that is no longer here."""
    import tests.test_music_wiring as wiring

    tick(window, 0)
    app.processEvents()
    listing = in_flow(window, app)
    assert rows(listing)

    monkeypatch.setattr("flightdvr.ui.ScanWorker", wiring._NoScan)
    window._scan()
    window._scan_done(window._scan_generation, 0)
    app.processEvents()

    assert rows(listing) == []
    window.set_view_mode(Mode.CLASSIC)


def test_a_job_that_starts_while_flow_is_open_shows_that_it_is_running(
        window, app, monkeypatch):
    from flightdvr.jobs import JobStatus

    monkeypatch.setattr("flightdvr.ui.QMessageBox.warning",
                        staticmethod(lambda *a, **k: None))
    tick(window, 0)
    app.processEvents()
    window._add_to_queue()
    app.processEvents()
    assert window.jobs
    listing = in_flow(window, app)
    assert any("Waiting" in text for text in rows(window.sidebar_submitted))

    window.jobs[0].status = JobStatus.RUNNING
    window._job_started(0)
    app.processEvents()

    assert any("Encoding" in text for text in rows(window.sidebar_submitted)), (
        rows(window.sidebar_submitted))
    window.set_view_mode(Mode.CLASSIC)


def test_removing_a_job_while_flow_is_open_takes_it_off_the_list(
        window, app, monkeypatch):
    monkeypatch.setattr("flightdvr.ui.QMessageBox.warning",
                        staticmethod(lambda *a, **k: None))
    tick(window, 0)
    app.processEvents()
    window._add_to_queue()
    app.processEvents()
    listing = in_flow(window, app)
    assert rows(window.sidebar_submitted)

    window.jobs.clear()
    window._rebuild_queue()
    app.processEvents()

    assert rows(window.sidebar_submitted) == []
    window.set_view_mode(Mode.CLASSIC)


def test_a_live_change_still_causes_one_rebuild_each(window, app):
    """The refresh is now on several signals. Each has to stay once-only."""
    in_flow(window, app)
    before = window._sidebar_rebuilds

    tick(window, 0)
    app.processEvents()

    assert 1 <= window._sidebar_rebuilds - before <= 2, (
        f"one tick caused {window._sidebar_rebuilds - before} rebuilds")
    window.set_view_mode(Mode.CLASSIC)


def test_classic_pays_nothing_for_the_sidebar(window, app):
    """It is not on screen there, so it is not rebuilt there."""
    assert window.view_mode is Mode.CLASSIC
    before = window._sidebar_rebuilds
    tick(window, 0)
    app.processEvents()
    assert window._sidebar_rebuilds == before


def test_removing_an_assembly_row_while_flow_is_open_updates_the_card(
        window, app, monkeypatch):
    """Reordering and removing arrive through `_capture_assembly`, which
    refreshed nothing — so a row taken out left its joined card standing."""
    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1")]
    window.clips[1].selects = [Select(2.0, 6.0, "two", sid="r-2")]
    tick(window, 0)
    tick(window, 1)
    app.processEvents()
    monkeypatch.setattr(window.export_panel, "join_enabled", lambda: True)
    window._fill_assembly()
    app.processEvents()
    listing = in_flow(window, app)
    joined = rows(listing)
    assert len(joined) == 1 and "2 ranges joined" in joined[0], joined

    panel = window.export_panel.assembly_panel
    kept = list(panel.items())[:1]
    panel.set_items(kept) if hasattr(panel, "set_items") else None
    window._store_assembly(kept)
    app.processEvents()

    # One range left, so the queue would refuse to join and there is nothing
    # to list rather than a stale two-range card.
    assert "2 ranges joined" not in "".join(rows(listing)), rows(listing)
    window.set_view_mode(Mode.CLASSIC)


def test_reordering_the_assembly_while_flow_is_open_refreshes(window, app,
                                                              monkeypatch):
    """`_capture_assembly` is the only handler a drag reaches."""
    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1")]
    window.clips[1].selects = [Select(2.0, 6.0, "two", sid="r-2")]
    tick(window, 0)
    tick(window, 1)
    app.processEvents()
    monkeypatch.setattr(window.export_panel, "join_enabled", lambda: True)
    window._fill_assembly()
    app.processEvents()
    in_flow(window, app)

    before = window._sidebar_rebuilds
    window._capture_assembly()
    app.processEvents()

    assert window._sidebar_rebuilds > before, (
        "a reorder did not reach the sidebar at all")
    window.set_view_mode(Mode.CLASSIC)


# -- Assemble, and a picture on the pages that decide by looking (#84) ----------

def test_assemble_shows_the_panel_export_already_owns(window, app):
    """Borrowed, not rebuilt — asserted by identity, because a copy would look
    the same and share nothing."""
    owned = window.export_panel.assembly_panel
    in_flow(window, app)
    window._show_stage(Stage.ASSEMBLE)
    app.processEvents()

    # The stage has to actually be the one showing, or this passes on a build
    # where Assemble is not offered at all and `_show_stage` returns early.
    assert window._flow_stage is Stage.ASSEMBLE
    assert window.export_panel.assembly_panel is owned
    assert owned.parentWidget() is window._flow_slots[Stage.ASSEMBLE], (
        "the stage is showing something other than the panel ExportPanel owns")
    window.set_view_mode(Mode.CLASSIC)


def test_classic_gets_the_assembly_back_at_its_own_index(window, app):
    owned = window.export_panel.assembly_panel
    home = owned.parentWidget().layout()
    index = home.indexOf(owned)
    assert index >= 0

    in_flow(window, app)
    # It has to have left, or coming back proves nothing.
    assert home.indexOf(owned) < 0, "the assembly never moved"
    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()

    assert owned.parentWidget().layout() is home
    assert home.indexOf(owned) == index, "the assembly came back elsewhere"


def test_the_picture_is_one_object_in_each_page_that_has_one(window, app):
    """One picture, one player, one decoder.

    The approved design no longer hoists it above every stage: each page keeps
    it in its own region, and Queue has no region for it. What has to stay
    true is that it is the *same* object wherever it appears — the claim the
    old "above every stage" assertion was really protecting.
    """
    box = window.preview_view.preview_box
    in_flow(window, app)

    seen = []
    for stage in window._offered_stages:
        window._show_stage(stage)
        app.processEvents()
        host = window.flow_shell.host(stage, Region.VIEWPORT)
        if stage is Stage.QUEUE:
            assert host is None, "Queue grew a viewport region"
            # Out of the window entirely, not merely hidden in it. It
            # travels in its frame, so the frame is what has no parent.
            assert not window.isAncestorOf(box), (
                "the picture followed the page that has no region for it")
            continue
        assert host is not None
        seen.append(window.preview_view.preview_box)
        assert host.isAncestorOf(box), (
            f"{stage.value} did not receive the one picture")

    assert seen, "no page took the picture at all"
    assert all(one is box for one in seen), "a second picture was made"
    window.set_view_mode(Mode.CLASSIC)


def test_the_picture_goes_home_to_the_left_column(window, app):
    box = window.preview_view.preview_box
    home = window._left_column.layout()
    index = home.indexOf(box)
    assert index >= 0

    in_flow(window, app)
    assert home.indexOf(box) < 0, "it never left the column"
    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()

    assert home.indexOf(box) == index, "the picture came back elsewhere"


def test_the_music_band_keeps_its_panel_and_controls(window, app):
    """Hoisting the picture must not cost the Music stage anything."""
    panel = window.music_panel
    in_flow(window, app)
    window._show_stage(Stage.MUSIC)
    app.processEvents()

    assert window.music_panel is panel
    assert window.preview_view.listen_check is not None
    assert window.preview_view.restart_button is not None
    window.set_view_mode(Mode.CLASSIC)


# -- what the picture is allowed to say ----------------------------------------

def test_assemble_and_output_identify_what_the_picture_can_prove(window, app):
    """Assemble names a paused joined position; Output stays source-honest."""
    make_aba_assembly(window, app)
    tick(window, 0)
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    app.processEvents()
    in_flow(window, app)

    window._show_stage(Stage.ASSEMBLE)
    app.processEvents()
    said = window.flow_source_note.text()
    assert "Joined position" in said and "hdz_001.ts" in said
    assert "not continuous playback" in said
    assert "finished file" in said

    window._show_stage(Stage.OUTPUT)
    app.processEvents()
    said = window.flow_source_note.text()
    assert "not the finished file" in said
    window.set_view_mode(Mode.CLASSIC)


def test_browse_and_trim_are_not_captioned(window, app):
    """They already show source. A line under every page is one nobody reads."""
    in_flow(window, app)
    for stage in (Stage.BROWSE, Stage.TRIM):
        window._show_stage(stage)
        app.processEvents()
        assert window.flow_source_note.text() == ""
        assert window.flow_source_note.isHidden()
    window.set_view_mode(Mode.CLASSIC)


def test_with_nothing_in_focus_no_target_is_named(window, app):
    """An unresolved focus must not show some other output as though it were
    the one selected.

    Nothing is assigned here. A card nobody has clicked has no focus already,
    and reaching that state by writing `_trim_clip = None` tested the branch
    rather than any way of arriving at it.
    """
    assert window._trim_clip is None
    in_flow(window, app)
    window._show_stage(Stage.OUTPUT)
    app.processEvents()

    said = window.flow_source_note.text()
    assert "choose an output" in said
    assert "hdz_" not in said, said
    window.set_view_mode(Mode.CLASSIC)


def test_a_rescan_that_finds_nothing_stops_the_caption_naming_it(
        window, app, monkeypatch):
    """The focus outlives the list it came from, and the caption read it raw.

    Scanning clears the clips and the table and puts nothing down, so a
    recording that had just been rescanned away was still named as this
    output's source — a stale picture with a line underneath asserting it was
    current.
    """
    import tests.test_music_wiring as wiring

    tick(window, 0)
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    app.processEvents()
    in_flow(window, app)
    window._show_stage(Stage.OUTPUT)
    app.processEvents()
    assert "hdz_001.ts" in window.flow_source_note.text()

    monkeypatch.setattr("flightdvr.ui.ScanWorker", wiring._NoScan)
    window._scan()
    window._scan_done(window._scan_generation, 0)
    app.processEvents()

    said = window.flow_source_note.text()
    assert "hdz_001.ts" not in said, said
    assert "choose an output" in said, said
    window.set_view_mode(Mode.CLASSIC)


def test_choosing_another_assembly_row_moves_the_picture_and_the_caption(
        window, app, monkeypatch):
    """The stage borrows a list that already had a selection of its own.

    With a picture above it that selection acquired a claim it could not keep:
    nothing connected it to the focus, so choosing the second row left both
    the frame and the caption on the first.
    """
    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1")]
    window.clips[1].selects = [Select(2.0, 6.0, "two", sid="r-2")]
    tick(window, 0)
    tick(window, 1)
    app.processEvents()
    window._fill_assembly()
    app.processEvents()
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    app.processEvents()
    in_flow(window, app)
    window._show_stage(Stage.ASSEMBLE)
    app.processEvents()
    assert window._trim_clip is window.clips[0]

    shown = []
    monkeypatch.setattr(window.player, "load",
                        lambda clip, *a, **k: shown.append(clip))
    listing = window.export_panel.assembly_panel.list
    second = [listing.item(r) for r in range(listing.count())
              if listing.item(r).data(ASSEMBLY_ITEM_ROLE).fingerprint
              == window.clips[1].fingerprint]
    assert second, "the fill did not put the second recording in the list"
    listing.setCurrentItem(second[0])
    app.processEvents()

    assert window._trim_clip is window.clips[0], "joined scrub edited source focus"
    assert shown and shown[-1] is window.clips[1], shown
    assert window._sequence_occurrence.ordinal == 1
    assert "hdz_002.ts" in window.flow_source_note.text(), (
        window.flow_source_note.text())
    window.set_view_mode(Mode.CLASSIC)


def test_choosing_a_second_range_of_the_same_recording_moves_the_picture(
        window, app, monkeypatch):
    """Two ranges of one recording never reload the clip, so the frame only
    moves if the seek happens — the caption alone would prove nothing."""
    window.clips[0].selects = [
        Select(1.0, 5.0, "one", sid="r-1"),
        Select(6.0, 9.0, "two", sid="r-2"),
    ]
    tick(window, 0)
    app.processEvents()
    window._fill_assembly()
    app.processEvents()
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    app.processEvents()
    in_flow(window, app)
    window._show_stage(Stage.ASSEMBLE)
    app.processEvents()

    loaded = []
    monkeypatch.setattr(
        window.player, "load",
        lambda clip, position=0.0: loaded.append((clip, position)))
    listing = window.export_panel.assembly_panel.list
    rows_for = [listing.item(r) for r in range(listing.count())
                if listing.item(r).data(ASSEMBLY_ITEM_ROLE).sid == "r-2"]
    assert rows_for, "the second range is not in the list"
    listing.setCurrentItem(rows_for[0])
    app.processEvents()

    assert window.clips[0].current == 0, "joined scrub changed source range"
    assert loaded and loaded[-1] == (window.clips[0], 6.0), loaded
    assert window._sequence_occurrence.ordinal == 1
    assert "two" in window.flow_source_note.text(), (
        window.flow_source_note.text())
    window.set_view_mode(Mode.CLASSIC)


def test_arriving_on_assemble_with_a_row_selected_shows_that_row(window, app):
    """A selection made while another stage was showing is gated out, so the
    list can be pointing at one range while the picture holds another.
    Arriving is the moment that has to be settled — the caption is about to
    name whatever the picture is."""
    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1")]
    window.clips[1].selects = [Select(2.0, 6.0, "two", sid="r-2")]
    tick(window, 0)
    tick(window, 1)
    app.processEvents()
    window._fill_assembly()
    app.processEvents()
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    app.processEvents()
    in_flow(window, app)
    window._show_stage(Stage.BROWSE)
    app.processEvents()

    listing = window.export_panel.assembly_panel.list
    second = [listing.item(r) for r in range(listing.count())
              if listing.item(r).data(ASSEMBLY_ITEM_ROLE).fingerprint
              == window.clips[1].fingerprint]
    listing.setCurrentItem(second[0])
    app.processEvents()
    assert window._trim_clip is window.clips[0], "Browse took the selection"

    window._show_stage(Stage.ASSEMBLE)
    app.processEvents()

    assert window._trim_clip is window.clips[0], "joined scrub edited source focus"
    assert window._sequence_occurrence.ordinal == 1
    assert "hdz_002.ts" in window.flow_source_note.text(), (
        window.flow_source_note.text())
    window.set_view_mode(Mode.CLASSIC)


def test_redrawing_the_assembly_does_not_steal_the_focus(window, app):
    """`show_rows` puts the selection back, which emits the same signal a
    person clicking emits. Answering it would reload the clip on every refresh
    and drag the picture off whatever the table had just focused."""
    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1")]
    window.clips[1].selects = [Select(2.0, 6.0, "two", sid="r-2")]
    tick(window, 0)
    tick(window, 1)
    app.processEvents()
    window._fill_assembly()
    app.processEvents()
    in_flow(window, app)
    window._show_stage(Stage.ASSEMBLE)
    app.processEvents()

    listing = window.export_panel.assembly_panel.list
    second = [listing.item(r) for r in range(listing.count())
              if listing.item(r).data(ASSEMBLY_ITEM_ROLE).fingerprint
              == window.clips[1].fingerprint]
    listing.setCurrentItem(second[0])
    app.processEvents()
    assert window._trim_clip is None, "joined row selection edited source focus"
    assert window._sequence_occurrence.ordinal == 1

    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    app.processEvents()
    assert window._trim_clip is window.clips[0]

    window._refresh_assembly()
    app.processEvents()

    assert window._trim_clip is window.clips[0], (
        "a redraw put the focus back on the row it happened to reselect")
    window.set_view_mode(Mode.CLASSIC)


def test_classic_is_not_given_the_new_assembly_behaviour(window, app):
    """The same panel sits in Classic's Output group, where picking rows to
    move or remove has never loaded anything. This slice does not change
    what Classic does."""
    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1")]
    window.clips[1].selects = [Select(2.0, 6.0, "two", sid="r-2")]
    tick(window, 0)
    tick(window, 1)
    app.processEvents()
    window._fill_assembly()
    app.processEvents()
    assert window._view_mode is Mode.CLASSIC

    listing = window.export_panel.assembly_panel.list
    listing.setCurrentRow(listing.count() - 1)
    app.processEvents()

    assert window._trim_clip is None


# -- the assembled clock and its source boundary ------------------------------

def test_literal_aba_scrubs_following_occurrence_seams_and_terminal(
        window, app, monkeypatch):
    """2.999 stays in A; 3 and 5 choose following B/A; 8 decodes nothing."""
    make_aba_assembly(window, app)
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    source_before = (
        window._trim_clip,
        window.clips[0].current,
        [(one.start, one.end, one.sid) for one in window.clips[0].selects],
        window.trim_bar.in_point,
        window.trim_bar.out_point,
        window.trim_bar.playhead,
    )

    loaded, sought, shown = [], [], []
    monkeypatch.setattr(
        window.player, "load",
        lambda clip, position=0.0: loaded.append((clip, position)))
    monkeypatch.setattr(window.player, "seek", lambda at: sought.append(at))
    monkeypatch.setattr(window.player, "show_frame_at",
                        lambda at: shown.append(at))

    in_flow(window, app)
    window._show_stage(Stage.ASSEMBLE)
    app.processEvents()
    strip = window.preview_view.sequence_strip
    assert not strip.isHidden()
    assert strip.seams == (0.0, 3.0, 5.0, 8.0)
    loaded.clear()
    sought.clear()
    shown.clear()

    strip.request_position(2.999)
    window._sharpen_timer.stop()
    window._sharpen()
    assert window._sequence_occurrence.ordinal == 0
    assert sought[-1] == 12.999
    assert shown[-1] == 12.999

    strip.request_position(3.0)
    window._sharpen_timer.stop()
    window._sharpen()
    assert window._sequence_occurrence.ordinal == 1
    assert loaded[-1] == (window.clips[1], 2.0)
    assert shown[-1] == 2.0

    strip.request_position(5.0)
    window._sharpen_timer.stop()
    window._sharpen()
    assert window._sequence_occurrence.ordinal == 2
    assert loaded[-1] == (window.clips[0], 10.0)
    assert shown[-1] == 10.0

    decoder_calls = (len(loaded), len(sought), len(shown))
    strip.request_position(8.0)
    assert window._sequence_occurrence is None
    assert (len(loaded), len(sought), len(shown)) == decoder_calls
    assert "terminal requests no source frame" in window.flow_source_note.text()

    # The actual mouse route reaches the same output clock: with an 800 px
    # inner track, its midpoint is output 4.0 and therefore B at source 3.0.
    strip.resize(802, 42)
    QTest.mouseClick(
        strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        QPoint(401, 21))
    assert window._sequence_occurrence.ordinal == 1
    assert loaded[-1] == (window.clips[1], 3.0)

    source_after = (
        window._trim_clip,
        window.clips[0].current,
        [(one.start, one.end, one.sid) for one in window.clips[0].selects],
        window.trim_bar.in_point,
        window.trim_bar.out_point,
        window.trim_bar.playhead,
    )
    assert source_after == source_before, "joined scrubbing changed source trim"
    window.set_view_mode(Mode.CLASSIC)


def test_reorder_revises_the_plan_and_refuses_old_scrubs_and_frames(
        window, app, monkeypatch):
    make_aba_assembly(window, app)
    loaded = []
    monkeypatch.setattr(
        window.player, "load",
        lambda clip, position=0.0: loaded.append((clip, position)))
    monkeypatch.setattr(window.player, "seek", lambda *_: None)
    monkeypatch.setattr(window.player, "show_frame_at", lambda *_: None)
    in_flow(window, app)
    window._show_stage(Stage.ASSEMBLE)
    app.processEvents()
    old_revision = window._sequence_plan.revision

    first, second = window.clips
    window._store_assembly([
        Item(second.fingerprint, "b"),
        Item(first.fingerprint, "a"),
        Item(first.fingerprint, "a"),
    ])
    app.processEvents()
    current = window._sequence_plan
    assert current.revision != old_revision
    assert current.occurrences[0].fingerprint == second.fingerprint

    loaded.clear()
    window.preview_view.sequence_strip.scrub_requested.emit(old_revision, 0.0)
    assert loaded == [], "an old drag was reinterpreted on the reordered plan"

    # PreviewPlayer owns the callback fence.  A retired precise worker must
    # not publish through the signal the window trusts.
    published = []
    window.player.precise_frame_ready.connect(
        lambda *args: published.append(args))
    window.player._frame_generation = 12
    window.player._frame_window_ready(11, object())
    assert published == []

    window.preview_view.sequence_strip.request_position(0.0)
    assert loaded[-1] == (second, 2.0)
    window.set_view_mode(Mode.CLASSIC)


def test_joined_precise_frame_cannot_become_source_trim_or_still_authority(
        window, app, monkeypatch):
    """The precise callback paints, but joined seconds stay out of source UI."""
    make_aba_assembly(window, app)
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    monkeypatch.setattr(window.player, "load", lambda *a, **k: None)
    monkeypatch.setattr(window.player, "seek", lambda *a, **k: None)
    monkeypatch.setattr(window.player, "show_frame_at", lambda *a, **k: None)

    in_flow(window, app)
    window._show_stage(Stage.ASSEMBLE)
    window.preview_view.sequence_strip.request_position(3.0)
    assert window._sequence_occurrence.ordinal == 1
    assert window._sequence_source_seconds == 2.0
    source_before = (
        window._trim_clip,
        window.clips[0].current,
        window.trim_bar.in_point,
        window.trim_bar.out_point,
        window.trim_bar.playhead,
    )

    image = QImage(2, 2, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.red)
    window._precise_frame_ready(image, 2.0, 17)

    assert window._precise_frame_number is None
    assert window._precise_frame_seconds is None
    assert not window.still_button.isEnabled()
    assert (
        window._trim_clip,
        window.clips[0].current,
        window.trim_bar.in_point,
        window.trim_bar.out_point,
        window.trim_bar.playhead,
    ) == source_before
    window.set_view_mode(Mode.CLASSIC)


def test_joined_assemble_routes_play_and_listen_but_guards_source_edits(
        window, app, monkeypatch):
    make_aba_assembly(window, app)
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    in_flow(window, app)
    window._show_stage(Stage.ASSEMBLE)
    app.processEvents()
    before = [(one.start, one.end, one.sid)
              for one in window.clips[0].selects]

    calls = []
    monkeypatch.setattr(window.player, "load",
                        lambda *a, **k: calls.append("load"))
    monkeypatch.setattr(window.player, "load_sequence",
                        lambda *a, **k: calls.append("sequence"))
    monkeypatch.setattr(window.player, "toggle",
                        lambda *a, **k: calls.append("toggle"))
    monkeypatch.setattr(window.player, "step_frames",
                        lambda *a, **k: calls.append("step"))
    monkeypatch.setattr(window.player, "seek",
                        lambda *a, **k: calls.append("seek"))
    monkeypatch.setattr(window.player, "play",
                        lambda *a, **k: calls.append("play"))
    monkeypatch.setattr(window.player, "stop",
                        lambda *a, **k: calls.append("stop"))
    monkeypatch.setattr(window, "_prepare_monitoring",
                        lambda *a, **k: calls.append("monitor"))
    monkeypatch.setattr(window.live_preview, "restart",
                        lambda *a, **k: calls.append("restart"))
    monkeypatch.setattr(window.live_preview, "set_listening",
                        lambda *a, **k: calls.append("listen mode"))

    window._toggle_play()
    window._step_frames(1)
    window._nudge(1.0)
    window._jump(4.0)
    window._set_in()
    window._set_out()
    window._add_select()
    window._play_selected()
    window._play_item(window.table.item(0, 0))
    window._stop_preview()
    window._on_listen_toggled(True)
    window._on_listening_changed("source")
    window._on_monitor_restart()

    assert calls[:3] == ["sequence", "play", "stop"]
    assert calls.count("monitor") == 1
    assert "listen mode" in calls
    assert "restart" in calls
    assert [(one.start, one.end, one.sid)
            for one in window.clips[0].selects] == before
    assert not window.live_preview.status.playing
    assert "return to Browse or Trim" in window.statusBar().currentMessage()
    window.set_view_mode(Mode.CLASSIC)


def test_joined_play_uses_output_ticks_and_never_promotes_source_authority(
        window, app, monkeypatch):
    make_aba_assembly(window, app)
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    source_before = (
        window._trim_clip,
        window.trim_bar.in_point,
        window.trim_bar.out_point,
        window.trim_bar.playhead,
    )
    in_flow(window, app)
    window._show_stage(Stage.ASSEMBLE)
    plan = window._sequence_plan
    assert plan is not None

    def start_without_ffmpeg(_width=0):
        window.player.is_playing = True
        window.player.state_changed.emit(True)

    monkeypatch.setattr(window.player, "play", start_without_ffmpeg)
    window._toggle_play()
    assert window.player.sequence_revision == plan.revision
    assert window.player._sequence_clips == {
        occurrence.id: window._sequence_clip(occurrence)
        for occurrence in plan.occurrences
    }
    assert window.live_preview.status.muted
    assert not window.live_preview.status.playing

    image = QImage(2, 2, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.green)
    second = plan.occurrences[1]
    window.player.sequence_frame_ready.emit(
        image, plan.revision, second.id, 3.0, 2.0)
    window.player.playback_tick.emit(3.0, False)

    assert window.preview_view.sequence_strip.position == 3.0
    assert window._sequence_occurrence == second.id
    assert window._sequence_source_seconds == 2.0
    assert window._precise_frame_number is None
    assert window._precise_frame_seconds is None
    assert not window.still_button.isEnabled()
    assert (
        window._trim_clip,
        window.trim_bar.in_point,
        window.trim_bar.out_point,
        window.trim_bar.playhead,
    ) == source_before
    assert "playing joined picture" in window.flow_source_note.text()
    assert "sound is not active" in window.flow_source_note.text()
    assert "finished exported file is not previewed" in (
        window.flow_source_note.text())
    window.set_view_mode(Mode.CLASSIC)


def test_reorder_fences_joined_frames_before_installing_the_new_revision(
        window, app, monkeypatch):
    make_aba_assembly(window, app)
    in_flow(window, app)
    window._show_stage(Stage.ASSEMBLE)
    old = window._sequence_plan
    resolved = {
        occurrence.id: window._sequence_clip(occurrence)
        for occurrence in old.occurrences
    }
    window.player.load_sequence(old, resolved)
    painted = []
    monkeypatch.setattr(window.frame_view, "set_image",
                        lambda image: painted.append(image))

    first, second = window.clips
    window._store_assembly([
        Item(second.fingerprint, "b"),
        Item(first.fingerprint, "a"),
        Item(first.fingerprint, "a"),
    ])
    app.processEvents()
    current = window._sequence_plan
    assert current.revision != old.revision
    assert window.player.sequence_revision is None

    image = QImage(2, 2, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.red)
    window.player.sequence_frame_ready.emit(
        image, old.revision, old.occurrences[0].id, 0.0, 10.0)
    assert painted == []
    window.set_view_mode(Mode.CLASSIC)


def test_precise_callback_after_joined_play_starts_is_ignored_entirely(
        window, app, monkeypatch):
    make_aba_assembly(window, app)
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    in_flow(window, app)
    window._show_stage(Stage.ASSEMBLE)
    plan = window._sequence_plan
    resolved = {
        occurrence.id: window._sequence_clip(occurrence)
        for occurrence in plan.occurrences
    }
    window.player.load_sequence(plan, resolved)
    painted = []
    monkeypatch.setattr(window.frame_view, "set_image",
                        lambda image: painted.append(image))
    before = window.trim_bar.playhead

    image = QImage(2, 2, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.blue)
    window._precise_frame_ready(image, 10.0, 600)

    assert painted == []
    assert window.trim_bar.playhead == before
    assert window._precise_frame_number is None
    assert not window.still_button.isEnabled()
    window.set_view_mode(Mode.CLASSIC)


def test_mixed_sources_are_inspected_nominally_and_browse_restores_source(
        window, app, monkeypatch):
    make_aba_assembly(window, app)
    window.clips[1].width = 1920
    window.clips[1].height = 1080
    window.clips[1].fps = 90.0
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    source = window._trim_clip
    source_position = window.trim_bar.playhead

    loaded, toggled = [], []
    monkeypatch.setattr(
        window.player, "load",
        lambda clip, position=0.0: loaded.append((clip, position)))
    monkeypatch.setattr(window.player, "seek", lambda *_: None)
    monkeypatch.setattr(window.player, "show_frame_at", lambda *_: None)
    monkeypatch.setattr(window.player, "toggle",
                        lambda *a, **k: toggled.append(True))

    in_flow(window, app)
    window._show_stage(Stage.ASSEMBLE)
    app.processEvents()
    assert window._sequence_plan is not None
    window.preview_view.sequence_strip.request_position(3.0)
    assert loaded[-1] == (window.clips[1], 2.0)
    assert "source-frame inspection" in window.flow_source_note.text()

    loaded.clear()
    window._show_stage(Stage.BROWSE)
    assert loaded[-1] == (source, source_position)
    assert window.preview_view.sequence_strip.isHidden()
    window._toggle_play()
    assert toggled == [True], "Browse did not regain source playback"
    window.set_view_mode(Mode.CLASSIC)


def test_the_caption_follows_the_focused_range(window, app):
    window.clips[0].selects = [
        Select(1.0, 5.0, "one", sid="r-1"),
        Select(6.0, 9.0, "two", sid="r-2"),
    ]
    tick(window, 0)
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    app.processEvents()
    in_flow(window, app)
    window._show_stage(Stage.OUTPUT)
    app.processEvents()
    assert "one" in window.flow_source_note.text()

    window._pick_select(1)
    app.processEvents()

    assert "two" in window.flow_source_note.text(), (
        window.flow_source_note.text())
    window.set_view_mode(Mode.CLASSIC)


# -- real actions, after entering Flow -----------------------------------------

def test_filling_the_assembly_after_entering_flow_reaches_the_stage(
        window, app):
    """One real action with the join state set first — no second store, and no
    patched state standing in for the mutation."""
    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1")]
    window.clips[1].selects = [Select(2.0, 6.0, "two", sid="r-2")]
    tick(window, 0)
    tick(window, 1)
    app.processEvents()
    in_flow(window, app)
    window._show_stage(Stage.ASSEMBLE)
    app.processEvents()

    # No patched `join_enabled`. It reads `assembly_panel.is_empty()`, which
    # the fill is supposed to change — patching it handed the test the very
    # answer the action was being asked to produce.
    window._fill_assembly()
    app.processEvents()

    assert not window.export_panel.assembly_panel.is_empty()
    listed = rows(window.sidebar_working)
    assert len(listed) == 1 and "joined" in listed[0], listed
    window.set_view_mode(Mode.CLASSIC)


def test_moving_between_stages_makes_no_second_player_or_worker(window, app):
    before = counts(window)
    in_flow(window, app)
    for stage in window._offered_stages:
        window._show_stage(stage)
        app.processEvents()
    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()

    assert counts(window) == before


def test_entering_a_stage_starts_no_playback_and_no_sound(window, app):
    in_flow(window, app)
    for stage in window._offered_stages:
        window._show_stage(stage)
        app.processEvents()
        assert window.live_preview.status.muted
        assert not window.live_preview.status.playing
    window.set_view_mode(Mode.CLASSIC)


def test_the_queue_is_reachable_from_the_new_stage_too(window, app):
    in_flow(window, app)
    window._show_stage(Stage.ASSEMBLE)
    app.processEvents()
    assert window.queue_panel.parentWidget() is not None
    assert window.queue_panel.start_button is not None
    window.set_view_mode(Mode.CLASSIC)


def test_a_round_trip_with_the_picture_hoisted_changes_nothing(window, app):
    decide_something(window)
    app.processEvents()
    before = snapshot(window)

    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    window._show_stage(Stage.ASSEMBLE)
    app.processEvents()
    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()

    assert snapshot(window) == before


# -- the one answer to "what is being looked at" -------------------------------


def two_planned_targets(window, app):
    """Two ordinary planned outputs, each with its own music."""
    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1")]
    window.clips[1].selects = [Select(2.0, 6.0, "two", sid="r-2")]
    tick(window, 0)
    tick(window, 1)
    app.processEvents()
    outputs = window._working_outputs()
    assert len(outputs) == 2, outputs
    return outputs[0].target, outputs[1].target


def test_a_to_b_to_a_gives_a_back_unchanged(window, app):
    """Settings belong to this output. If a context read whatever the panels
    were showing, B's music would come back as A's and the next commit would
    render something nobody chose."""
    first, second = two_planned_targets(window, app)
    window._store_music(first, MusicChoice(mode=AudioMode.NO_SOUND))
    window._store_music(second, MusicChoice(mode=AudioMode.ORIGINAL))

    there = window.context_for_working(second)
    back = window.context_for_working(first)

    assert back.music.mode is AudioMode.NO_SOUND, "A did not come back as A"
    assert there.music.mode is AudioMode.ORIGINAL
    assert back.target == first and there.target == second
    assert back.editable and there.editable


def test_a_submitted_context_keeps_its_own_values_after_the_plan_moves_on(
        window, app):
    """The queue's promise, made structural: editing the plan a job came from
    does not reach the job."""
    first, _second = two_planned_targets(window, app)
    window._store_music(first, MusicChoice(mode=AudioMode.NO_SOUND))
    job = Job(
        [window.clips[0]], "master", window.current_settings(),
        Path(window.export_panel.out_edit.currentText()) / "committed.mp4",
        audio=MusicChoice(mode=AudioMode.ORIGINAL), target=first)
    submitted = window.context_for_job(job)

    window._store_music(first, MusicChoice(mode=AudioMode.NO_SOUND))
    window.export_panel.preset_buttons["social"].click()
    app.processEvents()

    after = window.context_for_job(job)
    assert after.music.mode is AudioMode.ORIGINAL, "a later edit reached it"
    assert after.preset_key == "master", "the job's preset followed the panel"
    assert not after.editable and after.submitted


def test_a_source_context_does_not_impersonate_a_planned_selection(window, app):
    """Browse and Trim may inspect a recording without changing which output
    is selected — so a source context carries no target at all."""
    first, _second = two_planned_targets(window, app)
    # A planned output really is selected, or the assertion below would hold
    # for the wrong reason: with nothing selected, a context that leaked the
    # selection would still report None.
    window._sidebar_target = first
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    app.processEvents()
    assert window._sidebar_target == first

    looking = window.context_for_source(window._trim_clip)

    assert looking.target is None, "source inspection took the planned target"
    assert not looking.editable
    assert looking.clock.value == "source"
    assert window.context_for_working(first).clock.value == "output"


def test_an_ordinary_planned_output_is_not_read_on_source_time(window, app):
    """The trap: one range is still an output. Its first sample is output
    zero while the recording is somewhere else entirely."""
    first, _second = two_planned_targets(window, app)

    assert window.context_for_working(first).clock.value == "output"
    assert window.context_for_working(first).bound_to_sequence is False


def test_the_fixed_actions_say_what_this_page_can_do(window, app):
    """All versus one must be unmistakable, and neither is called "render":
    queued work waits for Start. Queue's own page cannot queue more, and
    Cancel is off while nothing is running."""
    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1")]
    tick(window, 0)
    app.processEvents()
    in_flow(window, app)

    window._show_stage(Stage.OUTPUT)
    app.processEvents()
    shell = window.flow_shell
    assert shell.primary_button.text() == "Queue all planned (1)"
    assert shell.primary_button.isEnabled(), "nothing planned to queue"
    assert "render" not in shell.primary_button.text().lower()
    assert window.export_panel.add_button.text() == "Queue this output"
    assert not shell.secondary_button.isEnabled(), "Cancel with nothing running"

    window._show_stage(Stage.QUEUE)
    app.processEvents()

    assert not shell.primary_button.isEnabled(), "Queue offered to queue more"
    assert shell.secondary_button.text() == "Cancel this render"
    window.set_view_mode(Mode.CLASSIC)
    assert window.export_panel.add_button.text() == "Add to queue", (
        "Classic lost its own wording")


def test_commit_is_offered_only_when_there_is_something_to_commit(window, app):
    """An enabled action that refuses is worse than a disabled one."""
    in_flow(window, app)
    window._show_stage(Stage.OUTPUT)
    app.processEvents()
    assert not window.flow_shell.primary_button.isEnabled()

    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1")]
    tick(window, 0)
    app.processEvents()
    window._show_stage(Stage.OUTPUT)
    app.processEvents()

    assert window.flow_shell.primary_button.isEnabled()
    window.set_view_mode(Mode.CLASSIC)


def test_the_sidebar_says_where_editing_stops_reaching(window, app):
    """The committed half and its line appear together with a real job, and
    not before — an empty heading says a thing exists and is broken."""
    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1")]
    tick(window, 0)
    app.processEvents()
    in_flow(window, app)
    sidebar = window.output_sidebar
    assert rows(sidebar.planned), "nothing planned to show"
    assert sidebar.committed_note.isHidden(), "the boundary showed with no job"

    window.jobs.append(Job(
        [window.clips[0]], "master", window.current_settings(),
        Path(window.export_panel.out_edit.currentText()) / "committed.mp4"))
    window._rebuild_queue()
    app.processEvents()

    assert not sidebar.committed_note.isHidden()
    assert "does not reach" in sidebar.committed_note.text()
    assert len(rows(sidebar.committed)) == 1
    window.set_view_mode(Mode.CLASSIC)


def test_a_round_trip_keeps_the_output_you_were_editing_selected(window, app):
    """Leaving Flow and coming back must not quietly change which output is
    selected: the next edit would land somewhere nobody chose.

    The sidebar restores a surviving selection of its own, but a round trip
    builds its lists from empty, so the window has to say which target it is
    still on.
    """
    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1")]
    window.clips[1].selects = [Select(2.0, 6.0, "two", sid="r-2")]
    tick(window, 0)
    tick(window, 1)
    app.processEvents()
    in_flow(window, app)
    second = window._working_outputs()[1].target
    window._on_sidebar_choice(second)
    app.processEvents()
    assert window.output_sidebar.selected_key == second

    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()
    window.set_view_mode(Mode.FLOW)
    app.processEvents()

    assert window._sidebar_target == second
    assert window.output_sidebar.selected_key == second, (
        "the round trip lost which output was being edited")
    window.set_view_mode(Mode.CLASSIC)


# -- both selectors, one answer ------------------------------------------------


def test_choosing_in_for_moves_the_sidebar_to_the_same_output(window, app):
    """The second output, not the first: a selector that let its first row
    stand in for the choice would pass a test that only ever picked row 0."""
    first, second = two_planned_targets(window, app)
    in_flow(window, app)
    combo = window.export_panel.target_combo
    assert combo.count() == 2

    combo.setCurrentIndex(1)
    app.processEvents()

    assert window._sidebar_target == second
    assert window.output_sidebar.selected_key == second, (
        "the sidebar still points at a different output")
    assert window.export_panel.selected_target() == second
    window.set_view_mode(Mode.CLASSIC)


def test_choosing_a_card_moves_for_to_the_same_output(window, app):
    first, second = two_planned_targets(window, app)
    in_flow(window, app)

    window.output_sidebar.planned.item(1).setSelected(True)
    app.processEvents()

    assert window._sidebar_target == second
    assert window.export_panel.selected_target() == second, (
        "Output's For: still names a different output")
    window.set_view_mode(Mode.CLASSIC)


def test_choosing_an_output_is_loading_not_editing(window, app):
    """Choosing changes no setting and queues nothing."""
    first, second = two_planned_targets(window, app)
    window._store_music(first, MusicChoice(mode=AudioMode.NO_SOUND))
    window._store_music(second, MusicChoice(mode=AudioMode.ORIGINAL))
    in_flow(window, app)
    before = snapshot(window)

    window.export_panel.target_combo.setCurrentIndex(1)
    app.processEvents()
    window.export_panel.target_combo.setCurrentIndex(0)
    app.processEvents()

    after = snapshot(window)
    assert after["music"] == before["music"], "choosing rewrote a choice"
    assert after["jobs"] == before["jobs"] == [], "choosing queued something"
    window.set_view_mode(Mode.CLASSIC)


def test_for_is_a_flow_control_and_classic_is_unchanged(window, app):
    two_planned_targets(window, app)
    row = window.export_panel.target_row
    assert row.isHidden(), "Classic grew a For: selector"

    in_flow(window, app)
    assert not row.isHidden()

    window.set_view_mode(Mode.CLASSIC)
    assert row.isHidden()


def test_for_shows_no_choice_rather_than_a_stand_in(window, app):
    """When the chosen output no longer exists, the list must not quietly
    offer its first row as though that had been picked."""
    from PySide6.QtCore import Qt

    two_planned_targets(window, app)
    in_flow(window, app)
    # Pick the output that belongs to table row 1, whatever the sort order,
    # so unticking that row genuinely removes the output that is selected.
    path = window.table.item(1, 0).data(Qt.ItemDataRole.UserRole)
    doomed = next(one.target for one in window._working_outputs()
                  if one.target.items[0].fingerprint
                  == window.clip_by_path[path].fingerprint)
    window._select_working_target(doomed)
    app.processEvents()
    assert window.export_panel.selected_target() == doomed

    window.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
    window._refresh_sidebar()
    app.processEvents()

    assert window.export_panel.target_combo.count() == 1
    assert window.export_panel.selected_target() is None, (
        "the first remaining output stood in for a choice nobody made")
    window.set_view_mode(Mode.CLASSIC)


def test_the_handler_tells_both_selectors_itself(window, app, monkeypatch):
    """Each selector is told the answer by the one handler, not by whatever
    happens to refresh afterwards.

    Checked with the focus step stubbed out on purpose. Choosing an output
    usually loads a clip, and loading a clip usually refreshes the sidebar,
    which re-syncs both lists as a side effect — so a handler that forgot to
    tell the other selector passed every end-to-end test. That refresh does
    not happen when the chosen recording is already the one loaded, which is
    exactly when the two selectors would be left disagreeing.
    """
    first, second = two_planned_targets(window, app)
    in_flow(window, app)
    monkeypatch.setattr(window, "_focus_piece", lambda *a, **k: None)

    window.export_panel.target_combo.setCurrentIndex(1)
    app.processEvents()
    assert window.output_sidebar.selected_key == second, (
        "choosing in For: did not tell the sidebar")

    window.output_sidebar.planned.item(0).setSelected(True)
    app.processEvents()
    assert window.export_panel.selected_target() == first, (
        "choosing a card did not tell For:")
    window.set_view_mode(Mode.CLASSIC)


def test_filling_for_announces_nothing(window, app):
    """The panel's own fence, exercised outside a sidebar rebuild — inside one
    the window's fence would absorb the emit and hide the panel's."""
    first, second = two_planned_targets(window, app)
    heard = []
    window.export_panel.target_chosen.connect(heard.append)

    window.export_panel.show_targets([("one", first), ("two", second)], None)
    window.export_panel.show_targets([("two", second)], second)

    assert heard == [], f"filling the list announced a choice: {heard}"


def test_for_promises_per_output_settings_only_now_they_are_kept(window, app):
    """Was pinned the other way while the queue used one preset for every
    output. The promise is made now because the batch builds each output from
    its own entry — and the folder and names, which really are batch-wide,
    are said to be."""
    two_planned_targets(window, app)
    in_flow(window, app)
    said = window.export_panel.target_note.text()

    assert "Settings belong to this output" in said
    assert "every planned output" in said, "batch-wide choices not stated"
    window.set_view_mode(Mode.CLASSIC)


def test_music_says_its_picture_is_not_the_finished_file(window, app):
    """Music decides things about an output, and until W5 the picture under
    it is still the source. Keyed on a list of page names, Music was left
    uncaptioned — a source frame under a page about the finished output, with
    nothing saying so."""
    first, _second = two_planned_targets(window, app)
    in_flow(window, app)
    window._select_working_target(first)
    app.processEvents()

    window._show_stage(Stage.MUSIC)
    app.processEvents()

    said = window.flow_source_note.text()
    assert "not the finished file" in said, repr(said)
    assert not window.flow_source_note.isHidden()
    window.set_view_mode(Mode.CLASSIC)


def test_source_inspection_stays_uncaptioned_with_an_output_selected(window,
                                                                      app):
    """Browse and Trim request a source context on purpose, and keep the
    planned selection while they do. Source shown as source needs no line."""
    first, _second = two_planned_targets(window, app)
    in_flow(window, app)
    window._select_working_target(first)
    app.processEvents()

    for stage in (Stage.BROWSE, Stage.TRIM):
        window._show_stage(stage)
        app.processEvents()
        assert window._active_context(stage).clock.value == "source"
        assert window.flow_source_note.text() == "", stage
    assert window._sidebar_target == first, "inspecting source lost the output"
    window.set_view_mode(Mode.CLASSIC)


def test_moving_between_output_pages_keeps_the_same_output_and_clock(window,
                                                                      app):
    """Next changes the page, not the material: Output and Music both show the
    selected output on its own clock."""
    first, second = two_planned_targets(window, app)
    in_flow(window, app)
    window._select_working_target(second)
    app.processEvents()

    seen = []
    for stage in (Stage.MUSIC, Stage.OUTPUT):
        window._show_stage(stage)
        app.processEvents()
        context = window._active_context(stage)
        seen.append((context.target, context.clock.value))

    assert seen == [(second, "output"), (second, "output")], seen
    window.set_view_mode(Mode.CLASSIC)


def test_inspecting_another_recording_does_not_move_the_selected_output(
        window, app):
    """Source focus and the planned selection may legitimately differ.

    Select output A, go to Browse and inspect a *different* recording, come
    back to Output: Output must still be showing A. Anything that took the
    target from the focused clip would silently show — and let you edit —
    the output belonging to whatever you last looked at.
    """
    from PySide6.QtCore import Qt

    first, _second = two_planned_targets(window, app)
    in_flow(window, app)
    window._select_working_target(first)
    app.processEvents()

    window._show_stage(Stage.BROWSE)
    other_row = next(
        row for row in range(window.table.rowCount())
        if window.clip_by_path[window.table.item(row, 0).data(
            Qt.ItemDataRole.UserRole)].fingerprint
        != first.items[0].fingerprint)
    window.table.setCurrentCell(other_row, 0)
    window._load_selected_clip()
    app.processEvents()
    assert window._trim_clip.fingerprint != first.items[0].fingerprint, (
        "the fixture never moved focus to another recording")

    window._show_stage(Stage.OUTPUT)
    app.processEvents()

    assert window._active_context(Stage.OUTPUT).target == first, (
        "Output followed the inspected recording instead of the selection")
    window.set_view_mode(Mode.CLASSIC)


# -- each output keeps its own preset and settings -----------------------------
#
# Expected values are what each test *sets*, never read back through the code
# under test. Outputs are addressed by target, never by row position, so a
# selector letting its first row stand in for a choice cannot pass by luck.


def silence_dialogs(monkeypatch):
    said = []
    monkeypatch.setattr(
        "flightdvr.ui.QMessageBox.warning",
        lambda _parent, title, text, *a, **k: said.append((title, text)))
    return said


def planned_pair(window, app):
    """Two outputs from two recordings, and Flow open."""
    first, second = two_planned_targets(window, app)
    in_flow(window, app)
    return first, second


def choose(window, app, target, preset, **fields):
    """Select an output, then make genuine edits through the panel."""
    window._select_working_target(target)
    app.processEvents()
    window.export_panel.preset_buttons[preset].setChecked(True)
    app.processEvents()
    if "size_mb" in fields:
        window.export_panel.social_size.setValue(fields["size_mb"])
        app.processEvents()


def jobs_by_source(window):
    return {job.clips[0].fingerprint: job for job in window.jobs}


def test_each_output_keeps_its_own_preset_across_a_b_a(window, app):
    first, second = planned_pair(window, app)
    choose(window, app, first, "social", size_mb=25)
    choose(window, app, second, "master")

    window._select_working_target(first)
    app.processEvents()

    assert window.export_panel.preset_key() == "social"
    assert window.export_panel.social_size.value() == 25
    assert window.output_plan.get(second).preset_key == "master"
    assert window.output_plan.get(first).settings.social_size_mb == 25
    window.set_view_mode(Mode.CLASSIC)


def test_loading_an_output_writes_nothing_to_the_plan(window, app,
                                                      monkeypatch):
    """Load is not edit: a pure A to B to A navigation records no choice."""
    first, second = planned_pair(window, app)
    choose(window, app, first, "social")
    choose(window, app, second, "master")
    writes = []
    real = window.output_plan.set_choices
    monkeypatch.setattr(window.output_plan, "set_choices",
                        lambda *a, **k: (writes.append(a), real(*a, **k))[1])

    for target in (first, second, first):
        window._select_working_target(target)
        app.processEvents()

    assert writes == [], f"loading recorded {len(writes)} choice(s)"
    window.set_view_mode(Mode.CLASSIC)


def test_queue_all_planned_gives_each_job_its_own_choices(window, app,
                                                          monkeypatch):
    silence_dialogs(monkeypatch)
    first, second = planned_pair(window, app)
    choose(window, app, first, "social", size_mb=25)
    choose(window, app, second, "master")

    window.flow_shell.primary_button.click()
    app.processEvents()

    jobs = jobs_by_source(window)
    a = jobs[first.items[0].fingerprint]
    b = jobs[second.items[0].fingerprint]
    assert (a.preset_key, b.preset_key) == ("social", "master")
    assert a.settings.social_size_mb == 25
    assert "_social" in a.out_path.name and "_master" in b.out_path.name
    window.set_view_mode(Mode.CLASSIC)


def test_queue_this_output_queues_only_that_output(window, app, monkeypatch):
    silence_dialogs(monkeypatch)
    first, second = planned_pair(window, app)
    choose(window, app, second, "master")
    choose(window, app, first, "social")
    b_before = window.output_plan.get(second)

    window.export_panel.add_button.click()
    app.processEvents()

    assert [job.preset_key for job in window.jobs] == ["social"]
    assert window.jobs[0].clips[0].fingerprint == first.items[0].fingerprint
    assert second in window._active_targets(), "B stopped being planned"
    assert window.output_plan.get(second) == b_before, "B's choices moved"
    window.set_view_mode(Mode.CLASSIC)


def test_a_queued_job_does_not_follow_later_edits(window, app, monkeypatch):
    silence_dialogs(monkeypatch)
    first, _second = planned_pair(window, app)
    choose(window, app, first, "social", size_mb=25)
    window.export_panel.add_button.click()
    app.processEvents()
    job = window.jobs[0]

    window.export_panel.social_size.setValue(40)
    window.export_panel.preset_buttons["upload"].setChecked(True)
    app.processEvents()

    assert (job.preset_key, job.settings.social_size_mb) == ("social", 25)
    window.set_view_mode(Mode.CLASSIC)


def test_a_mixed_batch_queues_nothing_and_names_what_failed(window, app,
                                                            monkeypatch):
    """A valid B must not be queued alone because A beside it failed."""
    said = silence_dialogs(monkeypatch)
    first, second = planned_pair(window, app)
    narrow = next(clip for clip in window.clips
                  if clip.fingerprint == first.items[0].fingerprint)
    narrow.width, narrow.height = 300, 720      # too narrow for 9:16
    choose(window, app, first, "vertical")
    choose(window, app, second, "master")

    window.flow_shell.primary_button.click()
    app.processEvents()

    assert window.jobs == [], "part of a failing batch was queued"
    assert said and narrow.path.name in said[-1][1], said
    window.set_view_mode(Mode.CLASSIC)


def test_two_outputs_that_would_share_a_file_queue_nothing(window, app,
                                                           monkeypatch):
    """Across presets too: two names that collide mean one file survives."""
    said = silence_dialogs(monkeypatch)
    window.clips[0].selects = [Select(1.0, 5.0, "one", sid="r-1"),
                               Select(6.0, 9.0, "two", sid="r-2")]
    tick(window, 0)
    app.processEvents()
    in_flow(window, app)
    first, second = [one.target for one in window._working_outputs()]
    window.export_panel.template_edit.setText("{clip}")
    # Without per-preset subfolders, so both genuinely land in one folder.
    # With them on, Social and Upload write to different folders and do not
    # collide — which is correct, and made the first version of this test
    # prove nothing.
    window.export_panel.subfolder_check.setChecked(False)
    choose(window, app, first, "social")
    choose(window, app, second, "upload")

    window.flow_shell.primary_button.click()
    app.processEvents()

    assert window.jobs == []
    assert said and "same name" in said[-1][0], said
    window.set_view_mode(Mode.CLASSIC)


def test_an_unplanned_output_leaves_the_batch_and_comes_back_with_its_choices(
        window, app, monkeypatch):
    from PySide6.QtCore import Qt

    silence_dialogs(monkeypatch)
    first, second = planned_pair(window, app)
    choose(window, app, second, "upload")
    choose(window, app, first, "social")
    row = next(r for r in range(window.table.rowCount())
               if window.clip_by_path[window.table.item(r, 0).data(
                   Qt.ItemDataRole.UserRole)].fingerprint
               == second.items[0].fingerprint)

    window.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
    app.processEvents()
    window.flow_shell.primary_button.click()
    app.processEvents()
    assert [job.preset_key for job in window.jobs] == ["social"], (
        "a retained, unplanned output was queued")

    window.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
    app.processEvents()
    assert window.output_plan.get(second).preset_key == "upload"
    window.set_view_mode(Mode.CLASSIC)


def test_classic_queues_every_ticked_output_with_its_one_preset(window, app,
                                                                monkeypatch):
    """The existing batch path, unchanged: per-output choices made in Flow do
    not reach it, and leaving Flow puts Classic's own choices back."""
    silence_dialogs(monkeypatch)
    first, second = planned_pair(window, app)
    choose(window, app, first, "social")
    choose(window, app, second, "upload")

    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()
    assert window.export_panel.preset_key() == "master", (
        "leaving Flow left an output's preset in Classic")
    window.export_panel.add_button.click()
    app.processEvents()

    assert sorted(job.preset_key for job in window.jobs) == ["master", "master"]


def test_choosing_nothing_queues_nothing(window, app, monkeypatch):
    """No selection never means the first output, or all of them."""
    said = silence_dialogs(monkeypatch)
    planned_pair(window, app)
    assert window._sidebar_target is None

    window.export_panel.add_button.click()
    app.processEvents()

    assert window.jobs == []
    assert said and "No output chosen" in said[-1][0], said
    window.set_view_mode(Mode.CLASSIC)


def test_a_new_output_starts_from_the_defaults_not_the_open_one(window, app):
    """Seeded once from explicit defaults. A repaint while A is open must not
    hand A's settings to an output appearing for the first time."""
    first, _second = two_planned_targets(window, app)
    in_flow(window, app)
    choose(window, app, first, "social")
    window.clips[0].selects = window.clips[0].selects + [
        Select(7.0, 9.0, "late", sid="r-late")]
    window._refresh_sidebar()
    app.processEvents()

    late = next(one.target for one in window._working_outputs()
                if one.target.items[0].sid == "r-late")
    assert window.output_plan.get(late).preset_key == "master"
    window.set_view_mode(Mode.CLASSIC)


def test_a_music_change_keeps_the_outputs_own_preset(window, app):
    """A music-only write used to stamp the panel's preset onto the output."""
    first, _second = planned_pair(window, app)
    choose(window, app, first, "social")
    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()
    assert window.export_panel.preset_key() == "master"

    window._store_music(first, MusicChoice(mode=AudioMode.NO_SOUND))

    assert window.output_plan.get(first).preset_key == "social"
    assert window.output_plan.get(first).music.mode is AudioMode.NO_SOUND


def test_queueing_starts_no_render(window, app, monkeypatch):
    silence_dialogs(monkeypatch)
    first, _second = planned_pair(window, app)
    choose(window, app, first, "social")

    window.flow_shell.primary_button.click()
    window.export_panel.add_button.click()
    app.processEvents()

    assert window.jobs, "nothing was queued to check"
    assert window.worker is None or not window.worker.isRunning()
    assert all(job.status is JobStatus.PENDING for job in window.jobs)
    window.set_view_mode(Mode.CLASSIC)


def test_output_estimates_the_output_that_would_be_queued(window, app):
    """"Queue this output" queues one output with its own choices, so that is
    what the estimate measures — not every ticked piece under its preset."""
    first, second = planned_pair(window, app)
    choose(window, app, second, "social", size_mb=10)
    choose(window, app, first, "social", size_mb=25)

    said = window.export_panel.estimate_label.text()

    assert "25" in said and "1 file" in said, repr(said)
    window.set_view_mode(Mode.CLASSIC)


def test_browse_never_shows_an_empty_list_region(window, app):
    """Found natively: the shell built Browse's list region beside the picture
    while the window lent the list to the region under it, so a third of the
    page was an empty column. The list goes beside the picture only where
    the two fit (they do not yet, at either reference width); until then the
    region is hidden rather than blank."""
    from flightdvr.flow_layout import Region

    in_flow(window, app)
    window._show_stage(Stage.BROWSE)
    app.processEvents()
    listing = window.flow_shell.host(Stage.BROWSE, Region.LIST)

    assert listing.isHidden() or listing.isAncestorOf(window.table), (
        "Browse shows a list region with no list in it")
    assert not window.table.isHidden()
    window.set_view_mode(Mode.CLASSIC)


def test_changing_mode_gives_back_room_it_only_needed_for_a_moment(
        window, app, monkeypatch):
    """Measured natively: moving the picture asked, for an instant, for 1194px
    on a 913px window, and a window that has grown stays grown — opening Flow
    left it taller than the screen, and going back to Classic left it 45px
    taller again. Offscreen cannot produce that instant, so the growth is
    made here, while the panels move, exactly where it happened."""
    import time

    window.show()          # a window nobody can see has no room to give back
    window.resize(1200, 900)
    app.processEvents()
    was = window.size()
    real = window._place_viewport

    def growing(stage):
        real(stage)
        window.resize(was.width(), was.height() + 300)

    monkeypatch.setattr(window, "_place_viewport", growing)
    window.set_view_mode(Mode.FLOW)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)
    assert window.size() == was, (
        f"opening Flow left the window at {window.size().toTuple()}")

    window.set_view_mode(Mode.CLASSIC)
    for _ in range(5):
        app.processEvents()
        time.sleep(0.01)
    assert window.size() == was


def test_giving_back_room_never_undoes_a_deliberate_resize(window, app):
    """CI caught the first version: a window reopening in Flow queued a
    restore to its construction-time size, and the resize that followed —
    the test's here, a restored geometry at startup in the app — was undone
    when the queue ran. Only growth the change itself caused is given back."""
    import time

    window.show()          # visible, so it is the size guard being tested
    window.resize(1000, 800)
    app.processEvents()
    real = window._place_viewport

    def growing(stage):
        real(stage)
        window.resize(1000, 1100)

    window._place_viewport = growing
    window.set_view_mode(Mode.FLOW)
    window.resize(1402, 900)
    for _ in range(5):
        app.processEvents()
        time.sleep(0.01)

    assert window.size().toTuple() == (1402, 900), (
        f"a deliberate resize was undone: {window.size().toTuple()}")
    window.set_view_mode(Mode.CLASSIC)


def test_with_no_output_chosen_output_estimates_nothing(window, app):
    """Found natively: with the selection gone, Output fell back to sizing
    every ticked piece under the defaults — "17 files, about 68 MB" beside a
    button that queues nothing. No selection never means all of them, in the
    estimate any more than in the queue."""
    planned_pair(window, app)
    assert window._sidebar_target is None
    window._update_estimate()

    said = window.export_panel.estimate_label.text()

    assert "file" not in said and "MB" not in said, repr(said)
    assert "Choose an output" in said, repr(said)
    window.set_view_mode(Mode.CLASSIC)


def test_for_says_what_to_do_when_nothing_is_chosen(window, app):
    """Found natively: a blank For: box above live controls reads as broken."""
    first, _second = planned_pair(window, app)
    window._select_working_target(first)
    app.processEvents()
    window.clips[0].selects = [Select(8.0, 12.0, "later", sid="r-9")]
    window._refresh_sidebar()
    app.processEvents()
    combo = window.export_panel.target_combo

    assert window._sidebar_target is None, "the vanished output stayed chosen"
    assert combo.currentIndex() == -1
    assert combo.placeholderText() == "Choose an output", (
        repr(combo.placeholderText()))
    window.set_view_mode(Mode.CLASSIC)


def test_music_edits_the_selected_output_not_the_last_inspected_one(window,
                                                                    app):
    """The same divergence as Output's, on the page where it writes.

    Select A, inspect a different recording in Browse, open Music: the music
    panel must be editing A. Following the focused clip, it edited whatever
    was inspected last — a music change landing on an output nobody chose.
    """
    from PySide6.QtCore import Qt

    first, _second = planned_pair(window, app)
    window._select_working_target(first)
    app.processEvents()

    window._show_stage(Stage.BROWSE)
    other = next(
        row for row in range(window.table.rowCount())
        if window.clip_by_path[window.table.item(row, 0).data(
            Qt.ItemDataRole.UserRole)].fingerprint != first.items[0].fingerprint)
    window.table.setCurrentCell(other, 0)
    window._load_selected_clip()
    app.processEvents()

    window._show_stage(Stage.MUSIC)
    app.processEvents()

    assert window._music_target == first, (
        "the music panel is editing the recording last inspected")
    label = next(one.label for one in window._working_outputs()
                 if one.target == first)
    assert label in window.music_panel.target_label.text(), (
        "the music panel names a different output from the one it edits")
    window.set_view_mode(Mode.CLASSIC)


# -- the Queue page: jobs at the top, and what each was submitted with ---------


def settled(app, rounds: int = 5) -> None:
    import time
    for _ in range(rounds):
        app.processEvents()
        time.sleep(0.01)


def queued_social_job(window, app, monkeypatch):
    silence_dialogs(monkeypatch)
    first, second = planned_pair(window, app)
    choose(window, app, first, "social", size_mb=25)
    window.export_panel.add_button.click()
    app.processEvents()
    assert len(window.jobs) == 1
    return first, second, window.jobs[0]


def select_job_row(window, app, row: int) -> None:
    window.queue_panel.table.selectRow(row)
    app.processEvents()


def test_queue_page_puts_the_jobs_at_the_top(window, app, monkeypatch):
    """Found natively and in Sol's review: the table was capped at Classic's
    150px strip and the page spread the spare height around it, so the jobs
    floated mid-page. On the page they sit under the header, with the
    actions directly under them."""
    queued_social_job(window, app, monkeypatch)
    window.show()
    window._show_stage(Stage.QUEUE)
    settled(app)
    panel = window.queue_panel
    table_bottom = panel.table.mapTo(panel, panel.table.rect().bottomLeft()).y()
    start_top = panel.start_button.mapTo(panel, panel.start_button.rect().topLeft()).y()
    header_bottom = panel.toggle.mapTo(panel, panel.toggle.rect().bottomLeft()).y()
    table_top = panel.table.mapTo(panel, panel.table.rect().topLeft()).y()

    assert table_top - header_bottom < 20, (header_bottom, table_top)
    assert 0 <= start_top - table_bottom < 20, (table_bottom, start_top)
    assert panel.table.maximumHeight() > 150
    window.set_view_mode(Mode.CLASSIC)


def test_classic_keeps_its_queue_strip_as_it_was(window, app, monkeypatch):
    """Classic's queue is a short strip and its open/closed state is its own.
    A trip through Flow's Queue page gives both back — and a job queued while
    away opens it, as a job queued in Classic always has."""
    panel = window.queue_panel
    assert not panel.toggle.isChecked()
    in_flow(window, app)
    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()
    assert panel.table.maximumHeight() == 150
    assert not panel.toggle.isChecked(), "Flow left Classic's strip open"

    queued_social_job(window, app, monkeypatch)
    window._show_stage(Stage.QUEUE)
    select_job_row(window, app, 0)
    assert not panel.details.isHidden(), "the fixture never showed details"
    window.set_view_mode(Mode.CLASSIC)
    app.processEvents()
    assert panel.toggle.isChecked(), "a job queued in Flow left the strip shut"
    assert panel.details.isHidden(), "the submitted details leaked into Classic"
    select_job_row(window, app, 0)
    assert panel.details.isHidden(), "selecting in Classic showed the details"


def test_a_selected_job_shows_what_it_was_submitted_with(window, app,
                                                          monkeypatch):
    first, _second, job = queued_social_job(window, app, monkeypatch)
    window._show_stage(Stage.QUEUE)
    select_job_row(window, app, 0)
    said = window.queue_panel.details_body.text()

    assert not window.queue_panel.details.isHidden()
    assert "Preset: Social · a file size, 25 MB" in said, said
    assert first.items[0].fingerprint and "hdz_001.ts" in said, said
    assert f"Will be written to: {job.out_path}" in said, said
    assert "Written to:" not in said.replace("be written to:", ""), (
        "a waiting job was described as a finished file")
    window.set_view_mode(Mode.CLASSIC)


def test_submitted_details_do_not_follow_later_edits(window, app, monkeypatch):
    first, _second, _job = queued_social_job(window, app, monkeypatch)
    window._show_stage(Stage.QUEUE)
    select_job_row(window, app, 0)
    before = window.queue_panel.details_body.text()

    choose(window, app, first, "upload")        # edit the planned output
    window._rebuild_queue()                     # and redraw everything
    app.processEvents()

    assert window.queue_panel.details_body.text() == before
    window.set_view_mode(Mode.CLASSIC)


def test_details_follow_the_job_not_the_row(window, app, monkeypatch):
    """Rows are rewritten in place. After the selected job is removed, the
    same row holds a different job — and describing it would describe
    something nobody selected."""
    first, second, _job = queued_social_job(window, app, monkeypatch)
    choose(window, app, second, "master")
    window.export_panel.add_button.click()
    app.processEvents()
    assert len(window.jobs) == 2
    window._show_stage(Stage.QUEUE)
    select_job_row(window, app, 0)
    assert "Social" in window.queue_panel.details_body.text()

    del window.jobs[0]
    window._rebuild_queue()
    app.processEvents()

    assert window.queue_panel.selected_job() is None
    assert window.queue_panel.details.isHidden(), (
        "details still describe a job that is gone, beside a different one")
    window.set_view_mode(Mode.CLASSIC)


def test_selecting_a_job_is_only_looking(window, app, monkeypatch):
    """No start, no removal, no audition: selection changes nothing."""
    _first, _second, job = queued_social_job(window, app, monkeypatch)
    window._show_stage(Stage.QUEUE)
    started = []
    monkeypatch.setattr(window, "_start", lambda *a: started.append(a))

    select_job_row(window, app, 0)
    window.queue_panel.table.clearSelection()
    app.processEvents()

    assert started == []
    assert window.jobs == [job] and job.status is JobStatus.PENDING
    assert window.worker is None or not window.worker.isRunning()
    assert window.queue_panel.details.isHidden(), "cleared selection kept details"
    window.set_view_mode(Mode.CLASSIC)


def test_submitted_details_name_every_occurrence_of_a_repeated_source(
        window, app, monkeypatch):
    """A/B/A from one recording is three ranges, not two files."""
    silence_dialogs(monkeypatch)
    make_aba_assembly(window, app)
    in_flow(window, app)
    window.flow_shell.primary_button.click()
    app.processEvents()
    assert len(window.jobs) == 1 and len(window.jobs[0].clips) == 3
    window._show_stage(Stage.QUEUE)
    select_job_row(window, app, 0)
    said = window.queue_panel.details_body.text()

    assert "3 ranges, joined in this order" in said, said
    first, second = window.clips
    lines = [line.strip() for line in said.splitlines()]
    assert lines[1].startswith("1. ") and first.path.name in lines[1]
    assert lines[2].startswith("2. ") and second.path.name in lines[2]
    assert lines[3].startswith("3. ") and first.path.name in lines[3]
    window.set_view_mode(Mode.CLASSIC)


def test_the_music_page_opens_its_controls(window, app):
    """Found natively and in Sol's review: Flow's Music page arrived with
    Classic's collapsed band — a picture and one checkbox, nothing of what
    the page is for."""
    band = window.preview_view.music_band
    assert not band.isChecked()
    first, _second = planned_pair(window, app)
    window._select_working_target(first)
    window._show_stage(Stage.MUSIC)
    app.processEvents()

    assert band.isChecked()
    assert not window.preview_view.music_content.isHidden()
    assert window.flow_shell.host(Stage.MUSIC, Region.PANEL).isAncestorOf(band)
    window.set_view_mode(Mode.CLASSIC)


def test_classic_gets_its_own_music_band_state_back(window, app):
    band = window.preview_view.music_band
    for classic_had in (False, True):
        band.setChecked(classic_had)
        in_flow(window, app)
        window._show_stage(Stage.MUSIC)
        app.processEvents()
        window.set_view_mode(Mode.CLASSIC)
        app.processEvents()
        assert band.isChecked() is classic_had, (
            f"Classic had the band {'open' if classic_had else 'shut'}")


# -- the picture fits its page, and Browse puts the list beside it -------------


def shown_flow(window, app, width=1060, height=700):
    window.show()
    window.resize(width, height)
    settled(app)
    in_flow(window, app)
    settled(app)


def global_rect(widget):
    from PySide6.QtCore import QRect
    return QRect(widget.mapToGlobal(widget.rect().topLeft()), widget.size())


def test_the_picture_never_spills_out_of_its_frame(window, app):
    """Measured natively: 347px of picture in a 275px region, over the caption
    beneath it. On every page with a picture, it fits inside its frame, is
    never below its own minimum, and the caption does not overlap it."""
    make_aba_assembly(window, app)
    shown_flow(window, app)
    box = window.preview_view.preview_box
    frame = window._picture_frame
    for stage in (Stage.BROWSE, Stage.TRIM, Stage.ASSEMBLE, Stage.MUSIC,
                  Stage.OUTPUT):
        window._show_stage(stage)
        settled(app)
        assert box.height() <= frame.height(), (stage, box.height(), frame.height())
        assert box.height() >= box.minimumSizeHint().height(), stage
        note = window.flow_source_note
        if note.isVisible() and note.text():
            assert not global_rect(note).intersects(global_rect(box)), stage
        # And when the page changes height under it, which is when a picture
        # that only sized itself from its width would spill.
        for size in ((1060, 960), (1060, 700)):   # height alone
            window.resize(*size)
            settled(app)
            assert box.height() <= frame.height(), (stage, size)
            # ...and takes the room its width earns when the page grows,
            # rather than keeping whatever height it had before.
            earned = min(box.useful_height(box.width()), frame.height())
            assert box.height() >= earned - 1, (stage, size, box.height(),
                                                earned)
    window.set_view_mode(Mode.CLASSIC)


def test_a_frame_with_only_the_picture_keeps_no_room_for_a_list(window, app):
    """Classic's column keeps 150px under the picture for the list; a frame
    that holds only the picture left that as a blank band."""
    shown_flow(window, app, 1400, 900)
    window._show_stage(Stage.OUTPUT)
    settled(app)
    box = window.preview_view.preview_box
    frame = window._picture_frame
    if box.useful_height(box.width()) >= frame.height():
        assert box.height() == frame.height(), (box.height(), frame.height())
    window.set_view_mode(Mode.CLASSIC)
    settled(app)
    assert box._list_room == MIN_LIST_HEIGHT, "Classic lost its list room"


def test_classic_gets_its_picture_controls_back_exactly(window, app):
    from PySide6.QtWidgets import QBoxLayout
    view = window.preview_view
    before = (view.sidebar.width(), view._side_actions.direction(),
              [gap.sizeHint().height() for gap in view._side_gaps],
              view._box_layout.direction(), view.sidebar.measured_at_width)
    shown_flow(window, app)
    for stage in (Stage.BROWSE, Stage.OUTPUT):
        window._show_stage(stage)
        settled(app)
    assert view._side_actions.direction() is QBoxLayout.Direction.LeftToRight
    window.set_view_mode(Mode.CLASSIC)
    settled(app)
    after = (view.sidebar.width(), view._side_actions.direction(),
             [gap.sizeHint().height() for gap in view._side_gaps],
             view._box_layout.direction(), view.sidebar.measured_at_width)
    assert after == before


def test_browse_puts_the_list_beside_the_picture_and_its_controls_below(
        window, app):
    """Sol's review, item 1: a tall list to the left of the picture."""
    shown_flow(window, app)
    window._show_stage(Stage.BROWSE)
    settled(app)
    listing = window.flow_shell.host(Stage.BROWSE, Region.LIST)
    view = window.preview_view
    table, picture, side = (global_rect(window.table),
                            global_rect(view.frame_view),
                            global_rect(view.sidebar))

    assert not listing.isHidden() and listing.isAncestorOf(window.table)
    assert table.right() < picture.left(), (table, picture)
    assert side.top() >= picture.bottom(), "the controls are not under the picture"
    assert window.browser_panel.stacked

    window._show_stage(Stage.OUTPUT)
    settled(app)
    picture, side = global_rect(view.frame_view), global_rect(view.sidebar)
    assert side.left() >= picture.right(), "the controls left the side elsewhere"
    assert not window.browser_panel.stacked
    window.set_view_mode(Mode.CLASSIC)


def test_stacking_the_list_rows_keeps_every_control_in_order(window, app):
    """Reversible: the same widgets, in the same order, whichever way."""
    panel = window.browser_panel

    def order():
        found = []
        for row in panel._rows:
            for index in range(row.count()):
                piece = row.itemAt(index).layout()
                for inner in range(piece.count()):
                    widget = piece.itemAt(inner).widget()
                    if widget is not None:
                        found.append(widget)
        return found

    before = order()
    panel.set_stacked(True)
    assert order() == before and panel.stacked
    panel.set_stacked(False)
    assert order() == before and not panel.stacked


def test_the_controls_floor_is_measured_at_their_real_width(window, app):
    """Qt's minimum hint wrapped the column's labels at a width it never has:
    208px claimed for 176, natively. In Flow the column says what it needs at
    its width; Classic keeps Qt's answer."""
    side = window.preview_view.sidebar
    classic = side.minimumSizeHint().height()
    shown_flow(window, app)
    window._show_stage(Stage.OUTPUT)
    settled(app)
    assert side.minimumSizeHint().height() == side.layout().heightForWidth(
        side.width())
    window.set_view_mode(Mode.CLASSIC)
    settled(app)
    assert side.minimumSizeHint().height() == classic


def test_visiting_browse_does_not_inflate_the_floor_elsewhere(window, app):
    """Natively, measuring the box's chrome with its controls below counted
    them as chrome, and every page after Browse had a 320px floor."""
    box = window.preview_view.preview_box
    window.show()
    window.resize(1060, 700)
    settled(app)
    chrome = box._chrome           # measured in Classic, beside the picture
    assert chrome is not None
    shown_flow(window, app)
    for stage in (Stage.BROWSE, Stage.OUTPUT, Stage.BROWSE):
        window._show_stage(stage)
        settled(app)
    assert box._chrome == chrome, "the controls were counted as chrome"
    window.set_view_mode(Mode.CLASSIC)


def test_the_caption_keeps_every_word(window, app):
    from flightdvr.flow_shell import OneLineNote
    note = window.flow_source_note
    assert isinstance(note, OneLineNote)
    note.setText("Source: hdz_001.ts · a very long range name — not the "
                 "finished file.")
    assert not note.hasHeightForWidth()
    assert note.toolTip() == note.text()
    assert note.minimumSizeHint().height() == note.sizeHint().height()


def test_a_narrow_list_keeps_its_clip_column_readable(window, app):
    """Natively at the compact size the Clip column stretched to about 40px
    beside a 120px thumbnail: the thumbnail covered Length and no name
    showed. Narrow, it keeps a readable width and the table scrolls sideways;
    every column stays. Elsewhere, and in Classic, it stretches as before."""
    from PySide6.QtWidgets import QHeaderView
    from flightdvr.ui import CLIP_NAME_ROOM

    shown_flow(window, app)
    window._show_stage(Stage.BROWSE)
    listing = window.flow_shell.host(Stage.BROWSE, Region.LIST)
    listing.setMaximumWidth(360)
    settled(app)
    head = window.table.horizontalHeader()
    floor = window.table.iconSize().width() + CLIP_NAME_ROOM

    assert head.sectionResizeMode(0) is QHeaderView.ResizeMode.Interactive, (
        "a narrow list left the Clip column to stretch into nothing")
    assert head.sectionSize(0) >= floor, head.sectionSize(0)
    assert not any(window.table.isColumnHidden(c)
                   for c in range(window.table.columnCount()))

    # Straight from Browse to Classic. The header's own resize signal would
    # usually put the stretch back too; with it quiet, leaving Flow must.
    blocked = head.blockSignals(True)
    try:
        window.set_view_mode(Mode.CLASSIC)
        settled(app)
    finally:
        head.blockSignals(blocked)
    assert head.sectionResizeMode(0) is QHeaderView.ResizeMode.Stretch


def test_browse_from_queue_still_shows_the_controls_under_the_picture(window,
                                                                      app):
    """Found natively: arriving on Browse from Queue — where the picture has
    no place and waits detached — left the box laid out the old way. The
    picture filled it and the controls sat below its bottom edge, clipped."""
    shown_flow(window, app)
    view = window.preview_view
    for stage in (Stage.MUSIC, Stage.QUEUE, Stage.BROWSE):
        window._show_stage(stage)
        settled(app)
    box = global_rect(view.preview_box)
    side = global_rect(view.sidebar)

    assert box.contains(side), (box, side)
    assert side.top() >= global_rect(view.frame_view).bottom()
    window.set_view_mode(Mode.CLASSIC)
