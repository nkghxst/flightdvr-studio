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

"""End-to-end check: run every preset through the real job queue on real footage.

Cuts a short segment off a genuine Box Pro recording, exports it with each
preset, and verifies the results with ffprobe.

    python tools/smoke_test.py "F:\\FPV clips\\hdz_260.ts"
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from flightdvr.jobs import ExportWorker, Job, JobStatus, write_concat_file  # noqa: E402
from flightdvr.media import NO_WINDOW, find_tools, probe  # noqa: E402
from flightdvr.presets import PRESET_ORDER, ExportSettings, output_path  # noqa: E402

SEGMENT_SECONDS = 12


def make_segment(tools, source: Path, target: Path) -> Path:
    """A short copy of the source, still a genuine MPEG-TS with the same quirks."""
    subprocess.run(
        [str(tools.ffmpeg), "-y", "-v", "error", "-ss", "10", "-t", str(SEGMENT_SECONDS),
         "-i", str(source), "-c", "copy", "-f", "mpegts", str(target)],
        check=True, creationflags=NO_WINDOW,
    )
    return target


def describe(tools, path: Path) -> str:
    result = subprocess.run(
        [str(tools.ffprobe), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,width,height,pix_fmt,color_range,r_frame_rate",
         "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, creationflags=NO_WINDOW,
    )
    return " ".join(result.stdout.split())


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("Pass the path to a .ts recording")
    source = Path(sys.argv[1])
    if not source.exists():
        raise SystemExit(f"No such file: {source}")

    QCoreApplication([])  # ExportWorker is a QThread and needs an event loop object
    tools = find_tools()

    with tempfile.TemporaryDirectory() as temporary:
        tmp = Path(temporary)
        out_dir = tmp / "out"
        segment = make_segment(tools, source, tmp / "segment.ts")
        clip = probe(tools, segment)

        print(f"\nSource segment: {describe(tools, segment)}")
        print(f"  full range: {clip.is_full_range}  duration: {clip.duration:.1f}s\n")

        settings = ExportSettings(social_size_mb=8)
        jobs = [
            Job([clip], key, settings, output_path(out_dir, clip.stem, key, True))
            for key in PRESET_ORDER
        ]
        # And one joined export, exercising the concat path.
        joined = write_concat_file([clip, clip], tmp, "joined")
        jobs.append(
            Job([clip, clip], "master", settings,
                output_path(out_dir, "joined", "master", True), concat_file=joined)
        )

        worker = ExportWorker(tools, jobs, tmp)
        worker.job_progress.connect(
            lambda i, f, s: print(f"\r  {jobs[i].preset_label:8s} {f * 100:5.1f}%  {s}",
                                  end="", flush=True)
        )
        worker.run()  # run inline rather than as a thread

        print("\n\nResults")
        print("-" * 96)
        failures = 0
        for job in jobs:
            tag = "ok " if job.status == JobStatus.DONE else "FAIL"
            if job.status != JobStatus.DONE:
                failures += 1
            size = job.out_path.stat().st_size / (1024 * 1024) if job.out_path.exists() else 0
            name = f"{job.preset_label}{' (joined)' if len(job.clips) > 1 else ''}"
            print(f"[{tag}] {name:18s} {size:8.1f} MB  {job.message}")
            if job.out_path.exists():
                print(f"       {describe(tools, job.out_path)}")

        print("-" * 96)
        print("all presets produced output" if not failures else f"{failures} preset(s) failed")
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
