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

"""Music, from the band to the queued job.

`test_music_panel.py` covers the widget on its own. This covers what the window
does with it: which output a choice belongs to, what happens to a choice that
cannot be exported, and what a queued job carries away.

Nothing here probes or decodes. `MusicAssetProbe` is replaced by a stand-in
that emits the signals the real one emits, because what is under test is the
window's half of that conversation — the generation and target binding, and the
promise that a stopped read never leaves an output waiting with nothing to do
about it.
"""

from __future__ import annotations

from datetime import datetime
from fractions import Fraction
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import QApplication

from flightdvr.audio_plan import AudioMode, AudioAsset, MusicChoice, SampleSpan
from flightdvr.assembly import Item
from flightdvr.flow_layout import Mode, Stage
from flightdvr.jobs import Job, JobStatus
from flightdvr.media import ClipInfo, Select
from flightdvr.output_plan import OutputTarget
from flightdvr.presets import ExportSettings


def a_clip(name: str, folder: Path | None = None) -> ClipInfo:
    return ClipInfo(
        path=(folder / name) if folder else Path(name), size=599_189_652,
        modified=datetime(2025, 10, 8, 18, 39),
        duration=212.7, width=1280, height=720, fps=60.0,
        video_codec="hevc", audio_codec="aac",
        pix_fmt="yuvj420p", color_range="pc",
    )


def an_asset(track: Path) -> AudioAsset:
    return AudioAsset(track, "a" * 64, 0, 44_100, 2, 44_100 * 30)


class _NoProbe(QObject):
    """A HardwareProbe that starts no thread.

    These tests have no opinion about encoders, and a real probe runs test
    encodes through ffmpeg. On macOS one is slow enough to still be running
    when the test that built it has finished — which perturbs anything else
    measuring how long a close takes, and leaves a live QThread behind.
    """

    result = Signal(object)

    def __init__(self, tools, parent=None):
        super().__init__(parent)

    def start(self, *_args) -> None:
        pass

    def isRunning(self) -> bool:               # noqa: N802 (Qt naming)
        return False

    def stop(self) -> None:
        pass

    def wait(self, *_args) -> bool:
        return True


class _NoStrip(QThread):
    """A FilmstripLoader that decodes nothing.

    A QThread because the window connects to the inherited `finished`, and
    carrying the same signals it connects to — a stand-in that is missing one
    fails where the real object would have worked, which proves nothing about
    the code under test.
    """

    ready = Signal(object)
    activity_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, *args, **kwargs):
        parent = args[3] if len(args) > 3 else kwargs.get("parent")
        super().__init__(parent)

    def start(self, *_args) -> None:
        self.finished.emit()

    def isRunning(self) -> bool:               # noqa: N802 (Qt naming)
        return False

    def stop(self) -> None:
        pass

    def wait(self, *_args) -> bool:
        return True


class _NoScan(QObject):
    """A ScanWorker that starts no thread."""

    counted = Signal(int, int)
    found = Signal(int, object)
    done = Signal(int, int)

    def __init__(self, tools, folder, recursive, generation, parent=None):
        super().__init__(parent)

    def start(self) -> None:
        pass

    def isRunning(self) -> bool:               # noqa: N802 (Qt naming)
        return False

    def stop(self) -> None:
        pass

    def wait(self, *_args) -> bool:
        return True


class _FakeProbe(QThread):
    """Stands in for `MusicAssetProbe` without reading anything.

    It never starts a real thread, so a test decides when — and whether — a
    result arrives. The real worker emits nothing at all when it is stopped,
    and this one keeps that promise, because the window's handling of exactly
    that case is what most of these tests are about.
    """

    ready = Signal(int, object)
    waveform_ready = Signal(object)
    failed = Signal(int, str)
    made: list["_FakeProbe"] = []

    def __init__(self, tools, track, generation, parent=None, *,
                 waveform_request=None):
        super().__init__(parent)
        self.track = Path(track)
        self.generation = generation
        self.waveform_request = waveform_request
        self.stopped = False
        self.running = True
        type(self).made.append(self)

    def start(self) -> None:                   # noqa: D102
        pass

    def isRunning(self) -> bool:               # noqa: N802 (Qt naming)
        return self.running

    def stop(self) -> None:
        self.stopped = True
        self._finish()

    def _finish(self) -> None:
        """End the way a real QThread ends.

        `finished` is what releases a retained thread, and the window retains
        one before stopping it. A stand-in that never emits it would sit in
        that registry for the rest of the process and hold the deferred quit
        open — which is a defect in the stand-in, not in the window, but it
        looks exactly like one in the window.
        """
        was = self.running
        self.running = False
        if was:
            self.finished.emit()

    def deliver_waveform(self, inspection) -> None:
        """The combined read's one success signal, then the thread ends."""
        if self.stopped:
            return
        self.waveform_ready.emit(inspection)
        self._finish()

    def deliver(self, asset=None, reason: str = "") -> None:
        """What the real worker does when it finishes, and only then."""
        if self.stopped:
            return
        if reason:
            self.failed.emit(self.generation, reason)
        else:
            self.ready.emit(self.generation, asset or an_asset(self.track))
        self._finish()


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def sessions_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


