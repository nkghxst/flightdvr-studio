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

"""`--check-export <folder>`: prove a packaged build can probe and export.

`--check` proves Qt starts and that ffmpeg resolves, but never runs it. This
runs the app's own probe and export code inside the packaged process, with the
ffmpeg it bundles, on media it generates itself, and reports what came out.
It is a diagnostic for CI and for a build that misbehaves on someone's
machine, not a feature: it opens no window, reads no settings and touches no
user files.

Everything it writes goes into a new folder it creates inside the one it is
given, and it refuses to reuse one that already exists. It fails closed: a
missing or system ffmpeg, a probe or export that fails or runs past its time
limit, or an output whose streams are not what the preset makes all return
non-zero. Whatever happens, `receipt.json` in that folder records the tools
and where they came from, the code paths exercised, the input and output
hashes, and every measured property and failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import traceback
from copy import deepcopy
from pathlib import Path

# The longest the exports below may run together before they are cancelled.
EXPORT_SECONDS = 600
SOURCE_SECONDS = 3.0

# What each representative export must contain, read back with the app's own
# probe. Master runs the Rec.709 conversion, which is the zscale path, on a
# trimmed range; Edit is the mezzanine; Remux copies the streams untouched.
EXPECTED = {
    "master": {"video_codec": "h264", "audio_codec": "aac", "duration": 2.0},
    "edit": {"video_codec": "dnxhd", "audio_codec": "pcm_s16le", "duration": SOURCE_SECONDS},
    "remux": {"video_codec": "hevc", "audio_codec": "aac", "duration": SOURCE_SECONDS},
}
DURATION_TOLERANCE = 0.15


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path: Path) -> dict:
    return {"path": str(path), "size": path.stat().st_size, "sha256": _sha256(path)}


def _write_receipt(folder: Path, receipt: dict) -> Path:
    target = folder / "receipt.json"
    temporary = folder / "receipt.json.partial"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=1, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    return target


def _new_child(parent: Path) -> Path:
    """A folder nobody else has written to: created here, never reused."""
    name = f"flightdvr-check-export-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
    child = parent / name
    child.mkdir()                      # FileExistsError rather than reuse
    return child


def _clip_facts(info) -> dict:
    return {"duration": info.duration, "width": info.width, "height": info.height,
            "fps": info.fps, "video_codec": info.video_codec,
            "audio_codec": info.audio_codec, "pix_fmt": info.pix_fmt,
            "color_range": info.color_range, "error": info.error}


def check_export(argument: str | None, export_seconds: float = EXPORT_SECONDS) -> tuple[str, int]:
    """Run the diagnostic and return (report, exit code)."""
    if not argument:
        return "--check-export needs a folder to write into.", 2
    parent = Path(argument)
    if not parent.is_dir():
        return f"--check-export: {parent} is not an existing folder.", 2
    try:
        child = _new_child(parent)
    except OSError as exc:
        return f"--check-export: could not create a new folder in {parent}: {exc}", 2

    receipt: dict = {"result": "FAIL", "failures": [], "folder": str(child),
                     "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    def fail(message: str) -> None:
        receipt["failures"].append(message)

    try:
        _run(child, receipt, fail, export_seconds)
    except Exception:                    # noqa: BLE001 — recorded, never raised
        fail("unexpected error:\n" + traceback.format_exc())
    finally:
        if not receipt["failures"]:
            receipt["result"] = "PASS"
        receipt["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        path = _write_receipt(child, receipt)

    lines = [f"check-export {receipt['result']}", f"receipt {path}"]
    lines += [f"  {line}" for message in receipt["failures"] for line in message.splitlines()]
    return "\n".join(lines), 0 if receipt["result"] == "PASS" else 1


def _run(child: Path, receipt: dict, fail, export_seconds: float) -> None:
    from PySide6.QtCore import QCoreApplication

    from . import __version__
    from .jobs import ExportWorker, Job, JobStatus
    from .media import ToolsMissing, find_tools, is_bundled, probe, run_hidden
    from .presets import REC709, ExportSettings, output_path

    receipt["app"] = {"version": __version__, "executable": sys.executable,
                      "frozen": bool(getattr(sys, "frozen", False)),
                      "bundle": getattr(sys, "_MEIPASS", None),
                      "platform": sys.platform}
    appimage = os.environ.get("APPIMAGE")
    if appimage and Path(appimage).is_file():
        receipt["app"]["appimage"] = _identity(Path(appimage))
    receipt["paths_exercised"] = [
        "flightdvr.media.find_tools", "flightdvr.media.is_bundled",
        "flightdvr.media.probe", "flightdvr.jobs.ExportWorker.run",
        "flightdvr.presets.output_path"]

    try:
        tools = find_tools()
    except ToolsMissing as exc:
        fail(f"ffmpeg not found: {exc}")
        return
    receipt["tools"] = {}
    for name, path in (("ffmpeg", tools.ffmpeg), ("ffprobe", tools.ffprobe)):
        entry = _identity(Path(path))
        entry["bundled"] = is_bundled(Path(path))
        receipt["tools"][name] = entry
        if not entry["bundled"]:
            fail(f"{name} at {path} is not the bundled copy; refusing to report on it")
    if receipt["failures"]:
        return
    version = run_hidden([str(tools.ffmpeg), "-hide_banner", "-version"], timeout=30)
    receipt["tools"]["ffmpeg"]["version"] = (version.stdout.splitlines() or [""])[0]

    app = QCoreApplication.instance() or QCoreApplication([])
    receipt["qt_application"] = type(app).__name__

    # Shaped like the goggles' recordings: MPEG-TS, HEVC, 60 fps, full range,
    # a keyframe every second, AAC sound.
    media = child / "media"
    media.mkdir()
    source = media / "generated.ts"
    made = run_hidden([
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=60:duration={SOURCE_SECONDS}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={SOURCE_SECONDS}",
        "-c:v", "libx265", "-preset", "ultrafast",
        "-x265-params", "keyint=60:min-keyint=60:scenecut=0:log-level=none",
        "-vf", "scale=in_range=limited:out_range=full",
        "-pix_fmt", "yuv420p", "-color_range", "pc",
        "-c:a", "aac", "-b:a", "96k", "-f", "mpegts", str(source)], timeout=120)
    receipt["source"] = {"exit": made.returncode, "stderr": made.stderr[-500:]}
    if made.returncode != 0 or not source.is_file():
        fail(f"could not generate the source clip (exit {made.returncode})")
        return
    receipt["source"].update(_identity(source))

    info = probe(tools, source)
    receipt["source"]["probe"] = _clip_facts(info)
    if info.error or not info.video_codec or info.video_codec != "hevc":
        fail(f"the app's probe did not read the source: {info.error or info.video_codec!r}")
        return
    if not info.is_full_range:
        fail("the app's probe did not see the source as full range")
    if abs(info.duration - SOURCE_SECONDS) > DURATION_TOLERANCE:
        fail(f"probed source duration {info.duration} is not {SOURCE_SECONDS}")

    exports = child / "exports"
    exports.mkdir()
    work = child / "work"
    work.mkdir()
    trimmed = deepcopy(info)
    trimmed.trim_in, trimmed.trim_out = 0.5, 2.5
    jobs = {
        "master": Job(clips=[trimmed], preset_key="master",
                      settings=ExportSettings(colour=REC709),
                      out_path=output_path(exports, "generated", "master", False)),
        "edit": Job(clips=[info], preset_key="edit", settings=ExportSettings(),
                    out_path=output_path(exports, "generated", "edit", False)),
        "remux": Job(clips=[info], preset_key="remux", settings=ExportSettings(),
                     out_path=output_path(exports, "generated", "remux", False)),
    }
    worker = ExportWorker(tools, list(jobs.values()), work)
    started = time.monotonic()
    worker.start()
    in_time = worker.wait(int(export_seconds * 1000))
    if not in_time:
        # The worker's own cancellation, exactly as the window uses it.
        worker.cancel()
        stopped = worker.wait(60_000)
        fail(f"exports did not finish within {export_seconds:g} s"
             + ("" if stopped else "; the worker did not stop within 60 s after cancel"))
    receipt["export_seconds"] = round(time.monotonic() - started, 3)

    receipt["exports"] = {}
    for name, job in jobs.items():
        entry = {"preset": job.preset_key, "status": job.status.value,
                 "message": job.message, "out_path": str(job.out_path)}
        receipt["exports"][name] = entry
        if job.status is not JobStatus.DONE:
            fail(f"{name} export ended {job.status.value}: {job.message}")
            continue
        if not job.out_path.is_file():
            fail(f"{name} export reported Done but {job.out_path} does not exist")
            continue
        entry.update(_identity(job.out_path))
        produced = probe(tools, job.out_path)
        entry["probe"] = _clip_facts(produced)
        expected = EXPECTED[name]
        for field in ("video_codec", "audio_codec"):
            if getattr(produced, field) != expected[field]:
                fail(f"{name}: {field} is {getattr(produced, field)!r}, expected {expected[field]!r}")
        if abs(produced.duration - expected["duration"]) > DURATION_TOLERANCE:
            fail(f"{name}: duration {produced.duration} is not {expected['duration']}")

    leftovers = sorted(str(p) for p in child.rglob("*") if ".flightdvr-part" in p.name)
    receipt["unfinished_files_left"] = leftovers
    if leftovers:
        fail("unfinished export files were left behind: " + ", ".join(leftovers))
