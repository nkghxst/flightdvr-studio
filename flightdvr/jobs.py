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

"""The export queue: one worker thread running ffmpeg jobs one at a time.

Jobs run sequentially on purpose. ffmpeg already saturates every core, so
running two encodes at once makes both slower and the progress bars useless.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QThread, Signal

from .media import NO_WINDOW, ClipInfo, Tools
from .presets import PRESETS, ExportSettings, build_commands, join_problems

# Pass 1 of a two-pass encode analyses without writing video, so it is quicker.
PASS_WEIGHTS = (0.35, 0.65)

# How long to let ffmpeg shut down politely before killing it.
TERMINATE_SECONDS = 5

# Enough stderr to explain a failure without holding a whole log in memory.
STDERR_LINES = 200

# ffmpeg reports the cause first and then cascades generic wrappers, thread
# bookkeeping and finally "Conversion failed!". Checked against real failures:
# rejecting a 127x95 encode puts "width not divisible by 2" first and nine less
# useful lines after it, and the app used to show the user the last one.
CASCADE_NOISE = (
    "conversion failed",
    "error splitting the argument list",
    "error while opening encoder",
    "error sending frames to consumers",
    "could not open encoder before eof",
    "nothing was written into output file",
    "task finished with error code",
    "terminating thread",
    "error opening output file",
)

ERROR_WORDS = ("error", "invalid", "unable", "cannot", "no such",
               "not supported", "denied", "full")


def _untagged(line: str) -> str:
    """The text after ffmpeg's "[component @ address]" prefix."""
    if line.startswith("[") and "] " in line:
        return line.split("] ", 1)[1]
    return line


def _describe_failure(log: Iterable[str], code: int) -> str:
    """The line that explains the failure, rather than the last one written.

    Lines carrying a component tag are the ones a failing part of ffmpeg
    emitted, so they are preferred over the informational preamble; the first
    surviving one is the cause, because everything after it is fallout.
    """
    lines = [line.strip() for line in log if line.strip()]
    useful = [ln for ln in lines
              if not _untagged(ln).lower().startswith(CASCADE_NOISE)]
    tagged = [ln for ln in useful if ln.startswith("[")]

    for candidate in (tagged, useful):
        for line in candidate:
            if any(word in line.lower() for word in ERROR_WORDS):
                return line[:300]
    if tagged:
        return tagged[0][:300]
    # Whatever it said, say it. Falling straight through to the exit code threw
    # away ffmpeg's own account of the failure whenever it phrased the problem
    # in words this function does not recognise, which is precisely when the
    # user most needs to read it. Older builds word things differently, and an
    # export failing on Ubuntu 22.04 reported nothing but "exit code 1".
    if useful:
        return useful[-1][:300]
    if lines:
        return lines[-1][:300]
    return f"exit code {code}"


class JobStatus(str, Enum):
    PENDING = "Waiting"
    RUNNING = "Encoding"
    DONE = "Done"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    SKIPPED = "Skipped"


@dataclass
class Job:
    clips: list[ClipInfo]
    preset_key: str
    settings: ExportSettings
    out_path: Path
    concat_file: Path | None = None

    # Kept so a queued job can be re-targeted if the output settings change
    # before it runs. Without this, ticking the flight-date box after queueing
    # silently left the already-queued jobs named the old way.
    out_dir: Path | None = None
    stem: str = ""
    subfolders: bool = True

    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    speed: str = ""
    message: str = ""
    elapsed: float = 0.0

    def retarget(self, flight_date) -> None:
        """Recompute the output path after an output setting changed."""
        if self.status is not JobStatus.PENDING or self.out_dir is None:
            return
        from .presets import output_path
        self.out_path = output_path(
            self.out_dir, self.stem, self.preset_key, self.subfolders, flight_date
        )

    @property
    def name(self) -> str:
        if len(self.clips) > 1:
            return f"{len(self.clips)} clips joined"
        return self.clips[0].path.name

    @property
    def preset_label(self) -> str:
        return PRESETS[self.preset_key].label

    @property
    def total_duration(self) -> float:
        """Footage this job will actually encode, after any trimming."""
        return sum(c.trimmed_duration or c.duration for c in self.clips)


def _parse_progress(line: str) -> tuple[str, str] | None:
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    return key.strip(), value.strip()


