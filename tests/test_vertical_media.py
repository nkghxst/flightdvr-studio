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

"""Measure the vertical export against frames made by a real ffmpeg."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conftest import probe_output
from flightdvr.jobs import ExportWorker, Job
from flightdvr.media import probe
from flightdvr.presets import ExportSettings


pytestmark = pytest.mark.integration


def _make_bars(tools, path: Path, width: int, height: int) -> None:
    """Build a short HEVC transport stream with three measurable regions."""
    if width == 1280:
        bars = (
            f"color=c=0xff0000:s=426x{height}:r=30:d=1[left];"
            f"color=c=0x00ff00:s=428x{height}:r=30:d=1[centre];"
            f"color=c=0x0000ff:s=426x{height}:r=30:d=1[right];"
            "[left][centre][right]hstack=inputs=3"
        )
    else:
        bars = f"color=c=0x404040:s={width}x{height}:r=30:d=1"

    result = subprocess.run([
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-y",
        "-f", "lavfi", "-i", bars,
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-c:v", "libx265", "-preset", "ultrafast",
        "-x265-params", "keyint=30:min-keyint=30:scenecut=0:log-level=none",
        "-pix_fmt", "yuv420p", "-color_range", "pc",
        "-c:a", "aac", "-b:a", "96k", "-shortest",
        "-f", "mpegts", str(path),
    ], capture_output=True, text=True)
    if result.returncode != 0 or not path.exists():
        tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        pytest.skip(f"could not build vertical test media:\n{tail}")


@pytest.fixture(scope="module")
def vertical_sources(tools, media_dir) -> dict[tuple[int, int], Path]:
    sources = {
        (1280, 720): media_dir / "vertical_bars_1280x720.ts",
        (1920, 1080): media_dir / "vertical_flat_1920x1080.ts",
    }
    for (width, height), path in sources.items():
        if not path.exists():
            _make_bars(tools, path, width, height)
        facts = probe_output(tools, path)
        assert (facts["width"], facts["height"]) == (width, height)
    return sources


def _stream_facts(tools, path: Path) -> dict:
    result = subprocess.run([
        str(tools.ffprobe), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,sample_aspect_ratio",
        "-of", "json", str(path),
    ], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)["streams"][0]


def _export(tools, source: Path, target: Path, work: Path, position: int):
    info = probe(tools, source)
    job = Job(
        clips=[info],
        preset_key="vertical",
        settings=ExportSettings(vertical_position=position),
        out_path=target,
    )
    return ExportWorker(tools, [job], work)._run_job(0, job)


def _centre_pixel(tools, path: Path) -> tuple[int, int, int]:
    pixel_path = path.with_suffix(".pixel.bmp")
    try:
        result = subprocess.run([
            str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error",
            "-y", "-i", str(path), "-map", "0:v:0", "-frames:v", "1",
            "-vf", "scale=1:1,format=rgb24",
            "-an", "-f", "image2", str(pixel_path),
        ], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        raw = pixel_path.read_bytes()
        # A 1x1 24-bit BMP stores the single pixel as B, G, R at byte 54.
        assert raw[0:2] == b"BM" and len(raw) >= 57
        return raw[56], raw[55], raw[54]
    finally:
        pixel_path.unlink(missing_ok=True)


def test_vertical_delivery_crop_positions_and_square_sar_are_measured(
    tools, vertical_sources, tmp_path
):
    """Real frames prove the crop positions, dimensions, audio and SAR."""
    source = vertical_sources[(1280, 720)]
    expected_channels = {0: 0, 50: 1, 100: 2}

    for position, channel in expected_channels.items():
        output = tmp_path / f"vertical-{position}.mp4"
        ok, message = _export(tools, source, output, tmp_path, position)
        assert ok, message

        facts = probe_output(tools, output)
        assert (facts["width"], facts["height"]) == (720, 1280)
        assert facts["has_audio"]
        stream = _stream_facts(tools, output)
        assert stream["sample_aspect_ratio"] == "1:1"

        pixel = _centre_pixel(tools, output)
        dominant = pixel[channel]
        assert dominant > 120, pixel
        assert dominant > max(pixel[(channel + 1) % 3],
                              pixel[(channel + 2) % 3]) + 50, pixel


def test_vertical_1080_line_source_reaches_the_1080_by_1920_delivery_size(
    tools, vertical_sources, tmp_path
):
    source = vertical_sources[(1920, 1080)]
    output = tmp_path / "vertical-1080.mp4"
    ok, message = _export(tools, source, output, tmp_path, 50)
    assert ok, message

    facts = probe_output(tools, output)
    assert (facts["width"], facts["height"]) == (1080, 1920)
    assert _stream_facts(tools, output)["sample_aspect_ratio"] == "1:1"
