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

import pytest

from flightdvr.audio_device import (
    ACTIVE, FRAME_BYTES, IDLE, UNKNOWN, AudioOutput, DeviceReport,
)
from flightdvr.audio_plan import OUTPUT_CHANNELS, OUTPUT_RATE
from flightdvr.audio_stream import Buffering, PcmBlock
from flightdvr.live_preview import (
    DRIFT_LIMIT_SAMPLES, STARVED_LIMIT, Listening, LivePreview,
)


def a_block(generation: int = 0, frames: int = 4) -> PcmBlock:
    samples = frames * OUTPUT_CHANNELS
    return PcmBlock(generation=generation, monitor_revision=0, output_start=0,
                    frames=frames, planned=[0.8] * samples,
                    monitored=[0.2] * samples)


class FakeStream:
    """A stream that can refuse, end, and be superseded."""

    def __init__(self, *, blocks: int = 4, generation: int = 0):
        self.generation = generation
        self.remaining = blocks
        self.calls = []
        self.monitor = None
        self.raises = None

    def pull(self):
        if self.raises is not None:
            raise self.raises
        if self.remaining <= 0:
            return None
        self.remaining -= 1
        return a_block(self.generation)

    def resume(self) -> None:
        self.calls.append("resume")

    def pause(self) -> None:
        self.calls.append("pause")

    def request_stop(self) -> None:
        self.calls.append("request_stop")

    def restart(self) -> int:
        self.calls.append("restart")
        self.generation += 1
        self.remaining = 4
        return self.generation

    def reprime(self, sample: int) -> int:
        self.calls.append(f"reprime:{sample}")
        self.generation += 1
        self.remaining = 4
        return self.generation

    def set_monitor(self, *, level=None, muted=None):
        self.monitor = (level, muted)
        return self.monitor


class FakeOutput:
    """An `AudioOutput` that holds what it is given and reports on itself."""

    def __init__(self):
        self.presented = []
        self.calls = []
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

    def resume(self) -> None:
        self.calls.append("resume")

    def pause(self) -> None:
        self.calls.append("pause")

    def stop(self) -> None:
        self.calls.append("stop")

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
    live = LivePreview(stream_factory=lambda _t: stream, output=output)
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
    live = LivePreview(stream_factory=lambda _t: FakeStream(),
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
    live = LivePreview(stream_factory=lambda _t: None, output=FakeOutput())
    live.set_target("no music here")
    assert not live.status.offered
    assert not live.status.available
    assert live.status.reason == ""


def test_a_stream_that_cannot_be_built_is_reported():
    def explode(_target):
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
    live = LivePreview(stream_factory=lambda _t: next(streams), output=output)

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
