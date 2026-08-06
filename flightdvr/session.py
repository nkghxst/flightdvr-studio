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

"""What you decided about a card, written down.

Until now nothing about a clip survived closing the window. On a card of a
hundred and twenty recordings that meant the work of getting through it could
only ever happen in one sitting, which is not how anyone actually does it.

A session references footage rather than containing it, so it can be moved,
backed up and reopened, and so it stays small enough to write after every
change.

Clips are identified by `ClipInfo.fingerprint` — path, size and modification
time together, never the name alone. Cards get reused and rewritten with the
same filenames, and a session that confidently attached last week's trim points
to this week's footage would be worse than one that remembered nothing. A
rewritten card looks like new material, and it is.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

SUFFIX = ".flightdvr.json"

# Bumped whenever the stored shape changes in a way that a straight read would
# get wrong. `_migrate` is where old versions become current ones, and there is
# a test for every step it knows about, because this file will outlive several
# of its own formats.
SCHEMA = 1

UNREVIEWED, KEEP, MAYBE, REJECT = "", "keep", "maybe", "reject"
REVIEW_STATES = (UNREVIEWED, KEEP, MAYBE, REJECT)


@dataclass
class Select:
    """One range worth keeping, out of a recording that may hold several."""

    start: float
    end: float
    name: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def as_dict(self) -> dict:
        return {"start": round(self.start, 3),
                "end": round(self.end, 3),
                "name": self.name}

    @classmethod
    def from_dict(cls, raw: dict) -> "Select":
        return cls(start=float(raw.get("start", 0.0)),
                   end=float(raw.get("end", 0.0)),
                   name=str(raw.get("name", "")))


@dataclass
class ClipMarks:
    """Everything decided about one recording."""

    fingerprint: str
    name: str = ""                      # the filename, for when the file is gone
    selects: list[Select] = field(default_factory=list)
    review: str = UNREVIEWED
    note: str = ""
    exported: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        """False when nothing has been decided, so empty marks are not stored."""
        return bool(self.selects or self.review or self.note or self.exported)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "selects": [s.as_dict() for s in self.selects],
            "review": self.review,
            "note": self.note,
            "exported": list(self.exported),
        }

    @classmethod
    def from_dict(cls, fingerprint: str, raw: dict) -> "ClipMarks":
        review = str(raw.get("review", UNREVIEWED))
        return cls(
            fingerprint=fingerprint,
            name=str(raw.get("name", "")),
            selects=[Select.from_dict(s) for s in raw.get("selects", [])],
            # An unknown state from a newer version reads as unreviewed rather
            # than being carried around as something nothing can display.
            review=review if review in REVIEW_STATES else UNREVIEWED,
            note=str(raw.get("note", "")),
            exported=[str(p) for p in raw.get("exported", [])],
        )


@dataclass
class Session:
    """One card, or one folder, and what has been decided about it."""

    title: str = ""
    source: str = ""
    clips: dict[str, ClipMarks] = field(default_factory=dict)
    path: Path | None = None

    # -- what is in it --------------------------------------------------------

    def marks(self, fingerprint: str, name: str = "") -> ClipMarks:
        """The marks for a clip, created empty if this is the first time."""
        found = self.clips.get(fingerprint)
        if found is None:
            found = ClipMarks(fingerprint=fingerprint, name=name)
            self.clips[fingerprint] = found
        elif name and not found.name:
            found.name = name
        return found

    def reviewed_count(self) -> int:
        return sum(1 for m in self.clips.values() if m.review)

    def is_empty(self) -> bool:
        return not any(self.clips.values())

    # -- reading and writing --------------------------------------------------

    def as_dict(self) -> dict:
        # Only clips something was decided about. Walking a 122-clip card
        # should not write 122 empty records.
        return {
            "schema": SCHEMA,
            "title": self.title,
            "source": self.source,
            "clips": {f: m.as_dict() for f, m in self.clips.items() if m},
        }

    def save(self, path: Path | None = None) -> Path:
        """Write it, atomically.

        Through a temporary beside the target and then a rename, which is the
        same shape the exports and the filmstrip cache use. A session is
        written after every change, so a crash lands in the middle of one
        sooner or later, and a half-written file is worse than a stale one.
        """
        target = Path(path or self.path or "")
        if not str(target):
            raise ValueError("a session needs somewhere to be saved")
        target.parent.mkdir(parents=True, exist_ok=True)

        temporary = target.with_name(target.name + ".part")
        text = json.dumps(self.as_dict(), indent=2, ensure_ascii=False)
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
        self.path = target
        return target

    @classmethod
    def load(cls, path: Path) -> "Session":
        """Read one back. A file that cannot be read gives an empty session
        rather than an exception: losing the decisions is bad, but refusing to
        open the app because of them would be worse."""
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(path=Path(path))
        if not isinstance(raw, dict):
            return cls(path=Path(path))

        raw = _migrate(raw)
        return cls(
            title=str(raw.get("title", "")),
            source=str(raw.get("source", "")),
            clips={
                str(f): ClipMarks.from_dict(str(f), m)
                for f, m in (raw.get("clips") or {}).items()
                if isinstance(m, dict)
            },
            path=Path(path),
        )


def _migrate(raw: dict) -> dict:
    """Bring a stored session up to the current schema.

    Unknown *newer* versions are read as best they can be rather than refused:
    the fields this version understands are still the fields this version
    understands, and refusing to open would lose the lot.
    """
    version = raw.get("schema", 0)
    if version == 0:
        # Before schema numbers existed there was no released format, so there
        # is nothing to convert — this exists so the path is written and tested
        # before it is needed rather than after.
        raw = dict(raw, schema=1)
    return raw
