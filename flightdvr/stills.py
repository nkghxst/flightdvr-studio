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

"""Extract the exact paused preview frame as a full-resolution PNG."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .media import ClipInfo, Tools, frame_rate_mode
from .player import SEEK_LEAD_IN
from .presets import colour_filters


@dataclass(frozen=True)
class StillRequest:
    """One picture whose frame identity came from the precise decoder."""

    clip: ClipInfo
    frame_number: int
    seconds: float
    colour: str
    target: Path


def still_temp_path(target: Path) -> Path:
    """The beside-target path used until a complete PNG is ready."""
    return target.with_name(f"{target.stem}.flightdvr-part{target.suffix}")


def build_still_command(tools: Tools, request: StillRequest,
                        output: Path) -> list[str]:
    """Build a one-frame extraction tied to the precise decoder's PTS.

    The native-rate preview decoder already proved which source frame is on
    screen and recorded its showinfo timestamp. Starting from half a frame
    before that timestamp repeats the same selection rule instead of deriving
    a frame later from the wall-clock playhead. ``showinfo`` stays in this
    command so the worker can prove that ffmpeg returned the requested frame
    before it publishes anything.

    There is deliberately no scale or pad filter. The PNG must keep the
    source dimensions; Qt scales only the preview painted on screen.
    """
    if request.frame_number < 0 or request.seconds < 0:
        raise ValueError("A still needs a real source frame and timestamp")

    rate = request.clip.fps if request.clip.fps > 0 else 30.0
    boundary = max(0.0, request.seconds - 0.5 / rate)
    fast = max(0.0, boundary - SEEK_LEAD_IN)
    filters = [
        f"select='gte(t,{boundary:.9f})'",
        *colour_filters(request.colour, request.clip, "rgb24"),
        "showinfo",
    ]

    command = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "info"]
    if fast > 0.01:
        command += ["-ss", f"{fast:.6f}"]
    command += ["-copyts", "-start_at_zero", "-i", str(request.clip.path)]
    command += [
        "-map", "0:v:0", "-an",
        "-vf", ",".join(filters),
        *frame_rate_mode(tools, "passthrough"),
        "-frames:v", "1", "-c:v", "png",
        "-f", "image2", "-update", "1", "-y", str(output),
    ]
    return command


def timestamp_matches(request: StillRequest, decoded_seconds: float) -> bool:
    """Whether showinfo identifies the exact requested source picture.

    A quarter-frame allowance covers timestamp rounding while remaining far
    enough below one whole frame that an adjacent picture cannot pass.
    """
    rate = request.clip.fps if request.clip.fps > 0 else 30.0
    return abs(decoded_seconds - request.seconds) <= 0.25 / rate
