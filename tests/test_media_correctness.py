# FlightDVR Studio - browse, trim and convert HDZero goggle DVR footage.
# Copyright (C) 2026 Isadu Nkemi
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.

"""P2: media correctness.

P2a, cancellation cleanup: a cancelled job removes its own unfinished file,
and when it cannot, it says so precisely rather than leaving it silently. The
Stage A Windows CI failure (the job's own partial left behind once, not
reproduced in six local repeats, passing on rerun) is preserved as history in
the P2 handoff; nothing here claims to have explained it. What these tests
hold is the contract around it.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from copy import deepcopy
from pathlib import Path

import pytest

import flightdvr.audio_export as audio_export
from flightdvr.audio_plan import AudioMode
from flightdvr.jobs import ExportWorker, Job, JobStatus
from flightdvr.presets import ExportSettings

# The generated media and tools Stage A already built for the same worker.
from tests.test_audio_export import choice, media, tools  # noqa: F401

pytestmark = pytest.mark.integration


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_holders(path: Path) -> list[str]:
    """Which processes hold `path` open, by the Windows Restart Manager.

    Diagnostic only, for a partial the worker could not remove: winerror 32
    says another handle is open, not whose. Empty off Windows or when the
    Restart Manager cannot say.
    """
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    rm = ctypes.WinDLL("rstrtmgr")

    class _UniqueProcess(ctypes.Structure):
        _fields_ = [("dwProcessId", wintypes.DWORD),
                    ("ProcessStartTime", wintypes.FILETIME)]

    class _ProcessInfo(ctypes.Structure):
        _fields_ = [("Process", _UniqueProcess),
                    ("strAppName", ctypes.c_wchar * 256),
                    ("strServiceShortName", ctypes.c_wchar * 64),
                    ("ApplicationType", ctypes.c_int),
                    ("AppStatus", wintypes.ULONG),
                    ("TSSessionId", wintypes.DWORD),
                    ("bRestartable", wintypes.BOOL)]

    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(64)
    if rm.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return ["(restart manager unavailable)"]
    try:
        files = (ctypes.c_wchar_p * 1)(str(path))
        if rm.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
            return ["(could not register the file)"]
        needed, count, reasons = wintypes.UINT(), wintypes.UINT(0), wintypes.DWORD()
        rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None,
                     ctypes.byref(reasons))
        infos = (_ProcessInfo * max(1, needed.value))()
        count = wintypes.UINT(needed.value)
        if rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                        infos, ctypes.byref(reasons)) != 0:
            return ["(could not list holders)"]
        return [f"{infos[i].strAppName} pid {infos[i].Process.dwProcessId}"
                for i in range(count.value)]
    finally:
        rm.RmEndSession(session)


def leftover_report(path: Path, residual: str) -> str:
    """For a failed assertion: the worker's own residual, who holds the file
    now, and how long until it can be removed. Observed here, in the test,
    after the fact; the worker itself never waits or retries."""
    holders = file_holders(path)
    released_after = None
    started = time.monotonic()
    while time.monotonic() - started < 10.0:
        try:
            with open(path, "rb+"):
                pass
            os.rename(path, path.with_suffix(path.suffix + ".probe"))
            os.rename(path.with_suffix(path.suffix + ".probe"), path)
            released_after = round(time.monotonic() - started, 3)
            break
        except OSError:
            time.sleep(0.02)
    return (f"{residual or '(no residual)'} | holders at failure: "
            f"{holders or '(none named)'} | exclusive access after: "
            f"{released_after if released_after is not None else '>10 s'}")


def _cancel_during_the_real_pcm_count(media, tmp_path, monkeypatch,  # noqa: F811
                                      unlink_refusal=None):
    """Two Edit jobs; the first is cancelled while the real PCM count is
    reading its finished partial. Returns what the test needs to judge."""
    out = tmp_path / "edit.mov"
    out.write_bytes(b"previous destination")
    neighbour = tmp_path / "edit-neighbour.flightdvr-part.mov"
    neighbour.write_bytes(b"another job's unfinished file")
    before = {path: _digest(path) for path in (out, neighbour)}
    first = Job([media.source], "edit", ExportSettings(edit_codec="prores_lt"),
                out, audio=choice(media.asset, AudioMode.REPLACE))
    second = Job([media.silent], "edit", ExportSettings(edit_codec="prores_lt"),
                 tmp_path / "second.mov", audio=choice(media.asset, AudioMode.MIX))
    second_before = deepcopy(second)
    part = out.with_name("edit.flightdvr-part.mov")

    real_popen = subprocess.Popen
    decodes, seen_part = [], []

    def slow_decode(command, *args, **kwargs):
        # Only the count's decode is slowed: read at playback speed.
        if "s16le" in command and "-map" in command:
            seen_part.append(part.exists() and part.stat().st_size)
            command = list(command)
            command.insert(command.index("-i"), "-re")
            proc = real_popen(command, *args, **kwargs)
            decodes.append(proc)
            return proc
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(audio_export.subprocess, "Popen", slow_decode)
    unlinked = []
    real_unlink = Path.unlink

    def watched_unlink(self, *args, **kwargs):
        unlinked.append(Path(self))
        if unlink_refusal is not None and Path(self) == part:
            raise unlink_refusal
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", watched_unlink)
    worker = ExportWorker(None, [first, second], tmp_path / "work")
    from flightdvr.media import find_tools
    worker.tools = find_tools()
    runner = threading.Thread(target=worker.run, daemon=True)
    runner.start()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not decodes:
        time.sleep(0.01)
    assert decodes, "the PCM count never started"
    assert decodes[0].poll() is None, "the count finished before cancel"
    assert worker._process is None, "FFmpeg's encode had already finished"
    worker.cancel()
    runner.join(timeout=15)
    assert not runner.is_alive(), "cancel was not seen during the count"
    return {
        "worker": worker, "first": first, "second": second,
        "second_before": second_before, "out": out, "neighbour": neighbour,
        "before": before, "part": part, "decodes": decodes,
        "seen_part": seen_part, "unlinked": unlinked,
    }


def test_a_cancel_during_the_real_pcm_count_removes_its_own_partial(
        media, tmp_path, monkeypatch):  # noqa: F811
    got = _cancel_during_the_real_pcm_count(media, tmp_path, monkeypatch)
    first, part = got["first"], got["part"]
    # The partial was real and the validator was really reading it.
    assert got["seen_part"] and got["seen_part"][0] > 0
    assert got["decodes"][0].returncode not in (None, 0), "decode not stopped"
    assert first.status is JobStatus.CANCELLED
    # If this fails, the worker says why: that is the diagnostic P2a adds.
    if part.exists():
        pytest.fail(leftover_report(part, got["worker"].residuals.get(0, "")))
    assert first.message == "Cancelled"
    assert 0 not in got["worker"].residuals
    for path, digest in got["before"].items():
        assert _digest(path) == digest, path
    # The second job never started, and its submission is untouched.
    assert got["second"].status is JobStatus.CANCELLED
    assert (got["second"].settings, got["second"].audio,
            got["second"].out_path) == (
        got["second_before"].settings, got["second_before"].audio,
        got["second_before"].out_path)
    assert not (tmp_path / "second.mov").exists()


def test_an_own_partial_that_cannot_be_removed_is_said_not_swallowed(
        media, tmp_path, monkeypatch):  # noqa: F811
    refusal = PermissionError(
        13, "The process cannot access the file because it is being used by "
            "another process", "edit.flightdvr-part.mov",
        *((32,) if os.name == "nt" else ()))
    got = _cancel_during_the_real_pcm_count(
        media, tmp_path, monkeypatch, unlink_refusal=refusal)
    first, part = got["first"], got["part"]
    assert first.status is JobStatus.CANCELLED, "still Cancelled"
    assert first.message.startswith("Cancelled — ")
    assert "could not be removed (PermissionError, errno 13" in first.message
    if os.name == "nt":
        assert "winerror 32" in first.message
    assert str(part) in first.message, "the exact path to remove by hand"
    assert "(the job was checking the sound)" in first.message
    assert part.exists(), "the refused partial is where the message says"
    # Only the job's own partial was ever unlinked; nothing else of anyone's.
    owned = {part}
    assert set(got["unlinked"]) <= owned | set(
        (tmp_path / "work").glob("*")), got["unlinked"]
    assert got["neighbour"] not in got["unlinked"]
    assert got["out"] not in got["unlinked"]
    for path, digest in got["before"].items():
        assert _digest(path) == digest, path
    assert got["second"].status is JobStatus.CANCELLED
    assert "could not be removed" not in got["second"].message


def test_a_failed_job_keeps_its_own_failure_and_adds_the_residual(
        media, tmp_path, monkeypatch):  # noqa: F811
    """Not only cancellation: a job that fails and then cannot remove its own
    partial reports both, the failure first."""
    from flightdvr.media import find_tools

    out = tmp_path / "failed.mov"
    part = out.with_name("failed.flightdvr-part.mov")
    job = Job([media.source], "edit", ExportSettings(edit_codec="prores_lt"),
              out, audio=choice(media.asset, AudioMode.REPLACE))
    monkeypatch.setattr(audio_export, "validate_expected_audio",
                        lambda *a, **k: (False, "the sound was wrong"))
    real_unlink = Path.unlink

    def refuse(self, *args, **kwargs):
        if Path(self) == part and part.exists():
            raise PermissionError(13, "held", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    worker = ExportWorker(find_tools(), [job], tmp_path / "work")
    worker.run()
    assert job.status is JobStatus.FAILED
    assert job.message.startswith("the sound was wrong — its unfinished file "
                                  "could not be removed")
    assert str(part) in job.message
    monkeypatch.setattr(Path, "unlink", real_unlink)
    part.unlink(missing_ok=True)


# -- P2b: the picture's origin ---------------------------------------------------

from datetime import datetime  # noqa: E402

import flightdvr.media as media_module  # noqa: E402
from flightdvr.media import ClipInfo, video_origin  # noqa: E402


@pytest.mark.parametrize("fmt, video, expected", [
    ("1.400000", "1.421333", 0.021333),     # AAC priming: sound first
    ("1.400000", "1.400000", 0.0),          # together
    ("1.408700", "1.400000", 0.0),          # sound later: never negative
    (None, "1.4", 0.0), ("1.4", None, 0.0),  # absent
    ("N/A", "1.4", 0.0), ("1.4", "N/A", 0.0),
    ("nan", "1.4", 0.0), ("1.4", "inf", 0.0), ("-inf", "1.4", 0.0),
])
def test_the_video_origin_is_defined_and_safe(fmt, video, expected):
    assert video_origin(fmt, video) == pytest.approx(expected, abs=1e-9)


def test_clipinfo_keeps_its_old_shape_and_copies_the_origin():
    from copy import copy
    positional = ClipInfo(Path("a.ts"), 1, datetime(2026, 9, 29), 2.0, 1280,
                          720, 30.0, "h264", "aac", "yuv420p", "tv")
    assert positional.video_start == 0.0
    positional.video_start = 0.021333
    assert copy(positional).video_start == 0.021333


class _FakeProbe:
    """A finished ffprobe with fixed JSON."""

    def __init__(self, payload):
        self._payload = payload
        self.returncode = 0

    def communicate(self, timeout=None):
        return self._payload, ""

    def poll(self):
        return 0


def _probed(monkeypatch, tmp_path, fmt_start, video_start, width=1280):
    import json as _json
    payloads = []

    def fake(*args, **kwargs):
        stream = {"codec_type": "video", "codec_name": "h264",
                  "width": width, "height": 720, "avg_frame_rate": "30/1"}
        if video_start is not None:
            stream["start_time"] = video_start
        fmt = {"duration": "20.0"}
        if fmt_start is not None:
            fmt["start_time"] = fmt_start
        payloads.append(1)
        return _FakeProbe(_json.dumps({"format": fmt, "streams": [
            stream, {"codec_type": "audio", "codec_name": "aac",
                     "start_time": "1.400000"}]}))

    monkeypatch.setattr(media_module.subprocess, "Popen", fake)
    path = tmp_path / "x.ts"
    path.write_bytes(b"x")
    return media_module.probe(media_module.Tools(Path("ffmpeg"), Path("ffprobe")), path), payloads


def test_probe_records_the_origin_from_ffprobe(monkeypatch, tmp_path):
    info, _ = _probed(monkeypatch, tmp_path, "1.400000", "1.421333")
    assert info.video_start == pytest.approx(0.021333)
    info, _ = _probed(monkeypatch, tmp_path, "N/A", "1.421333")
    assert info.video_start == 0.0
    info, _ = _probed(monkeypatch, tmp_path, "1.4", None)
    assert info.video_start == 0.0


def test_the_deep_retry_records_the_origin_the_same_way(monkeypatch, tmp_path):
    """A first pass that could not read the picture's size is retried with a
    deeper probe; the retry's answer carries the origin too."""
    import json as _json
    calls = []

    def fake(args, *a, **k):
        deep = "-analyzeduration" in args
        calls.append(deep)
        stream = {"codec_type": "video", "codec_name": "h264",
                  "width": 1280 if deep else 0, "height": 720,
                  "avg_frame_rate": "30/1", "start_time": "1.450000"}
        return _FakeProbe(_json.dumps({
            "format": {"duration": "20.0", "start_time": "1.400000"},
            "streams": [stream]}))

    monkeypatch.setattr(media_module.subprocess, "Popen", fake)
    path = tmp_path / "y.ts"
    path.write_bytes(b"y")
    info = media_module.probe(media_module.Tools(Path("ffmpeg"), Path("ffprobe")), path)
    assert calls == [False, True]
    assert info.width == 1280 and info.video_start == pytest.approx(0.05)


