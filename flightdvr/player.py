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

import math
import queue
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Iterator

from PySide6.QtCore import QObject, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import QSizePolicy, QWidget

from .media import (
    NO_WINDOW, ClipInfo, Tools, frame_rate_mode, request_stop, stop_process,
)

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

# How long a paused clip keeps its decoder. A pause is usually a moment's
# thought, so holding the process makes resuming instant; a pause that lasts
# this long is somebody who has walked away, and the card should be free to
# eject. Resuming after it simply costs one seek.
IDLE_STOP_SECONDS = 30

# Four seconds at the fastest recording mode the goggles offer: roughly two
# seconds either side of the cut, without a long clip ever turning into a
# whole-file decode. The absolute cap also keeps an unexpected higher-rate
# source bounded instead of trusting its metadata with memory.
FRAME_WINDOW_SECONDS = 4.0
FRAME_CACHE_MAX_FRAMES = 361
FRAME_CACHE_SIZE = (320, 180)

SHOWINFO_TIME = re.compile(r"\bpts_time:([-+0-9.eE]+)")


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


@dataclass(frozen=True)
class FrameWindow:
    """A bounded run of source frames to decode around one playhead."""

    first_frame: int
    frame_count: int
    fps: float

    @property
    def last_frame(self) -> int:
        return self.first_frame + self.frame_count - 1

    def seconds_for(self, frame_number: int) -> float:
        if not self.first_frame <= frame_number <= self.last_frame:
            raise IndexError(frame_number)
        return frame_number / self.fps


def plan_frame_window(position: float, duration: float, fps: float) -> FrameWindow:
    """The source-frame range worth caching around ``position``.

    Frame numbers, rather than rounded seconds, are the authority. That keeps
    stepping and the timestamp readout tied to the picture that was decoded.
    """
    rate = fps if fps > 0 else PREVIEW_FPS
    total = max(1, math.ceil(max(0.0, duration) * rate - 1e-9))
    target = math.floor(max(0.0, position) * rate + 0.5)
    target = min(target, total - 1)
    wanted = min(
        total,
        FRAME_CACHE_MAX_FRAMES,
        math.floor(FRAME_WINDOW_SECONDS * rate + 0.5) + 1,
    )
    first = target - wanted // 2
    first = max(0, min(first, total - wanted))
    return FrameWindow(first, wanted, rate)


@dataclass(frozen=True)
class CachedFrame:
    """One decoded source frame, with the timestamp ffmpeg reported for it."""

    frame_number: int
    seconds: float
    pixels: bytes


class FrameCache:
    """The current precise window. Refill replaces; it never accumulates."""

    def __init__(self, limit: int = FRAME_CACHE_MAX_FRAMES):
        self.limit = max(1, limit)
        self._frames: dict[int, CachedFrame] = {}

    def replace(self, frames) -> None:
        replacement: dict[int, CachedFrame] = {}
        for frame in frames:
            if len(replacement) >= self.limit:
                break
            replacement[frame.frame_number] = frame
        self._frames = replacement

    def clear(self) -> None:
        self._frames.clear()

    def get(self, frame_number: int) -> CachedFrame | None:
        return self._frames.get(frame_number)

    def __len__(self) -> int:
        return len(self._frames)


