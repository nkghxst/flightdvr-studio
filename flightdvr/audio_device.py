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


class Sink(Protocol):
    """What this adapter needs from `QAudioSink`, and no more."""

    def start(self): ...
    def suspend(self) -> None: ...
    def resume(self) -> None: ...
    def stop(self) -> None: ...
    def setVolume(self, volume: float) -> None: ...  # noqa: N802 (Qt naming)


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

    def resume(self) -> None:
        if self._sink is None or self._failure:
            return
        self._paused = False
        self._sink.resume()

    def pause(self) -> None:
        """Stop consuming, and drop what has not been handed over yet.

        The queue goes because it is stale the moment the person stops
        listening: resuming should continue from where the transport is, not
        replay a fifth of a second recorded before the pause.
        """
        if self._sink is None:
            return
        self._paused = True
        self._pending.clear()
        self._sink.suspend()

    def stop(self) -> None:
        """Release the sink. Safe to call twice, and safe after a failure."""
        sink, self._sink = self._sink, None
        self._device = None
        self._pending.clear()
        self._paused = True
        if sink is not None:
            sink.stop()

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
        self._pending.clear()

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
        # Whole frames only. Half a frame left at the front would swap the
        # channels for everything after it.
        written -= written % FRAME_BYTES
        if written:
            del self._pending[:written]
        return written

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