@pytest.fixture
def probes(monkeypatch):
    _FakeProbe.made = []
    # `raising=False` so the compatibility control below can also be run
    # against a build without this feature: an export nobody gave music to has
    # to behave identically there, and a harness that cannot start proves
    # nothing about it.
    monkeypatch.setattr("flightdvr.ui.MusicAssetProbe", _FakeProbe,
                        raising=False)
    monkeypatch.setattr("flightdvr.ui.ScanWorker", _NoScan)
    # Hermetic: these windows start no encoder probe and ask no release API.
    # Both are real work with real threads, neither has any bearing on music
    # wiring, and a probe still running at interpreter exit takes the process
    # with it after every assertion has already passed.
    monkeypatch.setattr("flightdvr.ui.HardwareProbe", _NoProbe)
    monkeypatch.setattr("flightdvr.updates.should_check",
                        lambda *a, **k: False)
    # Selecting a clip normally starts a filmstrip decode. These tests only
    # need the selection, and a strip still extracting after its test has
    # finished runs ffmpeg against whatever the next test is measuring.
    monkeypatch.setattr("flightdvr.ui.FilmstripLoader", _NoStrip)
    return _FakeProbe.made


@pytest.fixture
def window(app, tmp_path, sessions_home, probes, monkeypatch):
    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow

    card = tmp_path / "card"
    card.mkdir()
    out = tmp_path / "out"
    out.mkdir()

    made = MainWindow(find_tools())
    made.source_combo.insertItem(0, str(card), str(card))
    made.source_combo.setCurrentIndex(0)
    made._ready = True
    for clip in (a_clip("hdz_001.ts", card), a_clip("hdz_002.ts", card)):
        made._add_clip(made._scan_generation, clip)
    made._scan_done(made._scan_generation, 2)
    made.export_panel.out_edit.setCurrentText(str(out))
    made.export_panel.preset_buttons["master"].setChecked(True)
    made.export_panel.subfolder_check.setChecked(False)
    made.export_panel.date_check.setChecked(False)
    monkeypatch.setattr(made.thumbs, "request", lambda *_: None)
    monkeypatch.setattr(made.player, "load", lambda *a, **k: None)
    app.processEvents()
    yield made
    made.close()
    left = [t for t in made.findChildren(QThread) if t.isRunning()]
    assert not left, [type(t).__name__ for t in left]


def focus(window, index: int) -> ClipInfo:
    """Put one clip under the band, the way clicking its row does."""
    window.table.setCurrentCell(index, 0)
    window._load_selected_clip()
    return window._trim_clip


def choose_track(window, monkeypatch, track: Path):
    monkeypatch.setattr(
        "flightdvr.ui.QFileDialog.getOpenFileName",
        staticmethod(lambda *a, **k: (str(track), "")))
    window._choose_music_track()
    return _FakeProbe.made[-1] if _FakeProbe.made else None


def tick(window, index: int) -> None:
    from PySide6.QtCore import Qt
    window.table.item(index, 0).setCheckState(Qt.CheckState.Checked)


def warnings_from(monkeypatch) -> list:
    said = []
    monkeypatch.setattr("flightdvr.ui.QMessageBox.warning",
                        staticmethod(lambda *a, **k: said.append(a[2])))
    return said


def assembly_target(window, app) -> OutputTarget:
    """Bind the literal A/B/A run to Flow's valid joined output."""
    first, second = window.clips
    first.selects = [Select(10.0, 13.0, "A", sid="a")]
    second.selects = [Select(2.0, 4.0, "B", sid="b")]
    window._store_assembly([
        Item(first.fingerprint, "a"),
        Item(second.fingerprint, "b"),
        Item(first.fingerprint, "a"),
    ])
    window.set_view_mode(Mode.FLOW)
    window._show_stage(Stage.ASSEMBLE)
    app.processEvents()
    return OutputTarget.assembly((
        Item(first.fingerprint, "a"),
        Item(second.fingerprint, "b"),
        Item(first.fingerprint, "a"),
    ))


def test_valid_assembly_owns_one_editable_preview_choice(window, app):
    target = assembly_target(window, app)
    window._show_stage(Stage.MUSIC)
    app.processEvents()

    assert window._music_target == target
    assert window.output_plan.selected_target == target
    # The enclosing checkable band may be collapsed; locally, the control is
    # editable as soon as that existing presentation control is opened.
    assert window.music_panel.mode_combo.isEnabledTo(window.music_panel)
    assert window.music_panel.unsupported_label.text() == ""
    assert window._planned_music(target) == MusicChoice()


def test_reorder_rekeys_only_the_tracked_assembly_choice(window, app):
    old = assembly_target(window, app)
    retained = MusicChoice(mode=AudioMode.NO_SOUND)
    window._store_music(old, retained)
    first, second = window.clips
    current = OutputTarget.assembly((
        Item(second.fingerprint, "b"),
        Item(first.fingerprint, "a"),
        Item(first.fingerprint, "a"),
    ))

    window._store_assembly(list(current.items))
    app.processEvents()

    assert window._music_target == current
    assert window._planned_music(current) == retained
    assert old not in window.output_plan.targets
    assert all(window._planned_music(OutputTarget.clip_or_range(
        item.fingerprint, item.sid)) != retained for item in current.items)

    # A transient invalid run retains the last exact choice but offers no stream.
    window._store_assembly([current.items[0]])
    app.processEvents()
    assert window._sequence_plan is None
    assert window._music_target == current
    assert window._planned_music(current) == retained
    assert not window.live_preview.status.offered


