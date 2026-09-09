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
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from flightdvr.audio_export import file_sha256
from flightdvr.audio_plan import (
    AudioAsset, AudioMode, MusicChoice, OUTPUT_RATE, SampleSpan,
    ShortTrackPolicy,
)
from flightdvr.jobs import ExportWorker, Job
from flightdvr.media import ClipInfo, find_tools
from flightdvr.presets import ExportSettings


pytestmark = pytest.mark.integration


def run(command):
    return subprocess.run(command, check=True, capture_output=True, text=True)


@pytest.fixture(scope="module")
def tools():
    return find_tools()


@pytest.fixture
def media(tmp_path, tools):
    source = tmp_path / "source.mp4"
    silent = tmp_path / "silent.mp4"
    music = tmp_path / "music.wav"
    common_video = [
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=1.2",
    ]
    run([str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
         *common_video, "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000:duration=1.2",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(source)])
    run([str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
         *common_video, "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-an", str(silent)])
    run([str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i",
         "sine=frequency=880:sample_rate=48000:duration=0.4",
         "-c:a", "pcm_s16le", str(music)])

    def clip(path, audio):
        made = ClipInfo(
            path, path.stat().st_size, datetime.now(), 1.2, 160, 90, 30,
            "h264", "aac" if audio else "", "yuv420p", "tv",
        )
        made.trim_in = 0.2
        made.trim_out = 1.0
        return made

    found = AudioAsset(music, file_sha256(music), 0, OUTPUT_RATE, 1,
                       round(0.4 * OUTPUT_RATE))
    return clip(source, True), clip(silent, False), found


def choice(asset, mode, policy=ShortTrackPolicy.LOOP):
    return MusicChoice(
        asset.track, mode, asset, SampleSpan(2_400, 9_600, OUTPUT_RATE),
        policy, 1, 1, 0, 0,
    )


def streams(tools, path):
    result = run([str(tools.ffprobe), "-v", "error", "-count_frames",
                  "-show_entries",
                  "stream=codec_type,sample_rate,channels,nb_read_frames",
                  "-of", "json", str(path)])
    return json.loads(result.stdout)["streams"]


def export(tools, tmp_path, clip, audio):
    out = tmp_path / f"{audio.mode.value}.mp4"
    settings = ExportSettings(master_speed="ultrafast")
    job = Job([clip], "master", settings, out, audio=audio)
    worker = ExportWorker(tools, [job], tmp_path / "work")
    ok, message = worker._run_job(0, job)
    assert ok, message
    return out


def test_all_four_modes_write_the_expected_streams_and_keep_video_frames(
        media, tools, tmp_path):
    source, _, asset = media
    outputs = {
        mode: export(tools, tmp_path, source,
                     MusicChoice(mode=mode) if mode in (
                         AudioMode.ORIGINAL, AudioMode.NO_SOUND)
                     else choice(asset, mode))
        for mode in AudioMode
    }
    inspected = {mode: streams(tools, path) for mode, path in outputs.items()}
    frames = []
    for mode, found in inspected.items():
        video = next(s for s in found if s["codec_type"] == "video")
        frames.append(int(video["nb_read_frames"]))
        audio = [s for s in found if s["codec_type"] == "audio"]
        assert bool(audio) is (mode is not AudioMode.NO_SOUND)
        if audio:
            assert (int(audio[0]["sample_rate"]), int(audio[0]["channels"])) == (
                OUTPUT_RATE, 2)
    assert len(set(frames)) == 1
    assert frames[0] == 24  # 0.8 s at the fixture's asserted 30 fps.


def test_mix_without_dvr_audio_is_still_a_music_export(media, tools, tmp_path):
    _, silent, asset = media
    out = export(tools, tmp_path, silent, choice(asset, AudioMode.MIX))
    assert any(s["codec_type"] == "audio" for s in streams(tools, out))


def test_corrupt_music_preserves_an_existing_target_and_leaves_no_part(
        media, tools, tmp_path):
    source, _, _ = media
    corrupt = tmp_path / "corrupt.wav"
    corrupt.write_bytes(b"not audio")
    asset = AudioAsset(corrupt, file_sha256(corrupt), 0, OUTPUT_RATE, 1, 9_600)
    out = tmp_path / "kept.mp4"
    sentinel = b"existing target"
    out.write_bytes(sentinel)
    job = Job([source], "master", ExportSettings(master_speed="ultrafast"),
              out, audio=choice(asset, AudioMode.REPLACE))
    worker = ExportWorker(tools, [job], tmp_path / "work")
    ok, _ = worker._run_job(0, job)
    assert not ok
    assert out.read_bytes() == sentinel
    assert not out.with_name("kept.flightdvr-part.mp4").exists()


def test_changed_music_is_refused_before_the_target_is_touched(media, tools,
                                                                tmp_path):
    source, _, asset = media
    asset.track.write_bytes(asset.track.read_bytes() + b"changed")
    out = tmp_path / "not-created.mp4"
    job = Job([source], "master", ExportSettings(), out,
              audio=choice(asset, AudioMode.REPLACE))
    ok, message = ExportWorker(tools, [job], tmp_path / "work")._run_job(0, job)
    assert not ok and "changed" in message
    assert not out.exists()
