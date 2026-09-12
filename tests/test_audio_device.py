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
from types import SimpleNamespace

import pytest

from flightdvr.audio_device import (
    ACTIVE, FRAME_BYTES, IDLE, STOPPED, SUSPENDED, UNKNOWN, AudioOutput,
    DeviceFormat, DeviceReport, DeviceUnavailable, block_bytes, qt_state_name,
)
from flightdvr.audio_plan import OUTPUT_CHANNELS, OUTPUT_RATE
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
                 refuse: bool = False, buffer_size: int = 4096,
                 observe_fails: bool = False):
        self.written = bytearray()
        self.calls = []
        self.volume = None
        self._accept = accept
        self._fail = fail
        self._refuse = refuse
        self._observe_fails = observe_fails
        self.device = self
        # The backend's own account of itself. `processed` is deliberately
        # *behind* `written`: a device that had played everything the instant
        # it was handed over is the one shape that cannot show the difference
        # between submitted and processed, which is the distinction under test.
        self.reported_state = "StoppedState"
        self.reported_error = ""
        self.processed_usecs = 0
        self.buffer_size = buffer_size

    def play(self, usecs: int) -> None:
        """Let the backend work through some of what it holds."""
        self.processed_usecs += usecs

    def state(self):
        if self._observe_fails:
            raise OSError("the backend will not say")
        return SimpleNamespace(name=self.reported_state)

    def error(self) -> str:
        return self.reported_error

    def bytesFree(self) -> int:                   # noqa: N802 (Qt naming)
        # Qt reports zero outside Active and Idle. Modelled, because reading
        # that as a full buffer inverts the signal.
        if self.reported_state not in ("ActiveState", "IdleState"):
            return 0
        return max(0, self.buffer_size - len(self.written))

    def bufferSize(self) -> int:                  # noqa: N802 (Qt naming)
        return self.buffer_size

    def processedUSecs(self) -> int:              # noqa: N802 (Qt naming)
        return self.processed_usecs

    def start(self):
        self.calls.append("start")
        self.reported_state = "ActiveState"
        return self.device

    def suspend(self) -> None:
        self.calls.append("suspend")
        self.reported_state = "SuspendedState"

    def resume(self) -> None:
        self.calls.append("resume")
        self.reported_state = "ActiveState"

    def reset(self) -> None:
        """What `QAudioSink.reset()` does: drop the buffer, and stop.

        Modelled rather than ignored, because the finding was precisely that
        `suspend()` keeps this and only `reset()` drops it. A fake that kept
        the bytes would agree with the bug.
        """
        self.calls.append("reset")
        self.written.clear()
        # Qt restarts the processed count with the sink, so a fake that kept
        # it would hide exactly the re-anchoring this has to get right.
        self.processed_usecs = 0
        self.reported_state = "StoppedState"

    def stop(self) -> None:
        self.calls.append("stop")
        self.reported_state = "StoppedState"

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

# -- what the backend says about itself ----------------------------------------

def test_submitted_and_processed_are_different_numbers():
    """The distinction the whole observation exists for.

    Submitted is what the device took from us. Processed is what the backend
    says it has worked through. They are not the same, and the difference is
    sound the device is holding — which is why neither may be called heard.
    """
    out, sink = started()
    out.resume()
    out.present(a_block(frames=480))
    out.pump()
    submitted = out.submitted_bytes
    assert submitted > 0

    assert out.processed_bytes() == 0, "a fresh backend had already played it"
    assert out.unplayed_bytes() == submitted

    sink.play(5_000)                       # 5 ms of it
    processed = out.processed_bytes()
    assert 0 < processed < submitted
    assert out.unplayed_bytes() == submitted - processed
    out.stop()


def test_a_backend_ahead_of_us_reports_nothing_unplayed_rather_than_less_than_none():
    """Two counts from different places. A rounded microsecond can put the
    backend a shade ahead, and a negative gap there is an artefact — not sound
    that arrived before it was sent."""
    out, sink = started()
    out.resume()
    out.present(a_block(frames=4))
    out.pump()
    sink.play(1_000_000)                   # far more than we ever sent

    assert out.unplayed_bytes() == 0
    out.stop()


def test_bytes_free_is_only_meaningful_while_the_device_is_consuming():
    """Qt reports zero outside Active and Idle, and zero there means
    "not applicable" — reading it as a full buffer inverts the signal."""
    out, sink = started()
    out.resume()
    assert out.observe().room_known

    sink.reported_state = "SuspendedState"
    report = out.observe()
    assert report.state == SUSPENDED
    assert report.bytes_free == 0
    assert not report.room_known, "zero was read as a full buffer"
    out.stop()


