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

"""Bounded, device-independent PCM production for a future live preview.

The stream consumes readers that have already normalised their input to
48 kHz, stereo, interleaved floating-point samples.  It deliberately owns no
decoder, audio device, Qt object, or FFmpeg process.  The accepted proof maps
the shipped non-ASIO PortAudio DLL to its source; selection/hash enforcement,
complete notices, and physical-device acceptance remain packaging gates.

Stopping is cooperative.  ``request_stop`` never joins the worker and asks
each reader to unblock an outstanding read.  ``wait_stopped`` is the separate,
potentially blocking operation for a non-UI owner.  An arbitrary reader that
ignores that contract cannot be made promptly stoppable by this class.
"""

from __future__ import annotations

import math
import queue
import threading
from array import array
from collections.abc import Iterator
from dataclasses import dataclass, replace
from enum import Enum
from fractions import Fraction
from typing import Callable, Protocol, Sequence

from .audio_plan import (
    AudioMode,
    OUTPUT_CHANNELS,
    OUTPUT_RATE,
    OutputAudioPlan,
    SampleSpan,
    ShortTrackPolicy,
    round_samples,
)


BLOCK_FRAMES = 480
QUEUE_CAPACITY = 4
FLOAT_BYTES = 4
# A queued PcmBlock retains both the planned and monitored stereo buffers.
# This payload bound excludes one block being rendered by the worker, reader or
# decoder caches owned by adapters, and Python Queue/PcmBlock object overhead.
MAX_QUEUED_PCM_BYTES = (
    QUEUE_CAPACITY * BLOCK_FRAMES * OUTPUT_CHANNELS * FLOAT_BYTES * 2
)


class PcmReader(Protocol):
    """A random-access reader in the normalised 48 kHz stereo clock.

    ``read`` must return exactly ``frames * 2`` interleaved samples or raise.
    It should poll ``cancelled`` during long work.  ``request_stop`` must be a
    prompt, nonblocking request which also unblocks a currently blocked read.
    The worker calls ``close`` after it leaves its production loop.
    """

    @property
    def frames(self) -> int: ...

    def read(
        self,
        start: int,
        frames: int,
        cancelled: Callable[[], bool],
    ) -> Sequence[float]: ...

    def request_stop(self) -> None: ...

    def close(self) -> None: ...


class StreamState(str, Enum):
    READY = "ready"
    PAUSED = "paused"
    RUNNING = "running"
    EOF = "eof"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class Buffering(Exception):
    """No block is currently available, but the stream can still progress."""


class Paused(Buffering):
    """A pull was attempted while output consumption was paused."""


class StreamFailed(RuntimeError):
    """The producer failed before completing the current generation."""


@dataclass(frozen=True)
class MonitorState:
    level: float = 0.25
    muted: bool = True

    def __post_init__(self) -> None:
        level = float(self.level)
        if not math.isfinite(level) or not 0.0 <= level <= 1.0:
            raise ValueError("monitor level must be a finite value from zero to one")
        object.__setattr__(self, "level", level)
        object.__setattr__(self, "muted", bool(self.muted))


@dataclass(frozen=True)
class PcmBuffer(Sequence[float]):
    """Immutable, defensively copied float32 PCM storage."""

    _data: bytes

    @classmethod
    def from_samples(cls, samples: Sequence[float]) -> PcmBuffer:
        values = tuple(float(value) for value in samples)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("PCM buffers cannot contain non-finite samples")
        return cls(array("f", values).tobytes())

    @property
    def nbytes(self) -> int:
        return len(self._data)

    @property
    def view(self) -> memoryview:
        """A read-only float32 view suitable for a later sink adapter."""
        return memoryview(self._data).cast("f")

    def __len__(self) -> int:
        return len(self._data) // FLOAT_BYTES

    def __getitem__(self, index):
        found = self.view[index]
        return tuple(found) if isinstance(index, slice) else float(found)

    def __iter__(self) -> Iterator[float]:
        return (float(value) for value in self.view)


