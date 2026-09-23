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

"""The monitoring transport: which output is heard, and when it goes quiet.

Both sides are stand-ins, and each models the awkward half rather than the
happy one — a device that holds what it was given and reports its own progress,
a stream that can refuse, end, or hand back blocks belonging to a generation
that has been superseded. A fake that cannot exhibit the failure cannot catch
it, which is how three defects reached review in this branch's predecessor.

Nothing here plays anything. Every number is about what was *submitted* or what
a backend *says* it processed, and none of it is evidence about sound leaving a
speaker.
"""

from __future__ import annotations

import array
import enum
import time
from types import SimpleNamespace

import pytest

from flightdvr.audio_device import (
    ACTIVE, FRAME_BYTES, IDLE, UNKNOWN, AudioOutput, DeviceReport,
    DeviceUnavailable,
)
from flightdvr.audio_plan import OUTPUT_CHANNELS, OUTPUT_RATE
from flightdvr.audio_stream import Buffering, PcmBlock, StreamFailed
from flightdvr.live_preview import (
    DRIFT_LIMIT_SAMPLES, STARVED_LIMIT, Listening, LivePreview,
)


def a_block(generation: int = 0, frames: int = 4,
            output_start: int = 0) -> PcmBlock:
    samples = frames * OUTPUT_CHANNELS
    return PcmBlock(generation=generation, monitor_revision=0,
                    output_start=output_start,
                    frames=frames, planned=[0.8] * samples,
                    monitored=[0.2] * samples)


class FakeStream:
    """A stream that can refuse, end, and be superseded."""

    def __init__(self, *, blocks: int = 4, generation: int = 0,
                 output_start: int = 0, refuse_start: bool = False):
        self.generation = generation
        self.remaining = blocks
        self.output_start = output_start
        self.calls = []
        self.monitor = None
        self.raises = None
        self.started = False
        self.stopped = False
        self.waited = None
        self._refuse_start = refuse_start

    def start(self) -> None:
        """Refuses twice over, as the real one does."""
        if self._refuse_start:
            raise RuntimeError("a stopped audio stream cannot be started")
        self.calls.append("start")
        self.started = True

    def wait_stopped(self, timeout=None) -> bool:
        self.waited = timeout
        return True

    def pull(self):
        if self.raises is not None:
            raise self.raises
        if self.remaining <= 0:
            return None
        self.remaining -= 1
        block = a_block(self.generation, output_start=self.output_start)
        self.output_start += block.frames
        return block

    def resume(self) -> None:
        # The real stream refuses this before `start`, which is exactly the
        # thing a stand-in that shrugged would hide.
        if not self.started:
            raise RuntimeError("start the audio stream before resuming it")
        self.calls.append("resume")

    def pause(self) -> None:
        self.calls.append("pause")

    def request_stop(self) -> None:
        self.calls.append("request_stop")
        self.stopped = True

    def restart(self) -> int:
        self.calls.append("restart")
        self.generation += 1
        self.remaining = 4
        self.output_start = 0
        return self.generation

    def reprime(self, sample: int) -> int:
        self.calls.append(f"reprime:{sample}")
        self.generation += 1
        self.remaining = 4
        self.output_start = sample
        return self.generation

    def set_monitor(self, *, level=None, muted=None):
        self.monitor = (level, muted)
        return self.monitor


class FakeOutput:
    """An `AudioOutput` that holds what it is given and reports on itself."""

    def __init__(self, *, no_device: bool = False):
        self.presented = []
        self.calls = []
        self.opened = False
        self._no_device = no_device
        self.generation = 0
        self.failure = ""
        self.report = DeviceReport(state=ACTIVE, processed_usecs=0,
                                   bytes_free=4096, buffer_size=4096)
        self.cause = ""
        self.processed = 0

    def present(self, block) -> int:
        if block.generation != self.generation:
            return 0
        self.presented.append(block)
        return block.frames * FRAME_BYTES

    def pump(self) -> int:
        self.calls.append("pump")
        return 0

    def start(self) -> None:
        if self._no_device:
            raise DeviceUnavailable("this machine has no audio output")
        self.calls.append("start")
        self.opened = True

    def resume(self) -> None:
        self.calls.append("resume")

    def pause(self) -> None:
        self.calls.append("pause")

    def stop(self) -> None:
        self.calls.append("stop")
        self.opened = False

    def reset(self, generation: int) -> None:
        self.calls.append(f"reset:{generation}")
        self.generation = generation
        self.presented.clear()

    def observe(self) -> DeviceReport:
        return self.report

    def processed_bytes(self, report=None):
        return self.processed

    def starvation(self, report=None) -> str:
        return self.cause


def transport(*, blocks: int = 4) -> tuple[LivePreview, FakeStream, FakeOutput]:
    stream = FakeStream(blocks=blocks)
    output = FakeOutput()
    live = LivePreview(stream_factory=lambda _t, _l: stream, output=output)
    live.set_target("hdz_001.ts")
    return live, stream, output


# -- quiet, and offered ---------------------------------------------------------

def test_a_new_transport_offers_nothing_and_is_silent():
    live = LivePreview()
    assert not live.status.offered
    assert live.status.muted and not live.status.playing
    assert live.tick(0) == 0


def test_taking_a_target_leaves_it_paused_and_muted():
    live, stream, output = transport()
    assert live.status.offered and live.status.available
    assert live.status.muted and not live.status.playing
    assert "resume" not in stream.calls, "it started playing by itself"


def test_music_still_being_read_is_offered_as_a_reason_not_as_silence():
    """A pending track has nothing to mix. Saying so is the difference between
    a rule and a transport that looks broken."""
    live = LivePreview(stream_factory=lambda _t, _l: FakeStream(),
                       output=FakeOutput())
    live.set_target("hdz_001.ts", reason="its music track is still being read")

    assert not live.status.available
    assert "still being read" in live.status.reason
    assert live.status.muted and not live.status.playing
    live.play()
    assert not live.status.playing, "play worked while the track was pending"


def test_an_output_with_nothing_to_hear_is_not_a_refusal():
    """`offered` already says there is nothing. A reason on top of that would
    put an explanation where the ordinary standing note belongs, and a person
    who reads one excuse too many stops reading them."""
    live = LivePreview(stream_factory=lambda _t, _l: None, output=FakeOutput())
    live.set_target("no music here")
    assert not live.status.offered
    assert not live.status.available
    assert live.status.reason == ""


def test_a_stream_that_cannot_be_built_is_reported():
    def explode(_target, _listening):
        raise RuntimeError("no reader for this target")

    live = LivePreview(stream_factory=explode, output=FakeOutput())
    live.set_target("hdz_001.ts")
    assert "cannot be monitored" in live.status.reason
    assert "no reader" in live.status.reason


# -- the controls ---------------------------------------------------------------

def test_playing_feeds_the_device_and_pausing_stops():
    live, stream, output = transport()
    live.play()
    assert live.status.playing
    assert live.tick(0) > 0
    assert output.presented, "nothing reached the device"

    live.pause()
    assert not live.status.playing
    assert live.tick(0) == 0
    assert "pause" in stream.calls and "pause" in output.calls


def test_the_level_and_mute_are_applied_by_the_stream_only():
    """The stream renders the level into the block. Setting it on the device
    as well would square it."""
    live, stream, output = transport()
    live.set_level(0.5)
    live.set_muted(False)

    assert stream.monitor == (0.5, False)
    assert not any(call.startswith("volume") for call in output.calls)


def test_the_level_is_held_between_zero_and_one():
    live, stream, _output = transport()
    live.set_level(4.0)
    assert live.level == 1.0
    live.set_level(-1.0)
    assert live.level == 0.0


def test_starting_from_the_beginning_re_anchors_both_sides():
    live, stream, output = transport()
    live.play()
    live.tick(0)

    live.restart()

    assert "restart" in stream.calls
    assert f"reset:{stream.generation}" in output.calls
    assert output.presented == [], "sound from before the restart survived it"


def test_following_the_picture_elsewhere_re_anchors_both_sides():
    live, stream, output = transport()
    live.play()
    live.seek(48_000)

    assert "reprime:48000" in stream.calls
    assert f"reset:{stream.generation}" in output.calls


def test_the_listening_choice_is_remembered():
    live, _stream, _output = transport()
    assert live.listening is Listening.MIX
    live.set_listening(Listening.SOURCE)
    assert live.listening is Listening.SOURCE


# -- speed ----------------------------------------------------------------------

def test_monitoring_is_off_at_any_speed_but_one_and_says_why():
    """Nothing merged stretches audio in time, so the choice is between the
    wrong pitch and no sound. Saying so is the honest third option."""
    live, stream, _output = transport()
    live.play()

    live.set_speed(2.0)

    assert not live.status.playing
    assert "not 1x" in live.status.reason
    live.play()
    assert not live.status.playing, "it played at the wrong speed"


def test_returning_to_one_makes_monitoring_available_again():
    live, _stream, _output = transport()
    live.set_speed(0.5)
    assert not live.status.available
    live.set_speed(1.0)
    assert live.status.available, live.status.reason


# -- switching outputs ----------------------------------------------------------

def test_switching_target_cannot_emit_the_old_output_s_sound():
    """The one mistake here a person would hear rather than read."""
    first = FakeStream(generation=0)
    second = FakeStream(generation=7)
    output = FakeOutput()
    streams = iter((first, second))
    live = LivePreview(stream_factory=lambda _t, _l: next(streams), output=output)

    live.set_target("one")
    live.play()
    live.tick(0)
    assert output.presented

    live.set_target("two")

    assert output.presented == [], "sound from the first output survived"
    assert "request_stop" in first.calls
    assert not live.status.playing and live.status.muted
    # And a stale block from the old stream is refused outright.
    assert output.present(a_block(generation=0)) == 0


def test_switching_away_and_back_does_not_resume_by_itself():
    live, _stream, _output = transport()
    live.play()
    live.set_target(None)
    live.set_target("hdz_001.ts")
    assert not live.status.playing and live.status.muted


# -- when the sound cannot be trusted -------------------------------------------

def test_a_backend_that_stops_reporting_stops_monitoring():
    live, _stream, output = transport()
    live.play()
    output.report = DeviceReport(state=UNKNOWN)

    live.tick(0)

    assert not live.status.playing
    assert "stopped reporting" in live.status.reason


def test_a_device_error_stops_monitoring_and_names_it():
    live, _stream, output = transport()
    live.play()
    output.report = DeviceReport(state=ACTIVE, error="UnderrunError")

    live.tick(0)

    assert not live.status.playing
    assert "UnderrunError" in live.status.reason