# -- P2c: a trimmed join's second range, in every route ---------------------------
#
# Sol's literal oracle from the PR #152 review: 360 generated frames, each
# carrying its index as ten binary bars; ranges [2, 5) and [6, 8) at 30 fps
# must export frames 60..149 then 180..239, exactly. A transport stream with
# AAC (whose priming starts the file 21 ms before its picture, at 1.4 s) made
# No sound and Replace begin the second range at frame 138: the seeked input
# was rebased differently when its sound was not used.

_BITS, _W, _H = 10, 320, 180


def _ordinal_ids(tools, path: Path) -> list[int]:
    raw = subprocess.run(
        [str(tools.ffmpeg), "-v", "error", "-i", str(path), "-map", "0:v:0",
         "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        check=True, capture_output=True).stdout
    size, bar = _W * _H, _W // _BITS
    row = (_H // 2) * _W
    return [sum(1 << bit for bit in range(_BITS)
                if raw[base + row + bit * bar + bar // 2] > 128)
            for base in range(0, len(raw), size)]


@pytest.fixture(scope="module")
def ordinal_ts(tmp_path_factory):
    from flightdvr.audio_export import file_sha256
    from flightdvr.audio_plan import AudioAsset
    from flightdvr.media import find_tools

    tools_ = find_tools()
    root = tmp_path_factory.mktemp("ordinals")
    video, audio = root / "ordinals.mkv", root / "audio.wav"
    geq = ("geq=lum='if(bitand(floor(N/pow(2,floor(X/32))),1),235,16)'"
           ":cb=128:cr=128")
    subprocess.run([str(tools_.ffmpeg), "-v", "error", "-y", "-f", "lavfi",
                    "-i", f"color=black:s={_W}x{_H}:r=30:d=12", "-vf", geq,
                    "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0",
                    "-g", "30", str(video)], check=True)
    subprocess.run([str(tools_.ffmpeg), "-v", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=600:sample_rate=48000:duration=12",
                    "-ac", "2", str(audio)], check=True)
    source = root / "aac-priming.ts"
    subprocess.run([str(tools_.ffmpeg), "-v", "error", "-y", "-i", str(video),
                    "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c:v",
                    "copy", "-c:a", "aac", str(source)], check=True)
    assert _ordinal_ids(tools_, source) == list(range(360)), "fixture"
    asset = AudioAsset(audio.resolve(), file_sha256(audio), 0, 48000, 2,
                       12 * 48000)
    return tools_, source, asset


@pytest.mark.parametrize("mode", ["no_sound", "replace", "original"])
def test_a_trimmed_join_exports_each_range_s_own_frames(ordinal_ts, tmp_path,
                                                         mode):
    from flightdvr.audio_plan import MusicChoice, SampleSpan
    from flightdvr.media import probe
    from flightdvr.output_plan import working_outputs
    from flightdvr.presets import PASSTHROUGH
    from flightdvr.sequence_plan import Resolution, compile_sequence

    tools_, source, asset = ordinal_ts
    info = probe(tools_, source)
    assert info.video_start > 0, "the file starts before its picture"
    clips = []
    for start, end in ((2.0, 5.0), (6.0, 8.0)):
        clip = deepcopy(info)
        clip.trim_in, clip.trim_out = start, end
        clips.append(clip)
    output = working_outputs(clips, joined=True)[0]
    sequence = compile_sequence(output, revision="p2c",
                                resolution=Resolution.success())
    if mode == "replace":
        choice = MusicChoice(mode=AudioMode.REPLACE, asset=asset,
                             passage=SampleSpan(0, 12 * 48000, 48000),
                             fade_in_samples=0, fade_out_samples=0)
    else:
        choice = MusicChoice(mode=AudioMode(mode))
    out = tmp_path / f"join-{mode}.mp4"
    job = Job(clips, "master",
              ExportSettings(master_speed="ultrafast", colour=PASSTHROUGH),
              out, audio=choice, target=output.target, sequence=sequence)
    ok, message = ExportWorker(tools_, [job], tmp_path / "work")._run_job(0, job)
    assert ok, message
    ids = _ordinal_ids(tools_, out)
    expected = list(range(60, 150)) + list(range(180, 240))
    assert ids[87:94] == [147, 148, 149, 180, 181, 182, 183], ids[87:94]
    assert ids == expected
