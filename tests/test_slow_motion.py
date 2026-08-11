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

"""Half speed out of the frames that were recorded.

The whole preset rests on two arguments agreeing: `setpts=2*PTS` spreads the
frames over twice as long, and the output rate says what rate that stream now
has. Get the second wrong and ffmpeg duplicates every frame to fill the runtime
— twice the frames, each shown twice, which plays smoothly and is not the
footage. So these tests are about the *relationship* between the two arguments
rather than the presence of either.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flightdvr import media  # noqa: E402
from flightdvr.media import ClipInfo, Tools  # noqa: E402
from flightdvr.presets import (  # noqa: E402
    PRESETS, SLOW_FACTOR, ExportSettings, build_commands, slow_output_rate,
    templated_output_path,
)

TOOLS = Tools(Path("ffmpeg"), Path("ffprobe"))


def boxpro_clip(**overrides) -> ClipInfo:
    defaults = dict(
        path=Path("hdz_022.ts"),
        size=599_189_652,
        modified=datetime(2025, 10, 8, 18, 53),
        duration=212.7,
        width=1280,
        height=720,
        fps=60.0,
        video_codec="hevc",
        audio_codec="aac",
        pix_fmt="yuvj420p",
        color_range="pc",
        color_space="bt470bg",
        color_primaries="bt470bg",
        color_transfer="smpte170m",
    )
    defaults.update(overrides)
    return ClipInfo(**defaults)


def slow_command(clip: ClipInfo, **settings) -> list[str]:
    return build_commands(
        TOOLS, clip, "slowmo", ExportSettings(**settings),
        Path("out/hdz_022_slow.mp4"), Path("work"),
    )[0]


def rate_asked_for(command: list[str]) -> float:
    """The -r value, read back rather than searched for as a string."""
    return float(command[command.index("-r") + 1])


# -- the relationship the preset is ------------------------------------------

@pytest.mark.parametrize("source_fps, expected", [
    (60.0, 30.0),
    (90.0, 45.0),
    (50.0, 25.0),
    (30.0, 15.0),
])
def test_the_output_rate_is_the_recorded_rate_halved(source_fps, expected):
    """Halved, and not rounded onto the rates the interface offers elsewhere.

    45 is deliberate: `FPS_STEPS` in the export panel is [90, 60, 50, 30, 25],
    so a 90 fps recording slowed to 45 has no entry there. Snapping it to 50
    would need frames the recording does not contain, and snapping it to 30
    would throw a third of them away.
    """
    command = slow_command(boxpro_clip(fps=source_fps))
    assert rate_asked_for(command) == expected
    assert f"setpts={SLOW_FACTOR}*PTS" in " ".join(command)


def test_the_two_arguments_agree_so_no_frame_is_added_or_dropped():
    """Stated as arithmetic rather than as two separate assertions.

    A recording's frames, spread over `SLOW_FACTOR` times the runtime, are
    exactly `fps / SLOW_FACTOR` frames per second. If the output rate ever
    stops being that, the export gains or loses frames — which is the one thing
    this preset promises not to do.
    """
    clip = boxpro_clip(fps=60.0, duration=10.0)
    command = slow_command(clip)

    source_frames = clip.fps * clip.duration
    output_seconds = clip.duration * SLOW_FACTOR
    assert rate_asked_for(command) * output_seconds == source_frames


def test_the_source_rate_is_never_used_as_the_output_rate():
    """The specific wrong command, named so a later change cannot reintroduce
    it quietly: cfr at the source rate duplicates every frame."""
    command = slow_command(boxpro_clip(fps=60.0))
    assert rate_asked_for(command) != 60.0


def test_a_clip_with_no_readable_rate_still_produces_a_usable_command():
    """fps is 0 when ffprobe could not tell us, and a nameless division is how
    that becomes a crash mid-queue rather than a sensible default."""
    assert slow_output_rate([boxpro_clip(fps=0.0)]) == 60.0 / SLOW_FACTOR


# -- sound, which is not slowed ----------------------------------------------

def test_the_source_audio_is_dropped_whatever_the_tickbox_says():
    """Motor noise at half pitch is not slow motion, and audio left at speed
    over doubled video drifts apart by the length of the clip. Refused in the
    command, so no combination of settings can ask for it."""
    command = slow_command(boxpro_clip(), keep_audio=True)
    assert "-an" in command
    assert "-c:a" not in command


# -- the ffmpeg 5.1 rename, on this path too ---------------------------------

@pytest.mark.parametrize("supported, flag", [(True, "-fps_mode"), (False, "-vsync")])
def test_both_frame_rate_options_are_asked_for_not_assumed(monkeypatch,
                                                           supported, flag):
    """-fps_mode replaced -vsync in ffmpeg 5.1 and Ubuntu 22.04 ships 4.4. The
    slow path forces a rate of its own, so it needs the same question asked."""
    monkeypatch.setattr(media, "_fps_mode_supported", lambda _, v=supported: v)
    command = slow_command(boxpro_clip())
    assert [flag, "cfr"] == command[command.index(flag):command.index(flag) + 2]
    assert ("-fps_mode" in command) != ("-vsync" in command), "both spellings sent"


# -- a join is brought to one rate first -------------------------------------

def test_a_join_slows_the_rate_the_graph_normalised_to():
    """Every clip in a join is already brought to the largest rate present, so
    the halving applies to that rather than to whichever clip sorted first.

    Whether a mixed-rate join can keep the one-frame-once promise at all is a
    different question, answered before anything is queued.
    """
    fast = boxpro_clip(path=Path("hdz_030.ts"), fps=90.0)
    slow = boxpro_clip(path=Path("hdz_031.ts"), fps=60.0)
    assert slow_output_rate([slow, fast]) == 45.0
    assert slow_output_rate([fast, slow]) == 45.0


# -- it is an ordinary preset everywhere else --------------------------------

def test_every_preset_has_a_command_builder():
    """The fallthrough guard in build_commands exists because a preset without
    a branch was silently built as Social. This is the test that keeps the
    guard honest as presets are added."""
    for key in PRESETS:
        command = build_commands(
            TOOLS, boxpro_clip(), key, ExportSettings(),
            Path(f"out/hdz_022{PRESETS[key].extension}"), Path("work"),
        )
        assert command and command[0][0] == "ffmpeg", key


def test_the_filename_says_slow_without_a_second_naming_rule():
    """Naming templates put the preset suffix in the name, so this preset needs
    no naming code of its own — `{preset}` resolves to it."""
    target = templated_output_path(Path("/out"), "hdz_022_slow", "slowmo",
                                   subfolders=False)
    assert target.name == "hdz_022_slow.mp4"
