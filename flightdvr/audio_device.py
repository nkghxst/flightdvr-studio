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

"""Raw PCM to the machine's audio output, and nothing else.

This is the one production module allowed to name `QtMultimedia`, and it is
allowed only `QAudioFormat`, `QAudioSink` and `QMediaDevices`. The standing
rule against `QtMultimedia` is about *decoding*: its Windows backend cannot
decode HEVC in MPEG-TS, the only format this app exists for, and it would pass
every synthetic test and fail on every real recording. FFmpeg keeps all
decoding, here as everywhere else.

`QAudioSink` decodes nothing. It takes interleaved samples and hands them to a
device. Every other part of `QtMultimedia` — players, decoders, capture —
stays forbidden, and `tests/test_player.py` checks that this file is the only
exception and that it imports only those three names.

Nothing here opens a device by itself. The sink arrives through
`sink_factory`, so tests drive the whole adapter without a machine that can
make a sound, and so a machine with no output device is a state this can
report rather than a crash.
"""

from __future__ import annotations

import array
from dataclasses import dataclass
from typing import Callable, Protocol

from .audio_stream import PcmBlock
from .audio_plan import OUTPUT_CHANNELS, OUTPUT_RATE

# One float per sample, which is what `PcmBuffer` already holds.
SAMPLE_BYTES = 4
FRAME_BYTES = SAMPLE_BYTES * OUTPUT_CHANNELS

# A fifth of a second of stereo float at the output rate. Enough to ride out a
# late block, small enough that a stop is heard as a stop: anything already
# handed to the device still plays, so a deep queue is a long tail of sound
# after the person asked for silence.
MAX_QUEUED_BYTES = FRAME_BYTES * OUTPUT_RATE // 5


# What the backend says its own state is. Kept as plain strings so the fake
# seam needs no Qt, and so an unrecognised state stays reportable instead of
# being forced into one we happen to know.
ACTIVE, IDLE, SUSPENDED, STOPPED, UNKNOWN = (
    "active", "idle", "suspended", "stopped", "unknown")

# `bytesFree` means something only while the device is actually consuming.
# Qt returns zero in the other states, and zero there means "not applicable",
# not "the buffer is full" — reading it as fullness would invert the signal.
BYTES_FREE_STATES = (ACTIVE, IDLE)


class Sink(Protocol):
    """What this adapter needs from `QAudioSink`, and no more."""

    def start(self): ...
    def suspend(self) -> None: ...
    def resume(self) -> None: ...
    def reset(self) -> None: ...
    def stop(self) -> None: ...
    def setVolume(self, volume: float) -> None: ...  # noqa: N802 (Qt naming)
    def state(self) -> str: ...
    def error(self) -> str: ...
    def bytesFree(self) -> int: ...                  # noqa: N802 (Qt naming)
    def bufferSize(self) -> int: ...                 # noqa: N802 (Qt naming)
    def processedUSecs(self) -> int: ...             # noqa: N802 (Qt naming)


@dataclass(frozen=True)
class DeviceReport:
    """What the backend says about itself, and nothing inferred from it.

    Every field is the backend's own account. `processed_usecs` in particular
    is **audio data the backend says it has processed since the sink started**
    — not sound calibrated as having left a speaker. Between the two sit a
    device buffer, a driver and whatever the hardware does with it, none of
    which this reports. A transport may schedule against this and must not
    call it heard time.
    """

    state: str = UNKNOWN
    error: str = ""
    processed_usecs: int | None = None
    bytes_free: int | None = None
    buffer_size: int | None = None

    @property
    def usable(self) -> bool:
        """Whether the backend answered at all this time."""
        return self.state != UNKNOWN

    @property
    def room_known(self) -> bool:
        """Whether `bytes_free` means anything in the state reported."""
        return self.bytes_free is not None and self.state in BYTES_FREE_STATES


@dataclass(frozen=True)
class DeviceFormat:
    """The one format this adapter offers, matching what the stream renders."""

    rate: int = OUTPUT_RATE
    channels: int = OUTPUT_CHANNELS

    def __post_init__(self) -> None:
        if self.rate <= 0 or self.channels <= 0:
            raise ValueError("an audio format needs a positive rate and channels")