def test_reorder_stops_assembly_probe_and_late_result_cannot_land(
        window, app, monkeypatch, tmp_path):
    old = assembly_target(window, app)
    track = tmp_path / "assembly.mp3"
    probe = choose_track(window, monkeypatch, track)
    generation = probe.generation
    first, second = window.clips
    current = OutputTarget.assembly((
        Item(second.fingerprint, "b"),
        Item(first.fingerprint, "a"),
        Item(first.fingerprint, "a"),
    ))

    window._store_assembly(list(current.items))
    app.processEvents()

    assert probe.stopped
    assert old not in window.output_plan.targets
    assert window._planned_music(current).track == track
    assert window._planned_music(current).asset is None
    assert "Choose it again" in window._music_trouble[current]

    # Model a callback already queued before stop: its old generation has no
    # bound target now and therefore cannot validate the rekeyed choice.
    window._music_ready(generation, an_asset(track))
    assert window._planned_music(current).asset is None


def test_joined_master_queues_the_exact_target_sequence_and_choice_snapshot(
        window, app, monkeypatch):
    target = assembly_target(window, app)
    window._store_music(target, MusicChoice(mode=AudioMode.NO_SOUND))
    window._sync_music_panel()
    said = warnings_from(monkeypatch)

    window._add_to_queue()

    assert said == []
    assert len(window.jobs) == 1
    queued = window.jobs[0]
    assert queued.target == target
    assert queued.sequence is not None
    assert tuple(one.item for one in queued.sequence.occurrences) == target.items
    assert queued.audio == MusicChoice(mode=AudioMode.NO_SOUND)

    submitted_sequence = queued.sequence
    submitted_clips = tuple(clip.path for clip in queued.clips)
    first, second = window.clips
    changed = OutputTarget.assembly((
        Item(second.fingerprint, "b"),
        Item(first.fingerprint, "a"),
        Item(first.fingerprint, "a"),
    ))
    window._store_assembly(list(changed.items))
    app.processEvents()
    window._store_music(changed, MusicChoice(mode=AudioMode.ORIGINAL))

    assert queued.target == target
    assert queued.sequence == submitted_sequence
    assert tuple(clip.path for clip in queued.clips) == submitted_clips
    assert queued.audio == MusicChoice(mode=AudioMode.NO_SOUND)


# -- what a queued job carries -------------------------------------------------

def test_accepting_a_track_stores_the_displayed_full_passage_and_queues_it(
        window, monkeypatch, tmp_path, app):
    """The panel shows the whole validated track, so the authoritative choice
    and the queued snapshot must carry that same passage without a token edit.

    The missing integration stored ``passage=None`` at acceptance, even though
    the visible controls and ``capture()`` both said the whole track. The
    worker then rejected the plausible queued job before publication.
    """
    clip = focus(window, 0)
    target = window._music_target
    track = tmp_path / "song.mp3"
    asset = an_asset(track)
    probe = choose_track(window, monkeypatch, track)

    probe.deliver(asset)
    app.processEvents()

    expected = SampleSpan(0, asset.decoded_samples, asset.sample_rate)
    displayed = window.music_panel.capture()
    authoritative = window._planned_music(target)
    assert displayed.passage == expected
    assert authoritative.passage == expected, (
        "the displayed full-track default never reached OutputPlan")

    tick(window, 0)
    said = warnings_from(monkeypatch)
    window._add_to_queue()

    queued = [job for job in window.jobs if job.clips[0].path == clip.path]
    assert said == []
    assert len(queued) == 1
    assert queued[0].audio.passage == expected


def test_a_late_accepted_track_stores_its_default_on_the_bound_nonfocus_target(
        window, monkeypatch, tmp_path, app):
    """A probe result belongs to its bound target, not whichever row is open."""
    first = focus(window, 0)
    first_target = window._music_target
    track = tmp_path / "first.mp3"
    asset = an_asset(track)
    probe = choose_track(window, monkeypatch, track)

    second = focus(window, 1)
    probe.deliver(asset)
    app.processEvents()

    expected = SampleSpan(0, asset.decoded_samples, asset.sample_rate)
    stored = window._planned_music(first_target)
    assert stored.asset == asset
    assert stored.passage == expected
    assert window.music_panel.capture().track is None, (
        "the late result changed the output that happened to be focused")
    assert first.path != second.path


def test_reaccepting_a_validated_track_preserves_an_explicit_user_passage(
        window, monkeypatch, tmp_path, app):
    """Normalising a missing default must never widen a passage the user set."""
    focus(window, 0)
    target = window._music_target
    track = tmp_path / "song.mp3"
    asset = an_asset(track)
    probe = choose_track(window, monkeypatch, track)
    probe.deliver(asset)
    app.processEvents()

    window.music_panel.passage_start.setValue(2.0)
    window.music_panel.passage_end.setValue(5.0)
    app.processEvents()
    chosen = window._planned_music(target).passage
    assert chosen == SampleSpan(88_200, 220_500, 44_100)

    window._start_music_probe(target, track)
    _FakeProbe.made[-1].deliver(asset)
    app.processEvents()

    assert window._planned_music(target).passage == chosen


