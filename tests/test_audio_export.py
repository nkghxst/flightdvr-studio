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

"""Actual one-range Master exports through the submitted audio plan."""

from __future__ import annotations

import json
import math
import subprocess
import threading
import time
import wave
from array import array
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

import flightdvr.jobs as jobs_module
from flightdvr.audio_export import file_sha256
from flightdvr.audio_plan import (
    AudioAsset, AudioMode, MusicChoice, OUTPUT_RATE, SampleSpan,
    ShortTrackPolicy,
)
from flightdvr.jobs import ExportWorker, Job, JobStatus
from flightdvr.media import ClipInfo, find_tools
from flightdvr.presets import ExportSettings


pytestmark = pytest.mark.integration

SOURCE_SECONDS = 1.2
TRIM_IN = 0.2
TRIM_OUT = 1.0
OUTPUT_SECONDS = TRIM_OUT - TRIM_IN
OUTPUT_SAMPLES = round(OUTPUT_SECONDS * OUTPUT_RATE)
SOURCE_VIDEO_FRAMES = 36
OUTPUT_VIDEO_FRAMES = 24
MUSIC_BLOCK_SAMPLES = 4_800
MUSIC_PASSAGE = SampleSpan(4_800, 19_200, OUTPUT_RATE)
MUSIC_SAMPLES = MUSIC_PASSAGE.samples
MUSIC_TONES = (880, 1320, 1760)
OUTSIDE_TONES = (330, 2200)

# Declared before seeing the outputs. One AAC frame is 1,024 samples. Event
# edges get a smaller 768-sample allowance because each side is measured with
# independent 512-sample spectral windows rather than sample equality.
SAMPLE_TOLERANCE = 1_024
EVENT_TOLERANCE = 768
GAIN_TOLERANCE = 0.10


def run(command, *, text=True):
    return subprocess.run(command, check=True, capture_output=True, text=text)


@pytest.fixture(scope="module")
def tools():
    return find_tools()


@dataclass(frozen=True)
class MediaFixture:
    source: ClipInfo
    silent: ClipInfo
    asset: AudioAsset
    review_ids: dict[str, str]


def _write_tone_blocks(path: Path) -> None:
    """Write five exact 100 ms blocks without asking FFmpeg to round them."""
    samples = array("h")
    amplitude = 18_000
    for frequency in (*OUTSIDE_TONES[:1], *MUSIC_TONES, *OUTSIDE_TONES[1:]):
        for offset in range(MUSIC_BLOCK_SAMPLES):
            value = amplitude * math.sin(
                2 * math.pi * frequency * offset / OUTPUT_RATE)
            samples.append(round(value))
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(OUTPUT_RATE)
        target.writeframes(samples.tobytes())


def _probe_streams(tools, path: Path) -> list[dict]:
    result = run([
        str(tools.ffprobe), "-v", "error", "-count_frames", "-show_entries",
        "stream=index,codec_type,codec_name,sample_rate,channels,"
        "nb_read_frames,r_frame_rate,duration,width,height",
        "-of", "json", str(path),
    ])
    return json.loads(result.stdout)["streams"]


def _decode_mono(tools, path: Path) -> array:
    result = run([
        str(tools.ffmpeg), "-v", "error", "-i", str(path), "-map", "0:a:0",
        "-ac", "1", "-ar", str(OUTPUT_RATE), "-f", "f32le", "-",
    ], text=False)
    assert len(result.stdout) % 4 == 0
    decoded = array("f")
    decoded.frombytes(result.stdout)
    return decoded


