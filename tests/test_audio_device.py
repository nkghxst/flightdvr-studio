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

"""The PCM output adapter, driven by a sink that cannot make a sound.

Every device here is a stand-in. That is deliberate and it is also the limit:
these settle what the adapter does with blocks, queues, generations and
failures, and they settle **nothing** about whether anything is audible, in
time with the picture, or free of dropouts on real hardware. A fake sink
cannot hear itself.

What a real device would still have to be observed doing, before a transport
can claim synchronisation, is written down in
`test_what_a_real_device_still_has_to_tell_us` rather than left implied.
"""

from __future__ import annotations

import array
from fractions import Fraction

import pytest

from flightdvr.audio_device import (
    FRAME_BYTES, AudioOutput, DeviceFormat, DeviceUnavailable, block_bytes,
)
from flightdvr.audio_plan import (
    OUTPUT_CHANNELS, OUTPUT_RATE, AudioMode, SampleSpan,
)
from flightdvr.audio_stream import PcmBlock


def a_block(*, generation: int = 0, frames: int = 4, planned: float = 0.8,
            monitored: float = 0.2, start: int = 0) -> PcmBlock:
    """One block whose two renderings are deliberately different values.

    Different on purpose: every test that cares which buffer was used can then
    say so from the bytes alone, rather than from a buffer that would look the
    same either way.
    """
    samples = frames * OUTPUT_CHANNELS
    return PcmBlock(
        generation=generation, monitor_revision=0, output_start=start,
        frames=frames,
        planned=[planned] * samples,
        monitored=[monitored] * samples,
    )


class FakeSink:
    """A sink that records what it was asked to do and plays nothing."""

    def __init__(self, *, accept: int | None = None, fail: bool = False,
                 refuse: bool = False):
        self.written = bytearray()
        self.calls = []
        self.volume = None
        self._accept = accept
        self._fail = fail
        self._refuse = refuse
        self.device = self

    def start(self):
        self.calls.append("start")
        return self.device

    def suspend(self) -> None:
        self.calls.append("suspend")

    def resume(self) -> None:
        self.calls.append("resume")

    def reset(self) -> None:
        """What `QAudioSink.reset()` does: drop the buffer, and stop.

        Modelled rather than ignored, because the finding was precisely that
        `suspend()` keeps this and only `reset()` drops it. A fake that kept
        the bytes would agree with the bug.
        """
        self.calls.append("reset")
        self.written.clear()

    def stop(self) -> None:
        self.calls.append("stop")

    def setVolume(self, volume: float) -> None:   # noqa: N802 (Qt naming)
        self.volume = volume

    # the QIODevice half
    def write(self, raw: bytes) -> int:
        if self._fail:
            raise OSError("the device went away")
        if self._refuse:
            return -1
        taken = len(raw) if self._accept is None else min(self._accept, len(raw))
        self.written += raw[:taken]
        return taken


def started(**kwargs) -> tuple[AudioOutput, FakeSink]:
    sink = FakeSink(**kwargs)
    out = AudioOutput(sink_factory=lambda: sink)
    out.start()
    return out, sink


# -- quiet by default ----------------------------------------------------------

def test_starting_opens_the_sink_and_makes_no_sound():
    out, sink = started()
    assert sink.calls == ["start"]
    assert out.paused and not out.running
    assert out.pump() == 0, "a started adapter wrote before anything resumed it"
    out.stop()


def test_the_sink_volume_is_left_alone_so_the_level_is_not_applied_twice():
    """The stream already rendered the listening level into `monitored`.

    Setting it on the device as well squares it: a level of 0.25 would play at
    0.0625, and the quiet end of the control would stop doing anything long
    before it reached the bottom.
    """
    out, sink = started()
    out.resume()
    out.present(a_block())
    out.pump()
    assert sink.volume is None, "the adapter set a device volume of its own"
    out.stop()


def test_nothing_plays_until_something_resumes_it():
    out, sink = started()
    assert out.present(a_block()) == FRAME_BYTES * 4
    assert out.pump() == 0
    assert sink.written == b""
    out.resume()
    assert out.pump() == FRAME_BYTES * 4
    out.stop()


# -- which rendering reaches the device ----------------------------------------

def test_the_device_is_fed_the_monitored_rendering_never_the_planned_one():
    out, sink = started()
    out.resume()
    block = a_block(planned=0.8, monitored=0.2)
    out.present(block)
    out.pump()

    heard = array.array("f")
    heard.frombytes(bytes(sink.written))
    assert heard, "nothing reached the device"
    assert all(value == pytest.approx(0.2) for value in heard), list(heard)[:4]
    assert not any(value == pytest.approx(0.8) for value in heard), (
        "the export rendering was played")
    out.stop()


def test_presenting_a_block_does_not_change_it():
    """The block is a value the export may still be built from."""
    out, _sink = started()
    out.resume()
    block = a_block()
    before = (list(block.planned), list(block.monitored))
    out.present(block)
    out.pump()
    assert (list(block.planned), list(block.monitored)) == before
    out.stop()


# -- bounded buffering and partial writes --------------------------------------

