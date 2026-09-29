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


def exclusive_open(path: Path) -> bool:
    """Whether `path` can be opened with no sharing at all: no other handle
    of any kind is open on it. A reopen or a rename is weaker, since both
    succeed alongside handles that share. True off Windows."""
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        return False
    kernel.CloseHandle(handle)
    return True


class UnlinkObserver:
    """Watches the worker's own removal of `part`, from the test.

    When the real unlink of the partial fails, the observer notes the moment,
    asks the Restart Manager who holds the file right then, and times, from
    that moment, how long until the file can be opened exclusively. It does
    this on its own thread, so the worker gets its error at once and is not
    delayed; the worker itself never waits or retries.
    """

    def __init__(self, monkeypatch, part: Path, refusal=None):
        self.part = part
        self.unlinked: list[Path] = []
        self.holders_at_failure = None
        self.holders_queried = None       # (started, finished) after the fail
        self.release_after = None
        self._thread = None
        self._holder_thread = None
        real_unlink = Path.unlink
        observer = self

        def watched(path_self, *args, **kwargs):
            observer.unlinked.append(Path(path_self))
            if refusal is not None and Path(path_self) == part:
                raise refusal
            try:
                return real_unlink(path_self, *args, **kwargs)
            except PermissionError:
                # The moment of the failure, taken before anything else.
                failed_at = time.monotonic()
                if Path(path_self) == part and observer._thread is None:
                    observer._start(failed_at)
                raise

        monkeypatch.setattr(Path, "unlink", watched)

    def _start(self, failed_at: float) -> None:
        # Two threads: the holder lookup is slow and must not delay the
        # access timing, and it is a later observation, timed as such.
        def access():
            while time.monotonic() - failed_at < 10.0:
                if exclusive_open(self.part):       # its handle is closed
                    self.release_after = round(time.monotonic() - failed_at, 4)
                    return
                time.sleep(0.002)

        def holders():
            began = time.monotonic() - failed_at
            self.holders_at_failure = file_holders(self.part)
            self.holders_queried = (round(began, 4),
                                    round(time.monotonic() - failed_at, 4))

        self._thread = threading.Thread(target=access, daemon=True)
        self._holder_thread = threading.Thread(target=holders, daemon=True)
        self._thread.start()
        self._holder_thread.start()

    def report(self, residual: str) -> str:
        for thread in (self._thread, self._holder_thread):
            if thread is not None:
                thread.join(timeout=12)
        release = (f"<= {self.release_after} s after the failed unlink "
                   "(first exclusive open; an observed upper bound)"
                   if self.release_after is not None else "not within 10 s")
        queried = (f"{self.holders_queried[0]}-{self.holders_queried[1]} s "
                   "after it" if self.holders_queried else "not queried")
        return (f"{residual or '(no residual)'} | exclusive access: {release}"
                f" | holders, queried {queried}: "
                f"{self.holders_at_failure or '(none named)'}")


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
    observer = UnlinkObserver(monkeypatch, part, refusal=unlink_refusal)
    unlinked = observer.unlinked
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
        "seen_part": seen_part, "unlinked": unlinked, "observer": observer,
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
        pytest.fail(got["observer"].report(got["worker"].residuals.get(0, "")))
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
    if ids != expected:
        pytest.fail(_join_report(tools_, source, info, clips, choice,
                                 sequence, out, ids, expected))