def exact_timestamp(seconds: float) -> str:
    """A clip timestamp precise enough to distinguish adjacent DVR frames."""
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    whole, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole:02d}.{millis:03d}"


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

    Both numbers are measured from the start of the file, which is only true
    because of the `-copyts -start_at_zero` in `build_command`. Without those
    the second seek is relative to wherever the first one landed, which is not
    where it was aimed — see the comment there.
    """
    if start <= 0.01:
        return 0.0, 0.0
    return max(0.0, start - SEEK_LEAD_IN), start


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

    # No range conversion here, unlike the export and the filmstrip. Measured:
    # in a chain that ends in rgb24 the range filters are inert, because the
    # conversion out of YUV already reads the source's range tag. Applying
    # full-to-limited, applying limited-to-full, and applying neither all give
    # byte-identical frames, and all three match the export decoded back to RGB
    # to within H.264's own loss. So the preview agrees with the export by
    # construction, and a filter here would only be a comment that costs a
    # scale pass.
    filters = [
        f"scale={size.width}:{size.height}:force_original_aspect_ratio=decrease",
        f"pad={size.width}:{size.height}:(ow-iw)/2:(oh-ih)/2",
        f"fps={max(1, fps)}",
    ]

    command = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error"]
    # Default probe settings on purpose: forcing large ones costs eight times
    # as much reading from a card over USB and tells us nothing extra.
    if fast > 0.01:
        command += ["-ss", f"{fast:.3f}"]
    # Measured, on a file whose audio begins 23 ms before its video: without
    # these, the seek after the input is counted from the keyframe the seek
    # before it landed on, rather than from the position that was asked for.
    # Asking for 2.5 s produced the frame at 2.0 s — bit-exact, no warning, and
    # a preview that lies about where the playhead is. It only shows up when
    # the file's start time is not zero and the audio is dropped, which is why
    # HDZero footage never showed it and a synthetic fixture did.
    command += ["-copyts", "-start_at_zero", "-i", str(clip.path)]
    if accurate > 0.01:
        command += ["-ss", f"{accurate:.3f}"]
    command += [
        "-an",
        "-vf", ",".join(filters),
        *frame_rate_mode(tools, "cfr"),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    return command


def build_frame_window_command(tools: Tools, clip: ClipInfo,
                               window: FrameWindow,
                               size: PreviewSize) -> list[str]:
    """Decode only ``window`` at the source rate, retaining source PTS.

    Playback deliberately converts to 30 fps. Precise stepping cannot: at 60
    or 90 fps that would discard the very frames this path exists to reach.
    ``showinfo`` records each decoded frame's PTS on stderr while raw pixels
    remain arithmetically framed on stdout.
    """
    start = window.seconds_for(window.first_frame)
    fast, accurate = seek_pair(start)
    filters = [
        f"scale={size.width}:{size.height}:force_original_aspect_ratio=decrease",
        f"pad={size.width}:{size.height}:(ow-iw)/2:(oh-ih)/2",
        "showinfo",
    ]
    command = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "info"]
    if fast > 0.01:
        command += ["-ss", f"{fast:.6f}"]
    command += ["-copyts", "-start_at_zero", "-i", str(clip.path)]
    if accurate > 0.01:
        command += ["-ss", f"{accurate:.6f}"]
    command += [
        "-an", "-vf", ",".join(filters),
        *frame_rate_mode(tools, "passthrough"),
        "-frames:v", str(window.frame_count),
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

        Asks without waiting, because seeking, changing clip, Escape and
        closing all arrive here on the UI thread. The bounded wait and the
        kill escalation happen in run()'s own cleanup, on this thread.
        """
        self._cancel = True
        request_stop(self._process)

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


