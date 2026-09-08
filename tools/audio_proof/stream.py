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

"""Bounded worker and captured-PCM sink. Deliberately no hardware stream API."""
from array import array
from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass, replace
import math
import queue
import sys
import threading
import time
import wave

from .model import AudioPlan

RATE = 48000
CHANNELS = 2
BLOCK = 480                       # 10 ms; this is not a hardware latency claim


class PcmStem:
    """Normalized PCM16 WAV, at most four decoded blocks, never whole-file RAM."""
    def __init__(self, path):
        self.file = wave.open(str(path), "rb")
        if (self.file.getframerate(), self.file.getnchannels(),
                self.file.getsampwidth(), self.file.getcomptype()) != (RATE, 2, 2, "NONE"):
            self.file.close()
            raise ValueError("stem must be normalized stereo PCM16 at 48 kHz")
        self.frames = self.file.getnframes()
        if self.frames <= 0:
            self.file.close()
            raise ValueError("empty stem")
        self.cache = OrderedDict()
        self.high_water = 0
        self.reads = 0

    def at(self, sample):
        if not 0 <= sample < self.frames:
            raise ValueError("source sample outside asserted stem")
        block = sample // BLOCK
        data = self.cache.get(block)
        if data is None:
            self.file.setpos(block * BLOCK)
            raw = self.file.readframes(BLOCK)
            wanted = min(BLOCK, self.frames - block * BLOCK) * 4
            if len(raw) != wanted:
                raise ValueError("truncated PCM stem")
            data = array("h")
            data.frombytes(raw)
            if sys.byteorder != "little":
                data.byteswap()
            self.reads += 1
            self.cache[block] = data
            if len(self.cache) > 4:
                self.cache.popitem(last=False)
            self.high_water = max(self.high_water, len(self.cache))
        self.cache.move_to_end(block)
        offset = (sample % BLOCK) * 2
        return data[offset] / 32768, data[offset + 1] / 32768

    def close(self):
        self.file.close()


@dataclass(frozen=True)
class Levels:
    music: float = 1.0
    dvr: float = 0.25
    monitor: float = 0.25
    muted: bool = True

    def __post_init__(self):
        if any(not math.isfinite(v) or not 0 <= v <= 1
               for v in (self.music, self.dvr, self.monitor)):
            raise ValueError("levels must be finite fractions from zero to one")

    def pair(self, include_dvr=True):
        dvr = self.dvr if include_dvr else 0.0
        divisor = max(1.0, self.music + dvr)
        return self.music / divisor, dvr / divisor


@dataclass(frozen=True)
class StemBlock:
    generation: int
    start: int
    dvr: array
    music: array


@dataclass(frozen=True)
class CapturedBlock:
    generation: int
    revision: int
    start: int
    mix: array
    monitored: array
    control_latency_ms: float


class Cancelled(RuntimeError):
    pass


class Buffering(RuntimeError):
    pass


