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
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .media import NO_WINDOW, ClipInfo, Tools
from .presets import PRESETS, ExportSettings, build_commands

# Pass 1 of a two-pass encode analyses without writing video, so it is quicker.
PASS_WEIGHTS = (0.35, 0.65)


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
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
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
        try:
            job.out_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, f"Cannot create output folder: {exc}"

        try:
            commands = build_commands(
                self.tools,
                job.clips[0],
                job.preset_key,
                job.settings,
                job.out_path,
                self.work_dir,
                sources=[c.path for c in job.clips],
                concat_file=job.concat_file,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return False, f"Could not build command: {exc}"

        # Remembered so a half-written file can be cleared up without ever
        # deleting something that was already there.
        existed_before = job.out_path.exists()

        duration = job.total_duration
        offset = 0.0
        for pass_index, command in enumerate(commands):
            weight = PASS_WEIGHTS[pass_index] if len(commands) > 1 else 1.0
            ok, message = self._run_one(index, command, duration, offset, weight)
            if not ok:
                self._cleanup_pass_logs(job)
                removed = self._discard_partial(job, existed_before)
                if removed:
                    message = f"{message} (partial file removed)"
                return False, message
            offset += weight

        self._cleanup_pass_logs(job)

        if not job.out_path.exists() or job.out_path.stat().st_size == 0:
            return False, "ffmpeg finished but produced no output file"
        return True, f"{job.out_path.stat().st_size / (1024 * 1024):.0f} MB"

    def _discard_partial(self, job: Job, existed_before: bool) -> bool:
        """Delete the incomplete output left behind by a cancelled encode.

        An MP4 killed part way through has no moov atom and will not play, but
        it sits in the output folder under a perfectly ordinary name. Leaving it
        there is worse than not having it. Files that were already on disk
        before this job started are never touched.
        """
        if existed_before or not job.out_path.exists():
            return False
        try:
            job.out_path.unlink()
            return True
        except OSError:
            return False

    def _run_one(
        self, index: int, command: list[str], duration: float, offset: float, weight: float
    ) -> tuple[bool, str]:
        # -progress writes machine-readable status to stdout; -nostats silences
        # the human-readable version that would otherwise clutter stderr.
        command = command[:1] + ["-progress", "pipe:1", "-nostats"] + command[1:]

        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                creationflags=NO_WINDOW,
            )
        except OSError as exc:
            return False, f"Could not start ffmpeg: {exc}"

        proc = self._process
        assert proc.stdout is not None
        speed = ""
        for line in proc.stdout:
            if self._cancel:
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

        stderr = ""
        try:
            stderr = (proc.stderr.read() if proc.stderr else "") or ""
        except (OSError, ValueError):
            pass
        code = proc.wait()
        self._process = None

        if self._cancel:
            return False, "Cancelled"
        if code != 0:
            tail = [ln for ln in stderr.strip().splitlines() if ln.strip()]
            detail = tail[-1][:300] if tail else f"exit code {code}"
            return False, detail
        return True, ""

    def _cleanup_pass_logs(self, job: Job) -> None:
        prefix = f"pass_{job.out_path.stem}"
        for leftover in self.work_dir.glob(prefix + "*"):
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