class FrameWindowWorker(QThread):
    """Decode one bounded native-rate window for paused frame stepping."""

    ready = Signal(int, object)           # generation, tuple[CachedFrame, ...]
    failed = Signal(int, str)

    def __init__(self, tools: Tools, clip: ClipInfo, window: FrameWindow,
                 size: PreviewSize, generation: int, parent=None):
        super().__init__(parent)
        self.tools = tools
        self.clip = clip
        self.window = window
        self.size = size
        self.generation = generation
        self._cancel = False
        self._process: subprocess.Popen | None = None

    def stop(self) -> None:
        self._cancel = True
        request_stop(self._process)

    def run(self) -> None:  # noqa: D102
        command = build_frame_window_command(
            self.tools, self.clip, self.window, self.size)
        try:
            proc = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=1 << 20, creationflags=NO_WINDOW,
            )
        except OSError as exc:
            self.failed.emit(self.generation, f"Could not start ffmpeg: {exc}")
            return

        self._process = proc
        if self._cancel:
            stop_process(proc)

        log: deque[str] = deque(maxlen=STDERR_LINES)
        timestamps: list[float] = []
        reader = threading.Thread(
            target=self._drain, args=(proc.stderr, log, timestamps), daemon=True)
        reader.start()

        pixels: list[bytes] = []
        try:
            for frame in read_frames(proc.stdout, self.size.frame_bytes,
                                     should_stop=lambda: self._cancel):
                if len(pixels) >= FRAME_CACHE_MAX_FRAMES:
                    break
                pixels.append(frame)
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
        if len(timestamps) < len(pixels):
            self.failed.emit(
                self.generation,
                f"ffmpeg returned {len(pixels)} frames but "
                f"{len(timestamps)} timestamps",
            )
            return

        # showinfo runs before the accurate output-side seek discards the
        # lead-in. Measured on the 60 fps integration fixture at a 0.5 s
        # window start: 271 timestamps were logged for 241 output pictures;
        # the first 30 describe frames deliberately thrown away. The pictures
        # therefore pair with the tail, not the head, of the PTS list.
        timestamps = timestamps[-len(pixels):]

        frames = tuple(
            CachedFrame(
                frame_number=max(0, math.floor(seconds * self.window.fps + 0.5)),
                seconds=seconds,
                pixels=data,
            )
            for seconds, data in zip(timestamps, pixels)
        )
        self.ready.emit(self.generation, frames)

    @staticmethod
    def _drain(pipe, log: deque[str], timestamps: list[float]) -> None:
        """Drain stderr concurrently, separating showinfo PTS from errors."""
        if pipe is None:
            return
        try:
            for line in pipe:
                text = line.decode("utf-8", "replace").strip()
                match = SHOWINFO_TIME.search(text)
                if match:
                    try:
                        timestamps.append(float(match.group(1)))
                    except ValueError:
                        log.append(text)
                elif text:
                    log.append(text)
        except (OSError, ValueError):
            pass


