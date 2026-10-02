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

# The longest the exports below may run together before they are cancelled,
# and how long a cancelled worker then has to confirm it has stopped.
EXPORT_SECONDS = 600
STOP_SECONDS = 60

# Exit code when the export worker could not be confirmed stopped. The receipt
# is already on disk by then; the process leaves without tearing the thread
# down, because destroying a QThread that is still running aborts Python.
UNSTOPPED_EXIT = 3

# Workers that never confirmed they stopped. Holding them here means nothing
# releases a running QThread before the process ends.
_UNSTOPPED: list = []
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


def check_export(argument: str | None, export_seconds: float = EXPORT_SECONDS,
                 _exit=os._exit) -> tuple[str, int]:
    """Run the diagnostic and return (report, exit code).

    The export worker belongs to this function from the moment it starts:
    whatever happens inside, timeout or exception, it is settled here before
    the receipt is written. A worker that cannot be confirmed stopped is
    recorded as such and kept referenced before anything else is attempted,
    and the process exits with UNSTOPPED_EXIT once the receipt and report have
    been tried, rather than returning into code that would release a running
    thread. A receipt that cannot be written makes the result FAIL; it is
    reported, never raised.
    """
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

    owned: dict = {"worker": None}
    try:
        _run(child, receipt, fail, export_seconds, owned)
    except Exception:                    # noqa: BLE001 — recorded, never raised
        fail("unexpected error:\n" + traceback.format_exc())
    finally:
        try:
            stopped = _settle(owned["worker"], receipt, fail)
        except Exception:                # noqa: BLE001 — recorded, never raised
            fail("could not settle the export worker:\n" + traceback.format_exc())
            stopped = False
        if not stopped:
            # Held before anything else can fail, so no later error, the
            # receipt write included, can unwind past a running worker.
            _UNSTOPPED.append(owned["worker"])
        if not receipt["failures"]:
            receipt["result"] = "PASS"
        receipt["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        try:
            where = str(_write_receipt(child, receipt))
        except Exception as exc:         # noqa: BLE001 — reported, never raised
            # A check whose receipt is not on disk has not passed.
            receipt["result"] = "FAIL"
            fail(f"the receipt could not be written: {type(exc).__name__}: {exc}")
            where = f"NOT WRITTEN in {child}"

    lines = [f"check-export {receipt['result']}", f"receipt {where}"]
    lines += [f"  {line}" for message in receipt["failures"] for line in message.splitlines()]
    report = "\n".join(lines)
    if not stopped:
        try:
            print(report, flush=True)
        except Exception:                # noqa: BLE001 — leaving regardless
            pass
        _exit(UNSTOPPED_EXIT)
    return report, 0 if receipt["result"] == "PASS" else 1


def _settle(worker, receipt: dict, fail) -> bool:
    """Make sure a started worker has stopped. False if that cannot be confirmed.

    Uses only the worker's own cancel(), as the window does: no thread or
    process is forced. Bounded by STOP_SECONDS.
    """
    if worker is None:
        return True
    record = receipt.setdefault("worker", {})
    record["started"] = True
    if worker.isRunning():
        worker.cancel()
        record["cancel_requested"] = True
        record["stopped"] = bool(worker.wait(STOP_SECONDS * 1000))
    else:
        record["stopped"] = True
    if not record["stopped"]:
        fail(f"the export worker could not be confirmed stopped within {STOP_SECONDS} s "
             "of cancelling; its ffmpeg may still be running and its files were not "
             "cleaned up")
    return record["stopped"]


def _run(child: Path, receipt: dict, fail, export_seconds: float, owned: dict) -> None:
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
    owned["worker"] = worker            # check_export settles it on every exit
    started = time.monotonic()
    worker.start()
    in_time = worker.wait(int(export_seconds * 1000))
    receipt["export_seconds"] = round(time.monotonic() - started, 3)
    if not in_time:
        # Cancelled, and confirmed stopped or not, by check_export's _settle
        # before the receipt is written.
        fail(f"exports did not finish within {export_seconds:g} s")
        return

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


# -- `--check-environment <folder> [--external-tools <dir>]` ---------------------------
#
# A second, separate diagnostic for the child-process environment (#131,
# adapted in child_env). It never replaces or weakens --check-export: the
# normal tools are still found by find_tools and must be the bundled pair.
#
# 1. An owned stand-in program written into the new folder (so outside the
#    bundle) is run through the real media.run_hidden. It reports the loader
#    variables it received and an unrelated sentinel. In the frozen Linux app
#    it must get the saved LD_LIBRARY_PATH_ORIG back as LD_LIBRARY_PATH (or
#    none when there was none to save), never the _ORIG itself, and keep the
#    sentinel; the app's own environment must be unchanged afterwards.
# 2. With --external-tools, that folder's ffmpeg and ffprobe -- explicitly
#    named, never discovered -- generate a short clip and a track, which the
#    app's own probe, music-asset inspection and streaming PCM reader read
#    with those external programs.
# 3. The bundled pair (from find_tools, required to be bundled) does the same
#    for a generated track: inspection, one bounded block from the streaming
#    reader, and an explicit close of that reader.

ENVIRONMENT_SENTINEL = "FLIGHTDVR_ENV_SENTINEL"

_STAND_IN = """#!/bin/sh
printf 'LD_LIBRARY_PATH=%s\\n' "${LD_LIBRARY_PATH-<unset>}"
printf 'LD_LIBRARY_PATH_ORIG=%s\\n' "${LD_LIBRARY_PATH_ORIG-<unset>}"
printf 'SENTINEL=%s\\n' "${FLIGHTDVR_ENV_SENTINEL-<unset>}"
"""


def _new_named_child(parent: Path, kind: str) -> Path:
    child = parent / f"flightdvr-check-{kind}-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
    child.mkdir()                      # FileExistsError rather than reuse
    return child


def _stand_in_report(stdout: str) -> dict:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


def _read_track(tools, track: Path, fail, label: str) -> dict:
    """The app's music-asset inspection and one bounded block from its
    streaming PCM reader, closed explicitly whatever happens."""
    from .audio_reader import MAX_READ_FRAMES, FfmpegPcmReader, inspect_music_asset
    record: dict = {}
    asset = inspect_music_asset(tools, track)
    record["asset"] = {"sample_rate": asset.sample_rate, "channels": asset.channels,
                       "decoded_samples": asset.decoded_samples}
    reader = FfmpegPcmReader.for_music(tools, asset)
    try:
        frames = min(MAX_READ_FRAMES, reader.frames)      # the reader's own bound
        values = reader.read(0, frames, lambda: False)
        record["block"] = {"frames": frames, "values": len(values),
                           "peak": round(max(abs(v) for v in values), 4)}
        if len(values) != frames * 2:
            fail(f"{label}: the reader returned {len(values)} values for {frames} frames")
        if not any(abs(v) > 0.01 for v in values):
            fail(f"{label}: the block read back silent")
    finally:
        reader.close()
        record["reader_closed"] = reader._closed and reader._process is None
    if not record["reader_closed"]:
        fail(f"{label}: the reader did not settle on close")
    return record


def _generate(tools, folder: Path, fail, label: str) -> tuple[Path | None, Path | None]:
    from .media import run_hidden
    clip, track = folder / f"{label}-clip.ts", folder / f"{label}-track.wav"
    made = run_hidden([str(tools.ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error",
                       "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=1",
                       "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                       "-f", "mpegts", str(clip)], timeout=120)
    if made.returncode != 0 or not clip.is_file():
        fail(f"{label}: could not generate a clip (exit {made.returncode}): "
             f"{made.stderr.strip()[-200:]}")
        clip = None
    made = run_hidden([str(tools.ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error",
                       "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
                       "-ac", "2", "-c:a", "pcm_s16le", str(track)], timeout=120)
    if made.returncode != 0 or not track.is_file():
        fail(f"{label}: could not generate a track (exit {made.returncode}): "
             f"{made.stderr.strip()[-200:]}")
        track = None
    return clip, track


def check_environment(argument: str | None, external: str | None = None
                      ) -> tuple[str, int]:
    """Run the environment diagnostic and return (report, exit code)."""
    if not argument:
        return "--check-environment needs a folder to write into.", 2
    parent = Path(argument)
    if not parent.is_dir():
        return f"--check-environment: {parent} is not an existing folder.", 2
    try:
        child = _new_named_child(parent, "environment")
    except OSError as exc:
        return f"--check-environment: could not create a new folder in {parent}: {exc}", 2
    receipt: dict = {"result": "FAIL", "failures": [], "folder": str(child),
                     "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    def fail(message: str) -> None:
        receipt["failures"].append(message)

    try:
        _run_environment(child, receipt, fail, external)
    except Exception:                    # noqa: BLE001 — recorded, never raised
        fail("unexpected error:\n" + traceback.format_exc())
    finally:
        if not receipt["failures"]:
            receipt["result"] = "PASS"
        receipt["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        try:
            where = str(_write_receipt(child, receipt))
        except Exception as exc:         # noqa: BLE001 — reported, never raised
            receipt["result"] = "FAIL"
            fail(f"the receipt could not be written: {type(exc).__name__}: {exc}")
            where = f"NOT WRITTEN in {child}"
    lines = [f"check-environment {receipt['result']}", f"receipt {where}"]
    lines += [f"  {line}" for message in receipt["failures"] for line in message.splitlines()]
    return "\n".join(lines), 0 if receipt["result"] == "PASS" else 1


def _run_environment(child: Path, receipt: dict, fail, external: str | None) -> None:
    from . import __version__
    from .media import Tools, ToolsMissing, child_env, find_tools, is_bundled, probe, run_hidden

    frozen_linux = bool(getattr(sys, "frozen", False)) and sys.platform.startswith("linux")
    before = dict(os.environ)
    receipt["app"] = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
                      "platform": sys.platform, "bundle": getattr(sys, "_MEIPASS", None)}
    receipt["parent"] = {
        "LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH"),
        "LD_LIBRARY_PATH_ORIG": os.environ.get("LD_LIBRARY_PATH_ORIG"),
        "orig_present": "LD_LIBRARY_PATH_ORIG" in os.environ,
        "sentinel": os.environ.get(ENVIRONMENT_SENTINEL)}
    receipt["paths_exercised"] = [
        "flightdvr.media.child_env", "flightdvr.media.run_hidden", "flightdvr.media.probe",
        "flightdvr.audio_reader.inspect_music_asset", "flightdvr.audio_reader.FfmpegPcmReader"]

    # 1. An owned program outside the bundle, through the real run_hidden.
    stand_in = child / "stand-in"
    stand_in.write_text(_STAND_IN, encoding="utf-8")
    stand_in.chmod(0o755)
    if not frozen_linux:
        fail("the stand-in phase needs the frozen Linux app (sys.frozen on Linux)")
    elif is_bundled(stand_in):
        fail(f"the stand-in at {stand_in} counts as bundled; it must be outside")
    else:
        ran = run_hidden([str(stand_in)], timeout=30)
        seen = _stand_in_report(ran.stdout)
        expected = (os.environ["LD_LIBRARY_PATH_ORIG"]
                    if "LD_LIBRARY_PATH_ORIG" in os.environ else "<unset>")
        receipt["stand_in"] = {"exit": ran.returncode, "saw": seen,
                               "expected_LD_LIBRARY_PATH": expected}
        if ran.returncode != 0:
            fail(f"the stand-in exited {ran.returncode}")
        if seen.get("LD_LIBRARY_PATH") != expected:
            fail(f"the stand-in got LD_LIBRARY_PATH={seen.get('LD_LIBRARY_PATH')!r}, "
                 f"expected {expected!r}")
        if seen.get("LD_LIBRARY_PATH_ORIG") != "<unset>":
            fail("the stand-in still received LD_LIBRARY_PATH_ORIG")
        if receipt["parent"]["sentinel"] is None:
            fail(f"no {ENVIRONMENT_SENTINEL} was set for the app, so retention is unchecked")
        elif seen.get("SENTINEL") != receipt["parent"]["sentinel"]:
            fail("the unrelated sentinel variable did not reach the stand-in")

    # The normal tools: still found the normal way, and still bundled.
    try:
        tools = find_tools()
    except ToolsMissing as exc:
        fail(f"ffmpeg not found: {exc}")
        tools = None
    if tools is not None:
        receipt["bundled_tools"] = {
            name: dict(_identity(Path(path)), bundled=is_bundled(Path(path)))
            for name, path in (("ffmpeg", tools.ffmpeg), ("ffprobe", tools.ffprobe))}
        if not all(t["bundled"] for t in receipt["bundled_tools"].values()):
            fail("find_tools did not return the bundled pair")
            tools = None
        elif frozen_linux:
            kept = child_env(tools.ffmpeg).get("LD_LIBRARY_PATH")
            receipt["bundled_child_LD_LIBRARY_PATH"] = kept
            if kept != os.environ.get("LD_LIBRARY_PATH"):
                fail("the bundled ffmpeg's environment lost the bundle's library path")

    # 2. Explicitly named external tools through the app's own readers.
    if external:
        folder = Path(external)
        outside = Tools(folder / "ffmpeg", folder / "ffprobe")
        record = {name: {"path": str(path), "exists": path.is_file(),
                         "bundled": is_bundled(path)}
                  for name, path in (("ffmpeg", outside.ffmpeg), ("ffprobe", outside.ffprobe))}
        receipt["external"] = record
        if not all(r["exists"] for r in record.values()):
            fail(f"--external-tools {folder} does not hold ffmpeg and ffprobe")
        elif any(r["bundled"] for r in record.values()):
            fail(f"--external-tools {folder} is inside the bundle; it must be external")
        else:
            version = run_hidden([str(outside.ffmpeg), "-hide_banner", "-version"], timeout=30)
            record["version"] = (version.stdout.splitlines() or [""])[0]
            if version.returncode != 0:
                fail(f"the external ffmpeg could not run under its environment "
                     f"(exit {version.returncode}): {version.stderr.strip()[-200:]}")
            clip, track = _generate(outside, child, fail, "external")
            if clip is not None:
                info = probe(outside, clip)
                record["probe"] = _clip_facts(info)
                if info.error or info.video_codec != "h264" or info.audio_codec != "aac":
                    fail(f"external probe: {info.error or (info.video_codec, info.audio_codec)}")
            if track is not None:
                record["track"] = _read_track(outside, track, fail, "external")
    else:
        receipt["external"] = "not requested"

    # 3. The bundled streaming reader on a generated track.
    if tools is not None:
        _clip, track = _generate(tools, child, fail, "bundled")
        if track is not None:
            receipt["bundled_track"] = _read_track(tools, track, fail, "bundled")

    after = dict(os.environ)
    receipt["parent_unchanged"] = before == after
    if before != after:
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        fail(f"the app's own environment changed: {changed}")