@pytest.fixture(scope="module")
def media(tmp_path_factory, tools):
    root = tmp_path_factory.mktemp("audio-events")
    source = root / "source.mp4"
    silent = root / "silent.mp4"
    music = root / "music-events.wav"
    common_video = [
        "-f", "lavfi", "-i",
        f"testsrc2=size=160x90:rate=30:duration={SOURCE_SECONDS}",
    ]
    run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
        *common_video, "-f", "lavfi", "-i",
        f"sine=frequency=440:sample_rate={OUTPUT_RATE}:duration={SOURCE_SECONDS}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(source),
    ])
    run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
        *common_video, "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-an", str(silent),
    ])
    _write_tone_blocks(music)

    source_streams = _probe_streams(tools, source)
    silent_streams = _probe_streams(tools, silent)
    music_streams = _probe_streams(tools, music)
    source_video = next(
        s for s in source_streams if s["codec_type"] == "video")
    silent_video = next(
        s for s in silent_streams if s["codec_type"] == "video")
    source_audio = next(
        s for s in source_streams if s["codec_type"] == "audio")
    music_audio = next(
        s for s in music_streams if s["codec_type"] == "audio")

    # These properties make the fixture an oracle. If an encoder or generator
    # rounds one away, the test stops instead of silently testing another case.
    assert int(source_video["nb_read_frames"]) == SOURCE_VIDEO_FRAMES
    assert int(silent_video["nb_read_frames"]) == SOURCE_VIDEO_FRAMES
    assert (int(source_video["width"]), int(source_video["height"])) == (160, 90)
    assert (int(source_audio["sample_rate"]), int(source_audio["channels"])) == (
        OUTPUT_RATE, 1)
    assert not [s for s in silent_streams if s["codec_type"] == "audio"]
    assert (music_audio["codec_name"], int(music_audio["sample_rate"]),
            int(music_audio["channels"])) == ("pcm_s16le", OUTPUT_RATE, 1)
    decoded_music = _decode_mono(tools, music)
    assert len(decoded_music) == 5 * MUSIC_BLOCK_SAMPLES
    assert MUSIC_PASSAGE.start == MUSIC_BLOCK_SAMPLES
    assert MUSIC_PASSAGE.end == 4 * MUSIC_BLOCK_SAMPLES

    def clip(path, audio):
        made = ClipInfo(
            path, path.stat().st_size, datetime.now(), SOURCE_SECONDS, 160, 90,
            30, "h264", "aac" if audio else "", "yuv420p", "tv",
        )
        made.trim_in = TRIM_IN
        made.trim_out = TRIM_OUT
        assert made.trimmed_duration == pytest.approx(OUTPUT_SECONDS)
        return made

    hashes = {
        name: file_sha256(path)
        for name, path in (("source", source), ("silent", silent),
                          ("music", music))
    }
    fixture = MediaFixture(
        clip(source, True), clip(silent, False),
        AudioAsset(music, hashes["music"], 0, OUTPUT_RATE, 1,
                   len(decoded_music)),
        {name: digest[:16] for name, digest in hashes.items()},
    )
    print(f"fixture review IDs: {fixture.review_ids}")
    return fixture


def choice(asset, mode, policy=ShortTrackPolicy.LOOP, *, music_level=1,
           dvr_level=1, fade_in=0, fade_out=0):
    return MusicChoice(
        asset.track, mode, asset, MUSIC_PASSAGE, policy, music_level, dvr_level,
        fade_in, fade_out,
    )


def export(tools, tmp_path, clip, audio, *, name=None):
    out = tmp_path / f"{name or audio.mode.value}.mp4"
    settings = ExportSettings(master_speed="ultrafast")
    job = Job([clip], "master", settings, out, audio=audio)
    worker = ExportWorker(tools, [job], tmp_path / "work")
    ok, message = worker._run_job(0, job)
    assert ok, message
    return out


def _stream_metrics(tools, path: Path) -> dict:
    found = _probe_streams(tools, path)
    video = next(s for s in found if s["codec_type"] == "video")
    audio = [s for s in found if s["codec_type"] == "audio"]
    samples = _decode_mono(tools, path) if audio else array("f")
    return {
        "video_frames": int(video["nb_read_frames"]),
        "video_seconds": float(video["duration"]),
        "audio_rate": int(audio[0]["sample_rate"]) if audio else None,
        "audio_channels": int(audio[0]["channels"]) if audio else None,
        "audio_samples": len(samples),
        "audio": samples,
    }