def test_a_run_of_starved_ticks_gives_up_rather_than_stuttering():
    """One is ordinary — a block arrived late. A run of them is a stutter
    nobody asked to listen to."""
    live, _stream, output = transport(blocks=0)
    live.play()
    output.cause = "starved"

    for _ in range(STARVED_LIMIT - 1):
        live.tick(0)
    assert live.status.playing, "it gave up on the first late block"

    live.tick(0)
    assert not live.status.playing
    assert "could not be kept up with" in live.status.reason


def test_a_single_late_block_is_forgiven():
    live, _stream, output = transport(blocks=0)
    live.play()
    output.cause = "starved"
    live.tick(0)
    output.cause = ""
    for _ in range(STARVED_LIMIT * 2):
        live.tick(0)
    assert live.status.playing, "an isolated late block was treated as failure"


def test_material_running_out_is_not_treated_as_a_failure():
    live, _stream, output = transport(blocks=0)
    live.play()
    output.cause = "ended"
    for _ in range(STARVED_LIMIT * 2):
        live.tick(0)
    assert live.status.playing, "the end of the material was read as a fault"


def test_drift_beyond_the_bound_stops_monitoring_rather_than_nudging():
    """An estimate, and when it is too wide the sound goes rather than the
    picture being moved to meet it."""
    live, _stream, output = transport()
    live.play()
    output.processed = 0

    live.tick(DRIFT_LIMIT_SAMPLES + 1)

    assert not live.status.playing
    assert "drifted too far" in live.status.reason


def test_drift_inside_the_bound_keeps_playing():
    live, _stream, output = transport()
    live.play()
    output.processed = 0
    live.tick(DRIFT_LIMIT_SAMPLES - 1)
    assert live.status.playing


def test_nonzero_seek_uses_the_requested_output_as_its_device_epoch():
    """A reset sink reports time since its own start, not output time zero."""
    live, _stream, output = transport()
    live.play()

    live.seek(48_000)
    output.processed = 0
    live.tick(48_000)
    assert live.status.playing, live.status.reason

    output.processed = 4_800 * FRAME_BYTES
    live.tick(52_800)
    assert live.status.playing, live.status.reason


def test_reseek_subtracts_one_fresh_nonzero_backend_baseline():
    """A backend may begin a reset epoch at a nonzero counter reading."""
    live, _stream, output = transport()
    live.play()
    live.seek(48_000)
    output.processed = 4_800 * FRAME_BYTES
    live.tick(52_800)
    assert live.status.playing, live.status.reason

    output.processed = 2_400 * FRAME_BYTES
    live.seek(144_000)
    output.processed = 7_200 * FRAME_BYTES
    live.tick(148_800)

    assert live.status.playing, live.status.reason


def test_device_epoch_keeps_the_literal_drift_boundary():
    """Re-anchoring at every tick would hide the second discrepancy."""
    live, _stream, output = transport()
    live.play()
    output.processed = 0

    live.tick(9_600)
    assert live.status.playing, "the inclusive 9600-sample bound changed"

    live.tick(9_601)
    assert not live.status.playing
    assert "drifted too far" in live.status.reason


def test_startup_buffering_waits_for_the_first_accepted_block_origin():
    """No producer cursor or assumed output zero may stand in for material."""
    stream = FakeStream(blocks=0, output_start=48_000)
    output = FakeOutput()
    output.processed = 2_400 * FRAME_BYTES
    live = LivePreview(stream_factory=lambda _t, _l: stream, output=output)
    live.set_target("nonzero-output")
    live.play()

    live.tick(48_000)
    assert live.status.playing, "buffering was mistaken for an output origin"

    stream.remaining = 1
    live.tick(48_000)
    assert output.presented[0].output_start == 48_000
    assert live.status.playing, live.status.reason


def test_pause_invalidates_the_epoch_without_changing_stream_generation():
    stream = FakeStream(blocks=0, output_start=72_000)
    output = FakeOutput()
    live = LivePreview(stream_factory=lambda _t, _l: stream, output=output)
    live.set_target("same-generation-resume")
    live.play()
    live.pause()

    # Model a newly fenced sink whose counter origin is not zero. The stream
    # generation deliberately stays unchanged across this pause/resume.
    output.processed = 12_000 * FRAME_BYTES
    stream.remaining = 1
    generation = stream.generation
    live.play()
    live.tick(72_000)

    assert stream.generation == generation
    assert live.status.playing, live.status.reason


def test_restart_uses_a_fresh_nonzero_backend_baseline():
    live, _stream, output = transport()
    live.play()
    live.tick(0)

    output.processed = 12_000 * FRAME_BYTES
    live.restart()
    live.tick(0)

    assert live.status.playing, live.status.reason


def test_target_replacement_cannot_reuse_the_previous_device_epoch():
    first = FakeStream()
    second = FakeStream(generation=7)
    streams = iter((first, second))
    output = FakeOutput()
    live = LivePreview(stream_factory=lambda _t, _l: next(streams),
                       output=output)
    live.set_target("first")
    live.play()
    live.tick(0)

    output.processed = 12_000 * FRAME_BYTES
    live.set_target("second")
    live.play()
    live.tick(0)

    assert output.generation == second.generation
    assert live.status.playing, live.status.reason


def test_a_counter_reversal_inside_one_epoch_is_a_named_failure():
    live, _stream, output = transport(blocks=1)
    output.processed = 5_000 * FRAME_BYTES
    live.play()
    live.tick(0)
    assert live.status.playing, live.status.reason

    output.processed = 7_000 * FRAME_BYTES
    live.tick(2_000)
    assert live.status.playing, live.status.reason

    output.processed = 6_500 * FRAME_BYTES
    live.tick(1_500)

    assert not live.status.playing
    assert "reset its progress" in live.status.reason


def test_a_backend_that_stops_reporting_progress_stops_monitoring():
    live, _stream, output = transport()
    live.play()
    output.processed = None

    live.tick(0)

    assert not live.status.playing
    assert "reporting its progress" in live.status.reason


def test_a_stream_that_is_buffering_is_not_a_failure():
    live, stream, output = transport()
    live.play()
    stream.raises = Buffering("nothing yet")

    live.tick(0)

    assert live.status.playing, "an ordinary buffering pause stopped monitoring"


# -- closing --------------------------------------------------------------------

def test_closing_asks_and_does_not_wait():
    live, stream, output = transport()
    live.play()
    live.close()

    assert "request_stop" in stream.calls
    assert "stop" in output.calls
    assert not live.status.playing
    assert not live.status.offered


def test_closing_twice_is_safe():
    live, _stream, _output = transport()
    live.close()
    live.close()


def test_nothing_here_is_written_anywhere():
    """Monitoring lives for as long as the window is open and no longer.

    Asserted on the surface: a transport that grew a save, a session or an
    export would have to name one.
    """
    from flightdvr import live_preview

    for forbidden in ("save", "session", "settings", "export", "persist"):
        assert not any(forbidden in name.lower()
                       for name in dir(LivePreview) if not name.startswith("_"))
        assert not any(forbidden in name.lower()
                       for name in vars(live_preview) if not name.startswith("_"))


# -- the window's half ----------------------------------------------------------

@pytest.fixture
def window(monkeypatch, tmp_path):
    """A window whose background work is stubbed, as #116 established.

    The audio output is replaced too: these settle the wiring, and a real sink
    would be a device opening in a test suite.
    """
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from datetime import datetime
    from pathlib import Path

    from flightdvr.media import ClipInfo, find_tools
    import tests.test_music_wiring as wiring

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("flightdvr.ui.ScanWorker", wiring._NoScan)
    monkeypatch.setattr("flightdvr.ui.HardwareProbe", wiring._NoProbe)
    monkeypatch.setattr("flightdvr.ui.FilmstripLoader", wiring._NoStrip)
    monkeypatch.setattr("flightdvr.updates.should_check", lambda *a, **k: False)
    monkeypatch.setattr("flightdvr.ui.AudioOutput", lambda *a, **k: FakeOutput())
    monkeypatch.setattr("flightdvr.ui.MusicAssetProbe", wiring._FakeProbe,
                        raising=False)

    from flightdvr.ui import MainWindow
    card = tmp_path / "card"
    card.mkdir()
    made = MainWindow(find_tools())
    made.source_combo.insertItem(0, str(card), str(card))
    made.source_combo.setCurrentIndex(0)
    monkeypatch.setattr(made.thumbs, "request", lambda *_: None)
    monkeypatch.setattr(made.player, "load", lambda *a, **k: None)
    clip = ClipInfo(path=card / "hdz_001.ts", size=1024,
                    modified=datetime(2025, 10, 8, 18, 39), duration=30.0,
                    width=1280, height=720, fps=60.0, video_codec="hevc",
                    audio_codec="aac", pix_fmt="yuvj420p", color_range="pc")
    made._add_clip(made._scan_generation, clip)
    made._scan_done(made._scan_generation, 1)
    app.processEvents()
    yield made
    made.close()


def test_the_window_offers_no_sound_until_it_is_asked(window):
    assert window.live_preview is not None
    assert not window.preview_view.listen_check.isChecked()
    assert not window.live_preview.status.playing
    assert window.live_preview.status.muted


def test_a_clip_with_no_music_says_the_preview_is_silent(window):
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    said = window.preview_view.music_silence_note.text()
    assert "no sound" in said and "finished file" in said


def test_the_level_slider_reaches_the_transport(window):
    window.preview_view.listen_level.setValue(70)
    assert window.live_preview.level == pytest.approx(0.70)


def test_the_listening_choice_reaches_the_transport(window):
    combo = window.preview_view.listening_combo
    combo.setCurrentIndex(combo.findData("source"))
    assert window.live_preview.listening is Listening.SOURCE


def test_asking_to_listen_with_nothing_to_hear_stays_quiet(window):
    """No target, so nothing is offered. It must not pretend otherwise."""
    window.preview_view.listen_check.setChecked(True)
    assert not window.live_preview.status.playing


def test_closing_the_window_closes_the_transport(window):
    transport = window.live_preview
    window.close()
    assert not transport.status.playing
    assert not transport.status.offered


def test_monitoring_never_reaches_a_job_or_the_session(window):
    """Listening is not an export choice and has nowhere to be stored."""
    window.preview_view.listen_level.setValue(90)
    window.preview_view.listen_check.setChecked(True)
    settings = window.current_settings()
    for absent in ("listen", "monitor", "mute", "volume"):
        assert not any(absent in name.lower() for name in dir(settings)
                       if not name.startswith("_"))
    assert window.jobs == []


def test_joined_picture_ticks_never_service_source_monitoring(
        window, monkeypatch):
    """S2 owns picture only; its output clock cannot tick S1 source audio."""
    driven = []
    monkeypatch.setattr(window, "_joined_assemble_active", lambda: True)
    monkeypatch.setattr(window, "_drive_monitoring", driven.append)

    window._preview_playback_tick(3.0, False)

    assert driven == []