def test_a_queued_job_carries_the_choice_that_was_on_screen(
        window, monkeypatch, tmp_path, app):
    """The whole point: the band's value reaches `Job.audio`."""
    clip = focus(window, 0)
    probe = choose_track(window, monkeypatch, tmp_path / "song.mp3")
    probe.deliver()
    app.processEvents()

    window.music_panel.mode_combo.setCurrentIndex(
        window.music_panel.mode_combo.findData(AudioMode.MIX))
    app.processEvents()

    tick(window, 0)
    warnings_from(monkeypatch)
    window._add_to_queue()

    queued = [j for j in window.jobs if j.clips[0].path == clip.path]
    assert len(queued) == 1, [j.name for j in window.jobs]
    carried = queued[0].audio
    assert carried.mode is AudioMode.MIX
    assert carried.asset is not None
    assert carried.track == (tmp_path / "song.mp3")


def test_a_clip_with_no_music_queues_exactly_as_it_always_did(
        window, monkeypatch, tmp_path):
    """The compatibility control. Passes before this feature and after it.

    An untouched export must be outside every rule music adds, so this asserts
    the unconfigured value *and* that the command built from it is identical to
    one built for a job that has no audio field set at all.
    """
    from flightdvr.presets import build_commands

    tick(window, 0)
    warnings_from(monkeypatch)
    window._add_to_queue()

    assert len(window.jobs) == 1
    job = window.jobs[0]
    assert job.audio == MusicChoice()
    assert not job.audio.configured

    plain = Job([job.clips[0]], job.preset_key, job.settings, job.out_path)
    assert (build_commands(window.tools, job.clips[0], job.preset_key,
                           job.settings, job.out_path, tmp_path)
            == build_commands(window.tools, plain.clips[0], plain.preset_key,
                              plain.settings, plain.out_path, tmp_path))


def test_editing_after_queueing_leaves_the_queued_job_alone(
        window, monkeypatch, tmp_path, app):
    focus(window, 0)
    probe = choose_track(window, monkeypatch, tmp_path / "song.mp3")
    probe.deliver()
    app.processEvents()
    tick(window, 0)
    warnings_from(monkeypatch)
    window._add_to_queue()
    was = window.jobs[0].audio

    window.music_panel.music_level.setValue(10)
    app.processEvents()

    assert window.jobs[0].audio == was, "a later edit reached a queued job"


def test_a_retarget_renames_the_output_and_leaves_the_music_alone(
        window, monkeypatch, tmp_path, app):
    """Music is not a naming input, and retargeting must not touch it."""
    focus(window, 0)
    probe = choose_track(window, monkeypatch, tmp_path / "song.mp3")
    probe.deliver()
    app.processEvents()
    tick(window, 0)
    warnings_from(monkeypatch)
    window._add_to_queue()

    job = window.jobs[0]
    before_audio, before_path = job.audio, job.out_path
    window.export_panel.template_edit.setText("renamed_{clip}_{preset}")
    window.export_panel.template_edit.editingFinished.emit()
    app.processEvents()

    assert job.out_path != before_path, "the retarget did not happen"
    assert job.audio == before_audio


# -- refusing, rather than dropping --------------------------------------------

def test_a_validated_track_without_a_passage_is_refused_before_queue_mutation(
        window, monkeypatch, tmp_path):
    """The UI rejects a genuinely incomplete choice before the worker has to."""
    clip = focus(window, 0)
    target = window._music_target
    track = tmp_path / "incomplete.mp3"
    asset = an_asset(track)
    window._store_music(target, MusicChoice(
        track=track, mode=AudioMode.REPLACE, asset=asset, passage=None))
    tick(window, 0)
    before = list(window.jobs)
    said = warnings_from(monkeypatch)

    window._add_to_queue()

    assert window.jobs == before
    assert said and "Nothing has been queued" in said[0]
    assert "passage" in said[0].lower()
    assert clip.path.name in said[0]


def test_an_unsupported_preset_refuses_instead_of_dropping_the_music(
        window, monkeypatch, tmp_path, app):
    """Never substituted with an unconfigured choice, and nothing queued."""
    focus(window, 0)
    probe = choose_track(window, monkeypatch, tmp_path / "song.mp3")
    probe.deliver()
    app.processEvents()

    # Music is exported for Master only; every other preset refuses, and the
    # panel is the one place that rule is written.
    window.export_panel.preset_buttons["upload"].setChecked(True)
    app.processEvents()

    said = warnings_from(monkeypatch)
    tick(window, 0)
    window._add_to_queue()

    assert window.jobs == [], "a refused action still queued something"
    assert said and "Nothing has been queued" in said[0]
    assert "upload" in said[0]


def test_a_track_still_being_read_refuses_the_action_upfront(
        window, monkeypatch, tmp_path):
    """No knowingly doomed job, and the reason says what to do."""
    focus(window, 0)
    choose_track(window, monkeypatch, tmp_path / "song.mp3")   # never delivers

    said = warnings_from(monkeypatch)
    tick(window, 0)
    window._add_to_queue()

    assert window.jobs == []
    assert "still being read" in said[0]


def test_a_track_that_could_not_be_read_refuses_and_says_why(
        window, monkeypatch, tmp_path, app):
    focus(window, 0)
    probe = choose_track(window, monkeypatch, tmp_path / "song.mp3")
    probe.deliver(reason="no audio stream")
    app.processEvents()

    said = warnings_from(monkeypatch)
    tick(window, 0)
    window._add_to_queue()

    assert window.jobs == []
    assert "no audio stream" in said[0]
    assert "Choose another" in said[0]


# -- one plan, several outputs -------------------------------------------------

