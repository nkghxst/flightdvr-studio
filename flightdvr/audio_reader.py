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

"""Cancellable local-file inputs for the bounded live-audio core.

This module opens FFmpeg and FFprobe child processes, never an audio device.
Music uses a contiguous decoded-sample clock; DVR source audio uses its media
timeline and pads only clean gaps/tail inside an explicit bound. Both become
48 kHz stereo float32 for :mod:`flightdvr.audio_stream`.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
import threading
from collections import deque
from fractions import Fraction
from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QThread, Signal

from .audio_plan import AudioAsset, OUTPUT_CHANNELS, OUTPUT_RATE, round_samples
from .audio_stream import BLOCK_FRAMES
from .media import NO_WINDOW, Tools, request_stop, stop_process


FLOAT_BYTES = 4
PCM_FRAME_BYTES = OUTPUT_CHANNELS * FLOAT_BYTES
MAX_READ_FRAMES = BLOCK_FRAMES
MAX_READ_BYTES = MAX_READ_FRAMES * PCM_FRAME_BYTES
HASH_CHUNK_BYTES = 1024 * 1024
DECODE_CHUNK_BYTES = 64 * 1024
STDERR_LINES = 30
STDERR_LINE_CHARS = 300
STDERR_READ_BYTES = 4 * 1024
STDERR_PARTIAL_BYTES = STDERR_LINE_CHARS * 4
PROBE_STDOUT_BYTES = 64 * 1024
ERROR_MESSAGE_CHARS = 300


class AudioReaderError(RuntimeError):
    """A local file could not satisfy its promised PCM reader contract."""


class AudioAssetError(ValueError):
    """A selected file could not become a validated ``AudioAsset``."""


class AudioOperationCancelled(RuntimeError):
    """Cooperative cancellation, kept distinct from a file failure."""


def _message(log: Sequence[str], fallback: str) -> str:
    for line in reversed(log):
        text = line.strip()
        if text:
            return text[:300]
    return fallback


def _drain_stderr(pipe, log: deque[str]) -> None:
    if pipe is None:
        return
    partial = b""

    def retain(raw: bytes) -> None:
        text = raw.rstrip(b"\r").decode("utf-8", "replace").strip()
        if text:
            log.append(text[-STDERR_LINE_CHARS:])

    try:
        while True:
            block = pipe.read(STDERR_READ_BYTES)
            if not block:
                break
            parts = block.split(b"\n")
            for complete in parts[:-1]:
                retain(partial + complete)
                partial = b""
            partial = (partial + parts[-1])[-STDERR_PARTIAL_BYTES:]
        if partial:
            retain(partial)
    except (OSError, ValueError):
        pass


def _drain_probe_stdout(pipe, output: bytearray,
                        overflow: threading.Event) -> None:
    """Drain all probe output while retaining only the bounded JSON prefix."""
    if pipe is None:
        return
    try:
        while True:
            block = pipe.read(STDERR_READ_BYTES)
            if not block:
                break
            room = PROBE_STDOUT_BYTES - len(output)
            if room:
                output.extend(block[:room])
            if len(block) > room:
                overflow.set()
    except (OSError, ValueError):
        pass


def _absolute(path: Path) -> Path:
    try:
        return Path(path).resolve(strict=True)
    except OSError as exc:
        raise AudioAssetError(f"music file cannot be read: {exc}") from exc


def _check_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise AudioOperationCancelled("Cancelled")


def _probe_first_audio(
    tools: Tools,
    path: Path,
    cancelled: Callable[[], bool],
    register_process: Callable[[subprocess.Popen | None], None],
) -> tuple[int, int]:
    command = [
        str(tools.ffprobe), "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels",
        "-of", "json", str(path),
    ]
    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, creationflags=NO_WINDOW,
        )
    except OSError as exc:
        raise AudioAssetError(f"could not start ffprobe: {exc}") from exc
    register_process(proc)
    stdout = bytearray()
    overflow = threading.Event()
    log: deque[str] = deque(maxlen=STDERR_LINES)
    drains = (
        threading.Thread(
            target=_drain_probe_stdout,
            args=(proc.stdout, stdout, overflow), daemon=True),
        threading.Thread(
            target=_drain_stderr, args=(proc.stderr, log), daemon=True),
    )
    for drain in drains:
        drain.start()
    try:
        while True:
            _check_cancelled(cancelled)
            try:
                code = proc.wait(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                continue
        _check_cancelled(cancelled)
    finally:
        if proc.poll() is None:
            stop_process(proc)
        for drain in drains:
            drain.join(timeout=2)
        for pipe in (proc.stdout, proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except OSError:
                pass
        for drain in drains:
            drain.join(timeout=2)
        register_process(None)
    if code != 0:
        raise AudioAssetError(_message(log, f"ffprobe stopped (code {code})"))
    if overflow.is_set():
        raise AudioAssetError("ffprobe returned too much stream metadata")
    try:
        streams = json.loads(
            stdout.decode("utf-8", "replace")).get("streams", [])
        stream = streams[0]
        rate = int(stream.get("sample_rate") or 0)
        channels = int(stream.get("channels") or 0)
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AudioAssetError("file has no readable first audio stream") from exc
    if rate <= 0 or channels <= 0:
        raise AudioAssetError("file has no usable first audio stream")
    return rate, channels


def _count_native_samples(
    tools: Tools,
    path: Path,
    rate: int,
    channels: int,
    cancelled: Callable[[], bool],
    register_process: Callable[[subprocess.Popen | None], None],
) -> int:
    command = [
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-xerror",
        "-nostdin",
        "-i", str(path), "-map", "0:a:0", "-vn", "-sn", "-dn",
        "-ar", str(rate), "-ac", str(channels), "-f", "f32le", "pipe:1",
    ]
    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, creationflags=NO_WINDOW,
        )
    except OSError as exc:
        raise AudioAssetError(f"could not start ffmpeg: {exc}") from exc
    register_process(proc)
    log: deque[str] = deque(maxlen=STDERR_LINES)
    drain = threading.Thread(
        target=_drain_stderr, args=(proc.stderr, log), daemon=True)
    drain.start()
    total = 0
    try:
        assert proc.stdout is not None
        while True:
            _check_cancelled(cancelled)
            block = proc.stdout.read(DECODE_CHUNK_BYTES)
            if not block:
                break
            total += len(block)
        code = proc.wait()
        _check_cancelled(cancelled)
    finally:
        if proc.poll() is None:
            stop_process(proc)
        drain.join(timeout=2)
        register_process(None)
    if code != 0:
        raise AudioAssetError(_message(log, f"ffmpeg stopped (code {code})"))
    frame_bytes = channels * FLOAT_BYTES
    if total == 0 or total % frame_bytes:
        raise AudioAssetError("audio decode returned an invalid sample extent")
    return total // frame_bytes


def _file_sha256(path: Path, cancelled: Callable[[], bool]) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while True:
                _check_cancelled(cancelled)
                block = source.read(HASH_CHUNK_BYTES)
                if not block:
                    break
                digest.update(block)
    except OSError as exc:
        raise AudioAssetError(f"music file cannot be read: {exc}") from exc
    _check_cancelled(cancelled)
    return digest.hexdigest()


def inspect_music_asset(
    tools: Tools,
    track: Path,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    register_process: Callable[[subprocess.Popen | None], None] = lambda proc: None,
) -> AudioAsset:
    """Validate and identify one file, for use only away from the UI thread."""
    path = _absolute(track)
    _check_cancelled(cancelled)
    try:
        before = path.stat()
    except OSError as exc:
        raise AudioAssetError(f"music file cannot be read: {exc}") from exc
    rate, channels = _probe_first_audio(
        tools, path, cancelled, register_process)
    samples = _count_native_samples(
        tools, path, rate, channels, cancelled, register_process)
    digest = _file_sha256(path, cancelled)
    try:
        after = path.stat()
    except OSError as exc:
        raise AudioAssetError(f"music file cannot be read: {exc}") from exc
    if (before.st_size, before.st_mtime_ns) != (
            after.st_size, after.st_mtime_ns):
        raise AudioAssetError("music file changed while it was being read")
    return AudioAsset(path, digest, 0, rate, channels, samples)


class MusicAssetProbe(QThread):
    """Asynchronously turn a selected path into the existing ``AudioAsset``."""

    ready = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, tools: Tools, track: Path,
                 generation: int, parent=None):
        super().__init__(parent)
        self.tools = tools
        self.track = Path(track)
        self.generation = generation
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None

    def stop(self) -> None:
        """Request cancellation and unblock a child without waiting."""
        self._cancel.set()
        with self._lock:
            proc = self._process
        request_stop(proc)

    def _register(self, proc: subprocess.Popen | None) -> None:
        with self._lock:
            self._process = proc
            stopped = self._cancel.is_set()
        if stopped:
            request_stop(proc)

    def run(self) -> None:  # noqa: D102
        try:
            asset = inspect_music_asset(
                self.tools, self.track,
                cancelled=self._cancel.is_set,
                register_process=self._register,
            )
        except AudioOperationCancelled:
            return
        except (AudioAssetError, OSError, subprocess.SubprocessError) as exc:
            with self._lock:
                if not self._cancel.is_set():
                    self.failed.emit(
                        self.generation, str(exc)[:ERROR_MESSAGE_CHARS])
            return
        with self._lock:
            if not self._cancel.is_set():
                self.ready.emit(self.generation, asset)


class FfmpegPcmReader:
    """One bounded FFmpeg pipe implementing the S3 ``PcmReader`` protocol."""

    def __init__(self, tools: Tools, path: Path, stream_index: int,
                 frames: int, *, source_timeline: bool):
        if type(stream_index) is not int or stream_index < 0:
            raise ValueError("audio stream index must be non-negative")
        if type(frames) is not int or frames <= 0:
            raise ValueError("reader extent must be a positive integer")
        self.tools = tools
        self.path = Path(path).resolve()
        self.stream_index = stream_index
        self._frames = frames
        self.source_timeline = source_timeline
        self._cursor: int | None = None
        self._process: subprocess.Popen | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr: deque[str] = deque(maxlen=STDERR_LINES)
        self._lock = threading.Lock()
        self._read_lock = threading.Lock()
        self._stop_requested = False
        self._closed = False

    @classmethod
    def for_music(cls, tools: Tools, asset: AudioAsset) -> FfmpegPcmReader:
        frames = round_samples(Fraction(
            asset.decoded_samples * OUTPUT_RATE, asset.sample_rate))
        return cls(tools, asset.track, asset.stream_index, frames,
                   source_timeline=False)

    @classmethod
    def for_source(cls, tools: Tools, path: Path, *,
                   stream_index: int, timeline_frames: int) -> FfmpegPcmReader:
        return cls(tools, path, stream_index, timeline_frames,
                   source_timeline=True)

    @property
    def frames(self) -> int:
        return self._frames

    def _command(self, start: int) -> list[str]:
        common = "aformat=sample_fmts=flt:channel_layouts=stereo"
        if self.source_timeline:
            filters = (
                f"aresample={OUTPUT_RATE}:async=1:first_pts=0,{common},"
                f"apad=whole_len={self.frames},atrim=end_sample={self.frames},"
                f"atrim=start_sample={start},asetpts=PTS-STARTPTS"
            )
        else:
            filters = (
                f"aresample={OUTPUT_RATE}:async=0,{common},"
                f"atrim=start_sample={start}:end_sample={self.frames},"
                "asetpts=PTS-STARTPTS"
            )
        return [
            str(self.tools.ffmpeg), "-hide_banner", "-loglevel", "error",
            "-xerror", "-nostdin", "-i", str(self.path), "-map",
            f"0:a:{self.stream_index}", "-vn", "-sn", "-dn",
            "-af", filters, "-ar", str(OUTPUT_RATE), "-ac",
            str(OUTPUT_CHANNELS), "-f", "f32le", "pipe:1",
        ]

    def read(self, start: int, frames: int,
             cancelled: Callable[[], bool]) -> Sequence[float]:
        if type(start) is not int or start < 0:
            raise ValueError("read start must be a non-negative integer")
        if type(frames) is not int or not 0 < frames <= MAX_READ_FRAMES:
            raise ValueError(f"read size must be from 1 to {MAX_READ_FRAMES} frames")
        if start + frames > self.frames:
            raise ValueError("read extends beyond the reader")
        if not self._read_lock.acquire(blocking=False):
            raise RuntimeError("concurrent PCM reads are not supported")
        try:
            with self._lock:
                if self._closed or self._stop_requested:
                    raise AudioOperationCancelled("audio reader is stopping")
            if cancelled():
                raise AudioOperationCancelled("Cancelled")
            if self._process is None or self._cursor != start:
                self._replace_process(start)
            data = self._read_bytes(frames * PCM_FRAME_BYTES, cancelled)
            self._check_process_after_read(
                final_extent=start + frames == self.frames,
                cancelled=cancelled,
            )
            values = tuple(item[0] for item in struct.iter_unpack("<f", data))
            if len(values) != frames * OUTPUT_CHANNELS:
                raise AudioReaderError("FFmpeg returned a truncated stereo block")
            if not all(math.isfinite(value) for value in values):
                raise AudioReaderError("FFmpeg returned a non-finite PCM sample")
            self._cursor = start + frames
            return values
        finally:
            self._read_lock.release()

    def _replace_process(self, start: int) -> None:
        self._settle_process()
        command = self._command(start)
        try:
            proc = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0, creationflags=NO_WINDOW,
            )
        except OSError as exc:
            raise AudioReaderError(f"could not start ffmpeg: {exc}") from exc
        log: deque[str] = deque(maxlen=STDERR_LINES)
        drain = threading.Thread(
            target=_drain_stderr, args=(proc.stderr, log), daemon=True)
        with self._lock:
            self._process = proc
            self._stderr = log
            self._stderr_thread = drain
            stopped = self._closed or self._stop_requested
        drain.start()
        if stopped:
            request_stop(proc)
            raise AudioOperationCancelled("audio reader is stopping")
        self._cursor = start

    def _read_bytes(self, wanted: int,
                    cancelled: Callable[[], bool]) -> bytes:
        chunks: list[bytes] = []
        remaining = wanted
        while remaining:
            if cancelled():
                raise AudioOperationCancelled("Cancelled")
            with self._lock:
                proc = self._process
            assert proc is not None and proc.stdout is not None
            block = proc.stdout.read(remaining)
            if not block:
                code = proc.wait()
                if cancelled() or self._stop_requested:
                    raise AudioOperationCancelled("Cancelled")
                self._join_stderr()
                if code != 0:
                    raise AudioReaderError(_message(
                        self._stderr, f"ffmpeg stopped (code {code})"))
                if self.source_timeline:
                    received = wanted - remaining
                    if received % PCM_FRAME_BYTES:
                        raise AudioReaderError(
                            "FFmpeg returned a truncated stereo frame")
                    chunks.append(bytes(remaining))
                    remaining = 0
                    break
                raise AudioReaderError("music ended before its validated extent")
            chunks.append(block)
            remaining -= len(block)
        return b"".join(chunks)

    def _check_process_after_read(
        self,
        *,
        final_extent: bool,
        cancelled: Callable[[], bool],
    ) -> None:
        """Surface a known failure, and settle success at the promised end."""
        with self._lock:
            proc = self._process
        assert proc is not None
        code = proc.poll()
        while final_extent and code is None:
            if cancelled() or self._stop_requested:
                raise AudioOperationCancelled("Cancelled")
            try:
                code = proc.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                continue
        if cancelled() or self._stop_requested:
            raise AudioOperationCancelled("Cancelled")
        if code is not None and code != 0:
            self._join_stderr()
            raise AudioReaderError(_message(
                self._stderr, f"ffmpeg stopped (code {code})"))

    def request_stop(self) -> None:
        """Terminal, nonblocking request which wakes a blocked pipe read."""
        with self._lock:
            if self._stop_requested:
                return
            self._stop_requested = True
            proc = self._process
        request_stop(proc)

    def close(self) -> None:
        """Settle the child and drain thread; called off the UI thread by S3."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop_requested = True
        self._settle_process()

    def _join_stderr(self) -> None:
        thread = self._stderr_thread
        if thread is not None:
            thread.join(timeout=2)

    def _settle_process(self) -> None:
        with self._lock:
            proc = self._process
            drain = self._stderr_thread
            self._process = None
            self._stderr_thread = None
        if proc is not None:
            stop_process(proc)
            for pipe in (proc.stdout, proc.stderr):
                try:
                    if pipe is not None:
                        pipe.close()
                except OSError:
                    pass
        if drain is not None:
            drain.join(timeout=2)
        self._cursor = None
