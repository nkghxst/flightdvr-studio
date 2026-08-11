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

"""Extract the exact paused preview frame as a full-resolution PNG."""

from __future__ import annotations

import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .media import (
    NO_WINDOW, ClipInfo, Tools, frame_rate_mode, request_stop, stop_process,
)
from .player import SEEK_LEAD_IN, SHOWINFO_TIME
from .presets import colour_filters


@dataclass(frozen=True)
class StillRequest:
    """One picture whose frame identity came from the precise decoder."""

    clip: ClipInfo
    frame_number: int
    seconds: float
    colour: str
    target: Path


def still_temp_path(target: Path, request_id: str = "") -> Path:
    """The beside-target path used until a complete PNG is ready."""
    marker = f".flightdvr-part-{request_id}" if request_id else ".flightdvr-part"
    return target.with_name(f"{target.stem}{marker}{target.suffix}")


def build_still_command(tools: Tools, request: StillRequest,
                        output: Path) -> list[str]:
    """Build a one-frame extraction tied to the precise decoder's PTS.

    The native-rate preview decoder already proved which source frame is on
    screen and recorded its showinfo timestamp. Starting from half a frame
    before that timestamp repeats the same selection rule instead of deriving
    a frame later from the wall-clock playhead. ``showinfo`` stays in this
    command so the worker can prove that ffmpeg returned the requested frame
    before it publishes anything.

    There is deliberately no scale or pad filter. The PNG must keep the
    source dimensions; Qt scales only the preview painted on screen.
    """
    if request.frame_number < 0 or request.seconds < 0:
        raise ValueError("A still needs a real source frame and timestamp")

    rate = request.clip.fps if request.clip.fps > 0 else 30.0
    boundary = max(0.0, request.seconds - 0.5 / rate)
    fast = max(0.0, boundary - SEEK_LEAD_IN)
    filters = [
        f"select='gte(t,{boundary:.9f})'",
        *colour_filters(request.colour, request.clip, "rgb24"),
        "showinfo",
    ]

    command = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "info"]
    if fast > 0.01:
        command += ["-ss", f"{fast:.6f}"]
    command += ["-copyts", "-start_at_zero", "-i", str(request.clip.path)]
    command += [
        "-map", "0:v:0", "-an",
        "-vf", ",".join(filters),
        *frame_rate_mode(tools, "passthrough"),
        "-frames:v", "1", "-c:v", "png",
        "-f", "image2", "-update", "1", "-y", str(output),
    ]
    return command


def timestamp_matches(request: StillRequest, decoded_seconds: float) -> bool:
    """Whether showinfo identifies the exact requested source picture.

    A quarter-frame allowance covers timestamp rounding while remaining far
    enough below one whole frame that an adjacent picture cannot pass.
    """
    rate = request.clip.fps if request.clip.fps > 0 else 30.0
    return abs(decoded_seconds - request.seconds) <= 0.25 / rate


class StillWorker(QThread):
    """Run one atomic still capture without holding the interface still."""

    saved = Signal(int, str)             # generation, final path
    failed = Signal(int, str)
    cancelled = Signal(int)

    def __init__(self, tools: Tools, request: StillRequest, generation: int,
                 parent=None):
        super().__init__(parent)
        self.tools = tools
        self.request = request
        self.generation = generation
        self._cancel = False
        self._published = False
        self._state_lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        # A clip change retires rather than waits for its old worker, so two
        # requests may briefly overlap. Distinct beside-target paths stop the
        # old worker's cleanup from deleting the new worker's PNG.
        self._temporary = still_temp_path(request.target, uuid.uuid4().hex)

    def stop(self) -> None:
        """Ask from the UI thread; the bounded wait stays on this worker."""
        with self._state_lock:
            if self._published:
                return
            self._cancel = True
            proc = self._process
        request_stop(proc)

    def run(self) -> None:  # noqa: D102 (QThread entry point)
        ok, message = self._capture()
        if self._was_cancelled():
            self.cancelled.emit(self.generation)
        elif ok:
            self.saved.emit(self.generation, str(self.request.target))
        else:
            self.failed.emit(self.generation, message)

    def _was_cancelled(self) -> bool:
        with self._state_lock:
            return self._cancel and not self._published

    def _capture(self) -> tuple[bool, str]:
        """Run, validate and publish; separated so integration can inspect it."""
        target = self.request.target
        temporary = self._temporary
        self._remove(temporary)

        try:
            command = build_still_command(self.tools, self.request, temporary)
        except (TypeError, ValueError) as problem:
            return False, str(problem)

        try:
            proc = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=NO_WINDOW,
            )
        except OSError as problem:
            return False, f"Could not start ffmpeg: {problem}"

        with self._state_lock:
            self._process = proc
            cancelled_before_start = self._cancel
        if cancelled_before_start:
            stop_process(proc)

        communication_problem = None
        stderr = b""
        code = None
        try:
            _, stderr = proc.communicate()
            code = proc.returncode
        except (OSError, subprocess.SubprocessError) as problem:
            communication_problem = problem
            code = proc.poll()
        finally:
            # communicate() normally reaps it. Cancellation can interrupt that
            # normal path, so the worker still owns the bounded escalation.
            stop_process(proc)
            with self._state_lock:
                self._process = None

        try:
            if self._was_cancelled():
                return False, "Cancelled"
            if communication_problem is not None:
                return False, f"Could not read ffmpeg output: {communication_problem}"
            log = (stderr or b"").decode("utf-8", "replace").splitlines()
            if code != 0:
                from .jobs import _describe_failure
                return False, _describe_failure(log, code)

            timestamps = []
            for line in log:
                match = SHOWINFO_TIME.search(line)
                if match:
                    try:
                        timestamps.append(float(match.group(1)))
                    except ValueError:
                        continue
            if not timestamps:
                return False, "ffmpeg did not identify the extracted frame"
            if not timestamp_matches(self.request, timestamps[0]):
                return False, (
                    "ffmpeg returned source time "
                    f"{timestamps[0]:.6f}s instead of the requested "
                    f"{self.request.seconds:.6f}s"
                )

            try:
                if not temporary.exists() or temporary.stat().st_size == 0:
                    return False, "ffmpeg finished but produced no PNG"
            except OSError as problem:
                return False, f"Could not read the finished PNG: {problem}"

            image = QImage(str(temporary))
            if image.isNull():
                return False, "ffmpeg produced a PNG that could not be read"
            expected = (self.request.clip.width, self.request.clip.height)
            actual = (image.width(), image.height())
            if all(expected) and actual != expected:
                return False, (
                    f"ffmpeg produced {actual[0]}x{actual[1]}, not the "
                    f"source {expected[0]}x{expected[1]}"
                )

            # stop() and publication are serialised. If cancellation wins the
            # lock, nothing is published; if replace wins, the request already
            # completed atomically and a late stop cannot relabel it cancelled.
            with self._state_lock:
                if self._cancel:
                    return False, "Cancelled"
                try:
                    temporary.replace(target)
                except OSError as problem:
                    return False, f"Could not put the PNG in place: {problem}"
                self._published = True
            return True, f"{actual[0]}x{actual[1]} PNG"
        finally:
            # Anything left here failed or was cancelled. The target, including
            # an earlier still approved for overwrite, remains untouched.
            self._remove(temporary)

    @staticmethod
    def _remove(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
