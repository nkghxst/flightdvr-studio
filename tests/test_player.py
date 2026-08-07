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

"""The parts of the preview player that decide something.

None of this runs ffmpeg or opens a window. The framing is tested against
BytesIO and the clock against a fake time source, which is the whole reason
they are separate from the thread that uses them.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Qt, Signal

from flightdvr.media import ClipInfo, Tools
from flightdvr.player import (
    FRAME_CACHE_MAX_FRAMES, FRAME_CACHE_SIZE, PREVIEW_FPS, PREVIEW_SIZES,
    QUEUE_FRAMES, SEEK_LEAD_IN, CachedFrame, FrameCache, FrameWindow,
    PlayClock, PreviewSize, build_command, build_frame_window_command,
    choose_size, exact_timestamp, pair_precise_frames, plan_frame_window,
    read_frames, seconds_for_index, seek_pair, should_restart,
)

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Tools(Path("ffmpeg"), Path("ffprobe"))


def clip(**overrides) -> ClipInfo:
    fields = dict(
        path=Path("hdz_022.ts"), size=10 ** 8, modified=datetime(2026, 7, 4),
        duration=212.7, width=1280, height=720, fps=60.0,
        video_codec="hevc", audio_codec="aac", color_range="pc",
    )
    fields.update(overrides)
    return ClipInfo(**fields)


def flatten(command) -> str:
    return " ".join(command)


# -- the constraint that shaped the whole design -------------------------------

def test_nothing_reaches_for_qt_multimedia():
    """Its Windows backend is Media Foundation, which cannot decode HEVC in an
    MPEG-TS — the only format this app exists for. It would pass every test on
    synthetic footage and fail on every real recording.

    Checks import lines rather than the whole file, because explaining why we
    avoid something necessarily means naming it.
    """
    for path in (ROOT / "flightdvr").glob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            statement = line.strip()
            if statement.startswith(("import ", "from ")):
                assert "QtMultimedia" not in statement, f"{path.name}:{number}"


# -- choosing a frame size -----------------------------------------------------

def test_the_preview_fits_the_space_it_is_given():
    assert choose_size(1000).width == 960
    assert choose_size(700).width == 640
    assert choose_size(500).width == 480


def test_a_tiny_or_unknown_view_still_gets_a_size():
    """Width can be zero before the window has laid itself out."""
    assert choose_size(0) == PreviewSize(*PREVIEW_SIZES[0])
    assert choose_size(-50) == PreviewSize(*PREVIEW_SIZES[0])


@pytest.mark.parametrize("width,height", PREVIEW_SIZES)
def test_every_frame_size_has_an_aligned_row(width, height):
    """QImage assumes four-byte scanline alignment. A width whose row of bytes
    is not divisible by four reads past the end of the buffer."""
    assert (width * 3) % 4 == 0
    assert height % 2 == 0


def test_frame_bytes_is_the_whole_picture():
    size = PreviewSize(640, 360)
    assert size.frame_bytes == 640 * 360 * 3
    assert size.stride == 640 * 3


# -- the precise-frame cache window ------------------------------------------

def test_the_frame_window_holds_sixty_steps_either_side_of_the_playhead():
    window = plan_frame_window(position=10.0, duration=60.0, fps=60.0)
    assert window.first_frame == 540
    assert window.last_frame == 660
    assert window.frame_count == 121


def test_precise_frames_use_the_smallest_playback_resolution():
    """Looking more closely must not make the paused picture softer."""
    assert FRAME_CACHE_SIZE == PREVIEW_SIZES[0] == (480, 270)


def test_the_frame_window_never_grows_to_the_whole_recording():
    """A 90 fps flight can be several minutes long; precise stepping must not
    turn selecting one cut into tens of thousands of cached frames."""
    window = plan_frame_window(position=120.0, duration=300.0, fps=90.0)
    assert window.frame_count == FRAME_CACHE_MAX_FRAMES
    assert window.frame_count < 300.0 * 90.0


def test_seeking_away_replaces_the_window_instead_of_extending_it():
    first = plan_frame_window(position=10.0, duration=300.0, fps=90.0)
    second = plan_frame_window(position=200.0, duration=300.0, fps=90.0)
    assert first.frame_count == second.frame_count == FRAME_CACHE_MAX_FRAMES
    assert first.last_frame < second.first_frame


def test_a_frame_number_has_one_exact_timestamp():
    window = plan_frame_window(position=10.0, duration=60.0, fps=60.0)
    current = round(10.0 * window.fps)
    assert window.seconds_for(current + 1) - window.seconds_for(current) == (
        pytest.approx(1 / 60.0)
    )


def test_the_frame_window_stays_inside_short_clips():
    window = plan_frame_window(position=99.0, duration=1.0, fps=60.0)
    assert window.first_frame == 0
    assert window.last_frame == 59
    assert window.frame_count == 60


def test_refilling_the_cache_discards_the_old_window():
    cache = FrameCache(limit=3)
    cache.replace(CachedFrame(n, n / 60, bytes([n])) for n in range(3))
    cache.replace(CachedFrame(n, n / 60, bytes([n])) for n in range(100, 103))
    assert len(cache) == 3
    assert cache.get(1) is None
    assert cache.get(101).pixels == bytes([101])


def test_even_bad_metadata_cannot_overfill_the_cache():
    cache = FrameCache(limit=3)
    cache.replace(CachedFrame(n, n / 60, b"") for n in range(20))
    assert len(cache) == 3


def test_exact_timestamps_keep_adjacent_frames_distinguishable():
    assert exact_timestamp(10.0) == "00:00:10.000"
    assert exact_timestamp(10.0 + 1 / 60) == "00:00:10.017"
    assert exact_timestamp(3661.234) == "01:01:01.234"


# -- seeking -------------------------------------------------------------------

def test_a_seek_is_split_so_the_decoder_can_catch_up():
    """One seek alone lands on an estimated byte offset in an MPEG-TS and the
    frames after it are torn until the next keyframe."""
    fast, accurate = seek_pair(10.5)
    assert fast == pytest.approx(8.5)
    assert accurate == pytest.approx(10.5)


def test_a_seek_never_lands_before_the_start_of_the_clip():
    fast, accurate = seek_pair(0.5)
    assert fast == 0.0
    assert accurate == pytest.approx(0.5)


def test_playing_from_the_beginning_seeks_at_all():
    assert seek_pair(0.0) == (0.0, 0.0)


@pytest.mark.parametrize("start", [0.0, 0.2, 1.0, 2.0, 2.5, 44.0, 180.0])
def test_the_two_seeks_always_reach_the_point_asked_for(start):
    """Both are measured from the start of the file, so the second one is the
    target itself rather than the distance from the first."""
    fast, accurate = seek_pair(start)
    assert accurate == pytest.approx(start)
    assert fast >= 0.0
    assert start - fast <= SEEK_LEAD_IN + 0.001


# -- the command ---------------------------------------------------------------

def test_the_stream_is_raw_frames_of_a_size_we_chose():
    size = PreviewSize(640, 360)
    command = build_command(TOOLS, clip(), 0.0, size)
    assert "-f rawvideo" in flatten(command)
    assert "-pix_fmt rgb24" in flatten(command)
    assert command[-1] == "pipe:1"


def test_the_frame_size_is_forced_rather_than_derived():
    """width and height can both be zero on a clip that would not probe, and
    scale=W:-2 gives a height nobody knows ahead of time — which breaks
    fixed-size reads outright."""
    size = PreviewSize(640, 360)
    command = flatten(build_command(TOOLS, clip(width=0, height=0), 0.0, size))
    assert "scale=640:360:force_original_aspect_ratio=decrease" in command
    assert "pad=640:360" in command


def test_an_oddly_shaped_source_is_letterboxed_not_stretched():
    size = PreviewSize(640, 360)
    command = flatten(build_command(TOOLS, clip(width=127, height=95), 0.0, size))
    assert "force_original_aspect_ratio=decrease" in command
    assert "pad=640:360:(ow-iw)/2:(oh-ih)/2" in command


def test_the_seek_goes_either_side_of_the_input():
    command = build_command(TOOLS, clip(), 10.5, PreviewSize(640, 360))
    i = command.index("-i")
    before = [command[n + 1] for n, a in enumerate(command[:i]) if a == "-ss"]
    after = [command[n + 1] for n, a in enumerate(command[i:], i) if a == "-ss"]
    assert before == ["8.500"]
    assert after == ["10.500"]


def test_the_second_seek_is_measured_from_the_start_of_the_file():
    """Measured on a file whose audio begins before its video: without
    -copyts -start_at_zero, the seek after the input counts from the keyframe
    the seek before it landed on rather than from where it was aimed, and
    asking for 2.5 s gives you the frame at 2.0 s with no complaint."""
    command = build_command(TOOLS, clip(), 10.5, PreviewSize(640, 360))
    i = command.index("-i")
    assert "-copyts" in command[:i]
    assert "-start_at_zero" in command[:i]


def test_no_range_conversion_is_attempted_on_the_way_to_rgb():
    """Measured: in a chain ending in rgb24 these are inert, because the
    conversion out of YUV already honours the source's range tag. Applying
    full-to-limited, applying its opposite and applying neither all produced
    byte-identical frames."""
    for source in (clip(color_range="pc"), clip(color_range="tv", pix_fmt="")):
        command = flatten(build_command(TOOLS, source, 0.0,
                                        PreviewSize(640, 360)))
        assert "in_range" not in command


def test_the_preview_carries_no_audio():
    assert "-an" in build_command(TOOLS, clip(), 0.0, PreviewSize(640, 360))


def test_the_frame_rate_option_is_asked_for_not_assumed():
    """ffmpeg 5.1 renamed -vsync, and Ubuntu 22.04 still ships 4.4."""
    command = flatten(build_command(TOOLS, clip(), 0.0, PreviewSize(640, 360)))
    assert "-fps_mode cfr" in command or "-vsync cfr" in command


def test_a_clip_with_no_frame_rate_still_builds_a_command():
    command = flatten(build_command(TOOLS, clip(fps=0.0), 0.0,
                                    PreviewSize(640, 360)))
    assert f"fps={PREVIEW_FPS}" in command


def test_the_probe_settings_are_left_at_their_defaults():
    """Forcing large ones costs eight times as much reading from a card."""
    command = flatten(build_command(TOOLS, clip(), 0.0, PreviewSize(640, 360)))
    assert "-analyzeduration" not in command
    assert "-probesize" not in command


def test_precise_frames_are_not_thinned_to_the_playback_rate():
    window = plan_frame_window(10.0, 60.0, 60.0)
    command = build_frame_window_command(
        TOOLS, clip(), window, PreviewSize(*FRAME_CACHE_SIZE))
    filters = command[command.index("-vf") + 1]
    assert "fps=" not in filters
    assert "showinfo" in filters
    assert "passthrough" in command


def test_ffmpeg_is_told_the_same_bound_as_the_cache():
    window = plan_frame_window(120.0, 300.0, 90.0)
    command = build_frame_window_command(
        TOOLS, clip(fps=90.0), window, PreviewSize(*FRAME_CACHE_SIZE))
    frame_limit = command.index("-frames:v")
    assert command[frame_limit + 1] == str(FRAME_CACHE_MAX_FRAMES)


def test_precise_window_seeking_keeps_the_source_timeline():
    window = plan_frame_window(10.0, 60.0, 60.0)
    command = build_frame_window_command(
        TOOLS, clip(), window, PreviewSize(*FRAME_CACHE_SIZE))
    input_at = command.index("-i")
    assert "-copyts" in command[:input_at]
    assert "-start_at_zero" in command[:input_at]
    assert "select='gte(t,8.991666667)'" in command[command.index("-vf") + 1]
    assert "-ss" not in command[input_at + 1:]


def test_precise_pairing_returns_the_planned_one_to_one_run():
    window = FrameWindow(first_frame=100, frame_count=3, fps=10.0)
    frames = pair_precise_frames(
        window,
        timestamps=[10.0, 10.1, 10.2],
        pixels=[b"a", b"b", b"c"],
    )
    assert [frame.frame_number for frame in frames] == [100, 101, 102]
    assert [frame.pixels for frame in frames] == [b"a", b"b", b"c"]


def test_precise_pairing_rejects_a_shifted_timeline():
    """A shifted PTS run must not silently renumber every picture by one
    plausible-looking source frame."""
    window = FrameWindow(first_frame=100, frame_count=3, fps=10.0)
    with pytest.raises(ValueError, match=r"101\.\.103.*100\.\.102"):
        pair_precise_frames(
            window,
            timestamps=[10.1, 10.2, 10.3],
            pixels=[b"a", b"b", b"c"],
        )


def test_precise_pairing_rejects_extra_showinfo_timestamps():
    """Moving showinfo ahead of the selection filter must fail loudly."""
    window = FrameWindow(first_frame=100, frame_count=3, fps=10.0)
    with pytest.raises(ValueError, match="3 frames but 5 timestamps"):
        pair_precise_frames(
            window,
            timestamps=[8.0, 9.0, 10.0, 10.1, 10.2],
            pixels=[b"a", b"b", b"c"],
        )


def test_precise_pairing_rejects_a_noncontiguous_timeline():
    window = FrameWindow(first_frame=100, frame_count=3, fps=10.0)
    with pytest.raises(ValueError, match="untrustworthy"):
        pair_precise_frames(
            window,
            timestamps=[10.0, 10.2, 10.3],
            pixels=[b"a", b"b", b"c"],
        )


# -- reading frames off a pipe -------------------------------------------------

def test_whole_frames_come_out_in_order():
    size = 12
    payload = bytes(range(size)) + bytes(range(size, size * 2))
    frames = list(read_frames(io.BytesIO(payload), size))
    assert len(frames) == 2
    assert frames[0] == bytes(range(size))


def test_a_truncated_final_frame_is_dropped():
    """A partial frame is not a picture. This is what a pulled card looks like
    from in here, and it must not reach the screen as garbage."""
    size = 12
    payload = bytes(size * 2) + b"\x01\x02\x03"
    assert len(list(read_frames(io.BytesIO(payload), size))) == 2


def test_an_empty_stream_yields_nothing():
    assert list(read_frames(io.BytesIO(b""), 12)) == []


class DribblingStream:
    """A pipe that returns a little at a time, as real ones do."""

    def __init__(self, payload: bytes, chunk: int):
        self._data, self._chunk, self._at = payload, chunk, 0

    def readinto(self, view) -> int:
        take = min(self._chunk, len(view), len(self._data) - self._at)
        if take <= 0:
            return 0
        view[:take] = self._data[self._at:self._at + take]
        self._at += take
        return take


def test_a_frame_split_across_several_reads_is_reassembled():
    """readinto returns whatever the pipe has, not what was asked for.
    Assuming one read per frame would tear every picture."""
    size = 12
    payload = bytes(range(size)) * 3
    frames = list(read_frames(DribblingStream(payload, chunk=5), size))
    assert len(frames) == 3
    assert all(frame == bytes(range(size)) for frame in frames)


def test_reading_stops_when_asked():
    size = 12
    stop = {"now": False}
    stream = io.BytesIO(bytes(size * 10))
    produced = []
    for frame in read_frames(stream, size, should_stop=lambda: stop["now"]):
        produced.append(frame)
        stop["now"] = True
    assert len(produced) == 1


# -- where a frame came from ---------------------------------------------------

def test_a_frames_position_is_its_place_in_the_clip():
    assert seconds_for_index(0, 10.0) == pytest.approx(10.0)
    assert seconds_for_index(30, 10.0, fps=30) == pytest.approx(11.0)
    assert seconds_for_index(15, 0.0, fps=30) == pytest.approx(0.5)


# -- deciding whether a seek needs a new ffmpeg --------------------------------

def test_a_small_jump_forward_is_skipped_to():
    """Restarting costs a two-stage seek and about half a second of nothing.
    A second ahead is already in the queue or moments away."""
    assert not should_restart(10.0, 11.0, playing=True)


def test_a_large_jump_forward_restarts():
    assert should_restart(10.0, 40.0, playing=True)


def test_any_jump_backwards_restarts():
    """A forward-only pipe cannot rewind."""
    assert should_restart(10.0, 9.9, playing=True)


def test_a_seek_while_paused_always_restarts():
    assert should_restart(10.0, 10.1, playing=False)


# -- the clock -----------------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


def test_the_clock_follows_real_time():
    fake = FakeClock()
    clock = PlayClock(origin=5.0, clock=fake)
    clock.start()
    fake.tick(0.5)
    assert clock.advance() == pytest.approx(5.5)
    fake.tick(0.25)
    assert clock.advance() == pytest.approx(5.75)


def test_a_late_tick_is_caught_up_with_rather_than_lost():
    """Counting frames instead would drift permanently after one slow repaint."""
    fake = FakeClock()
    clock = PlayClock(origin=0.0, clock=fake)
    clock.start()
    fake.tick(1.0)                       # the UI thread was busy
    assert clock.advance() == pytest.approx(1.0)


def test_a_starved_clock_stalls_rather_than_running_ahead():
    """When the card is too slow the picture should pause, not jump forward to
    a moment nobody has seen."""
    fake = FakeClock()
    clock = PlayClock(origin=3.0, clock=fake)
    clock.start()
    fake.tick(0.5)
    assert clock.advance(starved=True) == pytest.approx(3.0)
    fake.tick(0.5)
    assert clock.advance(starved=False) == pytest.approx(3.5)


def test_speed_multiplies_elapsed_time():
    fake = FakeClock()
    clock = PlayClock(origin=0.0, speed=2.0, clock=fake)
    clock.start()
    fake.tick(1.0)
    assert clock.advance() == pytest.approx(2.0)


def test_jumping_moves_the_clock_without_a_gap():
    fake = FakeClock()
    clock = PlayClock(origin=0.0, clock=fake)
    clock.start()
    fake.tick(5.0)
    clock.jump_to(60.0)
    fake.tick(0.5)
    assert clock.advance() == pytest.approx(60.5)


def test_the_first_advance_does_not_leap():
    """Nothing has elapsed before the clock is started."""
    clock = PlayClock(origin=2.0, clock=FakeClock())
    assert clock.advance() == pytest.approx(2.0)


# -- the controller ------------------------------------------------------------
#
# A fake decoder stands in for ffmpeg here. What is under test is which frames
# get shown and when a decoder gets restarted, and neither needs a real one —
# that ffmpeg accepts these arguments is what the integration tests are for.

@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


class FakeWorker(QObject):
    failed = Signal(int, str)
    ended = Signal(int)

    def __init__(self, tools, clip_info, start, size, generation, frames,
                 fps, parent=None):
        super().__init__(parent)
        self.start_at = start
        self.size = size
        self.generation = generation
        self.frames = frames
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def isRunning(self) -> bool:  # noqa: N802 (matching QThread)
        return self.started and not self.stopped

    def wait(self, _msecs=0) -> bool:
        return True


class FakeFrameWorker(QObject):
    ready = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, tools, clip_info, window, size, generation,
                 parent=None):
        super().__init__(parent)
        self.window = window
        self.size = size
        self.generation = generation
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def isRunning(self) -> bool:  # noqa: N802
        return self.started and not self.stopped

    def wait(self, _msecs=0) -> bool:
        return True


def player(fake_clock):
    from flightdvr.player import PreviewPlayer
    made, frame_workers = [], []

    def factory(*args, **kwargs):
        worker = FakeWorker(*args, **kwargs)
        made.append(worker)
        return worker

    def frame_factory(*args, **kwargs):
        worker = FakeFrameWorker(*args, **kwargs)
        frame_workers.append(worker)
        return worker

    instance = PreviewPlayer(
        TOOLS, clock=fake_clock, worker_factory=factory,
        frame_worker_factory=frame_factory,
    )
    instance.load(clip())
    instance.workers = made
    instance.frame_workers = frame_workers
    return instance


def fill(instance, *times):
    """Queue frames as though the decoder had produced them.

    Never more than QUEUE_FRAMES of them: the queue is bounded, and putting
    past its limit is exactly the block that gives the pipeline its
    back-pressure. Here it would simply hang the test.
    """
    assert len(times) <= QUEUE_FRAMES
    for when in times:
        instance._frames.put((when, b"\0" * instance.size.frame_bytes))


def test_playing_starts_a_decoder_at_the_playhead(qt_app):
    p = player(FakeClock())
    p.seek(30.0)
    p.play(view_width=640)

    assert p.is_playing
    assert len(p.workers) == 1
    assert p.workers[0].start_at == pytest.approx(30.0)
    assert p.workers[0].started


def test_the_first_frame_is_not_dropped_for_being_late(qt_app):
    """The clock must not run before anything has arrived, or the seek latency
    is charged to the frames that paid for it."""
    fake = FakeClock()
    p = player(fake)
    p.seek(30.0)
    p.play(view_width=640)

    fake.tick(0.6)                       # ffmpeg took this long to say anything
    fill(p, 30.0, 30.0 + 1 / PREVIEW_FPS)

    shown = []
    p.frame_ready.connect(lambda _image, when: shown.append(when))
    p._tick()
    assert shown == [pytest.approx(30.0)]


def test_a_frame_still_in_the_future_is_kept_for_its_moment(qt_app):
    p = player(FakeClock())
    p.play(view_width=640)
    fill(p, 0.0, 5.0)

    shown = []
    p.frame_ready.connect(lambda _image, when: shown.append(when))
    p._tick()
    p._tick()
    assert shown == [pytest.approx(0.0)]
    assert p._pending is not None and p._pending[0] == pytest.approx(5.0)


def test_late_frames_are_dropped_rather_than_played_in_slow_motion(qt_app):
    """A repaint that overran must cost frames, not put the picture behind the
    clock for the rest of the clip."""
    fake = FakeClock()
    p = player(fake)
    p.play(view_width=640)
    fill(p, 0.0)
    p._tick()                            # gets the clock running

    fake.tick(0.6)                       # the UI thread was busy for a while
    fill(p, *[i / PREVIEW_FPS for i in range(1, 21)])

    shown = []
    p.frame_ready.connect(lambda _image, when: shown.append(when))
    p._tick()
    # One frame painted out of the twenty that piled up, and it is the one for
    # now rather than the oldest of the backlog.
    assert len(shown) == 1
    assert shown[0] == pytest.approx(0.6, abs=1 / PREVIEW_FPS)


def test_the_playhead_follows_the_frame_shown_not_the_clock(qt_app):
    """Setting an in point has to mean the picture on screen. A playhead taken
    from the clock could name a frame that was never painted."""
    fake = FakeClock()
    p = player(fake)
    p.play(view_width=640)
    fill(p, 0.0, 0.25)
    p._tick()
    fake.tick(0.4)                       # the clock is past 0.25 now
    p._tick()
    assert p.position == pytest.approx(0.25)


def test_running_dry_stalls_the_picture_rather_than_skipping_ahead(qt_app):
    fake = FakeClock()
    p = player(fake)
    p.play(view_width=640)
    fill(p, 0.0)
    p._tick()

    fake.tick(2.0)
    p._tick()                            # nothing queued: starved
    fake.tick(0.1)
    fill(p, 1 / PREVIEW_FPS)

    shown = []
    p.frame_ready.connect(lambda _image, when: shown.append(when))
    p._tick()
    # The frame that finally arrived is shown, rather than thrown away for
    # being two seconds behind a clock that kept running without it.
    assert shown == [pytest.approx(1 / PREVIEW_FPS)]


def test_a_small_jump_forward_does_not_restart_the_decoder(qt_app):
    p = player(FakeClock())
    p.play(view_width=640)
    p.seek(1.0)
    assert len(p.workers) == 1
    assert p.position == pytest.approx(1.0)


def test_a_jump_backwards_restarts_the_decoder_at_the_target(qt_app):
    p = player(FakeClock())
    p.seek(40.0)
    p.play(view_width=640)
    p.seek(10.0)
    assert len(p.workers) == 2
    assert p.workers[1].start_at == pytest.approx(10.0)
    assert p.workers[0].stopped


def test_seeking_past_the_end_lands_inside_the_clip(qt_app):
    p = player(FakeClock())
    p.seek(9999.0)
    assert p.position == pytest.approx(clip().duration)


def test_seeking_while_paused_drops_the_decoder_it_had(qt_app):
    """Otherwise pressing play again resumes from where the pause happened,
    which is no longer where the playhead is."""
    p = player(FakeClock())
    p.play(view_width=640)
    p.pause()
    p.seek(90.0)
    assert p.workers[0].stopped

    p.play(view_width=640)
    assert len(p.workers) == 2
    assert p.workers[1].start_at == pytest.approx(90.0)


def test_pausing_keeps_the_decoder_so_resuming_is_instant(qt_app):
    p = player(FakeClock())
    p.play(view_width=640)
    p.pause()
    assert not p.is_playing
    assert not p.workers[0].stopped

    p.play(view_width=640)
    assert len(p.workers) == 1


def test_a_restart_gives_the_new_decoder_a_queue_of_its_own(qt_app):
    """A retired worker is usually blocked in a put. Letting it write where
    nobody reads is how it gets to notice it was cancelled and leave."""
    p = player(FakeClock())
    p.play(view_width=640)
    fill(p, 0.0, 1.0)
    p.seek(120.0)
    assert p._frames.empty()
    assert p.workers[1].frames is p._frames
    assert p.workers[0].frames is not p._frames


def test_the_end_of_the_stream_waits_for_the_frames_already_queued(qt_app):
    fake = FakeClock()
    p = player(fake)
    p.play(view_width=640)
    fill(p, 0.0, 0.1)
    p.workers[0].ended.emit(1)

    over = []
    p.ended.connect(lambda: over.append(True))
    p._tick()
    assert not over and p.is_playing

    fake.tick(1.0)
    p._tick()                            # shows the last queued frame
    p._tick()                            # nothing left, and the stream is over
    assert over and not p.is_playing


def test_a_retired_decoder_cannot_end_playback(qt_app):
    """Its signals arrive after it was replaced, describing a stream nobody is
    watching any more."""
    p = player(FakeClock())
    p.play(view_width=640)
    stale = p.workers[0]
    p.seek(120.0)

    over = []
    p.ended.connect(lambda: over.append(True))
    stale.ended.emit(stale.generation)
    p._tick()
    assert not over


def test_a_retired_decoder_cannot_report_a_failure(qt_app):
    p = player(FakeClock())
    p.play(view_width=640)
    stale = p.workers[0]
    p.seek(120.0)

    complaints = []
    p.failed.connect(complaints.append)
    stale.failed.emit(stale.generation, "Invalid data found")
    assert not complaints
    assert p.is_playing


def test_a_failure_stops_playback_and_says_so(qt_app):
    p = player(FakeClock())
    p.play(view_width=640)

    complaints, states = [], []
    p.failed.connect(complaints.append)
    p.state_changed.connect(states.append)
    p.workers[0].failed.emit(1, "Invalid data found when processing input")

    assert complaints == ["Invalid data found when processing input"]
    assert states == [False]
    assert not p.is_playing


def test_stopping_retires_the_decoder(qt_app):
    p = player(FakeClock())
    p.play(view_width=640)
    p.stop()
    assert p.workers[0].stopped
    assert not p.is_playing


def test_loading_another_clip_stops_the_one_playing(qt_app):
    p = player(FakeClock())
    p.play(view_width=640)
    p.load(clip(path=Path("hdz_023.ts")))
    assert p.workers[0].stopped
    assert not p.is_playing
    assert p.position == 0.0


def test_a_frame_becomes_an_image_that_owns_its_pixels(qt_app):
    """QImage does not copy the buffer it is handed, and the buffer here is a
    local that is about to go out of scope."""
    from PySide6.QtGui import QImage
    p = player(FakeClock())
    p.size = PreviewSize(4, 2)
    data = bytes([255, 0, 0] * 8)
    image = p._to_image(data)
    del data

    assert image.width() == 4 and image.height() == 2
    assert image.format() == QImage.Format.Format_RGB32
    assert image.pixelColor(0, 0).red() == 255


# -- paused source-frame stepping --------------------------------------------

def precise_frames(worker, *numbers):
    payload = b"\0" * worker.size.frame_bytes
    return tuple(CachedFrame(n, n / worker.window.fps, payload)
                 for n in numbers)


def test_a_frame_step_decodes_a_bounded_native_rate_window(qt_app):
    p = player(FakeClock())
    p.seek(10.0)
    p.step_frames(1)

    assert len(p.frame_workers) == 1
    worker = p.frame_workers[0]
    assert worker.started
    assert worker.window.fps == pytest.approx(60.0)
    assert worker.window.frame_count == 121
    assert worker.window.first_frame <= 601 <= worker.window.last_frame


def test_stepping_lands_on_the_exact_frame_it_displays(qt_app):
    p = player(FakeClock())
    p.seek(10.0)
    shown = []
    p.precise_frame_ready.connect(
        lambda _image, seconds, number: shown.append((seconds, number)))

    p.step_frames(1)
    worker = p.frame_workers[0]
    worker.ready.emit(worker.generation, precise_frames(worker, 600, 601, 602))

    assert shown == [(pytest.approx(601 / 60), 601)]
    assert p.position == pytest.approx(601 / 60)


def test_a_second_step_inside_the_cache_spawns_nothing(qt_app):
    p = player(FakeClock())
    p.seek(10.0)
    shown = []
    p.precise_frame_ready.connect(
        lambda _image, _seconds, number: shown.append(number))

    p.step_frames(1)
    worker = p.frame_workers[0]
    worker.ready.emit(worker.generation, precise_frames(worker, 600, 601, 602))
    p.step_frames(1)

    assert len(p.frame_workers) == 1
    assert shown == [601, 602]


def test_quick_steps_accumulate_while_the_window_is_still_loading(qt_app):
    p = player(FakeClock())
    p.seek(10.0)
    shown = []
    p.precise_frame_ready.connect(
        lambda _image, _seconds, number: shown.append(number))

    p.step_frames(1)
    p.step_frames(1)
    worker = p.frame_workers[0]
    worker.ready.emit(worker.generation, precise_frames(worker, 601, 602))

    assert len(p.frame_workers) == 1
    assert shown == [602]


def test_seeking_outside_the_cache_starts_a_replacement_window(qt_app):
    p = player(FakeClock())
    p.seek(10.0)
    p.step_frames(1)
    first = p.frame_workers[0]
    first.ready.emit(first.generation, precise_frames(first, 600, 601, 602))

    p.seek(200.0)
    p.step_frames(1)
    second = p.frame_workers[1]
    second.ready.emit(
        second.generation, precise_frames(second, 12000, 12001, 12002))

    assert len(p._frame_cache) == 3
    assert p._frame_cache.get(601) is None
    assert p._frame_cache.get(12001) is not None


def test_shift_sized_steps_move_ten_source_frames(qt_app):
    p = player(FakeClock())
    p.seek(10.0)
    p.step_frames(10)
    worker = p.frame_workers[0]
    worker.ready.emit(worker.generation, precise_frames(worker, 610))
    assert p.position == pytest.approx(610 / 60)


def test_frame_stepping_pauses_and_retires_the_playback_decoder(qt_app):
    p = player(FakeClock())
    p.play(view_width=640)
    playback = p.workers[0]
    p.step_frames(1)
    assert not p.is_playing
    assert playback.stopped


def test_frame_stepping_refuses_to_invent_an_unknown_source_rate(qt_app):
    p = player(FakeClock())
    p.load(clip(fps=0.0))
    problems = []
    p.precise_failed.connect(problems.append)
    p.step_frames(1)
    assert problems and "frame rate" in problems[0]
    assert not p.frame_workers


# -- the view ------------------------------------------------------------------

def test_the_view_reports_whether_it_has_a_picture(qt_app):
    from PySide6.QtGui import QImage
    from flightdvr.player import FrameView

    view = FrameView()
    assert not view.has_image()
    view.set_message("reading frames…")
    assert not view.has_image()

    view.set_image(QImage(8, 8, QImage.Format.Format_RGB32))
    assert view.has_image()
    view.set_message("no frames")
    assert not view.has_image()


def test_the_view_is_big_enough_to_judge_a_moment_by(qt_app):
    """The label it replaces was 176x99 — fine for telling clips apart, useless
    for deciding where a flight starts."""
    from flightdvr.player import FrameView
    view = FrameView()
    assert view.minimumWidth() >= 320
    # The playback keys are scoped to this widget, so it has to be focusable.
    assert view.focusPolicy() != Qt.FocusPolicy.NoFocus
