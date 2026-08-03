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

"""Playing a clip inside the window, so trim points can be set while it runs.

Qt has a video widget. It is not used here, deliberately. On Windows it decodes
through Media Foundation — the same decoder behind Windows Media Player, which
the README already tells people cannot play HEVC inside an MPEG-TS. That is the
only format this application exists for, so the obvious approach would work
perfectly on test footage and fail on every real recording. QtMultimedia is
excluded from the packaged build for the same reason, and a test asserts that
nothing here imports it.

So frames come from ffmpeg, which is already proven on this footage by the
thumbnails and the filmstrip. It emits raw RGB down a pipe, this reads it a
frame at a time, and Qt only ever has to paint.

Why raw rather than MJPEG down the same pipe: a frame is exactly
`width * height * 3` bytes, so finding frame boundaries is arithmetic and a
short read unambiguously means the stream ended. MJPEG would mean scanning for
markers, a JPEG encode and decode per frame, and generation loss in a preview
whose whole purpose is to show what the export will look like.

Memory is bounded by QUEUE_FRAMES * frame_bytes — about 16 MB at 640x360.
That bound is the back-pressure: when the window stops taking frames the queue
fills, the reader blocks, the pipe fills and ffmpeg blocks. Please do not
"optimise" it away.

There is no audio. It would need a second pipe, a second clock and an output
device, and DVR audio is motor noise and wind. Setting a trim point is
something you do by eye.
"""

from __future__ import annotations

import queue
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Iterator

from PySide6.QtCore import QObject, QThread, Signal

from .media import NO_WINDOW, ClipInfo, Tools, frame_rate_mode, stop_process

# Frame sizes offered to the view. Every width gives a row of bytes divisible
# by four, which keeps QImage's scanline alignment happy without padding.
PREVIEW_SIZES = ((480, 270), (640, 360), (960, 540))

# Enough to judge a moment by. Higher costs decode time for no benefit when the
# job is finding where a flight starts.
PREVIEW_FPS = 30

# About 0.8 seconds of slack at 640x360x30. Deep enough to ride out a slow
# repaint, shallow enough that a seek does not have to throw much away.
QUEUE_FRAMES = 24

# A seek this far ahead is cheaper to skip to than to restart ffmpeg for.
FORWARD_SKIP = 1.5

# How far before the in point to start decoding. Same reasoning as the export
# path: a seek into an MPEG-TS lands on an estimated byte offset rather than a
# keyframe, so the first frames after one are torn until the decoder catches up.
SEEK_LEAD_IN = 2.0

# Enough stderr to explain a failure without holding a whole log.
STDERR_LINES = 100


@dataclass(frozen=True)
class PreviewSize:
    width: int
    height: int

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height * 3

    @property
    def stride(self) -> int:
        return self.width * 3


def choose_size(view_width: int) -> PreviewSize:
    """The largest preview that is not bigger than the space it goes in.

    Picked once when playback starts. Resizing the window mid-clip must never
    restart the decoder — Qt can scale a frame far more cheaply than ffmpeg can
    be spawned again.
    """
    chosen = PREVIEW_SIZES[0]
    for width, height in PREVIEW_SIZES:
        if width <= max(0, view_width):
            chosen = (width, height)
    return PreviewSize(*chosen)


def seek_pair(start: float) -> tuple[float, float]:
    """(fast seek before the input, accurate seek after it).

    The same two-stage seek the exports and thumbnails use. The first is cheap
    and lands somewhere before the target; the second discards the lead-in once
    the decoder has produced real frames. One seek alone gives torn macroblocks
    for up to a full group of pictures, which on this footage is a second.
    """
    if start <= 0.01:
        return 0.0, 0.0
    lead_in = min(start, SEEK_LEAD_IN)
    return start - lead_in, lead_in