def test_each_joined_player_tick_services_one_output_sample_even_if_reentered(
        window, monkeypatch):
    """The joined picture clock is authority; source seconds never enter here."""
    from types import SimpleNamespace

    monkeypatch.setattr(window, "_joined_assemble_active", lambda: True)
    window._sequence_plan = SimpleNamespace(revision="joined-1")
    window.player._sequence_plan = window._sequence_plan
    window._monitor_snapshot = SimpleNamespace(
        output_sample=lambda seconds: round(seconds * OUTPUT_RATE))
    calls = []

    def reentrant(sample):
        calls.append(sample)
        window._preview_playback_tick(13.0, False)

    monkeypatch.setattr(window.live_preview, "tick", reentrant)
    window.live_preview._playing = True
    window.live_preview._stream = FakeStream(blocks=0)
    window.live_preview._reason = ""

    window._preview_playback_tick(1.0, False)

    assert calls == [OUTPUT_RATE], (
        "one output tick was serviced twice or used paused source second 13")


def test_joined_picture_state_does_not_start_sound_without_listen_intent(
        window, monkeypatch):
    monkeypatch.setattr(window, "_joined_assemble_active", lambda: True)
    calls = []
    monkeypatch.setattr(window.live_preview, "pause",
                        lambda: calls.append("pause"))
    monkeypatch.setattr(window.live_preview, "set_muted",
                        lambda value: calls.append(("muted", value)))

    window._preview_state_changed(True)

    assert calls == []


# -- what the review found (#121) -----------------------------------------------

def test_playing_starts_both_sides_rather_than_resuming_a_stream_that_never_ran():
    """`AudioStream.resume()` refuses before `start()`, and the output has no
    sink until it is started. Neither begins by itself, so the first play is
    where both are opened — and nothing here was opening them."""
    live, stream, output = transport()
    live.play()

    assert stream.started, "the producer was never started"
    assert output.opened, "the sink was never opened"
    assert live.status.playing
    assert "resume" in stream.calls


def test_a_machine_with_no_audio_output_says_so_and_stays_quiet():
    stream = FakeStream()
    output = FakeOutput(no_device=True)
    live = LivePreview(stream_factory=lambda _t, _l: stream, output=output)
    live.set_target("hdz_001.ts")

    live.play()

    assert not live.status.playing
    assert "nothing to listen on" in live.status.reason


def test_a_stream_that_refuses_to_start_is_reported_not_ignored():
    stream = FakeStream(refuse_start=True)
    live = LivePreview(stream_factory=lambda _t, _l: stream,
                       output=FakeOutput())
    live.set_target("hdz_001.ts")
    # The target was taken: this has to fail at `start`, not before it. A
    # factory with the wrong arity used to raise `TypeError` here, which
    # `set_target` catches — so the assertions below passed while `start` was
    # never reached at all.
    assert live.status.available, live.status.reason

    live.play()

    assert not live.status.playing
    assert "cannot be monitored" in live.status.reason
    assert "cannot be started" in live.status.reason


def test_choosing_source_only_rebuilds_rather_than_being_remembered():
    """A different thing to listen to is a different plan. Recording the
    choice and carrying on left the control looking as though it did
    something."""
    built = []

    def factory(target, listening):
        stream = FakeStream()
        built.append((listening, stream))
        return stream

    live = LivePreview(stream_factory=factory, output=FakeOutput())
    live.set_target("hdz_001.ts")
    assert len(built) == 1

    live.set_listening(Listening.SOURCE)

    assert len(built) == 2, "the mix was not rebuilt for a different choice"
    assert [listening for listening, _ in built] == [
        Listening.MIX, Listening.SOURCE], (
        "the factory was never told what to build")
    assert built[0][1].stopped, "the old mix was left running"


def test_choosing_the_same_thing_again_rebuilds_nothing():
    built = []

    def factory(target, listening):
        built.append((listening, FakeStream()))
        return built[-1][1]

    live = LivePreview(stream_factory=factory, output=FakeOutput())
    live.set_target("hdz_001.ts")
    live.set_listening(Listening.MIX)
    assert len(built) == 1


def test_changing_what_is_heard_while_playing_keeps_playing():
    built = []

    def factory(target, listening):
        built.append((listening, FakeStream()))
        return built[-1][1]

    live = LivePreview(stream_factory=factory, output=FakeOutput())
    live.set_target("hdz_001.ts")
    live.play()
    live.set_listening(Listening.SOURCE)

    assert live.status.playing
    assert built[-1][1].started


def test_a_producer_that_fails_stops_monitoring_instead_of_being_swallowed():
    """It used to say it was playing while the producer had given up behind
    it — silence that looks like a working transport."""
    live, stream, _output = transport()
    live.play()
    stream.raises = StreamFailed("the reader died")

    live.tick(0)

    assert not live.status.playing
    assert "could not be produced" in live.status.reason
    assert "the reader died" in live.status.reason


def test_a_stopped_stream_is_waited_for_somewhere_other_than_here():
    """`wait_stopped` joins a plain thread and its own docstring says keep that
    off the UI thread. Asking and never waiting leaks the producer; waiting
    here would hold the window."""
    live, stream, _output = transport()
    live.play()

    live.close()

    assert stream.stopped
    for waiter in live._reapers:
        waiter.join(2.0)
        assert not waiter.is_alive()
    assert stream.waited is not None, "nobody ever waited for the producer"


def test_switching_target_reaps_the_stream_it_leaves():
    first = FakeStream(generation=0)
    second = FakeStream(generation=5)
    streams = iter((first, second))
    live = LivePreview(stream_factory=lambda _t, _l: next(streams),
                       output=FakeOutput())
    live.set_target("one")
    live.play()

    live.set_target("two")

    assert first.stopped
    for waiter in live._reapers:
        waiter.join(2.0)
    assert first.waited is not None, "the old producer was never waited for"


def test_source_only_and_the_finished_mix_are_different_plans(window):
    """The finding my last correction missed (#121 rereview).

    Rebuilding proved only that a second object was made. What matters is that
    it carries a different plan: source only resolves `Original`, so the mix is
    the recording's own sound and no music at all. Asserted on the resolved
    plan, which is the thing the stream is built from.
    """
    from flightdvr.audio_plan import (
        AudioMode, AudioAsset, MusicChoice, SampleSpan,
    )

    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    target = window._music_target
    assert target is not None

    track = window._trim_clip.path.parent / "song.mp3"
    asset = AudioAsset(track, "b" * 64, 0, 44_100, 2, 44_100 * 30)
    window._store_music(target, MusicChoice(
        track=track, mode=AudioMode.REPLACE, asset=asset,
        passage=SampleSpan(0, asset.decoded_samples, asset.sample_rate)))

    mixed, _ = window._monitor_plan(target, Listening.MIX)
    source, _ = window._monitor_plan(target, Listening.SOURCE)

    assert mixed is not None and source is not None
    assert mixed.mode is AudioMode.REPLACE
    # Equal, not identical: `OutputPlan.get` hands back a defensive copy.
    assert mixed.asset == asset, "the mix lost the chosen track"
    assert source.mode is AudioMode.ORIGINAL, (
        "source only resolved the same plan as the finished mix")
    assert source.asset is None, "source only was still carrying the music"
    assert source.music_gain == 0