def test_the_queue_stops_growing_at_its_bound():
    """Every byte past the bound is sound that must play before a stop is
    heard, so the bound refuses rather than stretches."""
    sink = FakeSink()
    out = AudioOutput(sink_factory=lambda: sink,
                      max_queued_bytes=FRAME_BYTES * 8)
    out.start()
    assert out.present(a_block(frames=6)) == FRAME_BYTES * 6
    assert out.present(a_block(frames=6)) == FRAME_BYTES * 2, "the bound stretched"
    assert out.present(a_block(frames=6)) == 0
    assert out.queued_bytes == FRAME_BYTES * 8
    out.stop()


def test_a_short_write_keeps_the_rest_instead_of_dropping_it():
    """A device takes what it has room for. That is ordinary, not a failure."""
    out, sink = started(accept=FRAME_BYTES * 2)
    out.resume()
    out.present(a_block(frames=5))

    assert out.pump() == FRAME_BYTES * 2
    assert out.queued_bytes == FRAME_BYTES * 3
    assert not out.failure, out.failure
    assert out.pump() == FRAME_BYTES * 2
    assert out.pump() == FRAME_BYTES * 1
    assert out.queued_bytes == 0
    assert len(sink.written) == FRAME_BYTES * 5
    out.stop()


def test_a_short_write_that_stops_mid_frame_sends_each_byte_once():
    """The finding (#119 review), and the model this test used to have wrong.

    A device may accept a count that does not land on a frame boundary. The
    first version rounded down and dropped only the whole frames — but the
    device already held the rounded-away tail, so the next write sent those
    bytes a second time and the sound gained duplicated samples at every short
    write. Alignment is kept by continuing from exactly where the device
    stopped, not by rounding: a QIODevice is a byte stream.
    """
    out, sink = started(accept=FRAME_BYTES + 3)
    out.resume()
    block = a_block(frames=4)
    out.present(block)
    while out.queued_bytes and not out.failure:
        if out.pump() == 0:
            break

    expected = block_bytes(block)
    assert len(sink.written) == len(expected), (
        f"{len(expected)} bytes of sound arrived as {len(sink.written)}")
    assert bytes(sink.written) == expected, "the byte stream was not continuous"
    out.stop()


# -- generations ---------------------------------------------------------------

def test_a_block_from_an_older_generation_is_refused():
    out, _sink = started()
    out.resume()
    out.reset(3)
    assert out.present(a_block(generation=2)) == 0
    assert out.present(a_block(generation=3)) > 0
    out.stop()


def test_resetting_discards_sound_queued_before_the_change():
    """After a seek the queue is sound from before it. Playing it out first is
    how a scrub ends up behind the picture."""
    out, sink = started()
    out.resume()
    out.present(a_block(frames=6))
    assert out.queued_bytes > 0

    out.reset(1)
    assert out.queued_bytes == 0
    assert out.pump() == 0
    assert sink.written == b"", "old sound reached the device after a reset"
    out.stop()


def test_pausing_drops_the_queue_so_resuming_does_not_replay_it():
    out, sink = started()
    out.resume()
    out.present(a_block(frames=6))
    out.pause()

    assert out.queued_bytes == 0
    assert "suspend" in sink.calls
    out.resume()
    assert out.pump() == 0
    assert sink.written == b""
    out.stop()


# -- failure and cleanup -------------------------------------------------------

def test_a_device_that_fails_goes_quiet_and_says_why_without_raising():
    """The transport above has a picture to keep running."""
    out, sink = started(fail=True)
    out.resume()
    out.present(a_block())
    assert out.pump() == 0

    assert out.failure and "went away" in out.failure
    assert out.paused and not out.running
    assert out.queued_bytes == 0
    assert "stop" in sink.calls, "the failed sink was not released"
    assert out.present(a_block()) == 0, "it kept taking blocks after failing"
    out.stop()


def test_a_device_that_refuses_the_buffer_is_a_failure_not_a_short_write():
    out, _sink = started(refuse=True)
    out.resume()
    out.present(a_block())
    assert out.pump() == 0
    assert "refused" in out.failure
    out.stop()


def test_stopping_twice_is_safe_and_releases_the_sink_once():
    out, sink = started()
    out.resume()
    out.stop()
    out.stop()
    assert sink.calls.count("stop") == 1
    assert out.paused and out.queued_bytes == 0


def test_no_output_device_is_reported_rather_than_crashed():
    def refuse():
        raise DeviceUnavailable("this machine has no audio output")

    out = AudioOutput(sink_factory=refuse)
    with pytest.raises(DeviceUnavailable):
        out.start()
    assert "no audio output" in out.failure
    out.stop()          # still safe with nothing open


def test_a_backend_failing_its_own_way_becomes_one_named_error():
    def explode():
        raise RuntimeError("some backend detail")

    out = AudioOutput(sink_factory=explode)
    with pytest.raises(DeviceUnavailable):
        out.start()
    assert "could not start" in out.failure


# -- the format offered --------------------------------------------------------

