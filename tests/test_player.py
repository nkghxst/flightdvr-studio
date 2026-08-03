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

from flightdvr.media import ClipInfo, Tools
from flightdvr.player import (
    PREVIEW_FPS, PREVIEW_SIZES, SEEK_LEAD_IN, PlayClock, PreviewSize,
    build_command, choose_size, read_frames, seconds_for_index, seek_pair,
    should_restart,
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


# -- seeking -------------------------------------------------------------------

def test_a_seek_is_split_so_the_decoder_can_catch_up():
    """One seek alone lands on an estimated byte offset in an MPEG-TS and the
    frames after it are torn until the next keyframe."""
    fast, accurate = seek_pair(10.5)
    assert fast == pytest.approx(8.5)
    assert accurate == pytest.approx(2.0)
    assert fast + accurate == pytest.approx(10.5)


def test_a_seek_never_lands_before_the_start_of_the_clip():
    fast, accurate = seek_pair(0.5)
    assert fast == 0.0
    assert accurate == pytest.approx(0.5)
    assert fast + accurate == pytest.approx(0.5)


def test_playing_from_the_beginning_seeks_at_all():
    assert seek_pair(0.0) == (0.0, 0.0)


@pytest.mark.parametrize("start", [0.0, 0.2, 1.0, 2.0, 2.5, 44.0, 180.0])
def test_the_two_seeks_always_reach_the_point_asked_for(start):
    fast, accurate = seek_pair(start)
    assert fast + accurate == pytest.approx(start)
    assert fast >= 0.0 and accurate >= 0.0
    assert accurate <= SEEK_LEAD_IN


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
    assert after == ["2.000"]


def test_a_full_range_clip_keeps_the_colour_correction():
    """Without it the preview looks washed out and disagrees with the export it
    is meant to be predicting."""
    command = flatten(build_command(TOOLS, clip(color_range="pc"), 0.0,
                                    PreviewSize(640, 360)))
    assert "scale=in_range=full:out_range=limited" in command


def test_a_limited_range_clip_is_left_alone():
    command = flatten(build_command(TOOLS, clip(color_range="tv", pix_fmt=""),
                                    0.0, PreviewSize(640, 360)))
    assert "in_range=full" not in command


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