class ProofStream:
    """One bounded decoder owner; mix parameters applied after queued stems.

    This is a fake-sink worker proof, NOT a real-time callback implementation.
    The caller drains blocks, optionally paced, and may write captured PCM.
    """
    def __init__(self, plan: AudioPlan, source, music, *, start=0,
                 levels=Levels(), capacity=4):
        if music.frames != plan.music_samples:
            raise ValueError("music fixture duration disagrees with plan")
        if capacity < 1 or not 0 <= start < plan.timeline.duration:
            raise ValueError("invalid capacity or start")
        self.plan, self.source, self.music = plan, source, music
        self.cursor = start
        self.generation = 1
        self.revision = 0
        self.levels = levels
        self.changed_at = time.perf_counter()
        self.include_dvr = any(span.source_audio for span in plan.timeline.spans)
        self.previous_pair = levels.pair(self.include_dvr)
        self.blocks = queue.Queue(maxsize=capacity)
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.ended = None
        self.error = None
        self.high_water = 0
        self.discarded = 0
        self.worker_cpu_s = 0.0
        self.thread = threading.Thread(target=self._produce, name="audio-proof")

    def start(self):
        self.thread.start()

    def set_levels(self, **values):
        with self.lock:
            self.levels = replace(self.levels, **values)
            self.revision += 1
            self.changed_at = time.perf_counter()
            return self.revision

    def reprime(self, plan, start):
        if not 0 <= start < plan.timeline.duration or plan.music_samples != self.music.frames:
            raise ValueError("invalid new timeline/music")
        with self.lock:
            self.generation += 1
            self.plan, self.cursor = plan, start
            self.ended, self.error = None, None
            self.include_dvr = any(span.source_audio for span in plan.timeline.spans)
            self.previous_pair = self.levels.pair(self.include_dvr)
            self._clear()
            return self.generation

    def _clear(self):
        while True:
            try:
                self.blocks.get_nowait()
                self.discarded += 1
            except queue.Empty:
                return

    def _decode(self, plan, generation, start, count):
        dvr, music = array("f"), array("f")
        # Resolve spans per block/boundary, not Fraction arithmetic per sample.
        # The latter consumed more than real time in the first measured proof.
        span_end = start
        for pos in range(start, start + count):
            if self.cancel.is_set():
                raise Cancelled()
            if pos >= span_end:
                index = bisect_right(plan.timeline.edges, pos)
                span = plan.timeline.spans[index]
                offset = plan.timeline.edges[index - 1] if index else 0
                span_end = plan.timeline.edges[index]
            # Audio-bearing spans are 1x by the explicit current proof policy.
            source_pos = span.start + pos - offset
            dvr.extend(self.source.at(source_pos) if span.source_audio else (0, 0))
            music_pos, envelope = plan.music_at(pos)
            sample = self.music.at(music_pos) if music_pos is not None else (0, 0)
            music.extend(value * envelope for value in sample)
        return StemBlock(generation, start, dvr, music)

    def _produce(self):
        while not self.cancel.is_set():
            with self.lock:
                plan, generation, start = self.plan, self.generation, self.cursor
                count = min(BLOCK, plan.timeline.duration - start)
                if count > 0 and self.error is None:
                    self.cursor += count
                else:
                    self.ended = generation
            if count <= 0 or self.error is not None:
                self.cancel.wait(0.002)
                continue
            cpu = time.thread_time()
            try:
                block = self._decode(plan, generation, start, count)
            except Cancelled:
                return
            except Exception as exc:
                with self.lock:
                    if generation == self.generation:
                        self.error = str(exc)
                        self._clear()
                continue
            finally:
                self.worker_cpu_s += time.thread_time() - cpu
            while not self.cancel.is_set():
                with self.lock:
                    if generation != self.generation:
                        self.discarded += 1
                        break
                    try:
                        self.blocks.put_nowait(block)
                        self.high_water = max(self.high_water, self.blocks.qsize())
                        break
                    except queue.Full:
                        pass
                self.cancel.wait(0.002)

    def pull(self, timeout=1.0):
        deadline = time.perf_counter() + timeout
        while True:
            with self.lock:
                if self.cancel.is_set():
                    raise Cancelled("proof stopped")
                if self.error is not None:
                    raise ValueError(self.error)
                try:
                    block = self.blocks.get_nowait()
                except queue.Empty:
                    block = None
                if block is not None and block.generation == self.generation:
                    levels, revision = self.levels, self.revision
                    changed_at = self.changed_at
                    old_pair, pair = self.previous_pair, levels.pair(self.include_dvr)
                    self.previous_pair = pair
                    break
                if self.ended == self.generation and self.blocks.empty():
                    return None
            if time.perf_counter() >= deadline:
                raise Buffering("no valid PCM ready; not planned silence")
            self.cancel.wait(0.001)
        mix, monitored = array("f"), array("f")
        frames = len(block.dvr) // CHANNELS
        for index, (dvr, music) in enumerate(zip(block.dvr, block.music)):
            # A ten-ms transition for changed levels is not export automation.
            fraction = min(1.0, (index // CHANNELS + 1) / BLOCK)
            mg = old_pair[0] + (pair[0] - old_pair[0]) * fraction
            dg = old_pair[1] + (pair[1] - old_pair[1]) * fraction
            value = dvr * dg + music * mg
            mix.append(value)
            monitored.append(0.0 if levels.muted else value * levels.monitor)
        # A control may have changed during mixing. Never return old-card audio.
        with self.lock:
            if self.cancel.is_set():
                raise Cancelled("stopped during mix")
            if block.generation != self.generation:
                raise Buffering("generation changed during mix")
            if self.levels.muted:
                monitored = array("f", [0.0]) * (frames * CHANNELS)
        return CapturedBlock(block.generation, revision, block.start, mix, monitored,
                             1000 * (time.perf_counter() - changed_at))

    def stop(self):
        self.cancel.set()
        if self.thread.ident is not None:
            self.thread.join(2)
        if self.thread.is_alive():
            raise RuntimeError("decoder did not settle within two seconds")
        with self.lock:
            self._clear()
