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

from dataclasses import dataclass, replace

from .format import human_duration, natural_key
from .media import ClipInfo, Select


@dataclass(frozen=True)
class Item:
    """One entry in the list: which recording, and which range of it."""

    fingerprint: str
    # The range's stable id, or empty for "the whole recording". A clip nobody
    # has trimmed has no range to point at, and refusing to hold one would make
    # the assembly unable to express the commonest join there is: two untouched
    # recordings of one flight the DVR happened to split.
    sid: str = ""

    def as_dict(self) -> dict:
        return {"clip": self.fingerprint, "range": self.sid}

    @classmethod
    def from_dict(cls, raw: dict) -> "Item":
        return cls(fingerprint=str(raw.get("clip", "")),
                   sid=str(raw.get("range", "")))


@dataclass
class Row:
    """One entry, resolved or not, in the position the assembly stored it.

    Resolved and missing entries are deliberately the *same* type in one
    ordered list. They were two lists once, and the panel drew one after the
    other — which put an interleaved gap at the bottom, and then handed that
    display order back to be persisted. The stored order was quietly rewritten
    by the act of looking at it. A single sequence makes that unrepresentable.
    """

    item: Item
    clip: ClipInfo | None = None
    select: Select | None = None
    number: int = 1              # which range of its own clip, one-based
    remembered: str = ""         # the clip's filename, when only the session knows it

    @property
    def missing(self) -> bool:
        return self.clip is None or self.select is None

    @property
    def whole_clip(self) -> bool:
        """A reference to the recording itself rather than to a range of it."""
        return not self.item.sid

    @property
    def duration(self) -> float:
        return self.select.duration if self.select else 0.0

    def label(self) -> str:
        """What the row says. The clip always; the range only when it needs it."""
        if self.missing:
            name = self.remembered or "a recording that is no longer here"
            return f"{name}  ·  missing"
        if self.select.name:
            return f"{self.clip.path.name}  ·  {self.select.name}"
        if self.number > 1:
            return f"{self.clip.path.name}  ·  range {self.number}"
        return self.clip.path.name


def default_items(clips: list[ClipInfo]) -> list[Item]:
    """The order to start from: DVR counter, then along each recording.

    Counter order rather than timestamp order, because the goggles cannot keep
    time — the same reason the browser sorts that way. Within one recording the
    ranges stay in the order they occur, which is the only order that is not a
    guess. A clip nobody has trimmed contributes itself, whole.
    """
    ordered: list[Item] = []
    for clip in sorted(clips, key=lambda c: (c.sequence, natural_key(c.path.name))):
        ranges = sorted(clip.real_selects, key=lambda s: s.start)
        if not ranges:
            ordered.append(Item(clip.fingerprint))
            continue
        ordered.extend(Item(clip.fingerprint, s.sid) for s in ranges)
    return ordered


def resolve(items: list[Item], clips: list[ClipInfo],
            names: dict[str, str] | None = None) -> list[Row]:
    """Turn stored references into rows, in stored order, resolved or not.

    Nothing is dropped and nothing is moved. An assembly quietly one item
    shorter than it was, or one item out of order, would export cleanly and be
    wrong — which is the failure this whole design exists to prevent.
    """
    by_fingerprint = {c.fingerprint: c for c in clips}
    remembered = names or {}

    rows: list[Row] = []
    for item in items:
        clip = by_fingerprint.get(item.fingerprint)
        if clip is None:
            rows.append(Row(item, remembered=remembered.get(item.fingerprint, "")))
            continue
        if not item.sid:
            # The whole recording. Described rather than stored, so retrimming
            # the clip later changes what this row covers — which is right: it
            # means "this recording", and that is still what it is.
            rows.append(Row(item, clip, Select(0.0, clip.duration, "", sid=""), 1))
            continue
        ranges = clip.real_selects
        found = next((s for s in ranges if s.sid == item.sid), None)
        if found is None:
            rows.append(Row(item, remembered=clip.path.name))
            continue
        rows.append(Row(item, clip, found, ranges.index(found) + 1))
    return rows


def present(rows: list[Row]) -> list[Row]:
    return [r for r in rows if not r.missing]


def absent(rows: list[Row]) -> list[Row]:
    return [r for r in rows if r.missing]


def export_piece(row: Row) -> ClipInfo:
    """The clip a resolved row contributes to an export, carrying its trim.

    Built from what the *item* means rather than from `for_export()`, which
    expands a clip into its selects and therefore cannot represent "the whole
    recording" once that recording has gained a range. Copied, not shared, for
    the same reason `per_select_clips` copies: a queued job keeps its clip
    until it runs, and adjusting a select afterwards must not change an export
    already waiting.
    """
    if row.missing:
        raise ValueError("a missing row has no material to export")
    if row.whole_clip:
        return replace(row.clip, selects=[], current=0)
    one = row.select
    return replace(row.clip,
                   selects=[Select(one.start, one.end, one.name, sid=one.sid)],
                   current=0)


def total_duration(rows: list[Row]) -> float:
    return sum(r.duration for r in present(rows))


def summary(rows: list[Row]) -> str:
    """One line under the list, saying what is in it."""
    if not rows:
        return "Nothing in the assembly yet"

    count = len(present(rows))
    gaps = len(absent(rows))
    parts = [f"{count} item{'' if count == 1 else 's'}",
             human_duration(total_duration(rows))]
    if gaps:
        parts.append(f"{gaps} missing")
    return "  ·  ".join(parts)
