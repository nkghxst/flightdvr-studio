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
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication

from flightdvr.audio_plan import AudioMode, AudioAsset, MusicChoice
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
    failed = Signal(int, str)
    made: list["_FakeProbe"] = []

    def __init__(self, tools, track, generation, parent=None):
        super().__init__(parent)
        self.track = Path(track)
        self.generation = generation
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


# -- what a queued job carries -------------------------------------------------

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
