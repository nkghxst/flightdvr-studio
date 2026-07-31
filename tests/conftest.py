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

"""Shared fixtures, including real media for the integration tests.

The rest of the suite checks the commands the app *would* issue. That is fast
and it caught nothing, because every serious defect found in the July 2026
review was invisible to argument inspection: a perfectly well-formed command
produced half a second of corrupt video, or an empty container reported as a
success. These fixtures build real files so the integration tests can check
what actually comes out.

Fixtures imitate what a Box Pro writes, because the defects depended on it:
MPEG-TS, HEVC, 60fps, full range, and a keyframe every 1.000 seconds. That last
one is why a mid-GOP trim could corrupt up to a full second of video.

They are small (320x180) and cached for the session, so building them costs a
second or two rather than a minute.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# What a Box Pro writes, at a size that encodes quickly.
FPS = 60
GOP_SECONDS = 1.0
WIDTH, HEIGHT = 320, 180

# Below this, two renderings of the same frame are not the same picture. A
# correct trim scores ~40 dB against an accurate reference; a mid-GOP one
# scored 12 dB.
CLEAN_PSNR = 30.0


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: runs real ffmpeg against real media (needs ffmpeg installed)",
    )


@dataclass(frozen=True)
class Clip:
    """A synthetic recording, described well enough to reason about."""

    path: Path
    duration: float
    has_audio: bool
    width: int = WIDTH
    height: int = HEIGHT

    def keyframe_before(self, when: float) -> float:
        return (when // GOP_SECONDS) * GOP_SECONDS

    def is_mid_gop(self, when: float) -> bool:
        """True when a seek here lands between keyframes, which is the case
        that used to produce corrupt frames."""
        return abs(when - self.keyframe_before(when)) > 1e-6


@pytest.fixture(scope="session")
def tools():
    """The real ffmpeg, or skip everything that needs one."""
    from flightdvr.media import ToolsMissing, find_tools
    try:
        return find_tools()
    except ToolsMissing as exc:
        pytest.skip(f"integration tests need ffmpeg on PATH: {exc}")


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("media")


def _encode(tools, target: Path, duration: float, audio: bool,
            width: int = WIDTH, height: int = HEIGHT) -> None:
    keyint = int(GOP_SECONDS * FPS)
    command = [
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=size={width}x{height}:rate={FPS}:duration={duration}",
    ]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]

    command += [
        "-c:v", "libx265", "-preset", "ultrafast",
        # Fixed GOP with no scene-cut keyframes, so keyframe positions are
        # predictable and a test can deliberately seek between them.
        "-x265-params",
        f"keyint={keyint}:min-keyint={keyint}:scenecut=0:log-level=none",
        # Full range, as the goggles record and as the colour handling assumes.
        "-vf", "scale=in_range=limited:out_range=full",
        "-pix_fmt", "yuv420p", "-color_range", "pc",
    ]
    command += ["-c:a", "aac", "-b:a", "96k"] if audio else ["-an"]
    command += ["-f", "mpegts", str(target)]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not target.exists():
        tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        pytest.skip(f"could not build a test clip with this ffmpeg:\n{tail}")


@pytest.fixture(scope="session")
def clip(tools, media_dir) -> Clip:
    """Six seconds with sound: six keyframes, at 0.0 through 5.0."""
    path = media_dir / "hdz_001.ts"
    if not path.exists():
        _encode(tools, path, duration=6.0, audio=True)
    return Clip(path, duration=6.0, has_audio=True)


@pytest.fixture(scope="session")
def silent_clip(tools, media_dir) -> Clip:
    """The goggles write these when the microphone is off."""
    path = media_dir / "hdz_002.ts"
    if not path.exists():
        _encode(tools, path, duration=6.0, audio=False)
    return Clip(path, duration=6.0, has_audio=False)


@pytest.fixture(scope="session")
def second_clip(tools, media_dir) -> Clip:
    """A second recording with sound, for testing joins."""
    path = media_dir / "hdz_003.ts"
    if not path.exists():
        _encode(tools, path, duration=6.0, audio=True)
    return Clip(path, duration=6.0, has_audio=True)


@pytest.fixture(scope="session")
def odd_sized_clip(tools, media_dir) -> Clip:
    """A genuinely odd-sized source, which is harder to make than it looks.

    Not something a Box Pro produces, but the app opens any folder and lists
    .mkv alongside .ts.

    Two things round the dimensions back to even if you let them. `testsrc2`
    works in yuv420p and quietly emits 126x94, so this uses `testsrc`; and
    H.264 crops to macroblock boundaries, so this uses FFV1, which has no such
    constraint. Both were found by asserting the fixture rather than trusting
    it — the earlier versions of this tested nothing and passed.
    """
    path = media_dir / "odd_127x95.mkv"
    if not path.exists():
        result = subprocess.run([
            str(tools.ffmpeg), "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", f"testsrc=size=127x95:rate={FPS}:duration=3",
            "-c:v", "ffv1", "-pix_fmt", "yuv444p",
            "-an", "-f", "matroska", str(path),
        ], capture_output=True, text=True)
        if result.returncode != 0:
            pytest.skip("this ffmpeg cannot build an odd-sized clip")

    actual = probe_output(tools, path)
    if actual["width"] % 2 == 0 and actual["height"] % 2 == 0:
        pytest.skip(f"encoder rounded to {actual['width']}x{actual['height']}")
    return Clip(path, duration=3.0, has_audio=False,
                width=actual["width"], height=actual["height"])


# -- looking at what came out -------------------------------------------------

def probe_output(tools, path: Path) -> dict:
    """Codec, duration, dimensions and stream presence of a finished file."""
    result = subprocess.run(
        [str(tools.ffprobe), "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,nb_read_packets:format=duration",
         "-count_packets", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    import json
    try:
        raw = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    streams = raw.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {
        "has_video": bool(video),
        "has_audio": bool(audio),
        "video_codec": video.get("codec_name", ""),
        "audio_codec": audio.get("codec_name", ""),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "frames": int(video.get("nb_read_packets") or 0),
        "duration": float(raw.get("format", {}).get("duration") or 0),
    }


def frame_psnr(tools, produced: Path, reference: Path, work: Path,
               skip_reference_frames: int = 0) -> list[float]:
    """Per-frame PSNR of one file against another, optionally offset.

    An average hides the failure this exists to catch: the mid-GOP bug left the
    first thirty frames at 12 dB and the remaining ninety were fine, which
    averages out to something that looks merely mediocre.

    `skip_reference_frames` drops that many frames from the front of the
    reference, so a trimmed export can be lined up against the same footage
    inside a longer one.
    """
    log = work / "psnr.log"
    # shortest=1 matters more than it looks. Without it the filter repeats the
    # last frame of whichever input ends first and carries on scoring, so a
    # 60-frame export compared against a 240-frame reference reports 180 extra
    # "corrupt" frames that do not exist.
    compare = f"psnr=stats_file={log.name}:shortest=1"
    if skip_reference_frames:
        graph = (f"[1:v]trim=start_frame={skip_reference_frames},"
                 f"setpts=PTS-STARTPTS[ref];[0:v][ref]{compare}")
    else:
        graph = f"[0:v][1:v]{compare}"

    subprocess.run(
        [str(tools.ffmpeg), "-hide_banner", "-nostdin",
         "-i", str(produced), "-i", str(reference),
         "-lavfi", graph, "-f", "null", "-"],
        capture_output=True, text=True, cwd=work,
    )
    if not log.exists():
        return []
    values = []
    for line in log.read_text().splitlines():
        if "psnr_avg:" in line:
            raw = line.split("psnr_avg:")[1].split()[0]
            values.append(float("inf") if raw == "inf" else float(raw))
    return values