class DeviceUnavailable(RuntimeError):
    """There is no usable audio output, or the sink refused to start."""


def qt_sink_factory(fmt: DeviceFormat = DeviceFormat()) -> Sink:
    """Build a real `QAudioSink` for the default output.

    Imported inside the function on purpose. Nothing is loaded, and no device
    is looked at, until somebody actually asks for output — so importing this
    module on a machine or a CI runner with no audio costs nothing.

    Uses only what `PySide6>=6.5` provides: `QAudioFormat.SampleFormat.Float`
    and `QMediaDevices.defaultAudioOutput()` both date from the 6.0 rewrite.
    """
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

    device = QMediaDevices.defaultAudioOutput()
    if device is None or device.isNull():
        raise DeviceUnavailable("this machine has no audio output")
    wanted = QAudioFormat()
    wanted.setSampleRate(fmt.rate)
    wanted.setChannelCount(fmt.channels)
    wanted.setSampleFormat(QAudioFormat.SampleFormat.Float)
    if not device.isFormatSupported(wanted):
        raise DeviceUnavailable(
            f"the audio output does not accept {fmt.rate} Hz float stereo")
    return QAudioSink(device, wanted)


def _non_negative(value) -> int | None:
    """An integer the backend reported, or None when it did not answer."""
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def qt_state_name(state) -> str:
    """One of our plain names for a `QAudio.State`, or `UNKNOWN`.

    Matched on the enum's own name rather than its value, because the numbers
    are not part of the documented contract and an unrecognised state has to
    stay reportable rather than be forced into one of ours.
    """
    name = getattr(state, "name", None) or str(state)
    lowered = str(name).lower()
    for known in (ACTIVE, IDLE, SUSPENDED, STOPPED):
        if known in lowered:
            return known
    return UNKNOWN


def block_bytes(block: PcmBlock) -> bytes:
    """The monitored rendering of one block, as the device wants it.

    `monitored`, never `planned`: the first is what should be heard and already
    has the listening level in it, the second is what the export will contain.
    Reading `planned` here would play the export gain; writing to either would
    change what is exported. This only reads, and only the one of them.
    """
    return array.array("f", block.monitored).tobytes()


