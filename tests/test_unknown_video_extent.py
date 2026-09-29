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

"""P2f: a recording whose stream gives no picture length.

Measured on a Matroska recording with no video-stream duration: its file
duration (4.230) counts sound after the last picture (4.000), so a whole-clip
join with its own sound came out 247 pictures for 240, and with Replace the
music ran 0.46 s past the pictures. The picture's end is now read from the
video packets near the end of the file when the stream does not give it, and
a joined export with sound refuses a piece whose end is still unknown.
"""

from __future__ import annotations

import array
import json
import subprocess
import sys
import threading
import time
from copy import deepcopy
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import pytest

import flightdvr.media as media
from flightdvr.audio_plan import AudioMode, MusicChoice, SampleSpan
from flightdvr.jobs import ExportWorker, Job
from flightdvr.media import ClipInfo, find_tools, probe
from flightdvr.output_plan import working_outputs
from flightdvr.presets import PASSTHROUGH, ExportSettings
from flightdvr.sequence_plan import Resolution, compile_sequence

_W, _H, _BURST = 320, 180, 60


# -- the evidence rules, on packets fed to a real child ------------------------


def _child(lines="", errors="", code=0, delay=0.0, spam=0):
    """A real process standing in for ffprobe: prints `lines`, then `spam`
    more valid packets, writes `errors`, sleeps `delay`, exits `code`."""
    script = (
        "import sys, time\n"
        f"sys.stdout.write({lines!r})\n"
        f"for k in range({spam}): sys.stdout.write(f'{{k/60:.6f}},0.016667\\n')\n"
        "sys.stdout.flush()\n"
        f"sys.stderr.write({errors!r})\n"
        f"time.sleep({delay})\n"
        f"sys.exit({code})\n")
    return [sys.executable, "-c", script]


@pytest.fixture
def child(monkeypatch):
    """Route observe_picture_end's one Popen to a scripted real child, and
    remember it, so tests can see whether it was reaped."""
    made = {"args": None, "procs": [], "calls": []}
    real = subprocess.Popen

    def launch(*specs):
        """One spec for every read, or one per read in turn."""
        def fake(args, **kwargs):
            made["args"] = args
            made["calls"].append(args)
            spec = specs[min(len(made["procs"]), len(specs) - 1)]
            proc = real(spec, **kwargs)
            made["procs"].append(proc)
            return proc
        monkeypatch.setattr(media.subprocess, "Popen", fake)
    made["launch"] = launch
    return made


TOOLS = media.Tools(Path("ffmpeg"), Path("ffprobe"))
# The retained file: 4.230 s long, its picture starting at 0.
_CLOCK = {"format_duration": 4.23, "video_start": 0.0}
_LONG = {"format_duration": 300.0, "video_start": 0.0}


def _observe(clock=_CLOCK, **kwargs):
    return media.observe_picture_end(TOOLS, Path("x.mkv"), dict(clock), **kwargs)


def test_the_last_packet_end_is_the_picture_s_end(child):
    child["launch"](_child("3.900000,0.033000\n3.933000,0.033000\n"
                           "3.967000,0.033000\n"))
    assert _observe() == pytest.approx(4.0)
    # One read of the whole recording, from its start, without a seek.
    assert len(child["calls"]) == 1
    assert "-read_intervals" not in child["args"]
    assert "packet=pts_time,duration_time" in child["args"]


def test_a_recording_longer_than_a_whole_read_is_unknown_without_a_read(child):
    """Its end could only be read with a seek, and with FFmpeg 7.1.5 seeking
    reads returned, with exit 0 and no error, sometimes nothing and once only
    their first two packets: an end that looks complete and is not."""
    child["launch"](_child("299.983333,0.016667\n"))
    assert _observe(_LONG) == 0.0
    assert child["calls"] == []


