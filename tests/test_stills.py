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

"""The source-frame identity and transaction paths behind Grab still."""

from datetime import datetime
from pathlib import Path

from flightdvr.media import ClipInfo, Tools
from flightdvr.presets import LEVELS, REC709
from flightdvr.stills import (
    StillRequest, build_still_command, still_temp_path, timestamp_matches,
)


TOOLS = Tools(Path("ffmpeg"), Path("ffprobe"))


def clip(**overrides) -> ClipInfo:
    fields = dict(
        path=Path("hdz_047.ts"), size=600_000_000,
        modified=datetime(2026, 7, 4), duration=120.0,
        width=1920, height=1080, fps=60.0, video_codec="hevc",
        audio_codec="aac", color_range="pc",
    )
    fields.update(overrides)
    return ClipInfo(**fields)


def request(**overrides) -> StillRequest:
    fields = dict(
        clip=clip(), frame_number=630, seconds=10.499989,
        colour=LEVELS, target=Path("hdz_047.png"),
    )
    fields.update(overrides)
    return StillRequest(**fields)


def test_the_still_repeats_the_precise_decoders_frame_boundary():
    command = build_still_command(TOOLS, request(), Path("part.png"))
    filters = command[command.index("-vf") + 1]

    assert "select='gte(t,10.491655667)'" in filters
    assert "showinfo" in filters
    assert "-ss" in command
    assert command[command.index("-ss") + 1] == "8.491656"


def test_the_png_keeps_source_dimensions_instead_of_preview_dimensions():
    command = build_still_command(TOOLS, request(), Path("part.png"))
    filters = command[command.index("-vf") + 1]

    # The levels colour choice uses scale's range-conversion mode, but no
    # filter is allowed to specify a width or height.
    assert "force_original_aspect_ratio" not in filters
    assert "scale=480:" not in filters
    assert "scale=640:" not in filters
    assert "scale=960:" not in filters
    assert "pad=" not in filters
    assert command[command.index("-frames:v") + 1] == "1"
    assert command[command.index("-c:v") + 1] == "png"


def test_the_selected_colour_mode_is_applied_before_rgb_png_encoding():
    command = build_still_command(
        TOOLS, request(colour=REC709), Path("part.png"))
    filters = command[command.index("-vf") + 1]

    assert "matrix=709" in filters
    assert filters.index("matrix=709") < filters.index("format=rgb24")
    assert filters.index("format=rgb24") < filters.index("showinfo")


def test_an_adjacent_source_frame_cannot_pass_timestamp_validation():
    wanted = request()

    assert timestamp_matches(wanted, wanted.seconds + 0.001)
    assert not timestamp_matches(wanted, wanted.seconds + 1 / wanted.clip.fps)


def test_the_temporary_png_is_beside_the_target():
    target = Path("D:/exports/hdz_047.png")

    assert still_temp_path(target) == Path(
        "D:/exports/hdz_047.flightdvr-part.png")
