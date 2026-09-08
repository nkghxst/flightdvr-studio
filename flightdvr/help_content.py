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

"""Pure content and link policy for the Help and About dialogs.

The dialog should be able to open without asking GitHub anything. Naming
examples therefore come from the local naming functions, and release links are
selected from build identity supplied by the caller rather than inferred from
the version string in a source checkout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path
from types import SimpleNamespace

from .format import (
    DEFAULT_TEMPLATE, TEMPLATE_FIELDS, expand_template, export_fields,
)
from .presets import PRESETS, templated_output_path
from .updates import PROJECT_PAGE, RELEASES_PAGE

ALL_RELEASES_PAGE = f"{PROJECT_PAGE}/releases"
SOURCE_CHANGELOG_PAGE = f"{PROJECT_PAGE}/blob/main/CHANGELOG.md"

_PUBLISHED_VERSION = re.compile(r"\d+(?:\.\d+)*")


@dataclass(frozen=True)
class ReleaseLinks:
    """The truthful release destinations for one About dialog."""

    version: str
    current_url: str
    all_releases_url: str
    source_changelog_url: str
    is_published_build: bool


def release_links(version: str, published_tag: str | None = None) -> ReleaseLinks:
    """Choose About links without treating ``__version__`` as build proof.

    A packaged or source build may carry the same version string before a
    release is published. The version-specific target is therefore used only
    when a separately supplied, exact published tag agrees with it. The normal
    caller supplies no tag and receives truthful generic destinations.
    """
    current = (version or "").strip()
    normalised = current[1:] if current[:1].lower() == "v" else current
    tag = (published_tag or "").strip()
    tag_version = tag[1:] if tag[:1].lower() == "v" else tag
    verified = bool(
        _PUBLISHED_VERSION.fullmatch(normalised)
        and _PUBLISHED_VERSION.fullmatch(tag_version)
        and normalised == tag_version
    )
    return ReleaseLinks(
        version=normalised,
        current_url=(
            f"{PROJECT_PAGE}/releases/tag/v{normalised}"
            if verified else RELEASES_PAGE
        ),
        all_releases_url=ALL_RELEASES_PAGE,
        source_changelog_url=SOURCE_CHANGELOG_PAGE,
        is_published_build=verified,
    )


@dataclass(frozen=True)
class NamingExample:
    """One complete filename shown in the Help reference."""

    situation: str
    filename: str


def _piece(name: str, range_name: str = "") -> SimpleNamespace:
    """The small part of ClipInfo that the real naming path reads."""
    return SimpleNamespace(
        path=Path(name),
        selects=[SimpleNamespace(name=range_name)],
    )


def complete_filename(piece, index: int, total: int, preset_key: str,
                      template: str = DEFAULT_TEMPLATE,
                      flight_date: date | None = None,
                      session_name: str = "") -> str:
    """Render a complete output filename through the queue's path builder.

    Keeping the extension in ``templated_output_path`` is deliberate. A Help
    example that appends ``.mp4`` by hand can look right while the queue writes
    a duplicate preset suffix or a different extension for Edit.
    """
    preset = PRESETS[preset_key]
    stem = expand_template(
        template,
        export_fields(
            piece, index, total, preset.suffix,
            flight_date=flight_date,
            session_name=session_name,
        ),
    )
    return templated_output_path(
        Path("D:/Exports"), stem, preset_key, subfolders=False
    ).name


def naming_examples() -> tuple[NamingExample, ...]:
    """Examples generated from the source naming and output-path functions."""
    source = _piece("hdz_048.ts")
    ranges = [
        _piece("hdz_048.ts", "Launch"),
        _piece("hdz_048.ts", "Tree dive!"),
        _piece("hdz_048.ts"),
    ]
    total = len(ranges)
    return (
        NamingExample(
            "One unnamed range, Master",
            complete_filename(
                _piece("hdz_048.ts"), 0, 1, "master"
            ),
        ),
        NamingExample(
            "First of three ranges, named Launch, Upload",
            complete_filename(ranges[0], 0, total, "upload"),
        ),
        NamingExample(
            "Second of three ranges, named Tree dive!, Upload",
            complete_filename(ranges[1], 1, total, "upload"),
        ),
        NamingExample(
            "Third of three ranges, unnamed, Upload",
            complete_filename(ranges[2], 2, total, "upload"),
        ),
        NamingExample(
            "Remux",
            complete_filename(source, 0, 1, "remux"),
        ),
        NamingExample(
            "First range with the export date, Master",
            complete_filename(
                ranges[0], 0, total, "master",
                flight_date=date(2026, 7, 4),
            ),
        ),
        NamingExample(
            "A named session with a custom template",
            complete_filename(
                ranges[0], 0, total, "upload",
                template="{session}_{clip}_{range}_{preset}",
                session_name="Practice",
            ),
        ),
    )


NAMING_EXAMPLES = naming_examples()

_FIELD_DESCRIPTIONS = {
    "date": "selected flight date; not repeated when already in the clip name",
    "session": "session name, when one was given",
    "clip": "recording filename without its extension",
    "range_number": "one-based number when the clip has several ranges",
    "range": "range name when the clip has several ranges",
    "preset": "preset suffix without its leading underscore; blank for Remux",
}


def naming_help_html() -> str:
    """The compact naming reference placed below the shortcut groups."""
    fields = "<br>".join(
        f"<code>{{{escape(field)}}}</code> — "
        f"{escape(_FIELD_DESCRIPTIONS[field])}"
        for field in TEMPLATE_FIELDS
    )
    examples = "<br>".join(
        f"{escape(example.situation)}: "
        f"<code>{escape(example.filename)}</code>"
        for example in NAMING_EXAMPLES
    )
    return (
        "<b>Export naming</b><br>"
        "The <b>Name</b> box is a filename template, not a folder. "
        "Output chooses the folder; the optional preset subfolder stays outside "
        "the template.<br>"
        f"Default: <code>{escape(DEFAULT_TEMPLATE)}</code><br>"
        f"{fields}<br>"
        "An empty field takes its separator with it. Field values are cleaned "
        "for a filename, and the selected preset supplies the extension.<br>"
        f"{examples}<br>"
        "Unknown fields such as <code>{clipp}</code>, malformed braces, slashes, "
        "<code>..</code>, illegal literal characters, empty results and reserved "
        "Windows names are refused before queueing."
    )


NAMING_HELP_TITLE = "Export naming"