def test_identical_partial_reads_are_still_unknown(monkeypatch):
    """Sol's counterexample (P2f): two reads agreeing on the same early end,
    2 packets ending at 290.033334 of a 300 s picture, were accepted by a
    rule that believed agreement. Agreement is not completeness."""
    reads = []
    monkeypatch.setattr(media, "_read_packet_end",
                        lambda *a, **k: reads.append(1) or (2, 290.033334))
    assert _observe(_LONG) == 0.0
    assert reads == [], "a seeking read is not attempted at all"


def test_the_end_is_on_the_picture_s_clock_whatever_the_starts(child):
    """A picture 50 ms late, a file whose sound starts 23 ms early."""
    child["launch"](_child("4.017000,0.033000\n"))
    late = {"format_duration": 4.2, "video_start": 0.05}
    assert _observe(late) == pytest.approx(4.0)


def test_packets_out_of_order_still_give_the_latest_end(child):
    child["launch"](_child("3.967000,0.033000\n3.900000,0.033000\n"
                           "3.933000,0.033000\n"))
    assert _observe() == pytest.approx(4.0)


@pytest.mark.parametrize("clock", [
    {**_CLOCK, "video_start": None},
    {**_CLOCK, "format_duration": None},
    {**_CLOCK, "format_duration": 0.0},
])
def test_an_unknown_start_or_length_is_unknown(child, clock):
    child["launch"](_child("3.967000,0.033000\n"))
    assert _observe(clock) == 0.0


@pytest.mark.parametrize("lines, why", [
    ("", "no packets"),
    ("3.933000,0.033000\n3.967000,N/A\n", "last packet without a duration"),
    ("N/A,0.033000\n3.967000,0.033000\n", "a packet without a time"),
    ("3.933000,0.033000\n3.967000,0.000000\n", "a last packet of no length"),
    ("3.967000,0.033000,extra\n", "a malformed row"),
    ("4.230000,0.033000\n", "an end past the file's own length"),
    ("-0.100000,0.033000\n", "an end before the picture starts"),
])
def test_incomplete_evidence_is_unknown_not_an_earlier_end(child, lines, why):
    child["launch"](_child(lines))
    assert _observe() == 0.0, why


@pytest.mark.parametrize("errors, code", [
    ("[matroska,webm] File ended prematurely\n", 0),   # measured: truncation
    ("", 1),
])
def test_a_complaint_or_failed_exit_publishes_nothing(child, errors, code):
    child["launch"](_child("3.967000,0.033000\n", errors=errors, code=code))
    assert _observe() == 0.0


def test_too_much_output_stops_the_child_and_is_unknown(child, monkeypatch):
    monkeypatch.setattr(media, "PICTURE_END_MAX_PACKETS", 500)
    child["launch"](_child(spam=5000, delay=30))
    assert _observe() == 0.0
    assert child["procs"][0].poll() is not None, "child left running"


def test_a_timeout_stops_the_child_and_is_unknown(child, monkeypatch):
    monkeypatch.setattr(media, "PICTURE_END_TIMEOUT_SECONDS", 0.5)
    child["launch"](_child("3.967000,0.033000\n", delay=30))
    began = time.monotonic()
    assert _observe() == 0.0
    assert time.monotonic() - began < media.TERMINATE_SECONDS + 3
    assert child["procs"][0].poll() is not None


def test_a_stop_during_the_read_stops_and_unregisters_the_child(child):
    child["launch"](_child("3.967000,0.033000\n", delay=30))
    stop, owned = threading.Event(), []

    def register(proc):
        owned.append(proc)
        if proc is not None:
            stop.set()          # stop lands right after registration

    assert _observe(should_stop=stop.is_set, register=register) == 0.0
    assert owned[0] is child["procs"][0] and owned[-1] is None
    assert child["procs"][0].poll() is not None


def test_a_stop_before_the_read_starts_no_child(child):
    child["launch"](_child("3.967000,0.033000\n"))
    assert _observe(should_stop=lambda: True) == 0.0
    assert child["procs"] == []


