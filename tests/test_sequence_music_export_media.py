# FlightDVR Studio - browse, trim and convert HDZero goggle DVR footage.
# Copyright (C) 2026 Isadu Nkemi
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.

"""Independent generated-file proof for nominal-1x Assembly audio export.

The expected 3 s / 5 s seams, eight-second terminal and tone schedule below
are fixture declarations.  The oracle never asks SequencePlan, the command
builder or the production validator what should have come out.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import threading
import time
import wave
from array import array
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import pytest

import flightdvr.jobs as jobs_module
from flightdvr.assembly import Item, export_piece, resolve
from flightdvr.audio_export import file_sha256
from flightdvr.audio_plan import (
    AudioAsset, AudioMode, MusicChoice, OUTPUT_RATE, SampleSpan,
    ShortTrackPolicy,
)
from flightdvr.jobs import ExportWorker, Job
from flightdvr.media import Select, probe
from flightdvr.output_plan import working_outputs
from flightdvr.presets import PASSTHROUGH, ExportSettings
from flightdvr.sequence_plan import Resolution, compile_sequence


pytestmark = pytest.mark.integration

FPS = 30
WIDTH, HEIGHT = 160, 90
EXPECTED_FRAMES = 240
EXPECTED_SAMPLES = 8 * OUTPUT_RATE
EXPECTED_TRANSITIONS = (
    ("red", Fraction(0)),
    ("green", Fraction(1)),
    ("yellow", Fraction(2)),
    ("blue", Fraction(3)),
    ("red", Fraction(5)),
    ("green", Fraction(6)),
    ("yellow", Fraction(7)),
)
WINDOW = 4_800  # 0.1 s; an integer number of cycles for every test tone.


def _run(command: list[str], *, text: bool = True):
    return subprocess.run(command, check=True, capture_output=True, text=text)


def _write_event_track(path: Path) -> None:
    """Four literal one-second events; the selected passage is 880/1320 Hz."""
    frequencies = (220, 880, 1320, 1760)
    samples = array("h")
    for frequency in frequencies:
        for offset in range(44_100):
            samples.append(round(
                14_000 * math.sin(2 * math.pi * frequency * offset / 44_100)))
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(44_100)
        target.writeframes(samples.tobytes())


def _make_sources(tools, root: Path):
    first = root / "fixture-a.ts"
    second = root / "fixture-b.ts"
    _run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-y",
        "-f", "lavfi", "-i", f"color=red:size={WIDTH}x{HEIGHT}:rate={FPS}:duration=2",
        "-f", "lavfi", "-i", f"color=green:size={WIDTH}x{HEIGHT}:rate={FPS}:duration=1",
        "-f", "lavfi", "-i", f"color=yellow:size={WIDTH}x{HEIGHT}:rate={FPS}:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=5",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-map", "3:a:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", str(FPS),
        "-sc_threshold", "0", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-f", "mpegts", str(first),
    ])
    _run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-y", "-f", "lavfi", "-i",
        f"color=blue:size={WIDTH}x{HEIGHT}:rate={FPS}:duration=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", str(FPS),
        "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-an",
        "-f", "mpegts", str(second),
    ])
    a, b = probe(tools, first), probe(tools, second)
    assert not a.error and not b.error
    assert (a.width, a.height, round(a.fps), a.has_audio) == (
        WIDTH, HEIGHT, FPS, True)
    assert (b.width, b.height, round(b.fps), b.has_audio) == (
        WIDTH, HEIGHT, FPS, False)
    a.selects = [Select(1.0, 4.0, "A", sid="a")]
    b.selects = [Select(1.0, 3.0, "B", sid="b")]
    items = [
        Item(a.fingerprint, "a"),
        Item(b.fingerprint, "b"),
        Item(a.fingerprint, "a"),
    ]
    rows = resolve(items, [a, b])
    assert all(not row.missing for row in rows)
    pieces = [export_piece(row) for row in rows]
    output = working_outputs(pieces, joined=True)[0]
    sequence = compile_sequence(
        output, resolution=Resolution.success(), revision="literal-a-b-a")
    # Fixture-shape checks only. Expected output values below stay literal.
    assert [piece.trimmed_duration for piece in pieces] == [3.0, 2.0, 3.0]
    assert tuple(output.target.items) == tuple(items)
    return pieces, output.target, sequence, first, second


@dataclass(frozen=True)
class JoinedFixture:
    root: Path
    pieces: list
    target: object
    sequence: object
    asset: AudioAsset
    first: Path
    second: Path


@pytest.fixture(scope="module")
def joined_fixture(tools, tmp_path_factory):
    root = tmp_path_factory.mktemp("sequence-music")
    pieces, target, sequence, first, second = _make_sources(tools, root)
    track = root / "events-44100.wav"
    _write_event_track(track)
    asset = AudioAsset(
        track.resolve(), file_sha256(track), 0, 44_100, 1, 4 * 44_100)
    return JoinedFixture(root, pieces, target, sequence, asset, first, second)


def _choice(fixture: JoinedFixture, mode: AudioMode,
            policy: ShortTrackPolicy = ShortTrackPolicy.LOOP) -> MusicChoice:
    if mode in (AudioMode.ORIGINAL, AudioMode.NO_SOUND):
        return MusicChoice(mode=mode)
    return MusicChoice(
        asset=fixture.asset,
        passage=SampleSpan(44_100, 3 * 44_100, 44_100),
        mode=mode,
        short_track=policy,
        music_level=(Fraction(1) if mode is AudioMode.REPLACE
                     else Fraction(3, 4)),
        dvr_level=Fraction(1, 4),
        fade_in_samples=4_800,
        fade_out_samples=4_800,
    )


def _export(tools, fixture: JoinedFixture, tmp_path: Path, name: str,
            choice: MusicChoice) -> Path:
    target = tmp_path / f"{name}.mp4"
    job = Job(
        fixture.pieces, "master",
        ExportSettings(master_speed="ultrafast", colour=PASSTHROUGH),
        target, audio=choice, target=fixture.target,
        sequence=fixture.sequence,
    )
    worker = ExportWorker(tools, [job], tmp_path / "work")
    ok, message = worker._run_job(0, job)
    assert ok, message
    assert target.exists() and target.stat().st_size > 0
    assert worker._process is None
    assert not list(tmp_path.glob("*.flightdvr-part*"))
    return target


def _stream_facts(tools, path: Path) -> dict:
    result = _run([
        str(tools.ffprobe), "-v", "error", "-show_entries",
        "stream=codec_type,codec_name,sample_rate,channels,duration:format=duration",
        "-of", "json", str(path),
    ])
    return json.loads(result.stdout)


def _video_frames(tools, path: Path) -> tuple[list[Fraction], list[str]]:
    timing = json.loads(_run([
        str(tools.ffprobe), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=time_base:frame=best_effort_timestamp",
        "-of", "json",
        str(path),
    ]).stdout)
    time_base = Fraction(timing["streams"][0]["time_base"])
    pts = [int(frame["best_effort_timestamp"]) * time_base
           for frame in timing["frames"]]
    raw = _run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(path), "-map", "0:v:0", "-pix_fmt", "rgb24",
        "-f", "rawvideo", "-",
    ], text=False).stdout
    frame_bytes = WIDTH * HEIGHT * 3
    assert len(raw) % frame_bytes == 0
    labels = []
    for start in range(0, len(raw), frame_bytes):
        frame = raw[start:start + frame_bytes]
        count = WIDTH * HEIGHT
        red = sum(frame[0::3]) / count
        green = sum(frame[1::3]) / count
        blue = sum(frame[2::3]) / count
        if red > 1.5 * green and red > 1.5 * blue:
            labels.append("red")
        elif green > 1.5 * red and green > 1.5 * blue:
            labels.append("green")
        elif red > 1.5 * blue and green > 1.5 * blue:
            labels.append("yellow")
        elif blue > 1.5 * red and blue > 1.5 * green:
            labels.append("blue")
        else:
            labels.append("unknown")
    assert len(pts) == len(labels)
    return pts, labels


def _transitions(pts: list[Fraction], labels: list[str]):
    return tuple((label, pts[index]) for index, label in enumerate(labels)
                 if index == 0 or label != labels[index - 1])


def _video_oracle(pts: list[Fraction], labels: list[str]) -> bool:
    return (
        len(labels) == EXPECTED_FRAMES
        and _transitions(pts, labels) == EXPECTED_TRANSITIONS
        and pts[-1] == Fraction(239, 30)
    )


def _decode_mono(tools, path: Path) -> array:
    result = _run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar",
        str(OUTPUT_RATE), "-f", "f32le", "-",
    ], text=False)
    decoded = array("f")
    decoded.frombytes(result.stdout)
    return decoded


def _magnitude(samples: array, seconds: float, frequency: int) -> float:
    center = round(seconds * OUTPUT_RATE)
    start = center - WINDOW // 2
    window = samples[start:start + WINDOW]
    if len(window) != WINDOW:
        return 0.0
    mean = sum(window) / WINDOW
    cosine = sine = 0.0
    for index, value in enumerate(window):
        angle = 2 * math.pi * frequency * index / OUTPUT_RATE
        centered = value - mean
        cosine += centered * math.cos(angle)
        sine += centered * math.sin(angle)
    return 2 * math.hypot(cosine, sine) / WINDOW


def _dominates(samples: array, seconds: float, wanted: int,
               unwanted: tuple[int, ...]) -> bool:
    expected = _magnitude(samples, seconds, wanted)
    return expected >= 0.015 and all(
        expected >= 4 * _magnitude(samples, seconds, other)
        for other in unwanted
    )


def test_published_master_modes_follow_literal_a_b_a_finished_time(
        tools, joined_fixture, tmp_path):
    fixture = joined_fixture
    outputs = {
        "original": _export(
            tools, fixture, tmp_path, "original",
            _choice(fixture, AudioMode.ORIGINAL)),
        "no_sound": _export(
            tools, fixture, tmp_path, "no-sound",
            _choice(fixture, AudioMode.NO_SOUND)),
        "replace_loop": _export(
            tools, fixture, tmp_path, "replace-loop",
            _choice(fixture, AudioMode.REPLACE, ShortTrackPolicy.LOOP)),
        "mix_once": _export(
            tools, fixture, tmp_path, "mix-once",
            _choice(fixture, AudioMode.MIX, ShortTrackPolicy.PLAY_ONCE)),
    }

    pts, labels = _video_frames(tools, outputs["mix_once"])
    assert _video_oracle(pts, labels), _transitions(pts, labels)
    # Negative net: swapped order, missing repeated A, and a one-frame seam
    # extension are all rejected by the produced-file oracle.
    swapped = list(labels)
    swapped[:90], swapped[90:150] = swapped[60:120], swapped[:60]
    assert not _video_oracle(pts, swapped)
    assert not _video_oracle(pts[:150], labels[:150])
    shifted = list(labels)
    shifted[150] = "blue"
    assert not _video_oracle(pts, shifted)

    facts = {name: _stream_facts(tools, path)
             for name, path in outputs.items()}
    for name, value in facts.items():
        streams = value.get("streams", [])
        assert len([s for s in streams if s.get("codec_type") == "video"]) == 1
        assert Fraction(value["format"]["duration"]) == Fraction(8)
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        if name == "no_sound":
            assert audio == []
        else:
            assert len(audio) == 1
            assert (int(audio[0]["sample_rate"]), int(audio[0]["channels"])) == (
                OUTPUT_RATE, 2)

    original = _decode_mono(tools, outputs["original"])
    replace = _decode_mono(tools, outputs["replace_loop"])
    mixed = _decode_mono(tools, outputs["mix_once"])
    assert min(len(original), len(replace), len(mixed)) >= EXPECTED_SAMPLES

    # Original is A tone / explicit B silence / repeated A tone.
    for point in (0.5, 1.5, 2.5, 5.5, 6.5, 7.5):
        assert _magnitude(original, point, 440) >= 0.02
    for point in (3.5, 4.5):
        assert _magnitude(original, point, 440) < 0.002

    # Replace loops 880/1320 on finished time.  At 3.5 s it must be 1320:
    # restarting the passage at the 3 s seam would incorrectly produce 880.
    expected_music = (
        (0.5, 880), (1.5, 1320), (2.5, 880), (3.5, 1320),
        (4.5, 880), (5.5, 1320), (6.5, 880), (7.5, 1320),
    )
    for point, frequency in expected_music:
        other = 1320 if frequency == 880 else 880
        assert _dominates(replace, point, frequency, (other, 440))

    # Mix/Play once contains its two music events once, then only source A;
    # the silent B occurrence remains silent after music ends.
    assert _magnitude(mixed, 0.5, 440) >= 0.01
    assert _magnitude(mixed, 0.5, 880) >= 0.02
    assert _magnitude(mixed, 1.5, 440) >= 0.01
    assert _magnitude(mixed, 1.5, 1320) >= 0.02
    assert _magnitude(mixed, 2.5, 440) >= 0.01
    assert max(_magnitude(mixed, 2.5, tone) for tone in (880, 1320)) < 0.002
    assert max(_magnitude(mixed, 3.5, tone) for tone in (440, 880, 1320)) < 0.002
    assert _magnitude(mixed, 5.5, 440) >= 0.01

    # Requested gains and nonzero fades are measured against single-signal
    # outputs, not inferred from the filter expression.
    assert (_magnitude(mixed, 0.5, 440)
            / _magnitude(original, 0.5, 440)) == pytest.approx(0.25, abs=0.08)
    assert (_magnitude(mixed, 0.5, 880)
            / _magnitude(replace, 0.5, 880)) == pytest.approx(0.75, abs=0.10)
    assert _magnitude(replace, 0.05, 880) < _magnitude(replace, 0.5, 880)
    assert _magnitude(mixed, 1.95, 1320) < _magnitude(mixed, 1.5, 1320)

    # Audio negative net: silence, a wrong tone and a seam restart do not pass.
    silent = array("f", [0.0]) * EXPECTED_SAMPLES
    assert not _dominates(silent, 0.5, 880, (440, 1320))
    wrong = array("f", (
        0.4 * math.sin(2 * math.pi * 660 * n / OUTPUT_RATE)
        for n in range(OUTPUT_RATE)
    ))
    assert not _dominates(wrong, 0.5, 880, (440, 660, 1320))
    restarted = array("f", replace)
    center = round(3.5 * OUTPUT_RATE)
    for n in range(center - WINDOW // 2, center + WINDOW // 2):
        restarted[n] = 0.4 * math.sin(2 * math.pi * 880 * n / OUTPUT_RATE)
    assert not _dominates(restarted, 3.5, 1320, (440, 880))

    measurement = {
        "frame_count": len(labels),
        "transitions": [(name, str(point))
                        for name, point in _transitions(pts, labels)],
        "decoded_audio_samples": {
            "original": len(original), "replace_loop": len(replace),
            "mix_once": len(mixed),
        },
        "fixture_sha256_16": {
            "a": file_sha256(fixture.first)[:16],
            "b": file_sha256(fixture.second)[:16],
            "music": fixture.asset.sha256[:16],
        },
        "output_sha256_16": {
            name: file_sha256(path)[:16] for name, path in outputs.items()
        },
        "ffmpeg": str(tools.ffmpeg),
        "ffprobe": str(tools.ffprobe),
    }
    print("SEQUENCE_MUSIC_EXPORT_MEASUREMENT " + json.dumps(
        measurement, sort_keys=True))


def test_cancelling_a_live_joined_graph_preserves_the_previous_file(
        tools, joined_fixture, tmp_path, monkeypatch):
    fixture = joined_fixture
    target = tmp_path / "cancelled.mp4"
    sentinel = b"previous completed output"
    target.write_bytes(sentinel)
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    job = Job(
        fixture.pieces, "master",
        ExportSettings(master_speed="ultrafast", colour=PASSTHROUGH),
        target, audio=_choice(fixture, AudioMode.MIX),
        target=fixture.target, sequence=fixture.sequence,
    )
    worker = ExportWorker(tools, [job], tmp_path / "work")
    real_build = jobs_module.build_commands

    def throttled(*args, **kwargs):
        commands = real_build(*args, **kwargs)
        for command in commands:
            command.insert(command.index("-i"), "-re")
        return commands

    monkeypatch.setattr(jobs_module, "build_commands", throttled)
    result = []
    runner = threading.Thread(
        target=lambda: result.append(worker._run_job(0, job)), daemon=True)
    runner.start()
    part = target.with_name("cancelled.flightdvr-part.mp4")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if (worker._process is not None and worker._process.poll() is None
                and part.exists() and part.stat().st_size > 0):
            break
        time.sleep(0.02)
    else:
        pytest.fail("the joined graph never reached a live partial output")

    observed = part.stat().st_size
    worker.cancel()
    runner.join(timeout=10)
    assert not runner.is_alive(), "cancelled joined FFmpeg did not settle"
    assert result == [(False, "Cancelled")]
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before
    assert target.read_bytes() == sentinel
    assert not part.exists()
    assert worker._process is None
    print("SEQUENCE_MUSIC_CANCEL_MEASUREMENT " + json.dumps({
        "partial_bytes_before_cancel": observed,
        "previous_sha256": before,
        "previous_sha256_after": hashlib.sha256(target.read_bytes()).hexdigest(),
    }, sort_keys=True))


def test_hardcoded_music_input_one_is_caught_before_publication(
        tools, joined_fixture, tmp_path, monkeypatch):
    """Input 1 is silent B, not music; the old assumption must be observable."""
    import flightdvr.audio_export as audio_export

    fixture = joined_fixture
    actual = audio_export.planned_audio_chains

    def hardcoded(plan, *, music_input_index, source_label=None,
                  source_seek_samples=0, output_label="planned_audio"):
        return actual(
            plan, music_input_index=1, source_label=source_label,
            source_seek_samples=source_seek_samples, output_label=output_label)

    monkeypatch.setattr(audio_export, "planned_audio_chains", hardcoded)
    target = tmp_path / "wrong-index.mp4"
    job = Job(
        fixture.pieces, "master",
        ExportSettings(master_speed="ultrafast", colour=PASSTHROUGH),
        target, audio=_choice(fixture, AudioMode.REPLACE),
        target=fixture.target, sequence=fixture.sequence,
    )
    ok, message = ExportWorker(
        tools, [job], tmp_path / "work")._run_job(0, job)
    assert not ok, "hard-coded input 1 unexpectedly published a file"
    assert message
    assert not target.exists()
    assert not list(tmp_path.glob("*.flightdvr-part*"))