class PreviewPlayer(QObject):
    """Turns a queue of decoded frames into a picture that plays at real speed.

    The decoder runs flat out and this decides which of its frames to show and
    when. Anything that arrived too late to be shown is dropped rather than
    queued up, so a slow moment costs a frame instead of putting the picture
    permanently behind the clock.

    Nothing here paints. It emits a frame and the time it came from, and the
    window is free to do what it likes with both — which is what lets the trim
    playhead be driven by the picture actually on screen rather than by a clock
    that might be describing a frame nobody saw.
    """

    frame_ready = Signal(object, float)   # QImage, seconds into the clip
    precise_frame_ready = Signal(object, float, int)  # image, seconds, source frame
    precise_loading = Signal(bool)
    precise_failed = Signal(str)
    state_changed = Signal(bool)          # playing
    failed = Signal(str)
    ended = Signal()

    def __init__(self, tools: Tools, parent=None, clock=time.monotonic,
                 worker_factory=None, frame_worker_factory=None):
        super().__init__(parent)
        self.tools = tools
        self.clip: ClipInfo | None = None
        self.size = PreviewSize(*PREVIEW_SIZES[0])
        self.position = 0.0
        self.is_playing = False
        self._clock_source = clock
        self._make_worker = worker_factory or DecodeWorker
        self._make_frame_worker = frame_worker_factory or FrameWindowWorker

        self._generation = 0
        self._worker: DecodeWorker | None = None
        # A running QThread that gets collected takes its process down with it,
        # so workers asked to stop are held until they have actually finished.
        self._retired: list[DecodeWorker] = []
        self._frames: queue.Queue = queue.Queue(maxsize=QUEUE_FRAMES)
        # One frame read out of the queue and found to be in the future. There
        # is no way to look at the head of a Queue without taking it.
        self._pending: tuple[float, bytes] | None = None
        self._playclock = PlayClock(clock=clock)
        self._starved = True
        self._stream_ended = False

        self._frame_size = PreviewSize(*FRAME_CACHE_SIZE)
        self._frame_cache = FrameCache()
        self._frame_generation = 0
        self._frame_worker: FrameWindowWorker | None = None
        self._frame_retired: list[FrameWindowWorker] = []
        self._frame_pending: int | None = None

        self._timer = QTimer(self)
        # The default coarse timer rounds to about 15 ms on Windows, which at
        # 30 fps is half a frame and visible as judder.
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(max(1, 1000 // PREVIEW_FPS))
        self._timer.timeout.connect(self._tick)

        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(IDLE_STOP_SECONDS * 1000)
        self._idle.timeout.connect(self._release)

    # -- what the window asks for ---------------------------------------------

    def load(self, clip: ClipInfo | None, position: float = 0.0) -> None:
        """Point at a clip. Nothing decodes until somebody presses play."""
        self.stop()
        self._clear_frame_cache()
        self.clip = clip
        self.position = max(0.0, position)

    def play(self, view_width: int = 0) -> None:
        if self.clip is None or self.is_playing:
            return
        self._clear_frame_cache()
        self._idle.stop()
        if self._worker is None:
            # Chosen once, on the way in. Resizing the window mid-clip must not
            # restart ffmpeg: Qt can scale a frame for far less than a spawn.
            if view_width > 0:
                self.size = choose_size(view_width)
            self._start(self.position)
        self._playclock.jump_to(self.position)
        # Nothing has arrived yet, so the clock must not start running until
        # something does, or the first frames are dropped for being late.
        self._starved = True
        self.is_playing = True
        self._timer.start()
        self.state_changed.emit(True)

    def pause(self) -> None:
        """Stop the clock, keep the decoder.

        The queue stops draining, which fills the pipe and blocks ffmpeg where
        it stands, so a pause costs nothing and resuming is immediate.
        """
        if not self.is_playing:
            return
        self.is_playing = False
        self._timer.stop()
        self._idle.start()
        self.state_changed.emit(False)

    def toggle(self, view_width: int = 0) -> None:
        self.pause() if self.is_playing else self.play(view_width)

    def stop(self) -> None:
        was_playing = self.is_playing
        self.is_playing = False
        self._timer.stop()
        self._idle.stop()
        self._release()
        self._cancel_frame_window()
        if was_playing:
            self.state_changed.emit(False)

    def seek(self, seconds: float) -> None:
        """Move to a point in the clip.

        While paused this only records where the playhead is: the filmstrip
        already has a keyframe for every second and shows it instantly, which
        is a better answer than half a second of black waiting for a decoder.
        """
        target = max(0.0, seconds)
        if self.clip is not None and self.clip.duration > 0:
            target = min(target, self.clip.duration)
        self._cancel_frame_window()
        if not self.is_playing:
            # A paused decoder is holding frames from where it was paused, and
            # they are now the wrong ones. Dropping it here rather than letting
            # play() resume from the old spot.
            self._release()
            self.position = target
            return
        if should_restart(self.position, target, True):
            self._start(target)
            self._starved = True
        self.position = target
        self._playclock.jump_to(target)

    def step(self, seconds: float) -> None:
        self.seek(self.position + seconds)

    def step_frames(self, frames: int) -> None:
        """Pause and move by real source frames, decoding a window if needed."""
        clip = self.clip
        if clip is None or not frames:
            return
        if clip.fps <= 0:
            self.precise_failed.emit(
                "Could not determine this clip's source frame rate")
            return
        if self.is_playing:
            self.pause()
        # The playback decoder is a forward-only 30 fps stream. Keeping it
        # while a native-rate window is decoded would hold the card twice and
        # retain frames that can no longer resume from this exact position.
        self._idle.stop()
        self._release()

        rate = clip.fps if clip.fps > 0 else PREVIEW_FPS
        total = max(1, math.ceil(max(0.0, clip.duration) * rate - 1e-9))
        current = self._frame_pending
        if current is None:
            current = math.floor(max(0.0, self.position) * rate + 0.5)
        target = max(0, min(current + frames, total - 1))
        cached = self._frame_cache.get(target)
        if cached is not None:
            self._emit_precise(cached)
            return

        worker = self._frame_worker
        if (worker is not None
                and worker.window.first_frame <= target <= worker.window.last_frame):
            # Key presses can arrive during the real-footage refill measured at
            # about 1.5 s. Remember the latest target inside the window already
            # on its way instead of cancelling and spawning the same decode.
            self._frame_pending = target
            return

        self._frame_pending = target
        window = plan_frame_window(target / rate, clip.duration, rate)
        self._start_frame_window(window)

    def set_speed(self, speed: float) -> None:
        self._playclock.speed = max(0.1, speed)

    @property
    def speed(self) -> float:
        return self._playclock.speed

    def shutdown(self) -> None:
        """Wait for every decoder to be gone. Called when the window closes."""
        self.stop()
        for worker in self._retired:
            if worker.isRunning():
                worker.stop()
                worker.wait(2000)
        self._retired.clear()
        self._cancel_frame_window()
        for worker in self._frame_retired:
            if worker.isRunning():
                worker.stop()
                worker.wait(2000)
        self._frame_retired.clear()

    # -- the decoder ----------------------------------------------------------

    def _start(self, start: float) -> None:
        self._release()
        self._generation += 1
        worker = self._make_worker(self.tools, self.clip, start, self.size,
                                   self._generation, self._frames,
                                   PREVIEW_FPS, self)
        worker.failed.connect(self._worker_failed)
        worker.ended.connect(self._worker_ended)
        self._worker = worker
        worker.start()

    def _release(self) -> None:
        """Retire the current decoder and give the next one a clean queue.

        A fresh queue rather than draining the old one: the retired worker is
        very likely blocked inside a put, and letting it write into a queue
        nobody reads is how it gets to notice it has been cancelled and leave.
        """
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.stop()
            self._retired.append(worker)
        self._retired = [w for w in self._retired if w.isRunning()]
        self._frames = queue.Queue(maxsize=QUEUE_FRAMES)
        self._pending = None
        self._stream_ended = False

    def _worker_ended(self, generation: int) -> None:
        if generation == self._generation:
            # Not the end of playback — there can still be frames queued.
            self._stream_ended = True

    def _worker_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        was_playing = self.is_playing
        self.is_playing = False
        self._timer.stop()
        self._release()
        if was_playing:
            self.state_changed.emit(False)
        self.failed.emit(message)

    # -- the paused native-frame window --------------------------------------

    def _start_frame_window(self, window: FrameWindow) -> None:
        self._cancel_frame_window(clear_pending=False)
        self._frame_generation += 1
        worker = self._make_frame_worker(
            self.tools, self.clip, window, self._frame_size,
            self._frame_generation, self)
        worker.ready.connect(self._frame_window_ready)
        worker.failed.connect(self._frame_window_failed)
        self._frame_worker = worker
        worker.start()
        self.precise_loading.emit(True)

    def _cancel_frame_window(self, clear_pending: bool = True) -> None:
        worker, self._frame_worker = self._frame_worker, None
        if worker is not None:
            worker.stop()
            self._frame_retired.append(worker)
            self._frame_generation += 1
            self.precise_loading.emit(False)
        self._frame_retired = [w for w in self._frame_retired if w.isRunning()]
        if clear_pending:
            self._frame_pending = None

    def _clear_frame_cache(self) -> None:
        self._cancel_frame_window()
        self._frame_cache.clear()

    def _frame_window_ready(self, generation: int, frames) -> None:
        if generation != self._frame_generation:
            return
        worker, self._frame_worker = self._frame_worker, None
        if worker is not None and worker.isRunning():
            self._frame_retired.append(worker)
        self._frame_cache.replace(frames)
        self.precise_loading.emit(False)
        target, self._frame_pending = self._frame_pending, None
        cached = self._frame_cache.get(target) if target is not None else None
        if cached is None:
            self.precise_failed.emit(
                "The exact source frame was not returned by ffmpeg")
            return
        self._emit_precise(cached)

    def _frame_window_failed(self, generation: int, message: str) -> None:
        if generation != self._frame_generation:
            return
        worker, self._frame_worker = self._frame_worker, None
        if worker is not None and worker.isRunning():
            self._frame_retired.append(worker)
        self._frame_pending = None
        self.precise_loading.emit(False)
        self.precise_failed.emit(message)

    def _emit_precise(self, frame: CachedFrame) -> None:
        self.position = frame.seconds
        self.precise_frame_ready.emit(
            self._to_image(frame.pixels, self._frame_size),
            frame.seconds,
            frame.frame_number,
        )

    # -- the clock ------------------------------------------------------------

    def _tick(self) -> None:
        wanted = self._playclock.advance(starved=self._starved)
        frame = self._pick(wanted)
        if frame is not None:
            when, data = frame
            self.position = when
            self.frame_ready.emit(self._to_image(data), when)
            return
        if self._stream_ended and self._pending is None and self._frames.empty():
            self._finish()

    def _pick(self, wanted: float) -> tuple[float, bytes] | None:
        """The newest queued frame that is not still in the future.

        Everything older is thrown away rather than shown. Painting them would
        be playing catch-up in slow motion, and the point of pacing against a
        clock is that a late frame costs one frame, not a growing lag.
        """
        chosen = None
        while True:
            if self._pending is None:
                try:
                    self._pending = self._frames.get_nowait()
                except queue.Empty:
                    self._starved = chosen is None
                    return chosen
            if self._pending[0] > wanted + 1e-6:
                self._starved = False
                return chosen
            chosen, self._pending = self._pending, None

    def _to_image(self, data: bytes, size: PreviewSize | None = None) -> QImage:
        """Raw RGB into something Qt will paint.

        Done here rather than on the decode thread on purpose: frames that
        arrive too late are dropped by `_pick` and never reach this, so the
        conversion is paid for once per frame shown rather than once per frame
        decoded. It also keeps the worker free of GUI types.

        convertToFormat returns an independent image, which matters because
        QImage does not copy the buffer it is handed and `data` is about to go
        out of scope.
        """
        size = size or self.size
        image = QImage(data, size.width, size.height,
                       size.stride, QImage.Format.Format_RGB888)
        return image.convertToFormat(QImage.Format.Format_RGB32)

    def _finish(self) -> None:
        self.is_playing = False
        self._timer.stop()
        self._release()
        self.state_changed.emit(False)
        self.ended.emit()


class FrameView(QWidget):
    """Paints whatever picture it was last given, and nothing else.

    A QLabel with a scaled pixmap was doing this job at 176x99, which is fine
    for confirming which clip you are looking at and useless for deciding where
    a flight begins. This scales to whatever room it is given and keeps the
    aspect ratio, so the same widget shows a filmstrip still while paused and
    live frames while playing without the two disagreeing about geometry.
    """

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        # Focusable because the playback keys are scoped to this widget: they
        # only work when it has focus, which is what keeps Space free for the
        # clip list to tick rows with.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._image = QImage()
        self._message = "Select a clip"
        self._aspect = 16 / 9

    @property
    def aspect(self) -> float:
        """Width over height of the pictures this is being given.

        Read by the window to work out how tall this is worth making: past
        `width / aspect` every extra pixel of height is a black bar, and the
        clip list wants those pixels.
        """
        return self._aspect

    def set_aspect(self, ratio: float) -> None:
        self._aspect = ratio if ratio > 0.1 else 16 / 9

    def set_image(self, image: QImage) -> None:
        self._image = image
        self._message = ""
        self.update()

    def set_message(self, text: str) -> None:
        self._image = QImage()
        self._message = text
        self.update()

    def has_image(self) -> bool:
        return not self._image.isNull()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor(16, 16, 16))

        if not self._image.isNull():
            scaled = self._image.size().scaled(
                rect.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(rect.center())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, self._image)
        elif self._message:
            painter.setPen(self.palette().color(QPalette.ColorRole.Mid))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._message)

        if self.hasFocus():
            # Without this there is no way to tell whether the keys will do
            # anything, which is the whole risk of scoping them to a widget.
            painter.setPen(self.palette().color(QPalette.ColorRole.Highlight))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.end()

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.clicked.emit()
