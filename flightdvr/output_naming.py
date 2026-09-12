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

"""One source of truth for names shown before an export runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .format import check_stem, expand_template, export_fields
from .presets import PRESETS, templated_output_path


@dataclass(frozen=True)
class NamingInputs:
    """The source facts a later panel edit must be able to render again.

    These are deliberately not a rendered stem. A rendered stem has already
    lost which part was the date, range name or preset, so feeding it through a
    second naming pass duplicates those components instead of replacing them.
    """

    clip: str
    range_name: str = ""
    range_number: str = ""
    session: str = ""


@dataclass(frozen=True)
class ResolvedOutput:
    stem: str
    target: Path


def naming_inputs(piece, index: int, total: int, session_name: str = "",
                  joined: bool = False) -> NamingInputs:
    """Snapshot the original fields used to name one queued output."""
    fields = export_fields(
        piece, index, total, "", flight_date=None,
        session_name=session_name,
    )
    clip = fields["clip"] + ("_joined" if joined else "")
    return NamingInputs(
        clip=clip,
        range_name=fields["range"],
        range_number=fields["range_number"],
        session=fields["session"],
    )


def resolve_output(naming: NamingInputs, preset_key: str, out_dir: Path,
                   template: str, subfolders: bool,
                   flight_date: date | None) -> ResolvedOutput:
    """Purely render one complete output target from source facts and policy."""
    stamp = flight_date.strftime("%Y-%m-%d") if flight_date else ""
    if stamp and naming.clip.startswith(stamp):
        stamp = ""
    fields = {
        "date": stamp,
        "session": naming.session,
        "clip": naming.clip,
        "range": naming.range_name,
        "range_number": naming.range_number,
        "preset": PRESETS[preset_key].suffix.lstrip("_"),
    }
    stem = expand_template(template, fields)
    check_stem(stem)
    return ResolvedOutput(
        stem,
        templated_output_path(out_dir, stem, preset_key, subfolders),
    )