def test_each_output_keeps_its_own_music(window, monkeypatch, tmp_path, app):
    first = focus(window, 0)
    probe = choose_track(window, monkeypatch, tmp_path / "first.mp3")
    probe.deliver()
    app.processEvents()

    second = focus(window, 1)
    assert window.music_panel.capture().track is None, (
        "the second output inherited the first one's track")

    again = focus(window, 0)
    assert again.path == first.path
    assert window.music_panel.capture().track == (tmp_path / "first.mp3")
    assert second.path.name == "hdz_002.ts"


def test_a_late_result_reaches_its_own_output_and_no_other(
        window, monkeypatch, tmp_path, app):
    """Switching away while a read runs must not misdeliver it."""
    focus(window, 0)
    probe = choose_track(window, monkeypatch, tmp_path / "song.mp3")

    focus(window, 1)                       # away before the read finishes
    probe.deliver()
    app.processEvents()

    assert window.music_panel.capture().track is None, (
        "a late result landed on the output that happened to be in focus")
    focus(window, 0)
    assert window.music_panel.capture().asset is not None


def test_a_superseded_result_is_ignored(window, monkeypatch, tmp_path, app):
    focus(window, 0)
    first = choose_track(window, monkeypatch, tmp_path / "first.mp3")
    second = choose_track(window, monkeypatch, tmp_path / "second.mp3")
    assert first.stopped, "the replaced read was not asked to stop"

    first.deliver()                        # stopped: emits nothing
    second.deliver()
    app.processEvents()

    assert window.music_panel.capture().track == (tmp_path / "second.mp3")


# -- a read that is stopped leaves something to do -----------------------------

def test_a_rescan_during_a_read_leaves_an_actionable_message(
        window, monkeypatch, tmp_path, app):
    """A stopped probe emits nothing, so the window must say so itself.

    Without this the output sits at "Reading…" for the rest of the session with
    no way to get out of it.
    """
    focus(window, 0)
    probe = choose_track(window, monkeypatch, tmp_path / "song.mp3")
    window._scan()
    app.processEvents()

    assert probe.stopped
    said = window.preview_view.track_status.text()
    assert "Reading" not in said or "again" in said
    assert "Choose the track again" in said
    assert window._music_reading == {}


def test_closing_during_a_read_leaves_no_running_thread(
        app, tmp_path, sessions_home, probes, monkeypatch):
    """The window retains and stops; it never waits on the reader."""
    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow

    card = tmp_path / "card"
    card.mkdir()
    made = MainWindow(find_tools())
    made.source_combo.insertItem(0, str(card), str(card))
    made.source_combo.setCurrentIndex(0)
    made._add_clip(made._scan_generation, a_clip("hdz_001.ts", card))
    made._scan_done(made._scan_generation, 1)
    focus(made, 0)
    probe = choose_track(made, monkeypatch, tmp_path / "song.mp3")

    made.close()
    app.processEvents()

    assert probe.stopped
    assert not [t for t in made.findChildren(QThread) if t.isRunning()]


def test_music_cannot_be_edited_while_the_list_is_being_rebuilt(
        window, monkeypatch, tmp_path, app):
    """Music is a decision, so #108's rule covers it too."""
    focus(window, 0)
    window._scan()
    said = warnings_from(monkeypatch)
    choose_track(window, monkeypatch, tmp_path / "song.mp3")

    assert _FakeProbe.made == [], "a read started while the list was rebuilding"
    assert said == []


# -- what the export panel says ------------------------------------------------

def test_the_audio_checkbox_is_derived_without_its_value_being_rewritten(
        window, monkeypatch, tmp_path, app):
    """Disabling must not touch the stored value: the session keeps it."""
    window.export_panel.audio_check.setChecked(True)
    stored_before = window.export_panel.capture()["keep_audio"]

    focus(window, 0)
    probe = choose_track(window, monkeypatch, tmp_path / "song.mp3")
    probe.deliver()
    app.processEvents()

    assert not window.export_panel.audio_check.isEnabled()
    assert window.export_panel.capture()["keep_audio"] == stored_before
    assert window.export_panel.audio_check.isChecked() is True


def test_the_summary_names_the_output_and_disappears_again(
        window, monkeypatch, tmp_path, app):
    focus(window, 0)
    assert window.export_panel.music_summary.text() == ""

    probe = choose_track(window, monkeypatch, tmp_path / "song.mp3")
    probe.deliver()
    app.processEvents()

    said = window.export_panel.music_summary.text()
    assert "hdz_001.ts" in said
    assert "song.mp3" in said

    window.music_panel.mode_combo.setCurrentIndex(
        window.music_panel.mode_combo.findData(AudioMode.ORIGINAL))
    app.processEvents()
    assert window.export_panel.music_summary.text() == ""
    assert window.export_panel.audio_check.isEnabled()


# -- what is deliberately absent ----------------------------------------------

def test_the_band_says_the_preview_is_silent(window):
    """Drawing no transport is not enough; unexplained silence reads as a bug."""
    said = window.preview_view.music_silence_note.text()
    assert "no sound" in said
    assert "finished file" in said


def test_no_monitor_state_reaches_the_settings_or_the_choice(
        window, monkeypatch, tmp_path, app):
    """Asserted on the values, not on the absence of a widget."""
    focus(window, 0)
    probe = choose_track(window, monkeypatch, tmp_path / "song.mp3")
    probe.deliver()
    app.processEvents()

    captured = window.music_panel.capture()
    for absent in ("mute", "monitor", "volume", "listening"):
        assert not hasattr(captured, absent)
        assert not hasattr(window.current_settings(), absent)
    assert isinstance(captured.music_level, Fraction)


