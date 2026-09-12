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

"""Cached thumbnails for the clip list.

Thumbnails go through the same levels correction the exports use, so the
preview matches what you will actually get rather than the washed-out version.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .media import NO_WINDOW, ClipInfo, Tools, request_stop, stop_process

THUMB_WIDTH = 240

# How far past the seek point to decode before keeping a frame. Seeking in an
# MPEG-TS recording lands on an estimated byte offset rather than on a
# keyframe, so the first frames out of the decoder are torn macroblocks or flat
# grey. Decoding through a second and a half lets it resynchronise on a real
# keyframe. Without this, most thumbnails come out as noise.
RESYNC_SECONDS = 1.5
THUMB_TIMEOUT_SECONDS = 90
_WAIT_SLICE_SECONDS = 0.1


def cache_dir() -> Path:
    base = Path.home() / ".flightdvr" / "thumbs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _cache_key(clip: ClipInfo) -> str:
    raw = f"{clip.path}|{clip.size}|{clip.modified.timestamp()}|{THUMB_WIDTH}|{RESYNC_SECONDS}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def thumbnail_path(clip: ClipInfo) -> Path:
    return cache_dir() / f"{_cache_key(clip)}.jpg"


def build_command(tools: Tools, clip: ClipInfo, target: Path) -> list[str]:
    """ffmpeg arguments for one thumbnail."""
    duration = clip.duration if clip.duration > 0 else 0.0

    filters = []
    if clip.is_full_range:
        filters.append("scale=in_range=full:out_range=limited")
    filters.append(f"scale={THUMB_WIDTH}:-2:flags=bilinear")

    command = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-y", "-v", "error"]

    if duration > RESYNC_SECONDS + 2:
        # Seek fast to roughly a tenth in, then decode forward through the
        # resync window before keeping a frame.
        seek = min(max(1.0, duration * 0.12), duration - RESYNC_SECONDS - 1.0)
        command += ["-ss", f"{seek:.2f}", "-i", str(clip.path),
                    "-ss", f"{RESYNC_SECONDS:.2f}"]
    else:
        # Too short to seek into; decode from the start and take a late frame.
        command += ["-i", str(clip.path)]
        if duration > 1.0:
            command += ["-ss", f"{min(0.5, duration / 3):.2f}"]

    command += ["-frames:v", "1", "-vf", ",".join(filters), "-q:v", "4", str(target)]
    return command


def extract(tools: Tools, clip: ClipInfo, *, register=None, cancelled=None,
            publish=None) -> Path | None:
    """Grab a representative frame without exposing a partial cache entry.

    The task supplies the three callbacks.  `register` gives it the child it
    must be able to stop, `cancelled` prevents work continuing after its row is
    gone, and `publish` serializes the final rename with cancellation.  Keeping
    that last decision beside the task lock closes the race where a check made
    just before `replace()` was already stale by the time the file moved.
    """
    target = thumbnail_path(clip)
    if target.exists() and target.stat().st_size > 0:
        return publish(None, target) if publish is not None else target

    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{target.stem}-", suffix=target.suffix,
        dir=target.parent, delete=False,
    )
    handle.close()
    staged = Path(handle.name)
    proc = None
    try:
        if cancelled is not None and cancelled():
            return None
        proc = subprocess.Popen(
            build_command(tools, clip, staged),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
        if register is not None:
            register(proc)

        deadline = time.monotonic() + THUMB_TIMEOUT_SECONDS
        while True:
            if cancelled is not None and cancelled():
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                returncode = proc.wait(
                    timeout=min(_WAIT_SLICE_SECONDS, remaining))
                break
            except subprocess.TimeoutExpired:
                continue

        if returncode != 0 or (cancelled is not None and cancelled()):
            return None
        if not staged.exists() or staged.stat().st_size <= 0:
            return None
        if publish is not None:
            return publish(staged, target)
        staged.replace(target)
        return target
    except OSError:
        return None
    finally:
        # This is the task/worker side.  UI code only makes the prompt request;
        # the owner waits, escalates if needed, and removes its staging file.
        stop_process(proc)
        if register is not None:
            register(None)
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass


class _Signals(QObject):
    # loader generation, clip fingerprint, clip path, thumbnail path
    ready = Signal(int, str, str, str)
    # loader generation, clip fingerprint, clip path
    finished = Signal(int, str, str)


class _ThumbTask(QRunnable):
    def __init__(self, tools: Tools, clip: ClipInfo, signals: _Signals,
                 generation: int):
        super().__init__()
        self.tools = tools
        self.clip = clip
        self.signals = signals
        self.generation = generation
        self.fingerprint = clip.fingerprint
        self.token = (generation, self.fingerprint, str(clip.path))
        self._lock = threading.Lock()
        self._cancelled = False
        self._completed = False
        self._process = None
        # The loader, not QThreadPool's auto-delete timing, owns this task
        # until the queued finished signal has removed its final reference.
        self.setAutoDelete(False)

    def stop(self) -> None:
        """Ask from the UI thread; never wait here."""
        with self._lock:
            if self._completed:
                return
            self._cancelled = True
            proc = self._process
        request_stop(proc)

    def _register(self, proc) -> None:
        with self._lock:
            self._process = proc
            cancelled = self._cancelled
        # stop() may have won the race before Popen returned.
        if proc is not None and cancelled:
            request_stop(proc)

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def _publish(self, staged: Path | None, target: Path) -> Path | None:
        """Linearize cancellation with the one visible cache mutation."""
        with self._lock:
            if self._cancelled:
                return None
            if staged is not None:
                if target.exists() and target.stat().st_size > 0:
                    staged.unlink(missing_ok=True)
                else:
                    staged.replace(target)
            self._completed = True
            return target

    def run(self) -> None:
        try:
            result = extract(
                self.tools, self.clip, register=self._register,
                cancelled=self._is_cancelled, publish=self._publish,
            )
            if result:
                self.signals.ready.emit(
                    self.generation, self.fingerprint,
                    str(self.clip.path), str(result),
                )
        finally:
            self.signals.finished.emit(
                self.generation, self.fingerprint, str(self.clip.path))


class ThumbnailLoader(QObject):
    """Generates thumbnails on a small background pool.

    Requests are held back while a scan is running. Thumbnail extraction and
    clip probing both read from the same slow card, and letting them compete
    makes the listing crawl.
    """

    ready = Signal(int, str, str, str)
    idle = Signal()

    # Closing a window must not collect a loader whose QRunnable still owns a
    # process.  Shutdown detaches it from the window and this set holds it until
    # the last task's finished signal arrives.
    _retired: set["ThumbnailLoader"] = set()

    def __init__(self, tools: Tools, parent=None):
        super().__init__(parent)
        self.tools = tools
        self._signals = _Signals()
        self._signals.ready.connect(self.ready)
        self._signals.finished.connect(self._finished)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(3)
        self._queued: set[tuple[str, str]] = set()
        self._pending: set[tuple[int, str, str]] = set()
        self._tasks: dict[tuple[int, str, str], _ThumbTask] = {}
        self._held: list[tuple[tuple[int, str, str], ClipInfo]] = []
        self._paused = False
        self._generation = 0
        self._shutting_down = False

    @property
    def generation(self) -> int:
        return self._generation

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        held, self._held = self._held, []
        for token, clip in held:
            if token in self._pending:
                self._start(token, clip)

    def request(self, clip: ClipInfo) -> None:
        identity = (str(clip.path), clip.fingerprint)
        if identity in self._queued:
            return
        self._queued.add(identity)
        token = (self._generation, clip.fingerprint, str(clip.path))
        self._pending.add(token)
        if self._paused:
            self._held.append((token, clip))
        else:
            self._start(token, clip)

    def _start(self, token, clip: ClipInfo) -> None:
        task = _ThumbTask(self.tools, clip, self._signals, token[0])
        self._tasks[token] = task
        self._pool.start(task)

    def _finished(self, generation: int, fingerprint: str,
                  clip_path: str) -> None:
        token = (generation, fingerprint, clip_path)
        self._pending.discard(token)
        self._tasks.pop(token, None)
        if self._shutting_down and not self._tasks:
            self._retired.discard(self)
        elif self.is_idle:
            self.idle.emit()

    @property
    def is_idle(self) -> bool:
        """Whether no queued or running thumbnail can still touch the card."""
        return (not self._paused and not self._held and not self._pending
                and self._pool.activeThreadCount() == 0)

    def clear(self) -> None:
        self._generation += 1
        self._queued.clear()
        held, self._held = self._held, []
        for token, _clip in held:
            self._pending.discard(token)

        # tryTake distinguishes work Qt removed before it began from work that
        # may already own a process.  The latter remains in both dictionaries
        # until its finished signal, so `is_idle` cannot lie about card access.
        for token, task in list(self._tasks.items()):
            task.stop()
            if self._pool.tryTake(task):
                self._pending.discard(token)
                self._tasks.pop(token, None)
        self._held.clear()

    def shutdown(self) -> None:
        self._shutting_down = True
        self.clear()
        if self._tasks:
            self.setParent(None)
            self._retired.add(self)