@dataclass(frozen=True)
class LiveAudioMapping:
    """One output plan mapped to a normalised source-reader interval."""

    audio: OutputAudioPlan
    source: SampleSpan

    def __post_init__(self) -> None:
        if self.audio.output.rate != OUTPUT_RATE or self.source.rate != OUTPUT_RATE:
            raise ValueError("live readers use the normalised 48 kHz sample clock")
        if self.audio.output.samples != self.source.samples:
            raise ValueError("source and output intervals must have equal duration")

    @property
    def music_origin(self) -> int | None:
        """Selected passage start converted once to the reader's 48 kHz clock."""
        passage = self.audio.passage
        if passage is None:
            return None
        return round_samples(Fraction(passage.start * OUTPUT_RATE, passage.rate))


@dataclass(frozen=True)
class PcmBlock:
    """Immutable output plus the monitor rendering captured for one pull."""

    generation: int
    monitor_revision: int
    output_start: int
    frames: int
    planned: PcmBuffer
    monitored: PcmBuffer

    def __post_init__(self) -> None:
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if type(self.monitor_revision) is not int or self.monitor_revision < 0:
            raise ValueError("monitor revision must be a non-negative integer")
        if type(self.output_start) is not int or self.output_start < 0:
            raise ValueError("output start must be a non-negative integer")
        if type(self.frames) is not int or self.frames <= 0:
            raise ValueError("a PCM block must contain frames")
        planned = (self.planned if isinstance(self.planned, PcmBuffer)
                   else PcmBuffer.from_samples(self.planned))
        monitored = (self.monitored if isinstance(self.monitored, PcmBuffer)
                     else PcmBuffer.from_samples(self.monitored))
        expected = self.frames * OUTPUT_CHANNELS
        if len(planned) != expected or len(monitored) != expected:
            raise ValueError("PCM buffers must contain interleaved stereo frames")
        object.__setattr__(self, "planned", planned)
        object.__setattr__(self, "monitored", monitored)