def test_the_band_starts_collapsed_and_gives_the_picture_its_height_back(
        window, app):
    """Collapsed means hidden, not merely disabled: the space is the point."""
    assert not window.music_band.isChecked()
    # `isHidden`, not `isVisible`: an offscreen window's children are all
    # invisible, and what is under test is whether this one was hidden on
    # purpose so the layout stops reserving its height.
    assert window.preview_view.music_body.isHidden()
    collapsed = window.music_band.sizeHint().height()

    window.music_band.setChecked(True)
    app.processEvents()
    assert not window.preview_view.music_body.isHidden()
    assert window.music_band.sizeHint().height() > collapsed


def test_a_delivery_bundle_refuses_instead_of_dropping_the_music(
        window, monkeypatch, tmp_path, app):
    """The route the first version missed (#116 review).

    A bundle member is frozen at the name it was agreed under, and
    `resolve_audio_plan` refuses music for one. Without a check on this path
    the choice was dropped on the way in and the bundle queued Master with an
    unconfigured choice, silently — the exact substitution every other route
    was written to prevent.
    """
    focus(window, 0)
    probe = choose_track(window, monkeypatch, tmp_path / "song.mp3")
    probe.deliver()
    app.processEvents()

    said = warnings_from(monkeypatch)
    # Patched so this test can also be run against a build without the fix:
    # there the refusal never happens and the confirmation opens, which blocks
    # forever with nobody to close it. Rejecting it means a build that drops
    # the music fails here for saying nothing, rather than by hanging.
    from PySide6.QtWidgets import QDialog
    monkeypatch.setattr("flightdvr.ui.BundleDialog.exec",
                        lambda self: QDialog.DialogCode.Rejected)
    tick(window, 0)
    window._add_bundle()

    assert window.jobs == [], "a bundle queued despite music it cannot export"
    assert said, "the bundle dropped the music without saying anything"
    assert "Nothing has been queued" in said[0]
    assert "bundle" in said[0]


def test_a_bundle_with_no_music_is_untouched_by_the_new_check(
        window, monkeypatch, tmp_path, app):
    """The compatibility half: an unconfigured choice is not this rule's
    business, and the bundle confirmation must still open."""
    from PySide6.QtWidgets import QDialog

    focus(window, 0)
    said = warnings_from(monkeypatch)
    opened = []
    monkeypatch.setattr(
        "flightdvr.ui.BundleDialog.exec",
        lambda self: (opened.append(True), QDialog.DialogCode.Rejected)[1])
    tick(window, 0)
    window._add_bundle()

    assert opened, f"the bundle was refused before its confirmation: {said}"


# -- W3: one commit path, gestures, and the waveform ----------------------------

from dataclasses import replace  # noqa: E402

from flightdvr.waveform_data import (  # noqa: E402
    WaveformAssetKey, WaveformBin, WaveformEnvelope, WaveformInspection,
    WaveformRequest,
)


def with_track(window, monkeypatch, tmp_path, app):
    """Clip 0 focused, a track chosen and read: the ordinary starting point."""
    focus(window, 0)
    target = window._music_target
    track = tmp_path / "song.mp3"
    probe = choose_track(window, monkeypatch, track)
    probe.deliver(an_asset(track))
    app.processEvents()
    return target


def counting(window, monkeypatch):
    """Count stores, stream preparations and stream builds."""
    counts = {"stores": 0, "syncs": 0, "builds": 0}
    real_store = window._store_music
    real_sync = window._sync_live_preview

    def store(target, choice):
        counts["stores"] += 1
        real_store(target, choice)

    def sync():
        counts["syncs"] += 1
        real_sync()

    monkeypatch.setattr(window, "_store_music", store)
    monkeypatch.setattr(window, "_sync_live_preview", sync)
    real_make = window.live_preview._make_stream

    def make(*args):
        counts["builds"] += 1
        return real_make(*args)

    window.live_preview._make_stream = make
    return counts


def an_envelope(asset):
    key = WaveformAssetKey.from_asset(asset)
    return WaveformEnvelope(key, 0, asset.decoded_samples, (WaveformBin(
        0, asset.decoded_samples, (-0.5, -0.5), (0.5, 0.5)),))


def test_a_released_fade_is_stored_once_and_prepares_nothing(
        window, monkeypatch, tmp_path, app):
    """Correction 4: a parameter commit never falls through to a rebuild,
    and with nothing playing it starts nothing."""
    target = with_track(window, monkeypatch, tmp_path, app)
    counts = counting(window, monkeypatch)
    editor = window.music_editor

    assert editor.begin_gesture()
    for fade in (800, 1_600, 2_400):
        editor.draft(replace(editor.choice, fade_in_samples=fade))
    editor.end_gesture()

    assert counts == {"stores": 1, "syncs": 0, "builds": 0}
    assert window._planned_music(target).fade_in_samples == 2_400


def test_a_typed_level_is_a_parameter_commit_too(
        window, monkeypatch, tmp_path, app):
    target = with_track(window, monkeypatch, tmp_path, app)
    counts = counting(window, monkeypatch)

    window.music_panel.music_level.setValue(50)
    app.processEvents()

    assert counts == {"stores": 1, "syncs": 0, "builds": 0}
    assert window._planned_music(target).music_level == Fraction(1, 2)