class AbsoluteFrameReader:
    """A bounded reader whose PCM exposes the absolute frame requested."""

    def __init__(self, frames: int):
        self._frames = frames
        self.reads = []
        self.closed = False

    @property
    def frames(self) -> int:
        return self._frames

    def read(self, start: int, frames: int, cancelled) -> list[float]:
        self.reads.append((start, frames))
        values = []
        for frame in range(start, start + frames):
            values.extend((frame / OUTPUT_RATE, frame / OUTPUT_RATE))
        return values

    def request_stop(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class TaggedFrameReader(AbsoluteFrameReader):
    """Independent stereo signal for one exact source identity."""

    def __init__(self, frames: int, base: float):
        super().__init__(frames)
        self.base = base
        self.stop_calls = 0
        self.close_calls = 0

    def read(self, start: int, frames: int, cancelled) -> list[float]:
        self.reads.append((start, frames))
        values = []
        for frame in range(start, start + frames):
            left = self.base + (frame / OUTPUT_RATE) * 0.001
            values.extend((left, left + 0.01))
        return values

    def request_stop(self) -> None:
        self.stop_calls += 1

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


def _make_joined_window(window):
    """Literal A[10,13), B[2,4), A[10,13) through the real UI binding."""
    from datetime import datetime

    from PySide6.QtWidgets import QApplication

    from flightdvr.assembly import Item
    from flightdvr.flow_layout import Mode, Stage
    from flightdvr.media import ClipInfo, Select

    first = window.clips[0]
    second = ClipInfo(
        path=first.path.parent / "hdz_002.ts", size=2048,
        modified=datetime(2025, 10, 8, 18, 40), duration=30.0,
        width=1280, height=720, fps=60.0, video_codec="hevc",
        audio_codec="aac", pix_fmt="yuvj420p", color_range="pc")
    window._add_clip(window._scan_generation, second)
    first.selects = [Select(10.0, 13.0, "A", sid="a")]
    second.selects = [Select(2.0, 4.0, "B", sid="b")]
    items = (
        Item(first.fingerprint, "a"),
        Item(second.fingerprint, "b"),
        Item(first.fingerprint, "a"),
    )
    window._store_assembly(items)
    window.set_view_mode(Mode.FLOW)
    window._show_stage(Stage.ASSEMBLE)
    QApplication.processEvents()
    return first, second, window._music_target


def _pull_stream_at(stream, output_sample: int):
    if stream.state.value == "ready":
        stream.start()
    else:
        stream.pause()
        stream.reprime(output_sample)
    stream.resume()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            block = stream.pull()
        except Buffering:
            time.sleep(0.01)
            continue
        if block.output_start == output_sample:
            return block
    pytest.fail(f"stream produced no block at output sample {output_sample}")


def test_ui_constructed_sequence_emits_exact_aba_origins_and_channels(
        window, monkeypatch):
    """The real UI factory emits A/B/A, including repeated unticked A."""
    from flightdvr.audio_reader import FfmpegPcmReader
    from flightdvr.audio_stream import AudioStream, SequencePcmReader

    first, second, target = _make_joined_window(window)
    made = {}

    def source_factory(_cls, _tools, path, *, stream_index, timeline_frames):
        key = str(path)
        if key not in made:
            base = 0.1 if key == str(first.path) else 0.2
            made[key] = TaggedFrameReader(timeline_frames, base)
        return made[key]

    monkeypatch.setattr(
        FfmpegPcmReader, "for_source", classmethod(source_factory))
    window.live_preview.set_listening(Listening.SOURCE)
    stream = window.live_preview._stream

    assert isinstance(stream, AudioStream)
    assert isinstance(stream._source_reader, SequencePcmReader)
    assert tuple(one.output.start for one in stream._source_reader.segments) == (
        0, 3 * OUTPUT_RATE, 5 * OUTPUT_RATE)
    assert len(made) == 2, "the repeated A occurrence opened a second leaf"

    expected = (
        (0, 0.110, 0.120),
        (3, 0.202, 0.212),
        (5, 0.110, 0.120),
    )
    for second_at, left, right in expected:
        block = _pull_stream_at(stream, second_at * OUTPUT_RATE)
        assert block.planned[0] == pytest.approx(left, abs=2e-6)
        assert block.planned[1] == pytest.approx(right, abs=2e-6)
        assert block.planned[0] != 0.0
    assert first not in window.selected_clips(), (
        "the Assembly oracle accidentally depended on the browser checkbox")
    stream.request_stop()
    assert stream.wait_stopped(5.0)
    assert made[str(first.path)].stop_calls == 1
    assert made[str(first.path)].close_calls == 1


def test_ui_constructed_sequence_emits_silence_for_one_silent_occurrence(
        window, monkeypatch):
    """A known silent B is explicit zeroes, never guessed A continuation."""
    from flightdvr.audio_reader import FfmpegPcmReader

    first, second, _target = _make_joined_window(window)
    second.audio_codec = None
    made = []

    def source_factory(_cls, _tools, path, *, stream_index, timeline_frames):
        assert str(path) == str(first.path), "the silent clip opened a decoder"
        reader = TaggedFrameReader(timeline_frames, 0.1)
        made.append(reader)
        return reader

    monkeypatch.setattr(
        FfmpegPcmReader, "for_source", classmethod(source_factory))
    window.live_preview.set_listening(Listening.SOURCE)
    stream = window.live_preview._stream

    assert stream._source_reader.segments[1].reader is None
    before = _pull_stream_at(stream, 0)
    silent = _pull_stream_at(stream, 3 * OUTPUT_RATE)
    after = _pull_stream_at(stream, 5 * OUTPUT_RATE)
    assert before.planned[:2] == pytest.approx((0.110, 0.120), abs=2e-6)
    assert all(value == 0.0 for value in silent.planned)
    assert after.planned[:2] == pytest.approx((0.110, 0.120), abs=2e-6)
    assert len(made) == 1
    stream.request_stop()
    assert stream.wait_stopped(5.0)


def test_ui_constructed_all_silent_sequence_needs_no_source_reader(
        window, monkeypatch):
    from flightdvr.audio_reader import FfmpegPcmReader

    first, second, _target = _make_joined_window(window)
    first.audio_codec = None
    second.audio_codec = None
    monkeypatch.setattr(
        FfmpegPcmReader, "for_source",
        classmethod(lambda *_a, **_k: pytest.fail("silent run opened a decoder")))

    window.live_preview.set_listening(Listening.SOURCE)
    stream = window.live_preview._stream

    assert stream._source_reader is None
    block = _pull_stream_at(stream, 3 * OUTPUT_RATE)
    assert all(value == 0.0 for value in block.planned)
    stream.request_stop()
    assert stream.wait_stopped(5.0)


def test_ui_constructed_mix_keeps_music_continuous_and_loops_at_six(
        window, monkeypatch):
    from fractions import Fraction

    from flightdvr.audio_plan import (
        AudioAsset, AudioMode, MusicChoice, SampleSpan, ShortTrackPolicy,
    )
    from flightdvr.audio_reader import FfmpegPcmReader

    first, _second, target = _make_joined_window(window)
    track = first.path.parent / "music.wav"
    asset = AudioAsset(track, "c" * 64, 0, OUTPUT_RATE, 2, 10 * OUTPUT_RATE)
    readers = {}

    def source_factory(_cls, _tools, path, *, stream_index, timeline_frames):
        key = str(path)
        base = 0.1 if key == str(first.path) else 0.2
        readers.setdefault(key, TaggedFrameReader(timeline_frames, base))
        return readers[key]

    music = TaggedFrameReader(10 * OUTPUT_RATE, 0.3)
    monkeypatch.setattr(
        FfmpegPcmReader, "for_source", classmethod(source_factory))
    monkeypatch.setattr(
        FfmpegPcmReader, "for_music",
        classmethod(lambda _cls, _tools, _asset: music))
    window._store_music(target, MusicChoice(
        track=track, mode=AudioMode.MIX, asset=asset,
        passage=SampleSpan(4 * OUTPUT_RATE, 10 * OUTPUT_RATE, OUTPUT_RATE),
        short_track=ShortTrackPolicy.LOOP,
        music_level=Fraction(1), dvr_level=Fraction(1),
        fade_in_samples=0, fade_out_samples=0))
    window._sync_music_panel()
    window.live_preview.set_listening(Listening.MIX)
    stream = window.live_preview._stream

    for second_at, expected in (
            (0, (0.110 + 0.304) / 2),
            (3, (0.202 + 0.307) / 2),
            (5, (0.110 + 0.309) / 2),
            (6, (0.111 + 0.304) / 2)):
        block = _pull_stream_at(stream, second_at * OUTPUT_RATE)
        assert block.planned[0] == pytest.approx(expected, abs=2e-6)
        assert block.planned[0] not in (0.0, 0.110, 0.202)
    assert (4 * OUTPUT_RATE, 480) in music.reads
    assert (7 * OUTPUT_RATE, 480) in music.reads
    assert (9 * OUTPUT_RATE, 480) in music.reads
    assert music.reads.count((4 * OUTPUT_RATE, 480)) >= 2, (
        "loop six did not return to passage position four")
    stream.request_stop()
    assert stream.wait_stopped(5.0)


def test_joined_construction_failure_closes_an_already_allocated_leaf(
        window, monkeypatch):
    from flightdvr.audio_reader import FfmpegPcmReader

    first, _second, target = _make_joined_window(window)
    allocated = TaggedFrameReader(13 * OUTPUT_RATE, 0.1)

    def fail_after_a(_cls, _tools, path, *, stream_index, timeline_frames):
        if str(path) == str(first.path):
            return allocated
        raise OSError("B cannot be opened")

    monkeypatch.setattr(
        FfmpegPcmReader, "for_source", classmethod(fail_after_a))

    with pytest.raises(OSError, match="B cannot be opened"):
        window._build_monitor_stream(target, Listening.SOURCE)
    assert allocated.close_calls == 1
    assert window._monitor_snapshot is None


def test_joined_strip_seek_uses_output_one_not_paused_source_thirteen(
        window, monkeypatch):
    from flightdvr.audio_reader import FfmpegPcmReader

    first, _second, _target = _make_joined_window(window)
    monkeypatch.setattr(
        FfmpegPcmReader, "for_source",
        classmethod(lambda _cls, _tools, path, *, stream_index,
                           timeline_frames: TaggedFrameReader(
            timeline_frames, 0.1 if str(path) == str(first.path) else 0.2)))
    window.live_preview.set_listening(Listening.SOURCE)
    window.preview_view.listen_check.blockSignals(True)
    window.preview_view.listen_check.setChecked(True)
    window.preview_view.listen_check.blockSignals(False)
    window._sequence_source_seconds = 13.0
    sought = []
    pauses = []
    monkeypatch.setattr(window.live_preview, "seek", sought.append)
    monkeypatch.setattr(window.live_preview, "pause",
                        lambda: pauses.append(True))
    plan = window._sequence_plan

    window._on_sequence_scrub_requested(plan.revision, 1.0)
    window._on_sequence_scrub_requested(plan.revision, 8.0)

    assert sought == [OUTPUT_RATE]
    assert sought != [13 * OUTPUT_RATE]
    assert len(pauses) == 2
    assert window._sequence_occurrence is None
    assert window._sequence_source_seconds is None


def test_joined_restart_transitions_once_and_preserves_both_intent_states(
        window, monkeypatch):
    from flightdvr.audio_reader import FfmpegPcmReader

    first, _second, _target = _make_joined_window(window)
    monkeypatch.setattr(
        FfmpegPcmReader, "for_source",
        classmethod(lambda _cls, _tools, path, *, stream_index,
                           timeline_frames: TaggedFrameReader(
            timeline_frames, 0.1 if str(path) == str(first.path) else 0.2)))
    window.live_preview.set_listening(Listening.SOURCE)
    stream = window.live_preview._stream
    window.preview_view.listen_check.blockSignals(True)
    window.preview_view.listen_check.setChecked(True)
    window.preview_view.listen_check.blockSignals(False)
    window.player._sequence_plan = window._sequence_plan
    window.player.is_playing = True
    picture_seeks = []
    picture_plays = []
    monkeypatch.setattr(window.player, "seek", picture_seeks.append)
    monkeypatch.setattr(window.player, "play",
                        lambda *_a, **_k: picture_plays.append(True))
    window.live_preview.set_muted(False)
    window.live_preview.play()
    before = stream.generation

    window._on_monitor_restart()

    assert picture_seeks == [0.0]
    assert picture_plays == [True]
    assert stream.generation == before + 1
    assert window.live_preview.status.playing
    assert not window.live_preview.status.muted

    window.player.is_playing = False
    window.preview_view.listen_check.blockSignals(True)
    window.preview_view.listen_check.setChecked(False)
    window.preview_view.listen_check.blockSignals(False)
    window.live_preview.pause()
    window.live_preview.set_muted(True)
    paused_before = stream.generation
    window._on_monitor_restart()

    assert picture_seeks == [0.0, 0.0]
    assert picture_plays == [True]
    assert stream.generation == paused_before + 1
    assert not window.live_preview.status.playing
    assert window.live_preview.status.muted
    assert stream.paused


def test_reorder_and_hot_removal_fence_old_joined_pcm_before_rebinding(
        window, monkeypatch):
    from flightdvr.assembly import Item
    from flightdvr.audio_plan import AudioMode, MusicChoice
    from flightdvr.audio_reader import FfmpegPcmReader

    first, second, old_target = _make_joined_window(window)
    monkeypatch.setattr(
        FfmpegPcmReader, "for_source",
        classmethod(lambda _cls, _tools, path, *, stream_index,
                           timeline_frames: TaggedFrameReader(
            timeline_frames, 0.1 if str(path) == str(first.path) else 0.2)))
    retained = MusicChoice(mode=AudioMode.NO_SOUND)
    window._store_music(old_target, retained)
    window.live_preview.set_listening(Listening.SOURCE)
    old_stream = window.live_preview._stream
    window.live_preview.set_muted(False)
    window.live_preview.play()
    reordered_items = (
        Item(second.fingerprint, "b"),
        Item(first.fingerprint, "a"),
        Item(first.fingerprint, "a"),
    )

    window._store_assembly(reordered_items)

    assert old_stream.wait_stopped(5.0)
    reordered_target = window._sequence_target
    assert reordered_target.items == reordered_items
    assert window._planned_music(reordered_target) == retained
    assert window.live_preview._stream is not old_stream
    assert not window.live_preview.status.playing
    assert window.live_preview.status.muted

    rebound_stream = window.live_preview._stream
    window.live_preview.set_muted(False)
    window.live_preview.play()
    window._store_assembly([reordered_items[0]])

    assert rebound_stream.wait_stopped(5.0)
    assert window._sequence_plan is None
    assert not window.live_preview.status.offered
    assert window.live_preview.status.muted
    assert window._planned_music(reordered_target) == retained


def _absolute_source_reader(monkeypatch, frames: int = 960_000):
    from flightdvr.audio_reader import FfmpegPcmReader

    reader = AbsoluteFrameReader(frames)
    monkeypatch.setattr(
        FfmpegPcmReader, "for_source",
        classmethod(lambda _cls, _tools, _path, *, stream_index,
                           timeline_frames: (
            reader if timeline_frames <= reader.frames
            else pytest.fail(f"reader extent was {timeline_frames}"))))
    return reader


def _drive_until_presented(window, output_sample: int):
    output = window.live_preview._output
    deadline = time.monotonic() + 5.0
    while not output.presented and time.monotonic() < deadline:
        window.live_preview.tick(output_sample)
        time.sleep(0.01)
    assert output.presented, "the real AudioStream handed the adapter no PCM"
    return output.presented[0]


def test_focused_range_uses_its_absolute_source_origin_and_reader_extent(
        window, monkeypatch):
    """[12,18) is source 576000..864000, not the first six seconds."""
    from flightdvr.audio_reader import FfmpegPcmReader
    from flightdvr.audio_stream import AudioStream
    from flightdvr.media import Select

    clip = next(iter(window.clip_by_path.values()))
    clip.selects = [Select(12.0, 18.0, "flight", sid="range-12-18")]
    clip.current = 0
    reader = AbsoluteFrameReader(864_000)
    monkeypatch.setattr(
        FfmpegPcmReader, "for_source",
        classmethod(lambda _cls, _tools, _path, *, stream_index,
                           timeline_frames: (
            reader if timeline_frames == reader.frames
            else pytest.fail(f"reader extent was {timeline_frames}"))))

    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    window.live_preview.set_listening(Listening.SOURCE)

    stream = window.live_preview._stream
    assert isinstance(stream, AudioStream)
    assert stream._mapping.source.start == 576_000
    assert stream._mapping.source.end == 864_000
    assert stream._mapping.audio.output.start == 0
    assert stream._mapping.audio.output.end == 288_000

    stream.start()
    stream.resume()
    deadline = time.monotonic() + 5.0
    block = None
    while block is None and time.monotonic() < deadline:
        try:
            block = stream.pull()
        except Buffering:
            time.sleep(0.01)
    assert block is not None, "the real AudioStream produced no focused PCM"
    assert reader.reads and reader.reads[0][0] == 576_000
    assert block.output_start == 0
    assert block.planned[0] == pytest.approx(12.0)
    stream.request_stop()
    assert stream.wait_stopped(5.0)


def test_filmstrip_keyboard_and_precise_resume_use_focused_output_positions(
        window, monkeypatch):
    """Actual UI routes re-prime real PCM at 17, 13 and precise 13.5 seconds."""
    from flightdvr.media import Select

    reader = _absolute_source_reader(monkeypatch)
    clip = next(iter(window.clip_by_path.values()))
    clip.selects = [Select(12.0, 18.0, "flight", sid="range-12-18")]
    clip.current = 0
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    window.live_preview.set_listening(Listening.SOURCE)
    video_seeks = []
    window.player.seek = lambda seconds: (
        video_seeks.append(seconds), setattr(window.player, "position", seconds))
    window.player.position = 12.0
    window.player.is_playing = True
    window.preview_view.listen_check.setChecked(True)  # Listen during playback

    first = _drive_until_presented(window, 0)
    assert first.output_start == 0
    assert first.planned[0] == pytest.approx(12.0)

    previous = window.live_preview.generation
    window._on_playhead(17.0)                  # filmstrip route
    assert window.live_preview.generation == previous + 1
    block = _drive_until_presented(window, 240_000)
    assert block.output_start == 240_000
    assert block.planned[0] == pytest.approx(17.0)

    previous = window.live_preview.generation
    window._jump(13.0)                         # keyboard route
    assert window.live_preview.generation == previous + 1
    block = _drive_until_presented(window, 48_000)
    assert block.output_start == 48_000
    assert block.planned[0] == pytest.approx(13.0)

    # A native-rate precise result is paused and silent. Its reported PTS is
    # the value Play later converts, not the frame number requested earlier.
    window.player.is_playing = False
    window._preview_state_changed(False)
    window.player.position = 13.5
    window.trim_bar.set_playhead(13.5)
    window.player.is_playing = True
    window.preview_view.listen_check.setChecked(True)
    previous = window.live_preview.generation
    window._preview_state_changed(True)
    assert window.live_preview.generation == previous + 1
    block = _drive_until_presented(window, 72_000)
    assert block.output_start == 72_000
    assert block.planned[0] == pytest.approx(13.5)
    assert (624_000, 480) in reader.reads
    assert video_seeks[:2] == [17.0, 13.0]


def test_player_timer_services_focused_audio_without_moving_painted_position(
        window, monkeypatch):
    """12.02/12.04 service output 960/1920 while 12.0 stays painted."""
    import queue
    from flightdvr.media import Select
    from flightdvr.player import PlayClock

    class TimerClock:
        def __init__(self):
            self.now = 100.0

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    _absolute_source_reader(monkeypatch)
    clip = next(iter(window.clip_by_path.values()))
    clip.selects = [Select(12.0, 18.0, sid="focused")]
    clip.current = 0
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    window.live_preview.set_listening(Listening.SOURCE)
    player = window.player
    player.position = 12.0
    player.is_playing = True
    window.preview_view.listen_check.setChecked(True)

    calls = []
    window.live_preview.tick = lambda output_sample: calls.append(output_sample) or 0
    clock = TimerClock()
    player._playclock = PlayClock(origin=12.0, clock=clock)
    player._playclock.start()
    player._starved = False
    player._stream_ended = False
    player._pending = None
    player._frames = queue.Queue(maxsize=4)
    pixels = b"\0" * player.size.frame_bytes
    player._frames.put((12.0, pixels))
    player._frames.put((12.1, pixels))

    player._tick()                      # paints source 12.0 and services once
    calls.clear()
    clock.advance(0.02)
    player._tick()                      # 12.1 is still in the future
    clock.advance(0.02)
    player._tick()

    assert calls == [960, 1_920]
    assert player.position == pytest.approx(12.0)
    assert window.trim_bar.playhead == pytest.approx(12.0)

    clock.advance(0.06)
    player._tick()                      # paints 12.1, still one service call
    assert calls[-1] == 4_800
    assert len(calls) == 3, "frame-ready and timer both serviced one callback"
    assert player.position == pytest.approx(12.1)


def test_second_unticked_range_and_open_endpoint_compile_exactly(window):
    from PySide6.QtCore import Qt
    from flightdvr.media import Select

    clip = next(iter(window.clip_by_path.values()))
    clip.selects = [
        Select(2.0, 4.0, "first", sid="first"),
        Select(12.0, 0.0, "second", sid="second"),
    ]
    clip.current = 1
    window.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    window.live_preview.set_listening(Listening.SOURCE)

    snapshot = window._monitor_snapshot
    assert snapshot.target.items[0].sid == "second"
    assert snapshot.source.start == 576_000
    assert snapshot.source.end == 1_440_000
    assert snapshot.samples == 864_000
    assert snapshot.output_sample(12.0) == 0
    assert snapshot.output_sample(20.0) == 384_000
    assert snapshot.output_sample(30.0) is None, "terminal became a reprime"

    window._pick_select(0)
    assert window._monitor_snapshot.target.items[0].sid == "first"
    assert window._monitor_snapshot.source.start == 96_000
    assert window._monitor_snapshot.source.end == 192_000


def test_nonmaterial_row_is_whole_clip_but_invalid_spans_refuse(window):
    from flightdvr.media import Select

    clip = next(iter(window.clip_by_path.values()))
    clip.duration = 20.0
    clip.selects = [Select(0.0, 0.0, sid="editing-only")]
    clip.current = 0
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    snapshot = window._new_monitor_snapshot(window._music_target)
    assert snapshot.target.items[0].sid == ""
    assert snapshot.source.start == 0
    assert snapshot.source.end == 960_000

    for bad in (
            Select(12.0, 12.0, sid="empty"),
            Select(float("nan"), 18.0, sid="unknown"),
            Select(18.0, 12.0, sid="backwards")):
        clip.selects = [bad]
        clip.current = 0
        target = window._music_target_for(clip)
        with pytest.raises(ValueError):
            window._new_monitor_snapshot(target)


def test_outside_navigation_stays_video_only_until_explicit_rearm(
        window, monkeypatch):
    from flightdvr.media import Select

    _absolute_source_reader(monkeypatch)
    clip = next(iter(window.clip_by_path.values()))
    clip.selects = [Select(12.0, 18.0, sid="focused")]
    clip.current = 0
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    window.live_preview.set_listening(Listening.SOURCE)
    window.preview_view.listen_check.setChecked(True)
    window.player.seek = lambda seconds: setattr(window.player, "position", seconds)
    window.player.is_playing = True
    window.player.position = 13.0
    window._preview_state_changed(True)
    assert window.live_preview.status.playing
    _drive_until_presented(window, 48_000)

    for seconds in (18.0, 11.0, 19.0):
        if not window.live_preview.status.offered:
            window.player.position = 13.0
            window._preview_state_changed(True)  # explicit valid Play rearm
            assert window.live_preview.status.offered
        active_stream = window.live_preview._stream
        window._jump(seconds)
        assert window.player.position == seconds
        assert not window.live_preview.status.offered
        assert "outside the selected range" in window.live_preview.status.reason
        assert active_stream._cancel.is_set()
        assert window.live_preview._output.presented == [], (
            "silence left previously presented PCM downstream")


def test_sub_sample_position_rounding_to_terminal_refuses_without_raising(
        window, monkeypatch):
    """A rationally interior point can still quantize to the terminal sample."""
    from fractions import Fraction
    from flightdvr.media import Select

    _absolute_source_reader(monkeypatch)
    clip = next(iter(window.clip_by_path.values()))
    clip.selects = [Select(12.0, 18.0, sid="focused")]
    clip.current = 0
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    window.live_preview.set_listening(Listening.SOURCE)
    window.player.seek = lambda seconds: setattr(window.player, "position", seconds)
    window.player.position = 13.0
    window.preview_view.listen_check.setChecked(True)
    snapshot = window._monitor_snapshot
    active_stream = window.live_preview._stream
    near_end = 18.0 - 0.25 / OUTPUT_RATE

    assert snapshot.samples == 288_000
    assert snapshot.sequence.locate_source(
        snapshot.occurrence, Fraction(str(near_end))) is not None
    assert snapshot.output_sample(near_end) is None

    window._jump(near_end)

    assert window.player.position == pytest.approx(near_end)
    assert not window.live_preview.status.offered
    assert "outside the selected range" in window.live_preview.status.reason
    assert window.preview_view.listen_check.isChecked()
    assert active_stream._cancel.is_set()


def test_trim_change_fences_old_snapshot_before_explicit_rearm(
        window, monkeypatch):
    from flightdvr.media import Select

    _absolute_source_reader(monkeypatch)
    clip = next(iter(window.clip_by_path.values()))
    clip.selects = [Select(12.0, 18.0, sid="focused")]
    clip.current = 0
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    window.live_preview.set_listening(Listening.SOURCE)
    window.player.seek = lambda seconds: setattr(window.player, "position", seconds)
    old_snapshot = window._monitor_snapshot
    old_stream = window.live_preview._stream

    window._on_trim_changed(13.0, 17.0)

    assert window._monitor_snapshot is None
    assert not window.live_preview.status.offered
    assert "selected range changed" in window.live_preview.status.reason
    assert old_stream._cancel.is_set(), "old producer survived the trim edit"

    window.player.position = 13.5
    window.player.is_playing = True
    window.preview_view.listen_check.setChecked(True)
    window._preview_state_changed(True)
    snapshot = window._monitor_snapshot
    assert snapshot.sequence.revision != old_snapshot.sequence.revision
    assert snapshot.source.start == 624_000
    assert snapshot.source.end == 816_000
    block = _drive_until_presented(window, 24_000)
    assert block.output_start == 24_000
    assert block.planned[0] == pytest.approx(13.5)
    assert window.preview_view.listen_check.isChecked()

    window._jump(13.0)
    assert window.player.position == 13.0
    assert window.live_preview.status.offered
    assert window._monitor_snapshot is snapshot

    before_resume = window.live_preview.generation
    window._preview_state_changed(True)          # explicit Play/re-resume
    assert window.live_preview.status.playing
    assert window.live_preview.generation == before_resume + 1
    block = _drive_until_presented(window, 0)
    assert block.planned[0] == pytest.approx(13.0)

    window._jump(18.0)
    assert window.player.position == 18.0
    assert not window.live_preview.status.offered
    assert "outside the selected range" in window.live_preview.status.reason


def test_music_passage_origin_stays_independent_of_dvr_source_origin(
        window, monkeypatch):
    from fractions import Fraction
    from pathlib import Path
    from flightdvr.audio_plan import AudioAsset, AudioMode, MusicChoice, SampleSpan
    from flightdvr.audio_reader import FfmpegPcmReader
    from flightdvr.media import Select

    source = AbsoluteFrameReader(960_000)
    music = AbsoluteFrameReader(600_000)
    monkeypatch.setattr(
        FfmpegPcmReader, "for_source",
        classmethod(lambda _cls, *_a, **_k: source))
    monkeypatch.setattr(
        FfmpegPcmReader, "for_music",
        classmethod(lambda _cls, *_a, **_k: music))
    clip = next(iter(window.clip_by_path.values()))
    clip.selects = [Select(12.0, 18.0, sid="focused")]
    clip.current = 0
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    target = window._music_target
    track = Path("song.wav")
    asset = AudioAsset(track, "d" * 64, 0, OUTPUT_RATE, 2, 600_000)
    window._store_music(target, MusicChoice(
        track=track, mode=AudioMode.MIX, asset=asset,
        passage=SampleSpan(240_000, 528_000, OUTPUT_RATE),
        music_level=Fraction(1), dvr_level=Fraction(0),
        fade_in_samples=0, fade_out_samples=0))
    window._sync_live_preview()
    stream = window.live_preview._stream

    assert stream._mapping.source.start == 576_000
    assert stream._mapping.music_origin == 240_000
    window.live_preview.set_muted(False)
    window.live_preview.play()
    block = _drive_until_presented(window, 0)
    assert source.reads[0][0] == 576_000
    assert music.reads[0][0] == 240_000
    assert block.planned[0] == pytest.approx(5.0)


def test_source_only_on_a_silent_recording_has_nothing_to_offer(window,
                                                               monkeypatch):
    """`Original` on a clip with no sound of its own is silence. Offering it
    would be a control that plays nothing and says nothing."""
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    monkeypatch.setattr(type(window._trim_clip), "has_audio",
                        property(lambda _self: False))

    plan, _ = window._monitor_plan(window._music_target, Listening.SOURCE)
    assert plan is None


def test_the_window_passes_the_listening_choice_through_to_the_plan(window):
    """The whole path, not the halves: changing the control changes what the
    factory is asked to build."""
    from flightdvr.audio_plan import (
        AudioMode, AudioAsset, MusicChoice, SampleSpan,
    )

    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    target = window._music_target
    track = window._trim_clip.path.parent / "song.mp3"
    asset = AudioAsset(track, "c" * 64, 0, 44_100, 2, 44_100 * 30)
    window._store_music(target, MusicChoice(
        track=track, mode=AudioMode.REPLACE, asset=asset,
        passage=SampleSpan(0, asset.decoded_samples, asset.sample_rate)))

    asked = []
    original = window._build_monitor_stream
    window.live_preview._make_stream = lambda t, listening: asked.append(
        listening) or None
    combo = window.preview_view.listening_combo
    combo.setCurrentIndex(combo.findData("source"))

    assert Listening.SOURCE in asked, (
        "the control changed nothing the factory could see")


# -- the real AudioStream, not a stand-in --------------------------------------

class CountingReader:
    """A real `PcmReader`, so the real producer has something to read.

    Silence, because what is under test is the lifecycle rather than the
    sound: does the producer actually run, does it stop when asked, and is the
    reader closed by the worker rather than by whoever asked.
    """

    def __init__(self, frames: int = 48_000):
        self._frames = frames
        self.closed = False
        self.stopped = False
        self.reads = 0

    @property
    def frames(self) -> int:
        return self._frames

    def read(self, start: int, frames: int, cancelled) -> list[float]:
        self.reads += 1
        return [0.0] * (frames * OUTPUT_CHANNELS)

    def request_stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


def a_real_stream(frames: int = 48_000):
    """One genuine `AudioStream` over silent readers."""
    from flightdvr.audio_plan import (
        AudioMode, OutputAudioPlan, SampleSpan,
    )
    from flightdvr.audio_stream import AudioStream, LiveAudioMapping

    span = SampleSpan(0, frames, OUTPUT_RATE)
    plan = OutputAudioPlan(mode=AudioMode.ORIGINAL, output=span,
                           source_has_audio=True, dvr_gain=1)
    mapping = LiveAudioMapping(audio=plan, source=span)
    reader = CountingReader(frames)
    return AudioStream(mapping, source_reader=reader), reader


def test_the_real_producer_runs_and_then_stops_when_asked():
    """The lifecycle the stand-ins model, checked against the real thing once.

    A stand-in agrees with whatever I believed when I wrote it. This is the
    test that would have caught `resume` before `start` without anybody
    reviewing it.
    """
    from flightdvr.audio_stream import StreamState

    stream, reader = a_real_stream()
    assert stream.state is StreamState.READY

    stream.start()
    stream.resume()
    assert stream.state is StreamState.RUNNING

    stream.request_stop()
    assert stream.wait_stopped(5.0), "the producer never settled"
    assert reader.closed, "the worker did not close its reader"
    assert stream.state in (StreamState.STOPPED, StreamState.FAILED)


def test_the_real_producer_refuses_to_resume_before_it_is_started():
    """The contract the transport has to honour, stated by the producer."""
    stream, _reader = a_real_stream()
    with pytest.raises(RuntimeError):
        stream.resume()
    stream.request_stop()
    stream.wait_stopped(5.0)


def test_the_transport_drives_a_real_producer_from_play_to_close():
    """End to end on the real stream: started, fed, and let go."""
    stream, reader = a_real_stream()
    output = FakeOutput()
    live = LivePreview(stream_factory=lambda _t, _l: stream, output=output)
    live.set_target("hdz_001.ts")

    live.play()
    assert live.status.playing, live.status.reason
    # Waited for, not spun for. The producer is a real thread on a machine
    # that may be busy; twenty immediate ticks is a guess about scheduling,
    # and it is the kind of guess that passes here and fails on a loaded CI
    # runner — which is exactly what it did.
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not output.presented:
        live.tick(0)
        time.sleep(0.01)
    assert output.presented, "the real producer handed over nothing"

    live.close()
    for waiter in live._reapers:
        waiter.join(5.0)
    assert reader.closed, "the reader outlived the window"


# -- the picture and the sound are one transport --------------------------------

def test_the_pictures_play_button_starts_the_sound_when_listening(window):
    """Play belongs to the picture. Leaving them uncoupled meant pressing play
    started the picture in silence with Listen already ticked."""
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    started = []
    window.live_preview.play = lambda: started.append(True)
    window.live_preview.pause = lambda: started.append(False)
    window.preview_view.listen_check.setChecked(True)

    window._preview_state_changed(True)
    assert started and started[-1] is True

    window._preview_state_changed(False)
    assert started[-1] is False


def test_the_pictures_play_button_stays_silent_when_not_listening(window):
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    played = []
    window.live_preview.play = lambda: played.append(True)
    assert not window.preview_view.listen_check.isChecked()

    window._preview_state_changed(True)

    assert played == [], "the sound started without being asked for"


def test_restart_moves_once_and_preserves_real_stream_play_listen_intentions(
        window, monkeypatch):
    """The low-level quiet restart is deliberately restored by the UI."""
    from flightdvr.media import Select

    _absolute_source_reader(monkeypatch)
    clip = next(iter(window.clip_by_path.values()))
    clip.selects = [Select(12.0, 18.0, sid="focused")]
    clip.current = 0
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    window.live_preview.set_listening(Listening.SOURCE)
    window.preview_view.listen_check.setChecked(True)
    video_seeks = []
    window.player.seek = lambda seconds: (
        video_seeks.append(seconds), setattr(window.player, "position", seconds))
    window.player.position = 17.0
    window.player.is_playing = True
    window._preview_state_changed(True)
    stream = window.live_preview._stream
    before = stream.generation

    window.preview_view.restart_requested.emit()

    assert video_seeks == [12.0], "restart moved the picture more than once"
    assert stream.generation == before + 1, "restart reprised sound more than once"
    assert window.live_preview.status.playing
    assert not window.live_preview.status.muted
    assert not stream.paused
    block = _drive_until_presented(window, 0)
    assert block.output_start == 0
    assert block.planned[0] == pytest.approx(12.0)


def test_restart_keeps_a_paused_muted_real_stream_paused_and_muted(
        window, monkeypatch):
    from flightdvr.media import Select

    _absolute_source_reader(monkeypatch)
    clip = next(iter(window.clip_by_path.values()))
    clip.selects = [Select(12.0, 18.0, sid="focused")]
    clip.current = 0
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    window.live_preview.set_listening(Listening.SOURCE)
    stream = window.live_preview._stream
    window.player.seek = lambda seconds: setattr(window.player, "position", seconds)
    window.player.position = 17.0
    before = stream.generation

    window._on_monitor_restart()

    assert window.player.position == 12.0
    assert stream.generation == before + 1
    assert not window.live_preview.status.playing
    assert window.live_preview.status.muted
    assert stream.paused


# -- the real adapter, composed: what the device actually received -------------
#
# Everything above drives a `FakeOutput` that swallows whole blocks, so none of
# it settles what happens when the adapter takes a prefix or nothing at all.
# These compose the real `AudioOutput` with a sink that writes short, and then
# read the bytes back off the sink rather than believing either layer's own
# account of what it sent.


class QtLikeError(enum.Enum):
    """The shape Qt's `error()` answers with, healthy value included."""

    NoError = 0
    UnderrunError = 3


class ShortSink:
    """A device that accepts less than it is offered, on purpose.

    `accepts` is consumed one write at a time, so a test spells out the exact
    sequence of short writes it wants -- including zero, and including a
    deliberately mid-frame count, which a byte stream is allowed to do.
    """

    def __init__(self, accepts=(), buffer_size: int = 1 << 20):
        self.received = bytearray()
        self.accepts = list(accepts)
        self.buffer_size = buffer_size
        self.reported_state = "StoppedState"
        self.reported_error = QtLikeError.NoError
        self.processed_usecs = 0
        self.device = self

    def write(self, payload: bytes) -> int:
        allowed = self.accepts.pop(0) if self.accepts else len(payload)
        taken = max(0, min(int(allowed), len(payload)))
        self.received += payload[:taken]
        return taken

    def state(self):
        return SimpleNamespace(name=self.reported_state)

    def error(self):
        return self.reported_error

    def bytesFree(self) -> int:                   # noqa: N802 (Qt naming)
        if self.reported_state not in ("ActiveState", "IdleState"):
            return 0
        return self.buffer_size

    def bufferSize(self) -> int:                  # noqa: N802 (Qt naming)
        return self.buffer_size

    def processedUSecs(self) -> int:              # noqa: N802 (Qt naming)
        return self.processed_usecs

    def start(self):
        self.reported_state = "ActiveState"
        return self.device

    def suspend(self) -> None:
        self.reported_state = "SuspendedState"

    def resume(self) -> None:
        self.reported_state = "ActiveState"

    def reset(self) -> None:
        self.reported_state = "StoppedState"

    def stop(self) -> None:
        self.reported_state = "StoppedState"

    def setVolume(self, volume: float) -> None:   # noqa: N802 (Qt naming)
        pass


def numbered_block(index: int, generation: int = 0, frames: int = 4,
                   start: int = 0) -> PcmBlock:
    """One block whose samples say which block it is.

    Distinct on purpose: the oracle concatenates what the sink received and
    compares it to the expected PCM byte for byte, which a run of identical
    blocks could pass while dropping or reordering half of them.
    """
    samples = frames * OUTPUT_CHANNELS
    value = (index + 1) / 100.0
    return PcmBlock(generation=generation, monitor_revision=0,
                    output_start=start, frames=frames,
                    planned=[value] * samples, monitored=[value] * samples)


class NumberedStream(FakeStream):
    """Hands out distinct blocks and remembers every one it gave away.

    A pulled block is gone from the producer -- that is the whole hazard here,
    so what was handed over is recorded at the moment of handing over, and the
    expected sound is built from that record rather than from anything the
    transport says afterwards.
    """

    def __init__(self, *, blocks: int = 4, frames: int = 4, **kwargs):
        super().__init__(blocks=blocks, **kwargs)
        self._frames = frames
        self._index = 0
        self.handed_out = []

    def pull(self):
        if self.raises is not None:
            raise self.raises
        if self.remaining <= 0:
            return None
        self.remaining -= 1
        block = numbered_block(self._index, self.generation,
                               frames=self._frames,
                               start=self.output_start)
        self._index += 1
        self.output_start += self._frames
        self.handed_out.append(block)
        return block


def expected_bytes(blocks) -> bytes:
    """The monitored rendering of those blocks, in order, as the device wants
    it. Built independently of the transport, from the blocks themselves."""
    return b"".join(array.array("f", block.monitored).tobytes()
                    for block in blocks)


def composed(*, accepts=(), max_queued_bytes: int = 1 << 20, blocks: int = 4,
             frames: int = 4):
    """A transport over the real adapter over a sink that writes short."""
    sink = ShortSink(accepts=accepts)
    output = AudioOutput(sink_factory=lambda: sink,
                         max_queued_bytes=max_queued_bytes)
    stream = NumberedStream(blocks=blocks, frames=frames)
    live = LivePreview(stream_factory=lambda *a, **k: stream, output=output)
    live.set_target(object())
    live.play()
    return live, stream, output, sink


class TimerClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def timer_driven_transport(window, monkeypatch, *, blocks: int = 1):
    """Focused UI mapping over a real adapter, driven only by player ticks."""
    import queue
    from flightdvr.live_preview import LivePreview
    from flightdvr.media import Select
    from flightdvr.player import PlayClock

    _absolute_source_reader(monkeypatch)
    clip = next(iter(window.clip_by_path.values()))
    clip.selects = [Select(12.0, 18.0, sid="focused")]
    clip.current = 0
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()
    window.live_preview.set_listening(Listening.SOURCE)
    assert window._monitor_snapshot is not None
    window.live_preview.close()

    sink = ShortSink(accepts=(0, 0))
    output = AudioOutput(sink_factory=lambda: sink)
    stream = NumberedStream(blocks=blocks)
    live = LivePreview(stream_factory=lambda *a, **k: stream, output=output)
    live.set_target(object())
    live.set_muted(False)
    live.play()
    window.live_preview = live

    player = window.player
    player.clip = clip
    player.position = 12.0
    player.is_playing = True
    clock = TimerClock()
    player._playclock = PlayClock(origin=12.0, clock=clock)
    player._playclock.start()
    player._starved = False
    player._stream_ended = False
    player._pending = None
    player._frames = queue.Queue(maxsize=4)
    return player, clock, live, stream, output, sink


@pytest.mark.parametrize("producer_state", ("buffering", "ended"))
def test_player_timer_pumps_real_pending_bytes_without_a_new_frame(
        window, monkeypatch, producer_state):
    """Buffering/EOF cannot strand a suffix when no picture is repainted."""
    player, clock, live, stream, output, sink = timer_driven_transport(
        window, monkeypatch)
    shown = []
    player.frame_ready.connect(lambda *_: shown.append(True))

    player._tick()
    pending = output.queued_bytes
    assert pending > 0, "the first player callback left no adapter suffix"
    assert sink.received == b""
    assert stream.handed_out, "the producer handed out no PCM"
    if producer_state == "buffering":
        stream.raises = Buffering()
    else:
        assert stream.remaining == 0

    clock.advance(0.02)
    player._tick()

    assert output.queued_bytes < pending
    assert sink.received, "the later player callback made no nonempty write"
    assert bytes(sink.received) == expected_bytes(stream.handed_out)
    assert shown == [], "the oracle accidentally depended on a painted frame"
    assert player.is_playing and live.status.playing
    assert stream.generation == output.generation
    live.close()


@pytest.mark.parametrize("fence", ("pause", "target", "failure", "close"))
def test_obsolete_player_callbacks_cannot_revive_a_fenced_suffix(
        window, monkeypatch, fence):
    player, _clock, live, stream, output, sink = timer_driven_transport(
        window, monkeypatch)
    player._tick()
    assert output.queued_bytes > 0, "the fixture left no suffix to fence"
    handed = len(stream.handed_out)

    if fence == "pause":
        player.pause()
    elif fence == "target":
        live.set_target(object(), reason="the target changed")
    elif fence == "failure":
        player._worker_failed(player._generation, "decoder failed")
    else:
        window.close()

    assert output.queued_bytes == 0
    before = bytes(sink.received)
    player._tick()                       # a timeout already queued before fence

    assert bytes(sink.received) == before
    assert len(stream.handed_out) == handed
    assert not live.status.playing
    assert not output.running


def test_player_seek_fences_old_suffix_before_timer_services_new_generation(
        window, monkeypatch):
    player, clock, live, stream, output, sink = timer_driven_transport(
        window, monkeypatch)
    player._tick()
    assert output.queued_bytes > 0
    stale = list(stream.handed_out)
    stream.handed_out.clear()

    window._jump(13.0)

    assert output.queued_bytes == 0
    assert stream.generation == output.generation
    clock.advance(0.02)
    player._tick()
    fresh = bytes(sink.received)
    assert stream.handed_out, "the post-seek timer produced no new PCM"
    assert fresh == expected_bytes(stream.handed_out)
    for old in stale:
        assert expected_bytes([old]) not in fresh
    live.close()


def test_actual_video_eof_fences_audio_before_any_final_timer_service(
        window, monkeypatch):
    player, _clock, live, stream, output, sink = timer_driven_transport(
        window, monkeypatch)
    timing = []
    player.playback_tick.connect(lambda *_: timing.append(True))
    player._tick()
    assert output.queued_bytes > 0
    before = len(timing)

    player._stream_ended = True
    player._tick()

    assert not player.is_playing
    assert not live.status.playing
    assert output.queued_bytes == 0
    assert bytes(sink.received) == b""
    assert len(timing) == before, "video EOF published service after its fence"
    player._tick()                       # obsolete timeout stays inert
    assert len(timing) == before
    assert not stream.stopped, (
        "source EOF was mistaken for a decoder failure before video EOF")


def test_a_block_the_adapter_only_partly_took_is_not_thrown_away():
    """`pull` removes the block from the producer, and `present` may take a
    frame-aligned prefix. Pulling the next block after a partial acceptance
    drops the remainder of this one, and nothing upstream still has it."""
    # The device must refuse first, or the adapter drains to empty between
    # blocks and `present` never has to take a prefix at all. With one block
    # and one frame of room, the second block is accepted in part.
    live, stream, output, sink = composed(
        accepts=(0,), max_queued_bytes=5 * FRAME_BYTES, blocks=3, frames=4)

    for _ in range(12):
        live.tick(0)

    assert stream.handed_out, "the stream never gave anything away"
    assert bytes(sink.received) == expected_bytes(stream.handed_out), (
        f"{len(sink.received)} bytes arrived, "
        f"{len(expected_bytes(stream.handed_out))} were handed over")
    live.close()


def test_real_adapter_uses_a_nonzero_reset_baseline_once_and_keeps_bytes():
    """The transport owns coordinates; the adapter still owns exact bytes."""
    sink = ShortSink()
    sink.processed_usecs = 50_000             # 2,400 frames at 48 kHz
    output = AudioOutput(sink_factory=lambda: sink)
    stream = NumberedStream(blocks=0)
    live = LivePreview(stream_factory=lambda *a, **k: stream, output=output)
    live.set_target(object())
    live.play()

    live.seek(48_000)
    live.tick(48_000)
    assert live.status.playing, live.status.reason

    sink.processed_usecs = 150_000            # 7,200; delta is 4,800
    live.tick(52_800)

    assert live.status.playing, live.status.reason
    assert stream.handed_out, "the fixture submitted no epoch material"
    assert bytes(sink.received) == expected_bytes(stream.handed_out)
    live.close()


def test_known_seek_origin_waits_for_acceptance_then_enforces_drift():
    """A requested coordinate is known, but is not submitted material yet."""
    sink = ShortSink()
    output = AudioOutput(sink_factory=lambda: sink)
    stream = NumberedStream(blocks=0)
    live = LivePreview(stream_factory=lambda *a, **k: stream, output=output)
    live.set_target(object())
    live.play()
    live.seek(48_000)
    stream.raises = Buffering()

    live.tick(57_601)

    assert output.submitted_bytes == 0
    assert bytes(sink.received) == b""
    assert live.status.playing, live.status.reason

    stream.raises = None
    live.tick(48_000)
    assert output.submitted_bytes > 0, "the epoch never accepted PCM"
    assert live.status.playing, live.status.reason

    live.tick(57_601)
    assert not live.status.playing, "acceptance never armed drift checking"
    assert "drifted too far" in live.status.reason


def test_known_restart_origin_stays_pending_while_the_stream_buffers():
    sink = ShortSink()
    output = AudioOutput(sink_factory=lambda: sink)
    stream = NumberedStream(blocks=0)
    live = LivePreview(stream_factory=lambda *a, **k: stream, output=output)
    live.set_target(object())
    live.play()
    stream.raises = Buffering()

    live.restart()
    live.tick(9_601)

    assert output.submitted_bytes == 0
    assert bytes(sink.received) == b""
    assert live.status.playing, live.status.reason
    live.close()


def test_a_block_the_adapter_refused_outright_is_not_thrown_away():
    """Zero acceptance is the bound being reached, not the block being
    unwanted. It has already left the producer."""
    # Room for exactly one block, and a device that takes nothing on the
    # first write, so the second block meets a full queue.
    live, stream, output, sink = composed(
        accepts=(0,), max_queued_bytes=4 * FRAME_BYTES, blocks=4, frames=4)

    for _ in range(16):
        live.tick(0)

    assert bytes(sink.received) == expected_bytes(stream.handed_out)
    live.close()


def test_a_short_mid_frame_device_write_loses_nothing():
    """A QIODevice is a byte stream and may stop anywhere. The adapter already
    retains the exact remainder; composing it must not undo that."""
    live, stream, output, sink = composed(
        accepts=(7, 0, 13, 1, 0, 99), blocks=3)

    for _ in range(20):
        live.tick(0)

    assert bytes(sink.received) == expected_bytes(stream.handed_out)
    live.close()


def test_a_stalled_tail_is_serviced_without_a_new_block():
    """The producer has ended, but the adapter is still holding sound.

    Pumping only after a fresh acceptance leaves that tail with no service
    path at all: the last of the music never reaches the device.
    """
    # Two refusals: one for the offer inside the loop, one for the service
    # pass that closes the tick. With only one, the tick ends having already
    # cleared its own tail and there is nothing left for a later tick to do.
    live, stream, output, sink = composed(accepts=(0, 0), blocks=1)

    live.tick(0)                      # the device refuses everything offered
    assert stream.remaining == 0, "the fixture did not exhaust the producer"
    held = len(expected_bytes(stream.handed_out)) - len(sink.received)
    assert held > 0, "nothing was left held, so there is no tail to service"

    for _ in range(6):                # ordinary later ticks, no new block
        live.tick(0)

    assert bytes(sink.received) == expected_bytes(stream.handed_out), (
        "the tail never reached the device")
    live.close()


def test_a_buffering_producer_still_lets_the_device_drink():
    """`Buffering` says nothing new is ready. It does not say the bytes
    already queued should sit there."""
    live, stream, output, sink = composed(accepts=(0,), blocks=1)

    live.tick(0)
    stream.raises = Buffering()

    for _ in range(6):
        live.tick(0)

    assert bytes(sink.received) == expected_bytes(stream.handed_out)
    live.close()


def test_repeated_full_backpressure_neither_drops_nor_duplicates():
    """The device takes nothing for a while and then opens up. What arrives
    must be exactly what was handed over, once each and in order."""
    live, stream, output, sink = composed(
        accepts=(0, 0, 0, 0, 0), max_queued_bytes=6 * FRAME_BYTES, blocks=4)

    for _ in range(24):
        live.tick(0)

    arrived = bytes(sink.received)
    expected = expected_bytes(stream.handed_out)
    assert arrived == expected
    assert len(arrived) == len(expected), "a byte was duplicated or dropped"
    live.close()


def test_a_new_generation_does_not_leak_the_retained_old_sound():
    """A seek makes everything held stale. Retaining a suffix must not smuggle
    the old generation's sound past the fence."""
    live, stream, output, sink = composed(
        accepts=(0,), max_queued_bytes=4 * FRAME_BYTES, blocks=4)

    live.tick(0)
    before = len(sink.received)
    live.seek(9_000)
    stale = list(stream.handed_out)
    stream.handed_out.clear()

    for _ in range(12):
        live.tick(0)

    fresh = bytes(sink.received)[before:]
    # Both halves, and the first one matters most: holding a stale tail makes
    # `present` refuse it for ever, and a held block is re-offered before
    # anything newer, so the transport pulls nothing at all. Comparing two
    # empty byte strings would call that a pass.
    assert stream.handed_out, "the seek produced no new sound to check"
    assert fresh, "nothing reached the device after the seek"
    assert fresh == expected_bytes(stream.handed_out), (
        "the new generation did not arrive intact")
    for old in stale:
        assert expected_bytes([old]) not in fresh, (
            "sound from before the seek was played after it")
    live.close()


def test_pausing_does_not_keep_a_tail_the_fence_just_dropped():
    """`AudioOutput.pause` fences the queue and the sink's own buffer. Keeping
    a tail here would put back sound the pause decided nobody should hear."""
    live, stream, output, sink = composed(
        accepts=(0, 0), max_queued_bytes=5 * FRAME_BYTES, blocks=3)
    live.tick(0)
    assert live._held is not None, "the fixture left no tail to drop"

    live.pause()

    assert live._held is None
    live.close()


def test_restarting_does_not_keep_a_tail_from_before_the_restart():
    live, stream, output, sink = composed(
        accepts=(0, 0), max_queued_bytes=5 * FRAME_BYTES, blocks=3)
    live.tick(0)
    assert live._held is not None, "the fixture left no tail to drop"

    live.restart()

    assert live._held is None
    live.close()


def test_a_held_tail_never_becomes_a_second_queue():
    """At most one block is retained, however long the device refuses."""
    live, stream, output, sink = composed(
        accepts=(0,) * 40, max_queued_bytes=5 * FRAME_BYTES, blocks=8, frames=4)

    for _ in range(20):
        live.tick(0)
        if live._held is not None:
            assert live._held.frames <= 4, (
                "the retained tail grew beyond one block")

    assert stream.remaining > 0, (
        "the fixture never reached the bound, so nothing was held back")
    live.close()


def test_leaving_the_output_drops_the_tail_of_the_one_being_left():
    """`set_target` silences first so a single-target slice cannot emit sound
    belonging to the output just left. A retained tail is that sound."""
    live, stream, output, sink = composed(
        accepts=(0, 0), max_queued_bytes=5 * FRAME_BYTES, blocks=3)
    live.tick(0)
    assert live._held is not None, "the fixture left no tail to drop"

    live.set_target(None)

    assert live._held is None
    live.close()


def test_closing_after_a_partial_acceptance_leaves_nothing_running():
    live, stream, output, sink = composed(
        accepts=(0, 0), max_queued_bytes=5 * FRAME_BYTES, blocks=3)
    live.tick(0)
    assert live._held is not None, "the fixture left no tail to drop"

    live.close()

    assert live._held is None
    assert stream.stopped
    assert not live.status.playing


# -- W3: gains and fades reach the stream in place ------------------------------

def test_a_parameter_update_rebuilds_nothing_and_resets_nothing():
    """No factory call, no silence, no reprime, no device reset: the stream
    takes it or refuses it."""
    built = []
    stream = FakeStream()
    stream.updates = []
    stream.update_parameters = lambda plan: (stream.updates.append(plan), True)[1]
    output = FakeOutput()
    live = LivePreview(stream_factory=lambda t, l: (built.append(t), stream)[1],
                       output=output)
    live.set_target("hdz_001.ts")
    live.set_muted(False)
    live.play()
    before = (list(stream.calls), list(output.calls), len(built))

    assert live.update_parameters("hdz_001.ts", "the new plan")

    assert stream.updates == ["the new plan"]
    assert (stream.calls, output.calls, len(built)) == before
    assert live.status.playing and not live.status.muted


def test_a_parameter_update_for_another_output_is_refused():
    live, stream, _output = transport()
    stream.update_parameters = lambda plan: True
    assert not live.update_parameters("hdz_002.ts", "a plan")


def test_with_nothing_to_hear_an_update_starts_nothing():
    built = []
    live = LivePreview(stream_factory=lambda t, l: (built.append(t), None)[1],
                       output=FakeOutput())
    live.set_target("hdz_001.ts")
    assert not live.update_parameters("hdz_001.ts", "a plan")
    assert built == ["hdz_001.ts"], "an update built a stream"


def test_a_refused_target_takes_no_update():
    live = LivePreview(stream_factory=lambda t, l: FakeStream(),
                       output=FakeOutput())
    live.set_target("hdz_001.ts", reason="music is still being read")
    assert not live.update_parameters("hdz_001.ts", "a plan")