class ExportWorker(QThread):
    """Runs a list of jobs, reporting progress as it goes."""

    job_started = Signal(int)
    job_progress = Signal(int, float, str)   # index, 0..1, speed text
    job_finished = Signal(int, bool, str)    # index, ok, message
    queue_finished = Signal(int, int)        # completed, failed

    def __init__(self, tools: Tools, jobs: list[Job], work_dir: Path, parent=None):
        super().__init__(parent)
        self.tools = tools
        self.jobs = jobs
        self.work_dir = work_dir
        self._cancel = False
        self._process: subprocess.Popen | None = None

    def cancel(self) -> None:
        self._cancel = True
        proc = self._process
        if proc is not None:
            self._stop(proc)

    @staticmethod
    def _stop(proc: subprocess.Popen) -> None:
        """Stop ffmpeg and make sure it has actually gone.

        terminate() on its own is a request. An encode that ignores it used to
        be left running while the app carried on, and closing the window could
        orphan it entirely.
        """
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
        except OSError:
            return
        try:
            proc.wait(timeout=TERMINATE_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=TERMINATE_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                pass

    # -- execution ------------------------------------------------------------

    def run(self) -> None:  # noqa: D102  (QThread entry point)
        completed = failed = 0
        for index, job in enumerate(self.jobs):
            # Anything already run, skipped or cancelled stays as it is.
            # Without this, pressing Start again re-encoded finished jobs.
            if job.status is not JobStatus.PENDING:
                continue

            if self._cancel:
                job.status = JobStatus.CANCELLED
                self.job_finished.emit(index, False, "Cancelled")
                continue

            self.job_started.emit(index)
            job.status = JobStatus.RUNNING
            started = time.monotonic()
            ok, message = self._run_job(index, job)
            job.elapsed = time.monotonic() - started

            if self._cancel and not ok:
                job.status = JobStatus.CANCELLED
                message = "Cancelled"
            elif ok:
                job.status = JobStatus.DONE
                job.progress = 1.0
                completed += 1
            else:
                job.status = JobStatus.FAILED
                failed += 1

            job.message = message
            self.job_finished.emit(index, ok, message)

        self.queue_finished.emit(completed, failed)

    def _run_job(self, index: int, job: Job) -> tuple[bool, str]:
        # Refused here as well as in the window, so a job that reaches the
        # worker by any route cannot produce a file that is quietly wrong.
        if len(job.clips) > 1:
            # Stream copy cannot normalise anything, so a joined remux is held
            # to the stricter standard.
            problems = join_problems(job.clips,
                                     re_encoding=job.preset_key != "remux")
            if problems:
                return False, "Cannot join these clips: " + "; ".join(problems)

        try:
            job.out_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, f"Cannot create output folder: {exc}"

        # Everything is written beside the target under a temporary name and
        # moved into place only after it has been checked.
        #
        # ffmpeg is given -y, so aiming it at the real path meant it truncated
        # the file on open. A failed or cancelled encode then left a destroyed
        # file where a working one had been, and the old cleanup deliberately
        # refused to remove it because it had existed beforehand. Overwriting
        # an export is now all-or-nothing: same directory, so the move is
        # atomic and the previous file survives every failure.
        temp_path = job.out_path.with_name(
            f"{job.out_path.stem}.flightdvr-part{job.out_path.suffix}"
        )
        self._remove(temp_path)

        try:
            commands = build_commands(
                self.tools,
                job.clips[0],
                job.preset_key,
                job.settings,
                temp_path,
                self.work_dir,
                sources=[c.path for c in job.clips],
                concat_file=job.concat_file,
                clips=job.clips,
                # The finished file is as long as every clip together. Sizing a
                # joined export from clips[0] alone overshot the target by
                # roughly the number of clips in it.
                total_duration=job.total_duration,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return False, f"Could not build command: {exc}"

        duration = job.total_duration
        offset = 0.0
        try:
            for pass_index, command in enumerate(commands):
                weight = PASS_WEIGHTS[pass_index] if len(commands) > 1 else 1.0
                ok, message = self._run_one(index, command, duration, offset, weight)
                if not ok:
                    return False, message
                offset += weight

            ok, message = self._validate(temp_path)
            if not ok:
                return False, message

            size = temp_path.stat().st_size
            try:
                temp_path.replace(job.out_path)
            except OSError as exc:
                return False, f"Could not put the finished file in place: {exc}"
            return True, f"{size / (1024 * 1024):.0f} MB"
        finally:
            self._cleanup_pass_logs(temp_path)
            # A temporary file still here means the job failed or was
            # cancelled. Whatever the user already had is untouched.
            self._remove(temp_path)

    @staticmethod
    def _remove(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _validate(self, path: Path) -> tuple[bool, str]:
        """Prove ffmpeg produced playable video, rather than trusting exit 0.

        A remux of a selection containing no keyframe, and joined sub-GOP
        trims, both exit successfully having written a container header and
        nothing else. That is a 261-byte MP4 with no streams in it, which the
        queue used to report as "Done, 0 MB".
        """
        try:
            if not path.exists() or path.stat().st_size == 0:
                return False, "ffmpeg finished but produced no output file"
        except OSError as exc:
            return False, f"Could not read the finished file: {exc}"

        try:
            result = subprocess.run(
                [str(self.tools.ffprobe), "-v", "error",
                 "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name:format=duration",
                 "-of", "default=nw=1", str(path)],
                capture_output=True, text=True, timeout=60,
                creationflags=NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"Could not check the finished file: {exc}"

        fields = dict(
            line.split("=", 1)
            for line in result.stdout.splitlines() if "=" in line
        )
        if not fields.get("codec_name", "").strip():
            return False, (
                "ffmpeg produced a file with no video in it. Copy without "
                "re-encoding needs a keyframe inside the selection — move the "
                "in point, or choose a preset that re-encodes."
            )
        try:
            seconds = float(fields.get("duration", "") or 0)
        except ValueError:
            seconds = 0.0
        if seconds <= 0.05:
            return False, "ffmpeg produced a file with no playable video in it"
        return True, ""

    def _run_one(
        self, index: int, command: list[str], duration: float, offset: float, weight: float
    ) -> tuple[bool, str]:
        # -progress writes machine-readable status to stdout; -nostats silences
        # the human-readable version that would otherwise clutter stderr.
        command = command[:1] + ["-progress", "pipe:1", "-nostats"] + command[1:]

        if self._cancel:
            return False, "Cancelled"

        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                creationflags=NO_WINDOW,
            )
        except OSError as exc:
            return False, f"Could not start ffmpeg: {exc}"

        self._process = proc
        # cancel() can land between the check above and this assignment, where
        # it would find no process to stop. This is the only thing that catches
        # an encode cancelled during its own startup.
        if self._cancel:
            self._stop(proc)

        # stderr is drained on its own thread. Reading it only once stdout had
        # closed meant a chatty encode could fill the stderr pipe, block ffmpeg
        # writing to it, and so stop the stdout this loop waits on — a deadlock
        # with no timeout on either side. A DVR file truncated by a mid-flight
        # power loss produces exactly that volume of decoder warnings.
        log: deque[str] = deque(maxlen=STDERR_LINES)
        reader = threading.Thread(
            target=self._drain, args=(proc.stderr, log), daemon=True
        )
        reader.start()

        speed = ""
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if self._cancel:
                    self._stop(proc)
                    break
                parsed = _parse_progress(line)
                if not parsed:
                    continue
                key, value = parsed
                if key == "speed":
                    speed = value
                elif key == "out_time_us" and duration > 0:
                    try:
                        seconds = int(value) / 1_000_000
                    except ValueError:
                        continue
                    fraction = max(0.0, min(1.0, seconds / duration))
                    self.job_progress.emit(index, offset + fraction * weight, speed)
        finally:
            try:
                code = proc.wait(timeout=TERMINATE_SECONDS * 6)
            except subprocess.TimeoutExpired:
                self._stop(proc)
                code = proc.poll() if proc.poll() is not None else -1
            reader.join(timeout=TERMINATE_SECONDS)
            self._process = None

        if self._cancel:
            return False, "Cancelled"
        if code != 0:
            return False, _describe_failure(log, code)
        return True, ""

    @staticmethod
    def _drain(pipe, log: deque[str]) -> None:
        """Read stderr as it arrives so ffmpeg never blocks writing to it."""
        if pipe is None:
            return
        try:
            for line in pipe:
                text = line.rstrip()
                if text:
                    log.append(text)
        except (OSError, ValueError):
            pass

    def _cleanup_pass_logs(self, encoded_to: Path) -> None:
        """Remove the two-pass statistics files for a finished encode.

        Named after the path ffmpeg was actually pointed at, which is the
        temporary file rather than the final one.
        """
        for leftover in self.work_dir.glob(f"pass_{encoded_to.stem}*"):
            try:
                leftover.unlink()
            except OSError:
                pass


def write_concat_file(clips: list[ClipInfo], work_dir: Path, name: str) -> Path:
    """Concat-demuxer list used when several clips are joined into one export."""
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / f"concat_{name}.txt"
    lines = []
    for clip in clips:
        # The concat demuxer needs single quotes escaped this specific way.
        escaped = str(clip.path).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
        # Trims travel with the list, so a joined export honours each clip's
        # own in and out points rather than one range across the whole thing.
        if clip.trim_in > 0.01:
            lines.append(f"inpoint {clip.trim_in:.3f}")
        if clip.trim_out > 0.01:
            lines.append(f"outpoint {clip.out_point:.3f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