def _magnitude(samples, center: int, frequency: int, width: int = 512) -> float:
    start = max(0, center - width // 2)
    window = samples[start:start + width]
    assert len(window) == width
    mean = sum(window) / width
    cosine = sine = 0.0
    for index, value in enumerate(window):
        angle = 2 * math.pi * frequency * index / OUTPUT_RATE
        centered = value - mean
        cosine += centered * math.cos(angle)
        sine += centered * math.sin(angle)
    return 2 * math.hypot(cosine, sine) / width


def _dominant(samples, center: int) -> int:
    candidates = (*OUTSIDE_TONES, *MUSIC_TONES)
    magnitudes = {
        frequency: _magnitude(samples, center, frequency)
        for frequency in candidates
    }
    return max(magnitudes, key=magnitudes.get)


def _transition(samples, left: int, right: int, expected: int) -> int:
    centers = range(
        expected - EVENT_TOLERANCE, expected + EVENT_TOLERANCE + 1, 64)
    changes = []
    for center in centers:
        delta = (_magnitude(samples, center, right)
                 - _magnitude(samples, center, left))
        changes.append((center, delta))
    crossing = [
        (left_point[0] + right_point[0]) // 2
        for left_point, right_point in zip(changes, changes[1:])
        if left_point[1] <= 0 < right_point[1]
    ]
    assert crossing, f"no {left} Hz to {right} Hz transition near {expected}"
    return min(crossing, key=lambda value: abs(value - expected))


def test_all_four_modes_keep_the_requested_video_and_audio_extent(
        media, tools, tmp_path):
    outputs = {
        mode: export(
            tools, tmp_path, media.source,
            MusicChoice(mode=mode) if mode in (
                AudioMode.ORIGINAL, AudioMode.NO_SOUND)
            else choice(media.asset, mode),
        )
        for mode in AudioMode
    }
    metrics = {
        mode: _stream_metrics(tools, path) for mode, path in outputs.items()
    }
    for mode, measured in metrics.items():
        assert measured["video_frames"] == OUTPUT_VIDEO_FRAMES
        assert measured["video_seconds"] == pytest.approx(
            OUTPUT_SECONDS, abs=1 / 30)
        if mode is AudioMode.NO_SOUND:
            assert measured["audio_samples"] == 0
            continue
        assert (measured["audio_rate"], measured["audio_channels"]) == (
            OUTPUT_RATE, 2)
        assert abs(measured["audio_samples"] - OUTPUT_SAMPLES) <= SAMPLE_TOLERANCE
        assert abs(measured["audio_samples"] / OUTPUT_RATE
                   - measured["video_seconds"]) <= SAMPLE_TOLERANCE / OUTPUT_RATE
    print("mode measurements:", {
        mode.value: {
            "frames": value["video_frames"],
            "video_s": value["video_seconds"],
            "decoded_audio_samples": value["audio_samples"],
        }
        for mode, value in metrics.items()
    })


def test_selected_events_loop_from_the_in_point_without_outside_tones(
        media, tools, tmp_path):
    out = export(
        tools, tmp_path, media.source,
        choice(media.asset, AudioMode.REPLACE), name="event-loop")
    samples = _decode_mono(tools, out)
    expected_blocks = [
        (2_400, 880), (7_200, 1320), (12_000, 1760),
        (16_800, 880), (21_600, 1320), (26_400, 1760),
        (31_200, 880), (36_000, 1320),
    ]
    assert [_dominant(samples, center) for center, _ in expected_blocks] == [
        frequency for _, frequency in expected_blocks
    ]
    transitions = {
        expected: _transition(samples, left, right, expected)
        for expected, left, right in (
            (4_800, 880, 1320), (9_600, 1320, 1760),
            (14_400, 1760, 880), (19_200, 880, 1320),
            (24_000, 1320, 1760), (28_800, 1760, 880),
            (33_600, 880, 1320),
        )
    }
    assert all(abs(found - expected) <= EVENT_TOLERANCE
               for expected, found in transitions.items())
    print("event transitions (expected:decoded sample):", transitions)


def test_play_once_fades_once_then_is_silent_to_the_video_end(
        media, tools, tmp_path):
    reference = _decode_mono(tools, export(
        tools, tmp_path, media.source,
        choice(media.asset, AudioMode.REPLACE), name="fade-reference"))
    faded = _decode_mono(tools, export(
        tools, tmp_path, media.source,
        choice(media.asset, AudioMode.REPLACE, ShortTrackPolicy.PLAY_ONCE,
               fade_in=4_800, fade_out=4_800),
        name="play-once-faded"))
    ratios = {
        center: (_magnitude(faded, center, frequency)
                 / _magnitude(reference, center, frequency))
        for center, frequency in (
            (2_400, 880), (7_200, 1320), (12_000, 1760))
    }
    assert ratios[2_400] == pytest.approx(0.5, abs=GAIN_TOLERANCE)
    assert ratios[7_200] == pytest.approx(1.0, abs=GAIN_TOLERANCE)
    assert ratios[12_000] == pytest.approx(0.5, abs=GAIN_TOLERANCE)
    silent_level = max(
        _magnitude(faded, 16_800, frequency)
        for frequency in (*OUTSIDE_TONES, *MUSIC_TONES))
    reference_level = _magnitude(reference, 16_800, 880)
    assert silent_level <= reference_level * 0.03
    print("fade ratios at 2400/7200/12000 and post-end level:",
          ratios, silent_level, "audible_end=14400")


def test_mix_gains_are_measured_against_single_signal_exports(
        media, tools, tmp_path):
    original = _decode_mono(tools, export(
        tools, tmp_path, media.source, MusicChoice(mode=AudioMode.ORIGINAL),
        name="gain-original"))
    replace = _decode_mono(tools, export(
        tools, tmp_path, media.source, choice(media.asset, AudioMode.REPLACE),
        name="gain-replace"))
    mixed = _decode_mono(tools, export(
        tools, tmp_path, media.source,
        choice(media.asset, AudioMode.MIX, music_level=1, dvr_level=1),
        name="gain-mix"))
    no_dvr = _decode_mono(tools, export(
        tools, tmp_path, media.silent,
        choice(media.asset, AudioMode.MIX, music_level=0.6, dvr_level=1),
        name="gain-mix-no-dvr"))

    center = 2_400
    ratios = {
        "mix_music": (_magnitude(mixed, center, 880)
                      / _magnitude(replace, center, 880)),
        "mix_dvr": (_magnitude(mixed, center, 440)
                    / _magnitude(original, center, 440)),
        "no_dvr_music": (_magnitude(no_dvr, center, 880)
                         / _magnitude(replace, center, 880)),
    }
    assert ratios["mix_music"] == pytest.approx(0.5, abs=GAIN_TOLERANCE)
    assert ratios["mix_dvr"] == pytest.approx(0.5, abs=GAIN_TOLERANCE)
    assert ratios["no_dvr_music"] == pytest.approx(0.6, abs=GAIN_TOLERANCE)
    print("decoded gain ratios:", ratios)


def test_corrupt_music_preserves_an_existing_target_and_leaves_no_part(
        media, tools, tmp_path):
    corrupt = tmp_path / "corrupt.wav"
    corrupt.write_bytes(b"not audio")
    asset = AudioAsset(
        corrupt, file_sha256(corrupt), 0, OUTPUT_RATE, 1,
        MUSIC_PASSAGE.end)
    out = tmp_path / "kept.mp4"
    sentinel = b"existing target"
    out.write_bytes(sentinel)
    job = Job(
        [media.source], "master", ExportSettings(master_speed="ultrafast"),
        out, audio=choice(asset, AudioMode.REPLACE))
    worker = ExportWorker(tools, [job], tmp_path / "work")
    ok, _ = worker._run_job(0, job)
    assert not ok
    assert out.read_bytes() == sentinel
    assert not list(tmp_path.glob("*.flightdvr-part*"))


def test_changed_music_is_refused_before_the_target_is_touched(
        media, tools, tmp_path):
    changed = tmp_path / "changed.wav"
    changed.write_bytes(media.asset.track.read_bytes())
    asset = AudioAsset(
        changed, file_sha256(changed), 0, OUTPUT_RATE, 1,
        media.asset.decoded_samples)
    submitted = choice(asset, AudioMode.REPLACE)
    changed.write_bytes(changed.read_bytes() + b"changed")
    out = tmp_path / "not-created.mp4"
    job = Job(
        [media.source], "master", ExportSettings(), out, audio=submitted)
    ok, message = ExportWorker(
        tools, [job], tmp_path / "work")._run_job(0, job)
    assert not ok and "changed" in message
    assert not out.exists()
    assert not list(tmp_path.glob("*.flightdvr-part*"))


def test_cancelling_after_the_real_graph_starts_is_atomic_and_keeps_other_job(
        media, tools, tmp_path, monkeypatch):
    """A live FFmpeg graph is throttled, observed writing, then cancelled."""
    out = tmp_path / "cancelled.mp4"
    sentinel = b"existing destination"
    out.write_bytes(sentinel)
    job = Job(
        [media.source], "master", ExportSettings(master_speed="ultrafast"),
        out, audio=choice(media.asset, AudioMode.MIX))
    queued = Job(
        [media.silent], "master", ExportSettings(master_crf=19),
        tmp_path / "unrelated.mp4",
        audio=choice(media.asset, AudioMode.REPLACE, music_level=0.7))
    queued_before = deepcopy(queued)
    worker = ExportWorker(tools, [job, queued], tmp_path / "work")
    real_build = jobs_module.build_commands

    def throttled_build(*args, **kwargs):
        commands = real_build(*args, **kwargs)
        for command in commands:
            command.insert(command.index("-i"), "-re")
        return commands

    monkeypatch.setattr(jobs_module, "build_commands", throttled_build)
    result = []
    runner = threading.Thread(
        target=lambda: result.append(worker._run_job(0, job)), daemon=True)
    runner.start()
    part = out.with_name("cancelled.flightdvr-part.mp4")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        proc = worker._process
        if (proc is not None and proc.poll() is None and part.exists()
                and part.stat().st_size):
            break
        time.sleep(0.02)
    else:
        pytest.fail("the real music graph never reached a live, writing process")

    observed_size = part.stat().st_size
    worker.cancel()
    runner.join(timeout=10)
    assert not runner.is_alive(), "cancelled FFmpeg did not settle"
    assert result == [(False, "Cancelled")]
    assert out.read_bytes() == sentinel
    assert not list(tmp_path.glob("*.flightdvr-part*"))
    assert not list((tmp_path / "work").glob("pass_*"))
    assert queued.status is JobStatus.PENDING
    assert queued.settings == queued_before.settings
    assert queued.audio == queued_before.audio
    assert queued.out_path == queued_before.out_path
    print("cancelled live graph after part bytes:", observed_size,
          "destination bytes:", len(sentinel))