class AudioOutput:
    """Hands rendered blocks to a sink, quietly and in bounded amounts."""

    def __init__(self, *, sink_factory: Callable[[], Sink] | None = None,
                 fmt: DeviceFormat = DeviceFormat(),
                 max_queued_bytes: int = MAX_QUEUED_BYTES) -> None:
        self._make_sink = sink_factory or (lambda: qt_sink_factory(fmt))
        self._fmt = fmt
        self._max_queued = max(FRAME_BYTES, int(max_queued_bytes))
        self._sink: Sink | None = None
        self._device = None
        self._pending = bytearray()
        self._generation = 0
        self._paused = True
        self._failure: str = ""
        # Bytes handed to the device since the last anchor. `processedUSecs`
        # counts from the sink's own start, so both are re-anchored together
        # by start and by every fence — comparing a fresh backend count with
        # a submitted total from before a reset invents a gap that never
        # existed, and can make it negative.
        self._submitted = 0

    # -- state -----------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._sink is not None and not self._paused

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def failure(self) -> str:
        """Why output stopped, or empty. Never raised at the caller."""
        return self._failure

    @property
    def queued_bytes(self) -> int:
        return len(self._pending)

    @property
    def generation(self) -> int:
        return self._generation

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        """Open the sink and stay silent.

        Started, not playing. Everything upstream begins paused and muted, and
        an adapter that made a sound the moment it was built would be the one
        part of this that surprises somebody.

        The sink's own volume is left where it is, at unity. The listening
        level is already rendered into `monitored` by the stream, and setting
        it here as well would square it — the quiet end of the control would
        stop doing anything long before it reached the bottom.
        """
        if self._sink is not None:
            return
        try:
            sink = self._make_sink()
            device = sink.start()
        except DeviceUnavailable as exc:
            self._failure = str(exc)
            raise
        except Exception as exc:  # a backend can fail in its own ways
            self._failure = f"audio output could not start: {exc}"
            raise DeviceUnavailable(self._failure) from exc
        self._sink = sink
        self._device = device
        self._paused = True
        self._failure = ""
        self._submitted = 0

    def resume(self) -> None:
        if self._sink is None or self._failure:
            return
        self._paused = False
        self._sink.resume()

    def pause(self) -> None:
        """Stop consuming, and fence everything already sent.

        Clearing the queue is not enough. `QAudioSink.suspend()` keeps the
        audio it has already been handed and plays it on resume, so dropping
        only what this adapter still holds leaves a fifth of a second of sound
        from before the pause waiting to be heard after it.
        """
        if self._sink is None:
            return
        self._paused = True
        self._fence()
        # Read again: a sink that failed while clearing has already been
        # released by `_fence`, and suspending what is no longer there turns a
        # device fault into an AttributeError out of an ordinary pause.
        sink = self._sink
        if sink is not None:
            sink.suspend()

    def stop(self) -> None:
        """Release the sink, quietly and without waiting.

        `reset()` first, deliberately. Qt documents that `stop()` may play out
        what the backend still holds before returning, and on Linux and macOS
        that drain happens synchronously — on the UI thread, that is a window
        that will not close while half a second of music finishes. Dropping
        the buffer first makes the stop immediate and silent, which is what
        closing a window means.
        """
        sink, self._sink = self._sink, None
        self._device = None
        self._pending.clear()
        self._submitted = 0
        self._paused = True
        if sink is None:
            return
        try:
            sink.reset()
        except Exception:
            # Nothing to salvage: we are closing either way, and a backend
            # that cannot drop its buffer must not stop us releasing it.
            pass
        try:
            sink.stop()
        except Exception:
            pass

    # -- what the backend says about itself ------------------------------------

    @property
    def submitted_bytes(self) -> int:
        """Bytes the device accepted since the last anchor.

        What was *handed over*. Not what was played, and not what was heard.
        """
        return self._submitted

    def observe(self) -> DeviceReport:
        """Ask the backend how it is doing. Never raises at the caller.

        A backend that cannot answer gives `UNKNOWN`, which is a state a
        transport can act on — pausing and saying so — rather than a crash in
        the middle of playback. Reporting failure is itself a thing that can
        happen, so it is a value here and not an exception.
        """
        sink = self._sink
        if sink is None:
            return DeviceReport(state=STOPPED)
        try:
            state = qt_state_name(sink.state())
            error = sink.error()
            report = DeviceReport(
                state=state,
                error="" if error is None else str(error),
                processed_usecs=_non_negative(sink.processedUSecs()),
                bytes_free=_non_negative(sink.bytesFree()),
                buffer_size=_non_negative(sink.bufferSize()),
            )
        except Exception:
            return DeviceReport(state=UNKNOWN)
        return report

    def processed_bytes(self, report: DeviceReport | None = None) -> int | None:
        """The backend's processed count, in bytes of this format.

        `None` when the backend did not answer. Derived from its own microsecond
        count, so it inherits exactly the same limit: this is data the backend
        says it has worked through, not sound anybody has heard.
        """
        report = self.observe() if report is None else report
        if report.processed_usecs is None:
            return None
        frames = report.processed_usecs * self._fmt.rate // 1_000_000
        return frames * FRAME_BYTES

    def unplayed_bytes(self, report: DeviceReport | None = None) -> int | None:
        """Submitted but not yet processed, as the backend accounts for it.

        Clamped at zero rather than allowed to go negative. The two counts come
        from different places and are re-anchored together, but a backend that
        rounds its microseconds up can still report having processed a shade
        more than we handed it; a negative gap there is an artefact, not sound
        arriving before it was sent.
        """
        processed = self.processed_bytes(report)
        if processed is None:
            return None
        return max(0, self._submitted - processed)

    def starvation(self, report: DeviceReport | None = None) -> str:
        """Why the device has nothing to play, told apart rather than guessed.

        Three different things look alike from a distance and mean different
        work:

        - `"ended"` — the backend went idle with nothing left unplayed. The
          material finished. Nobody is late.
        - `"starved"` — the backend has room and is out of material while we
          are still meant to be feeding it, and our own queue is empty too.
          That is us being late.
        - `"backend"` — the backend reports an error of its own, which is
          neither of the above and is not fixed by feeding it faster.

        Empty when none of them applies, including when the backend did not
        answer — an unknown state is reported through `observe`, and guessing a
        cause from a non-answer is how a transport ends up blaming the wrong
        thing.
        """
        report = self.observe() if report is None else report
        if report.error:
            return "backend"
        if not report.usable:
            return ""
        unplayed = self.unplayed_bytes(report)
        if report.state == IDLE and not self._pending:
            return "ended" if unplayed == 0 else "starved"
        if (self.running and not self._pending and report.room_known
                and report.bytes_free > 0 and unplayed == 0):
            return "starved"
        return ""

    # -- sound -----------------------------------------------------------------

    def reset(self, generation: int) -> None:
        """A new generation. Everything queued belongs to the old one.

        After a seek or a target change the queued bytes are sound from before
        the change. Playing them out first is how a scrub ends up half a second
        behind what the picture is showing.
        """
        if type(generation) is not int or generation < 0:
            raise ValueError("a generation is a non-negative integer")
        self._generation = generation
        self._fence()

    def present(self, block: PcmBlock) -> int:
        """Queue one block's monitored rendering. Returns the bytes taken.

        A block from an older generation is dropped rather than queued, and a
        queue already at its bound takes nothing rather than growing: the bound
        is the whole point, because every byte past it is sound that still has
        to play before a stop can be heard.
        """
        if not isinstance(block, PcmBlock):
            raise TypeError("present needs a PcmBlock")
        if block.generation != self._generation:
            return 0
        if self._sink is None or self._failure:
            return 0
        room = self._max_queued - len(self._pending)
        if room <= 0:
            return 0
        raw = block_bytes(block)
        taken = (len(raw) // FRAME_BYTES) * FRAME_BYTES
        taken = min(taken, (room // FRAME_BYTES) * FRAME_BYTES)
        if taken <= 0:
            return 0
        self._pending += raw[:taken]
        return taken

    def pump(self) -> int:
        """Write what the device will take. Returns the bytes it accepted.

        A device accepts what it has room for and no more, so a short write is
        ordinary rather than an error: the remainder stays queued for the next
        call. Treating a short write as a failure would drop sound every time
        the buffer happened to be full.
        """
        if self._device is None or self._paused or not self._pending:
            return 0
        try:
            written = self._device.write(bytes(self._pending))
        except Exception as exc:
            self._fail(f"audio output failed while writing: {exc}")
            return 0
        if written is None or written < 0:
            self._fail("the audio device refused the buffer")
            return 0
        written = min(int(written), len(self._pending))
        # Exactly what the device took, whether or not that lands on a frame
        # boundary. Rounding down to a whole frame and dropping only that much
        # left the rounded-away tail still queued — and the device already had
        # it, so the next write sent those bytes a second time and the sound
        # gained a few duplicated samples at every short write.
        #
        # Alignment survives because a QIODevice is a byte stream: continuing
        # from exactly where it stopped hands over a continuous sequence, and
        # the frame boundaries are wherever they always were. It is the
        # duplication that breaks it, not the offset.
        if written:
            del self._pending[:written]
            self._submitted += written
        return written

    def _fence(self) -> None:
        """Drop queued sound here *and* whatever the device is still holding.

        `reset()` is the only thing that discards a sink's own buffer, and it
        leaves the sink stopped, so the device handle has to be taken again
        afterwards. That is a real cost, which is why this is called when the
        sound is genuinely stale — a pause or a new generation — and not on an
        ordinary write.
        """
        self._pending.clear()
        self._submitted = 0
        sink = self._sink
        if sink is None:
            return
        try:
            sink.reset()
            self._device = sink.start()
        except Exception as exc:
            self._fail(f"audio output failed while clearing: {exc}")

    def _fail(self, why: str) -> None:
        """Go quiet and remember why. A caller polls `failure`; nothing raises
        out of the write path, because the transport above has a picture to
        keep running."""
        self._failure = why
        self._pending.clear()
        self._paused = True
        sink, self._sink = self._sink, None
        self._device = None
        if sink is not None:
            try:
                sink.stop()
            except Exception:
                pass