class AudioStream:
    """Produce at most four immutable 10 ms PCM blocks ahead of consumption."""

    def __init__(
        self,
        mapping: LiveAudioMapping,
        *,
        source_reader: PcmReader | None = None,
        music_reader: PcmReader | None = None,
        monitor: MonitorState = MonitorState(),
    ) -> None:
        self._validate_readers(mapping, source_reader, music_reader)
        self._mapping = mapping
        self._source_reader = source_reader
        self._music_reader = music_reader
        self._monitor = monitor
        self._monitor_revision = 0
        self._generation = 0
        self._cursor = 0
        self._paused = True
        self._started = False
        self._ended_generation: int | None = None
        self._failure: BaseException | None = None
        self._blocks: queue.Queue[PcmBlock] = queue.Queue(QUEUE_CAPACITY)
        self._cancel = threading.Event()
        self._stopped = threading.Event()
        self._settled = False
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._worker: threading.Thread | None = None

    @staticmethod
    def _validate_readers(
        mapping: LiveAudioMapping,
        source_reader: PcmReader | None,
        music_reader: PcmReader | None,
    ) -> None:
        plan = mapping.audio
        needs_source = plan.source_has_audio and plan.mode in (
            AudioMode.ORIGINAL, AudioMode.MIX)
        needs_music = plan.mode in (AudioMode.REPLACE, AudioMode.MIX)
        if needs_source and source_reader is None:
            raise ValueError("this audio plan needs a normalised source reader")
        if needs_music and music_reader is None:
            raise ValueError("this audio plan needs a normalised music reader")
        if source_reader is not None and mapping.source.end > source_reader.frames:
            raise ValueError("source mapping extends beyond the normalised reader")
        if needs_music:
            origin = mapping.music_origin
            if origin is None or origin + plan.music_samples > music_reader.frames:
                raise ValueError("music passage extends beyond the normalised reader")

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def monitor_revision(self) -> int:
        with self._lock:
            return self._monitor_revision

    @property
    def state(self) -> StreamState:
        with self._lock:
            if self._failure is not None:
                return StreamState.FAILED
            if self._cancel.is_set():
                return (StreamState.STOPPED if self._stopped.is_set()
                        else StreamState.STOPPING)
            if not self._started:
                return StreamState.READY
            if self._paused:
                return StreamState.PAUSED
            if (self._ended_generation == self._generation
                    and self._blocks.empty()):
                return StreamState.EOF
            return StreamState.RUNNING

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def at_eof(self) -> bool:
        return self.state is StreamState.EOF

    @property
    def queued_blocks(self) -> int:
        with self._lock:
            return self._blocks.qsize()

    @property
    def queued_pcm_bytes(self) -> int:
        """Exact queued PCM payload; see ``MAX_QUEUED_PCM_BYTES`` exclusions."""
        with self._lock:
            blocks = self._blocks
        with blocks.mutex:
            return sum(
                block.planned.nbytes + block.monitored.nbytes
                for block in blocks.queue)

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            if self._cancel.is_set():
                raise RuntimeError("a stopped audio stream cannot be started")
            self._started = True
            self._worker = threading.Thread(
                target=self._produce, name="flightdvr-audio-stream", daemon=True)
            self._worker.start()

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            if not self._started:
                raise RuntimeError("start the audio stream before resuming it")
            if self._cancel.is_set():
                raise RuntimeError("a stopped audio stream cannot be resumed")
            self._paused = False

    def set_monitor(
        self,
        *,
        level: float | None = None,
        muted: bool | None = None,
    ) -> MonitorState:
        with self._lock:
            changed = replace(
                self._monitor,
                level=self._monitor.level if level is None else level,
                muted=self._monitor.muted if muted is None else muted,
            )
            if changed != self._monitor:
                self._monitor = changed
                self._monitor_revision += 1
            return self._monitor

    def pull(self) -> PcmBlock | None:
        """Return the next block, ``None`` at EOF, or raise while buffering."""
        with self._lock:
            if self._paused:
                raise Paused("audio output is paused")
            if self._failure is not None:
                raise StreamFailed("audio production failed") from self._failure
            if self._cancel.is_set():
                raise Buffering("audio output is stopping")
            blocks = self._blocks
            generation = self._generation
            monitor = self._monitor
            revision = self._monitor_revision
            try:
                captured = blocks.get_nowait()
            except queue.Empty:
                if self._ended_generation == generation:
                    return None
                raise Buffering("audio block is not ready") from None
        # Monitor state belongs to the pull, not to the earlier queued render.
        # Re-rendering here makes mute/level changes affect the very next pull.
        monitored = self._apply_monitor(captured.planned, monitor)
        return PcmBlock(
            captured.generation,
            revision,
            captured.output_start,
            captured.frames,
            captured.planned,
            monitored,
        )

    def reprime(self, output_sample: int) -> int:
        """Fence queued/in-flight work and start a new generation at a sample."""
        with self._changed:
            if self._failure is not None:
                raise RuntimeError("a failed audio stream cannot be reprimed")
            if self._cancel.is_set():
                raise RuntimeError("a stopped audio stream cannot be reprimed")
            output = self._mapping.audio.output
            if type(output_sample) is not int or not output.start <= output_sample < output.end:
                raise ValueError("reprime position is outside the output interval")
            self._generation += 1
            self._cursor = output_sample - output.start
            self._ended_generation = None
            self._blocks = queue.Queue(QUEUE_CAPACITY)
            self._changed.notify_all()
            return self._generation

    def restart(self) -> int:
        with self._lock:
            self._paused = True
            if not self._monitor.muted:
                self._monitor = replace(self._monitor, muted=True)
                self._monitor_revision += 1
            return self.reprime(self._mapping.audio.output.start)

    def is_current(self, block: PcmBlock) -> bool:
        """Generation fence for a sink immediately before it submits a block."""
        with self._lock:
            return not self._cancel.is_set() and block.generation == self._generation

    def request_stop(self) -> None:
        """Request cancellation without joining the worker."""
        with self._changed:
            if self._cancel.is_set():
                return
            self._cancel.set()
            self._changed.notify_all()
        seen: set[int] = set()
        for reader in (self._source_reader, self._music_reader):
            if reader is not None and id(reader) not in seen:
                seen.add(id(reader))
                reader.request_stop()

    def wait_stopped(self, timeout: float | None = None) -> bool:
        """Wait for cooperative shutdown; callers must keep this off the UI thread."""
        worker = self._worker
        if worker is None:
            if self._cancel.is_set():
                self._settle()
            return True
        worker.join(timeout)
        return not worker.is_alive()

    def _produce(self) -> None:
        try:
            while not self._cancel.is_set():
                with self._changed:
                    generation = self._generation
                    cursor = self._cursor
                    mapping = self._mapping
                    blocks = self._blocks
                    if cursor >= mapping.audio.output.samples:
                        self._ended_generation = generation
                        self._changed.wait_for(
                            lambda: self._cancel.is_set()
                            or self._generation != generation)
                        continue
                    frames = min(BLOCK_FRAMES, mapping.audio.output.samples - cursor)
                    monitor = self._monitor
                    revision = self._monitor_revision
                planned = self._render(mapping, cursor, frames)
                captured = PcmBlock(
                    generation,
                    revision,
                    mapping.audio.output.start + cursor,
                    frames,
                    planned,
                    self._apply_monitor(planned, monitor),
                )
                while not self._cancel.is_set():
                    with self._lock:
                        if generation != self._generation or blocks is not self._blocks:
                            break
                    try:
                        blocks.put(captured, timeout=0.02)
                    except queue.Full:
                        continue
                    with self._changed:
                        if generation == self._generation and blocks is self._blocks:
                            self._cursor += frames
                            self._changed.notify_all()
                    break
        except BaseException as exc:
            if not self._cancel.is_set():
                with self._lock:
                    self._failure = exc
        finally:
            self._settle()

    def _settle(self) -> None:
        """Release owned buffers/readers once, on the blocking cleanup side."""
        with self._lock:
            if self._settled:
                return
            self._settled = True
            blocks = self._blocks
        while True:
            try:
                blocks.get_nowait()
            except queue.Empty:
                break
            else:
                blocks.task_done()
        seen: set[int] = set()
        for reader in (self._source_reader, self._music_reader):
            if reader is not None and id(reader) not in seen:
                seen.add(id(reader))
                try:
                    reader.close()
                except Exception:
                    pass
        self._stopped.set()

    def _render(
        self,
        mapping: LiveAudioMapping,
        cursor: int,
        frames: int,
    ) -> tuple[float, ...]:
        plan = mapping.audio
        source = (0.0,) * (frames * OUTPUT_CHANNELS)
        if plan.source_has_audio and plan.mode in (AudioMode.ORIGINAL, AudioMode.MIX):
            source = self._read_exact(
                self._source_reader, mapping.source.start + cursor, frames)
        music = (0.0,) * (frames * OUTPUT_CHANNELS)
        if plan.mode in (AudioMode.REPLACE, AudioMode.MIX):
            music = self._read_music(mapping, cursor, frames)
        output: list[float] = []
        for frame in range(frames):
            at = cursor + frame
            music_gain = float(plan.music_gain * plan.envelope(at))
            source_gain = float(plan.dvr_gain)
            offset = frame * OUTPUT_CHANNELS
            for channel in range(OUTPUT_CHANNELS):
                output.append(
                    source[offset + channel] * source_gain
                    + music[offset + channel] * music_gain)
        return tuple(output)

    def _read_music(
        self,
        mapping: LiveAudioMapping,
        cursor: int,
        frames: int,
    ) -> tuple[float, ...]:
        plan = mapping.audio
        origin = mapping.music_origin
        assert origin is not None
        result: list[float] = []
        position = cursor
        remaining = frames
        while remaining:
            relative = plan.music_position(position)
            if relative is None:
                result.extend((0.0,) * (remaining * OUTPUT_CHANNELS))
                break
            if plan.short_track is ShortTrackPolicy.LOOP:
                count = min(remaining, plan.music_samples - relative)
            else:
                count = min(remaining, plan.audible_samples - position)
            result.extend(self._read_exact(self._music_reader, origin + relative, count))
            position += count
            remaining -= count
        return tuple(result)

    def _read_exact(
        self,
        reader: PcmReader | None,
        start: int,
        frames: int,
    ) -> tuple[float, ...]:
        assert reader is not None
        values = tuple(float(value) for value in reader.read(
            start, frames, self._cancel.is_set))
        if len(values) != frames * OUTPUT_CHANNELS:
            raise ValueError("PCM reader returned a truncated stereo block")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("PCM reader returned a non-finite sample")
        return values

    @staticmethod
    def _apply_monitor(
        planned: Sequence[float],
        monitor: MonitorState,
    ) -> tuple[float, ...]:
        gain = 0.0 if monitor.muted else monitor.level
        return tuple(value * gain for value in planned)