# -- real containers ---------------------------------------------------------------


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    """Sol's retained case rebuilt: 120 pictures carrying their index, a tone
    with a 2 kHz burst at picture 60's time, the sound 30 ms late and running
    to 4.23 s. Matroska (no video-stream duration) and TS (with one)."""
    tools = find_tools()
    root = tmp_path_factory.mktemp("extent")

    def run(*args):
        subprocess.run([str(tools.ffmpeg), "-v", "error", "-y", *map(str, args)],
                       check=True)

    pictures, sound = root / "pictures.mkv", root / "sound.wav"
    run("-f", "lavfi", "-i", f"color=black:s={_W}x{_H}:r=30:d=4", "-vf",
        "geq=lum='if(bitand(floor(N/pow(2,floor(X/32))),1),235,16)':cb=128:cr=128",
        "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-g", "30", pictures)
    run("-f", "lavfi", "-i",
        "aevalsrc='0.05*sin(2*PI*300*t)+if(gte(t,2)*lt(t,2.2),"
        "0.6*sin(2*PI*2000*t),0)':s=48000:d=4.2", "-ac", "2", sound)
    made = {}
    for name, container, codec in (("tail.mkv", "matroska", "pcm_s16le"),
                                   ("tail.ts", "mpegts", "aac")):
        made[name] = root / name
        run("-i", pictures, "-itsoffset", "0.03", "-i", sound, "-map", "0:v",
            "-map", "1:a", "-c:v", "copy", "-c:a", codec, "-f", container,
            made[name])
    made["late.mkv"] = root / "late.mkv"
    run("-itsoffset", "0.05", "-i", pictures, "-i", sound, "-map", "0:v",
        "-map", "1:a", "-c:v", "copy", "-c:a", "pcm_s16le", made["late.mkv"])
    made["long.mkv"] = root / "long.mkv"
    run("-f", "lavfi", "-i", f"color=black:s={_W}x{_H}:r=30:d=12",
        "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:duration=12.2",
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "pcm_s16le", made["long.mkv"])
    data = made["tail.mkv"].read_bytes()
    made["cut.mkv"] = root / "cut.mkv"
    made["cut.mkv"].write_bytes(data[: int(len(data) * 0.6)])
    music = root / "music.wav"
    run("-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:duration=12",
        "-ac", "2", music)
    return tools, made, music


def _stream_facts(tools, path):
    found = json.loads(subprocess.run(
        [str(tools.ffprobe), "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,start_time,duration", "-of", "json",
         str(path)], check=True, capture_output=True, text=True).stdout)
    return found["format"], {s["codec_type"]: s for s in found["streams"]}


def test_matroska_without_a_picture_length_gets_one_from_its_packets(sources):
    tools, made, _music = sources
    fmt, streams = _stream_facts(tools, made["tail.mkv"])
    assert "duration" not in streams["video"], "fixture: no stream duration"
    assert float(fmt["duration"]) > 4.2, "fixture: sound runs past pictures"
    found = probe(tools, made["tail.mkv"])
    assert (found.video_duration, found.video_duration_origin) == (4.0, "packets")
    assert found.duration == pytest.approx(float(fmt["duration"]))
    late = probe(tools, made["late.mkv"])
    # Its DURATION tag says 4.050, an end time: the picture lasts 4.000.
    assert (late.video_duration, late.video_duration_origin) == (4.0, "packets")


def test_a_long_recording_without_a_picture_length_stays_unknown(
        sources, monkeypatch):
    tools, made, _music = sources
    calls = []
    real = media.subprocess.Popen
    monkeypatch.setattr(media.subprocess, "Popen",
                        lambda args, **k: (calls.append(args), real(args, **k))[1])
    found = probe(tools, made["long.mkv"])
    assert found.duration > media.PICTURE_END_WHOLE_READ_SECONDS
    assert found.video_duration == 0.0 and found.video_duration_origin == ""
    assert len(calls) == 1, "no read of its packets was started"


def test_a_truncated_recording_stays_unknown(sources):
    tools, made, _music = sources
    found = probe(tools, made["cut.mkv"])
    assert found.video_duration == 0.0 and found.video_duration_origin == ""
    assert not found.error, "the recording itself is still usable"