def _join_report(tools_, source, info, clips, choice, sequence, out, ids,
                 expected) -> str:
    """Everything needed to diagnose a mismatch where it happened: the oldest
    FFmpeg on Linux, where a desktop run did not reproduce it."""
    import json as _json

    from flightdvr.audio_plan import resolve_audio_plan
    from flightdvr.presets import PASSTHROUGH, build_commands

    def probe_json(path, entries):
        return _json.loads(subprocess.run(
            [str(tools_.ffprobe), "-v", "error", "-show_entries", entries,
             "-of", "json", str(path)],
            check=True, capture_output=True, text=True).stdout)

    plan = resolve_audio_plan(choice, sequence.total_samples,
                              source_has_audio=info.has_audio,
                              preset_key="master", joined=True)
    command = build_commands(
        tools_, clips[0], "master",
        ExportSettings(master_speed="ultrafast", colour=PASSTHROUGH),
        Path("out.mp4"), Path("work"), clips=clips, audio_plan=plan,
        sequence=sequence, total_duration=5.0)[0]
    frames = probe_json(out, "frame=best_effort_timestamp_time")
    times = [f.get("best_effort_timestamp_time")
             for f in frames.get("frames", [])]
    version = subprocess.run([str(tools_.ffmpeg), "-version"],
                             capture_output=True,
                             text=True).stdout.splitlines()[0]
    mismatch = next((k for k, (a, b) in enumerate(zip(ids, expected))
                     if a != b), None)
    lines = [
        f"ids differ: got {len(ids)} want {len(expected)}; first mismatch "
        f"{mismatch}; last ids {ids[-4:]}; last frame times {times[-4:]}",
        "source: " + _json.dumps(probe_json(
            source, "format=start_time,duration:"
                    "stream=codec_type,start_time,duration")),
        f"video_start {info.video_start}",
        "output: " + _json.dumps(probe_json(
            out, "format=duration:stream=codec_type,start_time,duration,"
                 "nb_frames")),
        version,
        "command: " + " ".join(command[1:]),
    ]
    return "\n".join(lines)


# -- P2d: a whole clip in a join is its picture ---------------------------------


@pytest.mark.parametrize("value, expected", [
    ("20.000333", 20.000333), (None, 0.0), ("N/A", 0.0), ("nan", 0.0),
    ("inf", 0.0), ("0", 0.0), ("-1.5", 0.0),
])
def test_probe_records_the_picture_s_own_duration(monkeypatch, tmp_path,
                                                   value, expected):
    import json as _json

    def fake(*args, **kwargs):
        stream = {"codec_type": "video", "codec_name": "h264", "width": 1280,
                  "height": 720, "avg_frame_rate": "30/1",
                  "start_time": "1.471333"}
        if value is not None:
            stream["duration"] = value
        return _FakeProbe(_json.dumps({
            "format": {"duration": "20.071666", "start_time": "1.400000"},
            "streams": [stream, {"codec_type": "audio", "codec_name": "aac",
                                 "start_time": "1.400000"}]}))

    monkeypatch.setattr(media_module.subprocess, "Popen", fake)
    path = tmp_path / "z.ts"
    path.write_bytes(b"z")
    info = media_module.probe(
        media_module.Tools(Path("ffmpeg"), Path("ffprobe")), path)
    assert info.video_duration == pytest.approx(expected)
    assert info.duration == pytest.approx(20.071666), "the file's own"


_SECONDS, _BURST = 4, 60  # a 2 kHz burst at frame 60's instant (2.0 s)


def _offset_source(tools_, root, name, video_delay=0.0, audio_delay=0.0,
                   with_audio=True) -> Path:
    """Frame-index bars at 30 fps; a tone with a burst at frame 60's time on
    its own clock; each stream delayed as asked when muxed to a TS."""
    video, audio = root / f"{name}-v.mkv", root / f"{name}-a.wav"
    geq = ("geq=lum='if(bitand(floor(N/pow(2,floor(X/32))),1),235,16)'"
           ":cb=128:cr=128")
    subprocess.run([str(tools_.ffmpeg), "-v", "error", "-y", "-f", "lavfi",
                    "-i", f"color=black:s={_W}x{_H}:r=30:d={_SECONDS}",
                    "-vf", geq, "-c:v", "libx264", "-preset", "ultrafast",
                    "-qp", "0", "-g", "30", str(video)], check=True)
    burst = _BURST / 30
    tone = (f"aevalsrc='0.1*sin(2*PI*300*t)+if(gte(t,{burst})*"
            f"lt(t,{burst + 0.2}),0.6*sin(2*PI*2000*t),0)'"
            f":s=48000:d={_SECONDS}")
    subprocess.run([str(tools_.ffmpeg), "-v", "error", "-y", "-f", "lavfi",
                    "-i", tone, "-ac", "2", str(audio)], check=True)
    source = root / f"{name}.ts"
    command = [str(tools_.ffmpeg), "-v", "error", "-y"]
    if video_delay:
        command += ["-itsoffset", str(video_delay)]
    command += ["-i", str(video)]
    if with_audio:
        if audio_delay:
            command += ["-itsoffset", str(audio_delay)]
        command += ["-i", str(audio), "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac"]
    else:
        command += ["-map", "0:v", "-c:v", "copy"]
    subprocess.run(command + ["-f", "mpegts", str(source)], check=True)
    assert _ordinal_ids(tools_, source) == list(range(_SECONDS * 30)), "fixture"
    return source