def test_the_format_matches_what_the_stream_renders():
    fmt = DeviceFormat()
    assert (fmt.rate, fmt.channels) == (OUTPUT_RATE, OUTPUT_CHANNELS)
    assert FRAME_BYTES == 4 * OUTPUT_CHANNELS


def test_a_nonsense_format_is_refused():
    with pytest.raises(ValueError):
        DeviceFormat(rate=0)


def test_block_bytes_is_exactly_the_frames_it_was_given():
    block = a_block(frames=7)
    assert len(block_bytes(block)) == FRAME_BYTES * 7


# -- what this cannot settle ---------------------------------------------------

def test_what_a_real_device_still_has_to_tell_us():
    """Written as a test so it is read, and fails if the claim is overstated.

    Everything above runs against a stand-in. A stand-in accepts bytes
    instantly and forgets them, so it can prove what the adapter *sends* and
    nothing at all about what is *heard*. Before any transport claims
    synchronisation, a real device has to be observed for three things this
    adapter does not yet expose, because none of them can be invented from a
    fake:

    1. how far behind the write the device actually is — `QAudioSink` reports
       `processedUSecs` and a buffer size, and the difference between them is
       the latency a picture would have to be offset by;
    2. how often it underruns in practice, which is what decides whether the
       queue bound above is generous or mean;
    3. whether its clock drifts against the video clock over minutes, which
       no single measurement can answer.

    Until those are observed on a machine that can make a sound, this module
    is a sender, not a synchroniser.
    """
    from flightdvr import audio_device

    surface = {name for name in vars(audio_device) if not name.startswith("_")}
    for absent in ("latency", "processed_usecs", "drift", "sync"):
        assert not any(absent in name.lower() for name in surface), (
            f"{absent} appears in the adapter's surface; this module does not "
            "measure it and must not look as though it does")

def test_pausing_fences_sound_the_device_is_already_holding():
    """The second finding (#119 review).

    `QAudioSink.suspend()` keeps what it has already been handed and plays it
    on resume. Clearing only this adapter's queue therefore left a fifth of a
    second of sound from before the pause waiting to be heard after it.
    """
    out, sink = started()
    out.resume()
    out.present(a_block(frames=6))
    out.pump()
    assert sink.written, "nothing reached the device to begin with"

    out.pause()

    assert "reset" in sink.calls, "the device kept its buffered sound"
    assert sink.written == b"", "sound from before the pause survived it"
    assert out.queued_bytes == 0
    out.stop()


def test_a_reset_fences_sound_the_device_is_already_holding():
    """Same fence, for a seek. Old sound after the seek point is the defect."""
    out, sink = started()
    out.resume()
    out.present(a_block(frames=6))
    out.pump()
    assert sink.written

    out.reset(1)

    assert "reset" in sink.calls
    assert sink.written == b"", "sound from before the seek survived it"
    out.stop()


def test_the_device_handle_is_usable_again_after_a_fence():
    """`reset()` leaves a sink stopped, so the handle has to be retaken.

    Without that, everything after the first pause or seek would queue and
    never be written, which is silence that looks like a working transport.
    """
    out, sink = started()
    out.resume()
    out.present(a_block(frames=4))
    out.pump()
    out.reset(1)
    out.resume()

    out.present(a_block(generation=1, frames=4))
    assert out.pump() > 0, "nothing could be written after the fence"
    assert sink.written, "the device handle was not usable again"
    out.stop()


def test_a_sink_that_fails_while_clearing_goes_quiet_and_says_so():
    sink = FakeSink()

    def explode() -> None:
        raise OSError("the device went away")

    out = AudioOutput(sink_factory=lambda: sink)
    out.start()
    out.resume()
    out.present(a_block())
    sink.reset = explode
    out.reset(1)

    assert out.failure and "clearing" in out.failure
    assert out.paused


def test_a_pause_survives_a_sink_that_fails_while_clearing():
    """The correction-induced finding (#119 rereview).

    `_fence` releases the sink when it fails, so pausing had nothing left to
    suspend and raised `AttributeError` out of an ordinary pause — a device
    fault turned into a crash. The earlier failure test exercised `reset()`
    and never reached this path.
    """
    sink = FakeSink()

    def explode() -> None:
        raise OSError("the device went away")

    out = AudioOutput(sink_factory=lambda: sink)
    out.start()
    out.resume()
    out.present(a_block())
    sink.reset = explode

    out.pause()                      # must not raise

    assert out.failure and "clearing" in out.failure
    assert out.paused and not out.running
    assert out.queued_bytes == 0
    out.stop()                       # and stopping afterwards is still safe


def test_every_control_is_safe_after_a_failure():
    """Whatever order the transport calls them in, none of them raises."""
    sink = FakeSink()

    def explode() -> None:
        raise OSError("the device went away")

    out = AudioOutput(sink_factory=lambda: sink)
    out.start()
    out.resume()
    sink.reset = explode
    out.pause()

    out.resume()
    out.pause()
    out.reset(2)
    assert out.present(a_block(generation=2)) == 0
    assert out.pump() == 0
    out.stop()
    assert out.paused and not out.running