def test_a_stream_that_gives_its_length_starts_no_extra_child(sources, monkeypatch):
    tools, made, _music = sources
    calls = []
    real = media.subprocess.Popen
    monkeypatch.setattr(media.subprocess, "Popen",
                        lambda args, **k: (calls.append(args), real(args, **k))[1])
    found = probe(tools, made["tail.ts"])
    assert found.video_duration_origin == "stream"
    assert found.video_duration == pytest.approx(4.000333, abs=1e-6)
    assert len(calls) == 1, "only the usual probe ran"


def unresolved_picture_ends(*args, **kwargs):
    # Imported late so that the export tests still run against a checkout
    # without it (the red at the base is behavioural, not an import error).
    from flightdvr.jobs import unresolved_picture_ends as found
    return found(*args, **kwargs)


def test_clipinfo_keeps_its_shape_and_believes_a_caller_s_length():
    old = ClipInfo(Path("a.mkv"), 1, datetime(2026, 9, 29), 4.23)
    assert old.video_duration_origin == ""
    old.video_duration = 4.0            # set by a caller, no provenance
    assert unresolved_picture_ends(_job([old, deepcopy(old)])) == []


# -- joined exports ----------------------------------------------------------------


def _job(clips, mode=None, keep=True, preset="master", music=None, out=None,
         sequence=True):
    output = working_outputs(clips, joined=True)[0]
    plan = (compile_sequence(output, revision="p2f", resolution=Resolution.success())
            if sequence else None)
    if mode is None:
        choice = MusicChoice()
    elif mode == "replace":
        from flightdvr.audio_export import file_sha256
        from flightdvr.audio_plan import AudioAsset
        choice = MusicChoice(mode=AudioMode.REPLACE, asset=AudioAsset(
            music.resolve(), file_sha256(music), 0, 48000, 2, 12 * 48000),
            passage=SampleSpan(0, 12 * 48000, 48000),
            fade_in_samples=0, fade_out_samples=0)
    else:
        choice = MusicChoice(mode=AudioMode(mode))
    return Job(clips, preset,
               ExportSettings(master_speed="ultrafast", colour=PASSTHROUGH,
                              keep_audio=keep),
               out or Path("out.mp4"), audio=choice, target=output.target,
               sequence=plan)