@pytest.fixture(scope="module")
def whole_clip_sources(tmp_path_factory):
    from flightdvr.media import find_tools

    tools_ = find_tools()
    root = tmp_path_factory.mktemp("whole")
    found = {
        "picture_late": _offset_source(tools_, root, "picture-late",
                                       video_delay=0.05),
        "sound_late": _offset_source(tools_, root, "sound-late",
                                     audio_delay=0.03),
        "picture_only": _offset_source(tools_, root, "picture-only",
                                       with_audio=False),
    }
    import json as _json
    late = _json.loads(subprocess.run(
        [str(tools_.ffprobe), "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,duration", "-of", "json",
         str(found["picture_late"])], check=True, capture_output=True,
        text=True).stdout)
    picture = next(float(s["duration"]) for s in late["streams"]
                   if s["codec_type"] == "video")
    assert float(late["format"]["duration"]) - picture > 0.05, (
        "the file runs past its picture", late)
    return tools_, found


def _whole_join(tools_, sources, mode, tmp_path, name):
    from flightdvr.audio_plan import MusicChoice
    from flightdvr.media import probe
    from flightdvr.output_plan import working_outputs
    from flightdvr.presets import PASSTHROUGH
    from flightdvr.sequence_plan import Resolution, compile_sequence

    clips = [probe(tools_, source) for source in sources]
    output = working_outputs(clips, joined=True)[0]
    sequence = compile_sequence(output, revision="p2d",
                                resolution=Resolution.success())
    out = tmp_path / f"{name}-{mode}.mp4"
    job = Job(clips, "master",
              ExportSettings(master_speed="ultrafast", colour=PASSTHROUGH),
              out, audio=MusicChoice(mode=AudioMode(mode)),
              target=output.target, sequence=sequence)
    ok, message = ExportWorker(tools_, [job], tmp_path / "work")._run_job(0, job)
    assert ok, message
    return out


def _streams(tools_, path) -> dict:
    import json as _json
    found = _json.loads(subprocess.run(
        [str(tools_.ffprobe), "-v", "error", "-show_entries",
         "stream=codec_type,start_time,duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True).stdout)["streams"]
    return {s["codec_type"]: s for s in found}


@pytest.mark.parametrize("mode", ["original", "no_sound"])
@pytest.mark.parametrize("order", ["AA", "ABA"])
def test_a_whole_clip_join_shows_each_picture_once(whole_clip_sources,
                                                   tmp_path, order, mode):
    """P2d: the whole clip's extent was the file's, which runs past its last
    picture; with the recording's sound in, the join filled the difference
    with the picture repeated at the seam (1202 frames for 1200)."""
    tools_, found = whole_clip_sources
    a, b = found["picture_late"], found["picture_only"]
    sources = [a, a] if order == "AA" else [a, b, a]
    out = _whole_join(tools_, sources, mode, tmp_path, order)
    assert _ordinal_ids(tools_, out) == (
        list(range(_SECONDS * 30)) * len(sources))
    streams = _streams(tools_, out)
    if "audio" in streams:
        assert float(streams["audio"]["duration"]) == pytest.approx(
            float(streams["video"]["duration"]), abs=0.002)
