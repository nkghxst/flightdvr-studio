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

"""Turning values into the strings and paths the rest of the app uses.

Pure functions with no Qt in them, which is why they are here: several are
the kind of thing that looks obvious and is not, and each one that bit is
documented where it lives.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

def human_size(num_bytes: float) -> str:
    if num_bytes <= 0:
        return "-"
    mb = num_bytes / (1024 * 1024)
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def human_duration(seconds: float) -> str:
    """Runtime in units people actually use, not decimal minutes."""
    total = int(round(seconds))
    if total < 60:
        return f"{total} sec"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} hr {minutes:02d} min"
    return f"{minutes} min {secs:02d} sec"


def natural_key(text: str) -> str:
    """Sort key where digit runs compare numerically (hdz_9 before hdz_112)."""
    return re.sub(r"\d+", lambda m: m.group().zfill(12), text.lower())


def canonical_path(path) -> str:
    """One spelling of a path, so two ideas of identity cannot disagree.

    `G:\\Movies` and `g:\\movies` are the same folder on Windows and different
    strings everywhere. Sessions used `os.path.normcase` for the autosave
    filename and the raw spelling for the clip fingerprints, so opening a card
    through a differently-cased path found the right session and then reported
    every clip in it as missing.

    Resolved as well as case-folded: a session opened through a mapped drive,
    a symlink or a relative path is about the same footage as one opened
    through the real one.
    """
    text = Path(path)
    try:
        text = text.resolve()
    except OSError:
        text = text.absolute()
    return os.path.normcase(str(text))


def folder_label(source: str) -> str:
    """The last component of a path written on any platform.

    A session records the folder it was made from, and that string travels:
    written on Windows, it may well be read on Linux, where
    `Path(r"G:\\movies").name` is the whole string rather than "movies".
    """
    if not source:
        return ""
    text = str(source).replace("\\", "/").rstrip("/")
    tail = text.rsplit("/", 1)[-1]
    return tail if tail and not tail.endswith(":") else text
def safe_name(text: str) -> str:
    """A select's name, reduced to something a filesystem will accept.

    Names are typed by hand — "Tree dive!", "gap #2" — and go straight into a
    filename. Windows refuses several of those characters outright and silently
    strips a trailing dot or space.
    """
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text)
    cleaned = re.sub(r"\s+", "-", cleaned.strip())
    return cleaned.strip(". ")[:48]


TEMPLATE_FIELDS = ("date", "session", "clip", "range", "range_number", "preset")

# What the app has always produced, written as a template. Every field except
# `clip` can be empty, and an empty one takes its separator with it — which is
# what makes this one string cover a single untitled range (`hdz_048_upload`), a
# named one out of several (`hdz_048_2_Tree-dive!_upload`), and a Remux export
# whose preset suffix is deliberately blank (`hdz_048`).
DEFAULT_TEMPLATE = "{date}_{clip}_{range_number}_{range}_{preset}"

_FIELD = re.compile(r"\{([a-z_]+)\}")


class UnknownTemplateField(ValueError):
    """A template names something that cannot be filled in.

    Raised rather than expanded to an empty string, and raised while the
    template is being read rather than while a queue is being built: a typo in
    `{clipp}` should say so, not quietly export every file under one name.
    """

    def __init__(self, unknown):
        self.unknown = tuple(sorted(unknown))
        known = ", ".join(f"{{{name}}}" for name in TEMPLATE_FIELDS)
        listed = ", ".join(f"{{{name}}}" for name in self.unknown)
        super().__init__(f"{listed} is not a field. Available: {known}")


def template_fields(template: str) -> tuple[str, ...]:
    """The fields a template asks for, in the order they appear."""
    return tuple(match.group(1) for match in _FIELD.finditer(template))


def check_template(template: str) -> None:
    """Raise if a template names a field that does not exist."""
    unknown = set(template_fields(template)) - set(TEMPLATE_FIELDS)
    if unknown:
        raise UnknownTemplateField(unknown)


def expand_template(template: str, values: dict) -> str:
    """One export's filename stem, from a template and this clip's facts.

    Each value is sanitised on its own rather than the finished string, because
    `safe_name` strips separators the template itself supplies: cleaning the
    whole thing afterwards would let a name containing an underscore look like
    a field boundary.

    An empty value takes one adjacent separator with it. Without that, an
    untitled range in a dated export reads `2026-07-04_hdz_048_3__upload`, and
    a Remux export — whose preset suffix is empty on purpose — ends in a stray
    underscore.
    """
    check_template(template)

    def fill(match):
        return safe_name(str(values.get(match.group(1), "") or ""))

    rendered = _FIELD.sub(fill, template)
    # Collapse the gaps the empty fields left, then trim the ends. Done once at
    # the end rather than per field so a template with two empty fields in a
    # row behaves the same as one with a single empty field.
    rendered = re.sub(r"_{2,}", "_", rendered)
    rendered = re.sub(r"-{2,}", "-", rendered)
    return rendered.strip("_-. ")[:120]


def export_fields(piece, index: int, total: int, preset_suffix: str,
                  flight_date=None, session_name: str = "") -> dict:
    """What one export's template has to fill in.

    Kept here, next to the expansion, so the app and the test that proves the
    default reproduces today's filenames are reading the same rules. Written in
    the test instead, it would have proved only that the test agreed with
    itself.

    Two rules are not obvious and both come from `select_stem`:

    A lone range contributes neither its number nor its **name**. A clip
    trimmed the way every version before ranges trimmed it exports to the
    filename it always did, so typing a name on a single range does not rename
    its export.

    The date is dropped when the clip's own stem already starts with that same
    date, because files copied to the library are already dated and
    `2026-07-04_2026-07-04_hdz_048` helps nobody. A *different* date is never
    dropped, and a template that puts `{date}` somewhere other than the front
    still gets it — the guard is about repetition, not position.
    """
    stamp = flight_date.strftime("%Y-%m-%d") if flight_date else ""
    if stamp and piece.path.stem.startswith(stamp):
        stamp = ""
    several = total > 1
    return {
        "date": stamp,
        "session": session_name,
        "clip": piece.path.stem,
        "range": (piece.selects[0].name if several and piece.selects else ""),
        "range_number": str(index + 1) if several else "",
        "preset": preset_suffix.lstrip("_"),
    }


def select_stem(clip, index: int, total: int) -> str:
    """What to call the file for one select of a clip.

    A single select keeps the recording's own name, so a clip trimmed the way
    every version until now trimmed it exports to the filename it always did.
    Several of them need telling apart, and the name typed on the select is a
    better answer than a number — with the number kept as a prefix so they sort
    into the order they occur along the recording.
    """
    if total <= 1:
        return clip.stem
    named = ""
    if clip.selects:
        named = safe_name(clip.selects[0].name)
    return f"{clip.stem}_{index + 1}" + (f"_{named}" if named else "")


def output_key(path: Path) -> str:
    """One name per file, for spotting two jobs aimed at the same place.

    Absolute and case-folded, because Windows and macOS treat hdz_001.mp4 and
    HDZ_001.mp4 as one file. Comparing the paths as written meant two jobs from
    differently-cased folders queued happily and the second silently overwrote
    the first, without the overwrite prompt appearing.
    """
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    return os.path.normcase(str(resolved))


def existing_ancestor(path: Path) -> Path:
    """The nearest folder that exists, so free space can be measured.

    Looking only at the immediate parent meant a destination two levels below
    anything that existed skipped the capacity check altogether: disk_usage()
    failed, the failure came back as zero, and zero reads as "no warning".
    """
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def _clip_set_id(clips) -> str:
    """A short identifier for exactly this set of clips and their trims.

    Concat lists were named after the first clip alone, so two different joins
    beginning with the same recording shared one file and overwrote each other.
    """
    material = "|".join(
        f"{c.path}:{c.trim_in:.3f}:{c.trim_out:.3f}" for c in clips
    )
    return hashlib.sha1(material.encode("utf-8", "replace")).hexdigest()[:8]
def work_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "flightdvr"
    path.mkdir(parents=True, exist_ok=True)
    return path