def test_a_passage_drag_prepares_the_stream_once_on_release(
        window, monkeypatch, tmp_path, app):
    target = with_track(window, monkeypatch, tmp_path, app)
    counts = counting(window, monkeypatch)
    editor = window.music_editor
    passage = editor.choice.passage

    editor.begin_gesture()
    for start in (4_410, 8_820, 13_230):
        editor.draft(replace(editor.choice, passage=SampleSpan(
            start, passage.end, passage.rate)))
    assert counts["syncs"] == 0, "a drag prepared the stream before release"
    editor.end_gesture()

    assert counts["stores"] == 1 and counts["syncs"] == 1
    assert window._planned_music(target).passage.start == 13_230


def test_escape_stores_nothing_and_shows_the_stored_value(
        window, monkeypatch, tmp_path, app):
    target = with_track(window, monkeypatch, tmp_path, app)
    before = window._planned_music(target)
    counts = counting(window, monkeypatch)
    editor = window.music_editor

    editor.begin_gesture()
    editor.draft(replace(editor.choice, music_level=Fraction(1, 4)))
    assert window.music_panel.music_level.value() == 25
    editor.cancel_gesture()

    assert counts["stores"] == 0
    assert window._planned_music(target) == before
    assert window.music_panel.music_level.value() == 100


def test_a_gesture_whose_output_changes_underneath_it_commits_nothing(
        window, monkeypatch, tmp_path, app):
    """Begin on A, move to B, let go: neither output is written.

    Two defences stand here — loading B abandons the gesture, and the pin no
    longer matches — and either alone is enough, so this fails only with both
    gone. The pin's own proof, with no reload, is in test_music_timeline.
    """
    first = with_track(window, monkeypatch, tmp_path, app)
    before = window._planned_music(first)
    editor = window.music_editor
    editor.begin_gesture()
    editor.draft(replace(editor.choice, fade_in_samples=4_000))
    counts = counting(window, monkeypatch)

    focus(window, 1)
    second = window._music_target
    assert second != first
    editor.draft(replace(editor.choice, fade_in_samples=8_000))
    editor.end_gesture()

    assert counts["stores"] <= 1          # B may be seeded by the focus
    assert window._planned_music(first) == before
    assert window._planned_music(second).fade_in_samples != 8_000
    assert not editor.gesture_active


def test_the_read_asks_for_the_waveform_from_the_same_one_pass(
        window, monkeypatch, tmp_path, app, probes):
    focus(window, 0)
    probe = choose_track(window, monkeypatch, tmp_path / "song.mp3")
    request = probe.waveform_request
    assert isinstance(request, WaveformRequest)
    assert request.generation == probe.generation


def test_a_waveform_is_filed_under_the_track_it_came_from(
        window, monkeypatch, tmp_path, app):
    focus(window, 0)
    target = window._music_target
    track = tmp_path / "song.mp3"
    probe = choose_track(window, monkeypatch, track)
    asset = an_asset(track)
    envelope = an_envelope(asset)

    probe.deliver_waveform(WaveformInspection.ready(
        probe.waveform_request, asset, envelope))
    app.processEvents()

    assert window._planned_music(target).asset == asset
    assert window.music_editor.envelope is envelope


def test_a_superseded_waveform_is_dropped_whole(
        window, monkeypatch, tmp_path, app):
    """Generation fence: an older read carries neither its track nor its
    waveform onto the output."""
    focus(window, 0)
    target = window._music_target
    old = choose_track(window, monkeypatch, tmp_path / "old.mp3")
    choose_track(window, monkeypatch, tmp_path / "new.mp3")
    stale = an_asset(tmp_path / "old.mp3")

    # Emitted straight, not through the stopped stand-in: a real result can
    # already be queued across threads when its read is stopped.
    old.waveform_ready.emit(WaveformInspection.ready(
        old.waveform_request, stale, an_envelope(stale)))
    app.processEvents()

    assert window._planned_music(target).asset is None
    assert WaveformAssetKey.from_asset(stale) not in window._music_envelopes


def test_an_unavailable_waveform_is_said_and_is_not_silence(
        window, monkeypatch, tmp_path, app):
    focus(window, 0)
    track = tmp_path / "song.mp3"
    probe = choose_track(window, monkeypatch, track)
    asset = an_asset(track)
    probe.deliver_waveform(WaveformInspection.unavailable(
        probe.waveform_request, asset, "waveform unavailable: too odd"))
    app.processEvents()
    assert window.music_editor.envelope is None
    assert "too odd" in window.music_editor.envelope_note


def test_reading_a_known_track_s_waveform_writes_no_choice(
        window, monkeypatch, tmp_path, app, probes):
    """Correction 5: a track chosen before (no waveform held) has its
    waveform read once on first showing; that read stores nothing."""
    target = with_track(window, monkeypatch, tmp_path, app)
    envelope_probe = probes[-1]
    assert envelope_probe.waveform_request is not None, (
        "no waveform read was started for the known track")
    stored = window._planned_music(target)
    counts = counting(window, monkeypatch)

    envelope_probe.deliver_waveform(WaveformInspection.ready(
        envelope_probe.waveform_request, stored.asset,
        an_envelope(stored.asset)))
    app.processEvents()

    assert counts["stores"] == 0
    assert window._planned_music(target) == stored
    assert window.music_editor.envelope is not None