def test_a_backend_that_will_not_answer_is_unknown_rather_than_a_crash():
    out, _sink = started(observe_fails=True)
    out.resume()
    report = out.observe()
    assert report.state == UNKNOWN
    assert not report.usable
    assert out.processed_bytes() is None
    assert out.unplayed_bytes() is None
    assert out.starvation() == "", "a cause was guessed from a non-answer"
    out.stop()


def test_the_counters_re_anchor_together_on_a_fence():
    """`processedUSecs` restarts with the sink. Comparing a fresh backend count
    against a submitted total from before the reset invents a gap."""
    out, sink = started()
    out.resume()
    out.present(a_block(frames=480))
    out.pump()
    sink.play(3_000)
    assert out.submitted_bytes > 0

    out.reset(1)

    assert out.submitted_bytes == 0
    assert sink.processed_usecs == 0
    assert out.unplayed_bytes() == 0, "a gap survived the re-anchor"
    out.stop()


def test_the_counters_re_anchor_on_start_as_well():
    sink = FakeSink()
    out = AudioOutput(sink_factory=lambda: sink)
    out.start()
    out.resume()
    out.present(a_block(frames=8))
    out.pump()
    out.stop()
    assert out.submitted_bytes == 0


# -- telling the three quiet states apart --------------------------------------

def test_a_finished_stream_is_ended_not_starved():
    out, sink = started()
    out.resume()
    out.present(a_block(frames=480))
    out.pump()
    sink.play(10_000_000)                  # worked through all of it
    sink.reported_state = "IdleState"

    assert out.starvation() == "ended"
    out.stop()


def test_a_device_idle_with_sound_still_in_hand_is_starved():
    out, sink = started()
    out.resume()
    out.present(a_block(frames=480))
    out.pump()
    sink.reported_state = "IdleState"      # idle, but it has not played it

    assert out.starvation() == "starved"
    out.stop()


def test_an_empty_queue_against_a_hungry_device_is_starved():
    """Us being late, which is the one feeding faster would fix."""
    out, sink = started()
    out.resume()
    assert out.queued_bytes == 0
    sink.reported_state = "ActiveState"

    assert out.starvation() == "starved"
    out.stop()


def test_a_backend_error_is_its_own_cause_and_not_starvation():
    """Neither of the other two, and not fixed by feeding it faster."""
    out, sink = started()
    out.resume()
    sink.reported_error = "UnderrunError"

    assert out.starvation() == "backend"
    out.stop()


def test_a_busy_device_with_work_in_hand_is_not_starving():
    out, sink = started()
    out.resume()
    out.present(a_block(frames=480))
    out.pump()
    sink.reported_state = "ActiveState"

    assert out.starvation() == ""
    out.stop()


# -- closing quietly -----------------------------------------------------------

def test_stopping_drops_the_buffer_before_it_stops_the_sink():
    """Qt documents that `stop()` may play out what the backend holds, and on
    Linux and macOS that drain is synchronous. On the UI thread that is a
    window which will not close while half a second of music finishes.
    """
    out, sink = started()
    out.resume()
    out.present(a_block(frames=480))
    out.pump()
    assert sink.written

    out.stop()

    assert sink.calls.index("reset") < sink.calls.index("stop"), sink.calls
    assert sink.written == b"", "the backend still held sound when it stopped"


def test_stopping_still_releases_a_sink_that_cannot_drop_its_buffer():
    sink = FakeSink()

    def explode() -> None:
        raise OSError("the device went away")

    out = AudioOutput(sink_factory=lambda: sink)
    out.start()
    sink.reset = explode

    out.stop()                             # must not raise

    assert "stop" in sink.calls, "a failed reset stopped us releasing the sink"
    assert out.paused and not out.running


# -- the enum names Qt actually uses -------------------------------------------

def test_qt_state_names_map_to_ours():
    for name, expected in (("ActiveState", ACTIVE), ("IdleState", IDLE),
                           ("SuspendedState", SUSPENDED),
                           ("StoppedState", STOPPED)):
        assert qt_state_name(SimpleNamespace(name=name)) == expected


def test_an_unrecognised_state_stays_reportable_rather_than_forced():
    assert qt_state_name(SimpleNamespace(name="SomethingNewState")) == UNKNOWN
    assert qt_state_name(None) == UNKNOWN


def test_a_report_from_no_sink_is_stopped_not_unknown():
    out = AudioOutput(sink_factory=lambda: FakeSink())
    assert out.observe() == DeviceReport(state=STOPPED)
