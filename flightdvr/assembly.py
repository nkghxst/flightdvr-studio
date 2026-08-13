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

"""An ordered list of ranges, and what it resolves to on this card.

"Join the ticked clips" could already make one file, but the order was inferred
rather than chosen: DVR counter order, with a stored per-clip preference laid
over it. That cannot express two ranges of one recording in an order other than
the one they occur in, and it gives nobody a list to look at before committing
to an encode.

An assembly is stored as references — clip fingerprint plus range id — never as
positions or as the ranges themselves. A reference survives the card being
renamed, the range being retrimmed, and an earlier sibling being deleted. A
reference to something that has genuinely gone is *visible*, which matters more
than it sounds: silently joining a different range would produce a plausible
file that is not what anybody asked for.
"""

from __future__ import annotations

from dataclasses import dataclass

from .format import human_duration, natural_key
from .media import ClipInfo, Select


@dataclass(frozen=True)
class Item:
    """One entry in the list: which recording, and which range of it."""

    fingerprint: str
    sid: str

    def as_dict(self) -> dict:
        return {"clip": self.fingerprint, "range": self.sid}

    @classmethod
    def from_dict(cls, raw: dict) -> "Item":
        return cls(fingerprint=str(raw.get("clip", "")),
                   sid=str(raw.get("range", "")))


@dataclass
class Piece:
    """An item resolved against the clips actually in front of us."""

    item: Item
    clip: ClipInfo
    select: Select
    number: int             # which range of its own clip, one-based, for display

    @property
    def duration(self) -> float:
        return self.select.duration

    def label(self) -> str:
        """What the row says. The clip always; the range only when it needs it."""
        if self.select.name:
            return f"{self.clip.path.name}  ·  {self.select.name}"
        if self.number > 1:
            return f"{self.clip.path.name}  ·  range {self.number}"
        return self.clip.path.name


@dataclass
class Gone:
    """An item whose material is not here, kept so it can be shown."""

    item: Item
    name: str               # the clip's filename when the session remembers it

    def label(self) -> str:
        return f"{self.name or 'a recording that is no longer here'}  ·  missing"


def default_items(clips: list[ClipInfo]) -> list[Item]:
    """The order to start from: DVR counter, then along each recording.

    Counter order rather than timestamp order, because the goggles cannot keep
    time — the same reason the browser sorts that way. Within one recording the
    ranges stay in the order they occur, which is the only order that is not a
    guess.
    """
    ordered: list[Item] = []
    for clip in sorted(clips, key=lambda c: (c.sequence, natural_key(c.path.name))):
        for select in sorted(clip.real_selects, key=lambda s: s.start):
            ordered.append(Item(clip.fingerprint, select.sid))
    return ordered


def resolve(items: list[Item], clips: list[ClipInfo],
            names: dict[str, str] | None = None) -> tuple[list[Piece], list[Gone]]:
    """Turn stored references into the material they name, in stored order.

    Anything that cannot be found comes back as `Gone` rather than being
    dropped. An assembly quietly one item shorter than it was is the failure
    this design exists to prevent: the export would succeed and be wrong.
    """
    by_fingerprint = {c.fingerprint: c for c in clips}
    remembered = names or {}

    pieces: list[Piece] = []
    missing: list[Gone] = []
    for item in items:
        clip = by_fingerprint.get(item.fingerprint)
        if clip is None:
            missing.append(Gone(item, remembered.get(item.fingerprint, "")))
            continue
        ranges = clip.real_selects
        found = next((s for s in ranges if s.sid == item.sid), None)
        if found is None:
            missing.append(Gone(item, clip.path.name))
            continue
        pieces.append(Piece(item, clip, found, ranges.index(found) + 1))
    return pieces, missing


def total_duration(pieces: list[Piece]) -> float:
    return sum(p.duration for p in pieces)


def summary(pieces: list[Piece], missing: list[Gone]) -> str:
    """One line under the list, saying what is in it."""
    if not pieces and not missing:
        return "Nothing in the assembly yet"

    count = len(pieces)
    parts = [f"{count} item{'' if count == 1 else 's'}",
             human_duration(total_duration(pieces))]
    if missing:
        parts.append(f"{len(missing)} missing")
    return "  ·  ".join(parts)