def test_a_track_changed_on_disk_shows_no_waveform_and_keeps_the_choice(
        window, monkeypatch, tmp_path, app, probes):
    target = with_track(window, monkeypatch, tmp_path, app)
    envelope_probe = probes[-1]
    stored = window._planned_music(target)
    changed = replace(stored.asset, sha256="b" * 64)

    envelope_probe.deliver_waveform(WaveformInspection.ready(
        envelope_probe.waveform_request, changed, an_envelope(changed)))
    app.processEvents()

    assert window._planned_music(target) == stored
    assert window.music_editor.envelope is None
    assert "changed" in window.music_editor.envelope_note


def test_more_and_a_resize_read_nothing(window, monkeypatch, tmp_path, app,
                                        probes):
    with_track(window, monkeypatch, tmp_path, app)
    made = len(probes)
    counts = counting(window, monkeypatch)
    timeline = window.preview_view.music_timeline
    timeline.more_button.setChecked(True)
    timeline.more_button.setChecked(False)
    window.resize(window.width() - 40, window.height() - 40)
    app.processEvents()
    assert len(probes) == made
    assert counts == {"stores": 0, "syncs": 0, "builds": 0}


def test_monitoring_resolves_with_the_output_s_own_preset(window, app):
    """The panel's preset was used before; in Flow it is the output's own."""
    first, second = window.clips
    first.selects = [Select(1.0, 5.0, "one", sid="r-1")]
    second.selects = [Select(2.0, 6.0, "two", sid="r-2")]
    tick(window, 0)
    tick(window, 1)
    window.set_view_mode(Mode.FLOW)
    app.processEvents()
    a, b = [one.target for one in window._working_outputs()]
    window.output_plan.set_choices(b, "social", ExportSettings(), MusicChoice())
    window._select_working_target(a)
    app.processEvents()
    assert window.export_panel.preset_key() == "master"
    assert window._monitor_preset(b) == "social"
    window.set_view_mode(Mode.CLASSIC)


def test_a_waveform_is_held_only_while_something_uses_its_track(
        window, monkeypatch, tmp_path, app, probes):
    target = with_track(window, monkeypatch, tmp_path, app)
    stored = window._planned_music(target)
    probes[-1].deliver_waveform(WaveformInspection.ready(
        probes[-1].waveform_request, stored.asset, an_envelope(stored.asset)))
    app.processEvents()
    key = WaveformAssetKey.from_asset(stored.asset)
    assert key in window._music_envelopes

    window.music_editor.commit(MusicChoice(mode=AudioMode.ORIGINAL))
    app.processEvents()

    assert key not in window._music_envelopes


# -- W3: a submitted job's music, shown and never edited ----------------------------

def queued_with_music(window, monkeypatch, tmp_path, app):
    target = with_track(window, monkeypatch, tmp_path, app)
    tick(window, 0)
    said = warnings_from(monkeypatch)
    window._add_to_queue()
    app.processEvents()
    assert said == [] and len(window.jobs) == 1
    return target, window.jobs[0]


def test_a_submitted_job_shows_its_own_music_read_only(
        window, monkeypatch, tmp_path, app):
    _target, job = queued_with_music(window, monkeypatch, tmp_path, app)
    frozen = job.audio

    window._show_submitted(job)
    app.processEvents()
    editor = window._submitted_editor

    assert editor.stored == frozen
    assert not editor.editable and not editor.begin_gesture()
    assert not window.queue_panel.details_music.isHidden()
    assert window.queue_panel.details_playback.text() == (
        "Playing a submitted job is not available here.")


def test_working_edits_never_reach_the_submitted_music(
        window, monkeypatch, tmp_path, app):
    _target, job = queued_with_music(window, monkeypatch, tmp_path, app)
    frozen = job.audio
    window._show_submitted(job)
    app.processEvents()

    window.music_editor.commit(replace(window.music_editor.stored,
                                       music_level=Fraction(1, 5)))
    app.processEvents()
    window._show_submitted(job)            # shown again, after the edit
    app.processEvents()

    assert job.audio == frozen
    assert window._submitted_editor.stored == frozen
    assert window._submitted_editor.stored.music_level == 1


def test_reading_a_submitted_track_s_waveform_never_touches_the_job(
        window, monkeypatch, tmp_path, app, probes):
    _target, job = queued_with_music(window, monkeypatch, tmp_path, app)
    frozen = job.audio
    window._music_envelopes.clear()
    window._envelope_tried.clear()
    window._show_submitted(job)
    app.processEvents()
    reader = probes[-1]
    assert reader.waveform_request is not None, "no waveform read was started"

    reader.deliver_waveform(WaveformInspection.ready(
        reader.waveform_request, frozen.asset, an_envelope(frozen.asset)))
    app.processEvents()

    assert job.audio is frozen and job.audio == frozen
    assert window._submitted_editor.envelope is not None


def test_a_job_without_music_shows_no_music(window, monkeypatch, tmp_path,
                                           app):
    """After a job with music, one without must not keep showing it."""
    _target, with_music = queued_with_music(window, monkeypatch, tmp_path, app)
    focus(window, 1)
    tick(window, 1)
    window.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    window._add_to_queue()
    app.processEvents()
    without = next(job for job in window.jobs if job is not with_music)
    assert without.audio.mode is None or without.audio.asset is None

    window._show_submitted(with_music)
    assert not window.queue_panel.details_music.isHidden()
    window._show_submitted(without)
    assert window.queue_panel.details_music.isHidden()