def _ids(tools, path):
    raw = subprocess.run([str(tools.ffmpeg), "-v", "error", "-i", str(path),
                          "-map", "0:v:0", "-pix_fmt", "gray", "-f", "rawvideo",
                          "-"], check=True, capture_output=True).stdout
    size = _W * _H
    return [sum(1 << b for b in range(10)
                if raw[k + _H // 2 * _W + b * 32 + 16] > 128)
            for k in range(0, len(raw), size)]


def _onsets(tools, path):
    raw = subprocess.run([str(tools.ffmpeg), "-v", "error", "-i", str(path),
                          "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-af",
                          "highpass=f=1500", "-f", "f32le", "-"],
                         check=True, capture_output=True).stdout
    s = array.array("f")
    s.frombytes(raw)
    found, i, quiet, w = [], 0, True, 96
    while i < len(s) - w:
        e = sum(v * v for v in s[i:i + w]) / w
        if e > 0.001 and quiet:
            found.append(i / 48000)
            quiet, i = False, i + 12000
            continue
        if e < 0.0001:
            quiet = True
        i += w // 2
    return found


def _export(tools, job, tmp_path):
    ok, message = ExportWorker(tools, [job], tmp_path / "work")._run_job(0, job)
    return ok, message


def test_a_whole_matroska_join_keeps_each_picture_once_and_its_sound_aligned(
        sources, tmp_path):
    tools, made, _music = sources
    info = probe(tools, made["tail.mkv"])
    out = tmp_path / "whole.mp4"
    ok, message = _export(tools, _job([info, deepcopy(info)], "original",
                                      out=out), tmp_path)
    assert ok, message
    assert _ids(tools, out) == list(range(120)) * 2
    fmt, streams = _stream_facts(tools, out)
    assert float(streams["audio"]["duration"]) == pytest.approx(
        float(streams["video"]["duration"]), abs=0.002)
    fmt_src, src = _stream_facts(tools, made["tail.mkv"])
    [source_onset] = _onsets(tools, made["tail.mkv"])
    offset = (float(src["audio"]["start_time"]) + source_onset
              - (float(src["video"]["start_time"]) + _BURST / 30))
    heard = [float(streams["audio"]["start_time"]) + t
             for t in _onsets(tools, out)]
    shown = [float(streams["video"]["start_time"]) + (k * 120 + _BURST) / 30
             for k in (0, 1)]
    assert len(heard) == 2
    for sound, picture in zip(heard, shown):
        assert sound - picture == pytest.approx(offset, abs=0.002)


def _unknown(tools, made):
    info = probe(tools, made["tail.mkv"])
    info.video_duration, info.video_duration_origin = 0.0, ""
    return info


@pytest.mark.parametrize("mode, keep", [
    (None, True), ("original", True), ("mix", True), ("replace", True),
])
@pytest.mark.parametrize("span", [None, (1.0, 0.0)])   # whole; open-ended
def test_an_unknown_end_with_sound_is_refused_before_encoding(
        sources, tmp_path, mode, keep, span):
    tools, made, music = sources
    one, two = _unknown(tools, made), _unknown(tools, made)
    if span is not None:
        # Only the open-ended piece reaches the file's end; the other is a
        # finite range, which alone would not be refused.
        one.trim_in, one.trim_out = span
        two.trim_in, two.trim_out = 1.0, 3.0
    out = tmp_path / "refused.mp4"
    if mode == "mix":
        job = _job([one, two], "replace", music=music, out=out)
        job.audio = MusicChoice(mode=AudioMode.MIX, asset=job.audio.asset,
                                passage=job.audio.passage,
                                fade_in_samples=0, fade_out_samples=0)
    else:
        job = _job([one, two], mode, keep=keep, music=music, out=out)
    ok, message = _export(tools, job, tmp_path)
    assert not ok
    assert "tail.mkv" in message and "range" in message and "No sound" in message
    assert not out.exists() and not list(tmp_path.glob("*.flightdvr-part*"))


@pytest.mark.parametrize("mode, keep", [("no_sound", True), (None, False)])
def test_an_unknown_end_without_sound_still_exports_every_picture_once(
        sources, tmp_path, mode, keep):
    tools, made, _music = sources
    out = tmp_path / "silent.mp4"
    one, two = _unknown(tools, made), _unknown(tools, made)
    ok, message = _export(tools, _job([one, two], mode, keep=keep, out=out),
                          tmp_path)
    assert ok, message
    assert _ids(tools, out) == list(range(120)) * 2
    assert "audio" not in _stream_facts(tools, out)[1]


def test_an_explicit_range_of_an_unknown_recording_is_not_refused(
        sources, tmp_path):
    tools, made, _music = sources
    one, two = _unknown(tools, made), _unknown(tools, made)
    for clip in (one, two):
        clip.trim_in, clip.trim_out = 1.0, 3.0
    out = tmp_path / "ranges.mp4"
    ok, message = _export(tools, _job([one, two], "original", out=out), tmp_path)
    assert ok, message
    assert _ids(tools, out) == list(range(30, 90)) * 2


def test_the_guard_does_not_depend_on_a_submitted_sequence(sources):
    """A legacy join without a compiled sequence is the same export."""
    tools, made, _music = sources
    one, two = _unknown(tools, made), _unknown(tools, made)
    assert unresolved_picture_ends(_job([one, two], sequence=False)) == ["tail.mkv"]
    assert unresolved_picture_ends(
        _job([one, two], sequence=False, preset="slowmo")) == []


def test_preview_and_export_read_the_same_end(sources):
    tools, made, _music = sources
    info = probe(tools, made["tail.mkv"])
    plan = compile_sequence(working_outputs([info, deepcopy(info)], joined=True)[0],
                            revision="p2f-bind", resolution=Resolution.success())
    assert plan.occurrences[0].source.end == Fraction("4")
    assert plan.total_samples == 8 * 48000