def build_command(tools: Tools, clip: ClipInfo, start: float,
                  size: PreviewSize, fps: int = PREVIEW_FPS) -> list[str]:
    """ffmpeg arguments for a preview stream starting at `start` seconds.

    The frame size is forced rather than derived. `ClipInfo.width` and
    `.height` can both legitimately be zero, and `scale=W:-2` yields a height
    nobody knows in advance — which would break fixed-size reads outright. A
    fixed box with letterboxing keeps the frame size a constant this module
    chose, and handles odd or unusually shaped sources correctly as a bonus.
    """
    fast, accurate = seek_pair(start)

    filters = []
    if clip.is_full_range:
        # Or the preview looks washed out and disagrees with the export it is
        # supposed to be predicting.
        filters.append("scale=in_range=full:out_range=limited")
    filters += [
        f"scale={size.width}:{size.height}:force_original_aspect_ratio=decrease",
        f"pad={size.width}:{size.height}:(ow-iw)/2:(oh-ih)/2",
        f"fps={max(1, fps)}",
    ]

    command = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error"]
    # Default probe settings on purpose: forcing large ones costs eight times
    # as much reading from a card over USB and tells us nothing extra.
    if fast > 0.01:
        command += ["-ss", f"{fast:.3f}"]
    command += ["-i", str(clip.path)]
    if accurate > 0.01:
        command += ["-ss", f"{accurate:.3f}"]
    command += [
        "-an",
        "-vf", ",".join(filters),
        *frame_rate_mode(tools, "cfr"),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    return command


def read_frames(stream, frame_bytes: int,
                should_stop=None) -> Iterator[bytes]:
    """Whole frames from a binary stream, until it ends.

    A pipe returns short reads whenever it feels like it, so each frame is
    filled by repeated reads rather than assumed to arrive in one. Anything
    left over at the end is a truncated frame and is dropped: a partial frame
    is not a picture.

    Taking any binary stream rather than a process is what makes the framing
    testable against BytesIO, with no ffmpeg and no subprocess involved.
    """
    buffer = bytearray(frame_bytes)
    view = memoryview(buffer)
    while True:
        if should_stop is not None and should_stop():
            return
        filled = 0
        while filled < frame_bytes:
            chunk = stream.readinto(view[filled:])
            if not chunk:
                return                      # end of stream, or a partial frame
            filled += chunk
        yield bytes(buffer)


def seconds_for_index(index: int, start: float, fps: int = PREVIEW_FPS) -> float:
    """Where in the clip a given frame of this stream came from."""
    return start + index / max(1, fps)


def should_restart(current: float, target: float, playing: bool) -> bool:
    """Whether a seek needs a new ffmpeg or can be skipped to.

    Restarting costs a two-stage seek and roughly half a second before the
    first frame appears. A small forward jump is already sitting in the queue
    or moments away, so it is cheaper to run the clock forward and let the
    frames arrive.
    """
    if not playing:
        return True
    if target < current:
        return True
    return (target - current) > FORWARD_SKIP


class PlayClock:
    """Where playback has reached, in clip seconds.

    Paced against a monotonic clock rather than by counting frames, so a
    repaint that took too long is caught up with rather than accumulating into
    drift. When the decoder has not kept up the clock stalls instead of running
    ahead, which shows as the picture pausing rather than jumping.

    `clock` is injectable so this can be tested without waiting for real time.
    """

    def __init__(self, origin: float = 0.0, speed: float = 1.0,
                 clock=time.monotonic):
        self.origin = origin
        self.speed = speed
        self._clock = clock
        self._last = None

    def start(self) -> None:
        self._last = self._clock()

    def advance(self, starved: bool = False) -> float:
        """Move the clock on and return the position now wanted."""
        now = self._clock()
        if self._last is None:
            self._last = now
            return self.origin
        elapsed = max(0.0, now - self._last)
        self._last = now
        if not starved:
            self.origin += elapsed * self.speed
        return self.origin

    def jump_to(self, seconds: float) -> None:
        self.origin = seconds
        self._last = self._clock()


class DecodeWorker(QThread):
    """One ffmpeg, decoding a clip into a queue of frames.

    Frames travel by queue rather than by signal on purpose. A signal per frame
    piles up in Qt's event queue the moment the window stops draining, and the
    bounded-memory story collapses into a leak. Signals here carry only the
    three things that happen once.
    """

    started_stream = Signal(int)          # generation
    failed = Signal(int, str)             # generation, message
    ended = Signal(int)                   # generation

    def __init__(self, tools: Tools, clip: ClipInfo, start: float,
                 size: PreviewSize, generation: int,
                 frames: queue.Queue, fps: int = PREVIEW_FPS, parent=None):
        super().__init__(parent)
        self.tools = tools
        self.clip = clip
        self.start_at = start
        self.size = size
        self.generation = generation
        self.frames = frames
        self.fps = fps
        self._cancel = False
        self._process: subprocess.Popen | None = None

    def stop(self) -> None:
        """Ask the decode to end, and unblock it so it notices.

        The reader spends its life inside readinto(), where a flag is never
        seen. Closing ffmpeg's end of the pipe is what makes that read return.
        Order matters: flag first, then the process, or the thread can start
        another read after the process is gone.
        """
        self._cancel = True
        stop_process(self._process)

    def run(self) -> None:  # noqa: D102  (QThread entry point)
        command = build_command(self.tools, self.clip, self.start_at,
                                self.size, self.fps)
        try:
            proc = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=1 << 20, creationflags=NO_WINDOW,
            )
        except OSError as exc:
            self.failed.emit(self.generation, f"Could not start ffmpeg: {exc}")
            return

        self._process = proc
        # cancel() can land between the check above and this assignment, in
        # which case it found nothing to stop and this is the only thing that
        # will stop it.
        if self._cancel:
            stop_process(proc)

        log: deque[str] = deque(maxlen=STDERR_LINES)
        reader = threading.Thread(target=self._drain, args=(proc.stderr, log),
                                  daemon=True)
        reader.start()
        self.started_stream.emit(self.generation)

        index = 0
        try:
            for frame in read_frames(proc.stdout, self.size.frame_bytes,
                                     should_stop=lambda: self._cancel):
                when = seconds_for_index(index, self.start_at, self.fps)
                index += 1
                # A timeout rather than a bare put: the queue filling is how
                # back-pressure works, but a blocked put would ignore stop().
                while not self._cancel:
                    try:
                        self.frames.put((when, frame), timeout=0.2)
                        break
                    except queue.Full:
                        continue
                if self._cancel:
                    break
        finally:
            stop_process(proc)
            reader.join(timeout=2)
            code = proc.poll()
            self._process = None

        if self._cancel:
            return
        if code not in (0, None):
            self.failed.emit(self.generation,
                             _describe(log) or f"ffmpeg stopped (code {code})")
            return
        self.ended.emit(self.generation)

    @staticmethod
    def _drain(pipe, log: deque[str]) -> None:
        """Read stderr as it arrives so ffmpeg never blocks writing to it.

        This matters more here than in the export path. Preview is the feature
        most likely to be aimed at a recording cut short by a flat battery or a
        crash, and a torn transport stream produces exactly the flood of
        decoder warnings that filled the pipe and deadlocked the export queue
        before it was fixed there.
        """
        if pipe is None:
            return
        try:
            for line in pipe:
                text = line.decode("utf-8", "replace").strip()
                if text:
                    log.append(text)
        except (OSError, ValueError):
            pass


def _describe(log: deque[str]) -> str:
    """The most useful line ffmpeg wrote, as the export path does it."""
    from .jobs import _describe_failure
    return _describe_failure(list(log), 1)
