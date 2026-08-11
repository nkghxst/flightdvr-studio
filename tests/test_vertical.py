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

"""Tests for the source-space model behind the Vertical preset."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from flightdvr.media import ClipInfo
from flightdvr.presets import vertical_crop


def clip(width: int = 1280, height: int = 720) -> ClipInfo:
    return ClipInfo(
        path=Path("hdz_022.ts"),
        size=599_189_652,
        modified=datetime(2025, 10, 8, 18, 53),
        duration=10.0,
        width=width,
        height=height,
        fps=60.0,
        video_codec="hevc",
        audio_codec="aac",
        pix_fmt="yuvj420p",
        color_range="pc",
        color_space="bt470bg",
        color_primaries="bt470bg",
        color_transfer="smpte170m",
    )


def test_vertical_crop_uses_an_even_square_sar_source_rect():
    """The preview and ffmpeg must share a true 9:16 source-space crop.

    A full-height 1280x720 crop would be 405 pixels wide, which is odd and
    produces a non-square sample aspect ratio after scaling. The largest exact
    even crop is 396x704, centred eight pixels from the top and bottom.
    """
    model = vertical_crop(clip(), position=50)

    assert (model.x, model.y, model.width, model.height) == (442, 8, 396, 704)
    assert (model.output_width, model.output_height) == (720, 1280)
    assert model.width * 16 == model.height * 9
    assert all(value % 2 == 0 for value in (
        model.x, model.y, model.width, model.height,
        model.output_width, model.output_height,
    ))
