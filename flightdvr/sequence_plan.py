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

"""Pure, immutable timing for one resolved sequence output.

The UI and queue already resolve ordinary clips and Assembly rows into
``WorkingOutput.pieces``.  This module records that answer at a value boundary
for later preview and export work.  It does not resolve clips, inspect files,
start workers, or talk to Qt.  In particular, a frozen dataclass containing a
``ClipInfo`` would still expose that mutable clip, so this module copies the
source path, identity and timing primitives instead.

The first contract is deliberately nominal 1x timing.  Source, assembled and
output clocks are separate fields even while their offsets are equal; later
speed or decoded-PTS work can then add a coordinate without pretending that
the current exporter or preview decoder has already supplied it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .assembly import Item
from .audio_plan import OUTPUT_RATE, SampleSpan, round_samples
from .output_plan import ResolvedPieceProvenance


class SequencePlanError(ValueError):
    """The supplied resolved sequence cannot form a safe timing contract."""


class UnresolvedSequenceError(SequencePlanError):
    """The caller supplied an incomplete resolution or an explicit gap."""


class StaleSequenceRevision(SequencePlanError):
    """An occurrence from an older caller-owned sequence revision was used."""


def _fraction(value: Any, name: str) -> Fraction:
    """Convert a finite timing value without importing binary float error."""
    if value is None:
        raise SequencePlanError(f"{name} is unknown")
    if isinstance(value, bool):
        raise SequencePlanError(f"{name} must be numeric")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SequencePlanError(f"{name} must be finite")
        # The media model stores several timings as floats.  Its displayed
        # decimal value is the contract we can preserve here, not the binary
        # representation hidden inside Fraction(float).
        value = str(value)
    try:
        result = Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise SequencePlanError(f"{name} must be a finite rational value") from exc
    return result


def _revision(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SequencePlanError("sequence revision must be a non-empty string")
    return value


@dataclass(frozen=True)
class Resolution:
    """Explicit provenance for the pieces handed to the compiler.

    A successful result must name no gaps.  An unsuccessful result must name
    at least one gap, so ``compile_sequence`` cannot silently treat omitted
    rows as a successful empty or shortened sequence.
    """

    successful: bool
    gaps: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.successful) is not bool:
            raise TypeError("resolution success must be a bool")
        if isinstance(self.gaps, (str, bytes)) or self.gaps is None:
            raise TypeError("resolution gaps must be an iterable of strings")
        try:
            gaps = tuple(self.gaps)
        except TypeError as exc:
            raise TypeError("resolution gaps must be an iterable of strings") from exc
        if any(not isinstance(gap, str) or not gap.strip() for gap in gaps):
            raise ValueError("resolution gaps must be non-empty strings")
        if self.successful and gaps:
            raise ValueError("a successful resolution cannot contain gaps")
        if not self.successful and not gaps:
            raise ValueError("an unsuccessful resolution must name its gaps")
        object.__setattr__(self, "gaps", gaps)

    @classmethod
    def success(cls) -> "Resolution":
        """Create the explicit no-gap result required by the compiler."""
        return cls(True, ())

    @classmethod
    def failed(cls, *gaps: str) -> "Resolution":
        """Create an explicit incomplete result for a refused sequence."""
        return cls(False, tuple(gaps))


@dataclass(frozen=True)
class TimeSpan:
    """A positive half-open interval on a rational seconds clock."""

    start: Fraction
    end: Fraction

    def __post_init__(self) -> None:
        start = _fraction(self.start, "span start")
        end = _fraction(self.end, "span end")
        if start < 0:
            raise SequencePlanError("span start cannot be negative")
        if end <= start:
            raise SequencePlanError("a span must be non-empty and half-open")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> Fraction:
        return self.end - self.start

    def contains(self, value: Fraction) -> bool:
        return self.start <= value < self.end


@dataclass(frozen=True, order=True)
class OccurrenceId:
    """The caller revision and ordinal that disambiguate repeated sources."""

    revision: str
    ordinal: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "revision", _revision(self.revision))
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise SequencePlanError("occurrence ordinal must be a non-negative integer")


@dataclass(frozen=True)
class SequenceOccurrence:
    """One ordered source occurrence and its three nominal clock intervals."""

    occurrence: OccurrenceId
    item: Item
    source_path: str
    source: TimeSpan
    assembled: TimeSpan
    output: TimeSpan
    sample_span: SampleSpan

    def __post_init__(self) -> None:
        if not isinstance(self.occurrence, OccurrenceId):
            raise TypeError("occurrence must be OccurrenceId")
        if not isinstance(self.item, Item):
            raise TypeError("occurrence item must be an Assembly Item")
        if not isinstance(self.item.fingerprint, str) or not self.item.fingerprint.strip():
            raise SequencePlanError("occurrence item needs a source fingerprint")
        if not isinstance(self.item.sid, str):
            raise SequencePlanError("occurrence item range id must be a string")
        item = Item(self.item.fingerprint, self.item.sid)
        if not isinstance(self.source_path, str) or not self.source_path.strip():
            raise SequencePlanError("occurrence source path cannot be empty")
        if self.source_path == ".":
            raise SequencePlanError("occurrence source path is unknown")
        for span in (self.source, self.assembled, self.output):
            if not isinstance(span, TimeSpan):
                raise TypeError("occurrence clocks must be TimeSpan values")
        if not (self.source.duration == self.assembled.duration
                == self.output.duration):
            raise SequencePlanError("1x occurrence clocks must have equal duration")
        if not isinstance(self.sample_span, SampleSpan):
            raise TypeError("occurrence sample span must be SampleSpan")
        if self.sample_span.rate != OUTPUT_RATE:
            raise SequencePlanError("sequence samples must use the 48 kHz clock")
        object.__setattr__(self, "item", item)

    @property
    def id(self) -> OccurrenceId:
        return self.occurrence

    @property
    def fingerprint(self) -> str:
        return self.item.fingerprint

    @property
    def sid(self) -> str:
        return self.item.sid


@dataclass(frozen=True)
class SequenceLocation:
    """A source point and its corresponding assembled/output coordinates."""

    occurrence: OccurrenceId
    source: Fraction
    assembled: Fraction
    output: Fraction

    def __post_init__(self) -> None:
        if not isinstance(self.occurrence, OccurrenceId):
            raise TypeError("location occurrence must be OccurrenceId")
        object.__setattr__(self, "source", _fraction(self.source, "source position"))
        object.__setattr__(self, "assembled",
                           _fraction(self.assembled, "assembled position"))
        object.__setattr__(self, "output", _fraction(self.output, "output position"))


@dataclass(frozen=True)
class SequenceTerminal:
    """The separate end result at exactly the total output duration."""

    revision: str
    assembled: Fraction
    output: Fraction
    sample: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "revision", _revision(self.revision))
        object.__setattr__(self, "assembled",
                           _fraction(self.assembled, "terminal assembled position"))
        object.__setattr__(self, "output",
                           _fraction(self.output, "terminal output position"))
        if type(self.sample) is not int or self.sample < 0:
            raise SequencePlanError("terminal sample must be a non-negative integer")

    @property
    def is_terminal(self) -> bool:
        return True


@dataclass(frozen=True)
class SequencePlan:
    """An immutable, occurrence-aware nominal 1x sequence snapshot."""

    revision: str
    occurrences: tuple[SequenceOccurrence, ...]
    sample_boundaries: tuple[int, ...]

    def __post_init__(self) -> None:
        revision = _revision(self.revision)
        occurrences = tuple(self.occurrences)
        boundaries = tuple(self.sample_boundaries)
        if not occurrences:
            raise SequencePlanError("a sequence needs at least one occurrence")
        if len(boundaries) != len(occurrences) + 1:
            raise SequencePlanError("sample boundaries must bracket every occurrence")
        if any(type(value) is not int or value < 0 for value in boundaries):
            raise SequencePlanError("sample boundaries must be non-negative integers")
        if boundaries[0] != 0 or any(left >= right
                                     for left, right in zip(boundaries, boundaries[1:])):
            raise SequencePlanError("sample boundaries must be strictly increasing from zero")
        previous_output = Fraction(0)
        for index, occurrence in enumerate(occurrences):
            if not isinstance(occurrence, SequenceOccurrence):
                raise TypeError("sequence occurrences must be SequenceOccurrence values")
            if occurrence.occurrence != OccurrenceId(revision, index):
                raise SequencePlanError("occurrence ids must match sequence order and revision")
            if occurrence.assembled.start != previous_output:
                raise SequencePlanError("assembled clock has a gap or overlap")
            if occurrence.output.start != previous_output:
                raise SequencePlanError("output clock has a gap or overlap")
            if (occurrence.sample_span.start != boundaries[index]
                    or occurrence.sample_span.end != boundaries[index + 1]):
                raise SequencePlanError("occurrence sample span disagrees with boundaries")
            previous_output = occurrence.output.end
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "occurrences", occurrences)
        object.__setattr__(self, "sample_boundaries", boundaries)

    @property
    def total_duration(self) -> Fraction:
        return self.occurrences[-1].output.end

    @property
    def total_samples(self) -> int:
        return self.sample_boundaries[-1]

    @property
    def terminal(self) -> SequenceTerminal:
        return SequenceTerminal(self.revision, self.total_duration,
                                self.total_duration, self.total_samples)

    def get_occurrence(self, occurrence: OccurrenceId) -> SequenceOccurrence:
        """Resolve an occurrence id, refusing ids from an older revision."""
        if not isinstance(occurrence, OccurrenceId):
            raise TypeError("source mapping requires an OccurrenceId")
        if occurrence.revision != self.revision:
            raise StaleSequenceRevision(
                f"stale occurrence revision {occurrence.revision!r}, "
                f"not {self.revision!r}"
            )
        if occurrence.ordinal >= len(self.occurrences):
            raise SequencePlanError("occurrence ordinal is not in this sequence")
        return self.occurrences[occurrence.ordinal]

    def locate_output(self, output_time: Any) -> SequenceLocation | SequenceTerminal:
        """Map an output time, choosing the next occurrence at each seam."""
        position = _fraction(output_time, "output position")
        if position < 0 or position > self.total_duration:
            raise SequencePlanError("output position is outside the sequence")
        if position == self.total_duration:
            return self.terminal
        for occurrence in self.occurrences:
            if not occurrence.output.contains(position):
                continue
            delta = position - occurrence.output.start
            return SequenceLocation(
                occurrence.id,
                occurrence.source.start + delta,
                occurrence.assembled.start + delta,
                position,
            )
        raise SequencePlanError("output position did not resolve to an occurrence")

    def output_to_source(self, output_time: Any) -> SequenceLocation | SequenceTerminal:
        """Named alias for the forward output-to-source mapping."""
        return self.locate_output(output_time)

    def locate_source(self, occurrence: OccurrenceId,
                      source_time: Any) -> SequenceLocation:
        """Map a source point only with its explicit occurrence identity."""
        found = self.get_occurrence(occurrence)
        position = _fraction(source_time, "source position")
        if not found.source.contains(position):
            raise SequencePlanError("source position is outside the occurrence")
        delta = position - found.source.start
        return SequenceLocation(
            found.id,
            position,
            found.assembled.start + delta,
            found.output.start + delta,
        )

    def source_to_output(self, occurrence: OccurrenceId,
                         source_time: Any) -> Fraction:
        """Return the output coordinate for one explicitly identified source."""
        return self.locate_source(occurrence, source_time).output


_MISSING = object()
_TRIM_MATERIAL_THRESHOLD = Fraction(1, 100)


def _items_and_pieces(
    working_output: Any,
) -> tuple[tuple[Item, ...], tuple[Any, ...], tuple[ResolvedPieceProvenance, ...]]:
    try:
        target = working_output.target
        raw_items = target.items
        raw_pieces = working_output.pieces
    except AttributeError as exc:
        raise SequencePlanError(
            "sequence input must be a resolved WorkingOutput"
        ) from exc
    if isinstance(raw_items, (str, bytes)) or isinstance(raw_pieces, (str, bytes)):
        raise SequencePlanError("WorkingOutput items and pieces must be sequences")
    try:
        items = tuple(raw_items)
        pieces = tuple(raw_pieces)
    except TypeError as exc:
        raise SequencePlanError("WorkingOutput items and pieces must be iterable") from exc
    if not items or not pieces:
        raise SequencePlanError("a resolved WorkingOutput cannot be empty")
    if len(items) != len(pieces):
        raise SequencePlanError(
            "WorkingOutput target references and resolved pieces must correspond one-to-one"
        )
    raw_provenance = getattr(working_output, "piece_provenance", _MISSING)
    if raw_provenance is _MISSING or raw_provenance is None:
        raise SequencePlanError(
            "resolved WorkingOutput needs explicit piece provenance"
        )
    if isinstance(raw_provenance, (str, bytes)):
        raise SequencePlanError("WorkingOutput piece provenance must be a sequence")
    try:
        provenance = tuple(raw_provenance)
    except TypeError as exc:
        raise SequencePlanError(
            "WorkingOutput piece provenance must be iterable"
        ) from exc
    if not provenance:
        raise SequencePlanError(
            "resolved WorkingOutput needs explicit piece provenance"
        )
    if len(provenance) != len(pieces):
        raise SequencePlanError(
            "WorkingOutput piece provenance must correspond one-to-one with pieces"
        )
    for index, entry in enumerate(provenance):
        if not isinstance(entry, ResolvedPieceProvenance):
            raise SequencePlanError(
                f"piece {index} lacks captured primitive provenance"
            )
        if entry.ordinal != index:
            raise SequencePlanError(
                f"piece {index} provenance ordinal does not match its position"
            )
        if entry.piece_identity != id(pieces[index]):
            raise SequencePlanError(
                f"piece {index} provenance does not correspond to the supplied piece"
            )
    return items, pieces, provenance


def _snapshot_item(raw_item: Any, index: int) -> Item:
    if not isinstance(raw_item, Item):
        raise SequencePlanError(f"item {index} is not an Assembly Item")
    if not isinstance(raw_item.fingerprint, str) or not raw_item.fingerprint.strip():
        raise SequencePlanError(f"item {index} has no source fingerprint")
    if not isinstance(raw_item.sid, str):
        raise SequencePlanError(f"item {index} has a non-string range id")
    return Item(raw_item.fingerprint, raw_item.sid)


def _raw_selects(clip: Any, index: int) -> tuple[Any, ...]:
    raw = getattr(clip, "selects", _MISSING)
    if raw is _MISSING:
        raw = getattr(clip, "real_selects", _MISSING)
    if raw is _MISSING or raw is None:
        raise SequencePlanError(f"piece {index} has unknown range resolution")
    if isinstance(raw, (str, bytes)):
        raise SequencePlanError(f"piece {index} ranges are not a sequence")
    try:
        return tuple(raw)
    except TypeError as exc:
        raise SequencePlanError(f"piece {index} ranges are not iterable") from exc


def _validated_ranges(
    clip: Any,
    index: int,
    duration: Fraction,
) -> list[tuple[str, Fraction, Fraction]]:
    records: list[tuple[str, Fraction, Fraction]] = []
    for select in _raw_selects(clip, index):
        start = _fraction(getattr(select, "start", None),
                          f"piece {index} range start")
        end = _fraction(getattr(select, "end", None),
                        f"piece {index} range end")
        if start < 0 or end < 0:
            raise SequencePlanError(f"piece {index} contains an invalid range")
        # ClipInfo.real_selects treats values at or below 0.01 seconds as an
        # empty editing row.  Keep validating those raw values so negative or
        # nonfinite input cannot disappear, but do not reinterpret a valid
        # nonmaterial row as range material for a whole-recording Item.
        if (start <= _TRIM_MATERIAL_THRESHOLD
                and end <= _TRIM_MATERIAL_THRESHOLD):
            continue
        sid = getattr(select, "sid", None)
        if not isinstance(sid, str) or not sid.strip():
            raise SequencePlanError(f"piece {index} range has no stable id")
        # ClipInfo.out_point uses the known duration when trim_out is at or
        # below the same threshold.  That is an effective open endpoint, not
        # whole-clip substitution: the positive trim_in remains the source
        # origin and the material ends at the known duration.
        effective_end = (duration if end <= _TRIM_MATERIAL_THRESHOLD else end)
        if effective_end <= start:
            raise SequencePlanError(f"piece {index} contains an invalid range")
        records.append((sid, start, effective_end))
    return records


def _source_span(clip: Any, item: Item, index: int) -> TimeSpan:
    duration = _fraction(getattr(clip, "duration", None),
                         f"piece {index} duration")
    if duration <= 0:
        raise SequencePlanError(f"piece {index} duration must be positive")
    ranges = _validated_ranges(clip, index, duration)
    if item.sid:
        if len(ranges) != 1 or ranges[0][0] != item.sid:
            raise SequencePlanError(
                f"piece {index} range does not correspond to target Item {item.sid!r}"
            )
        _, start, raw_end = ranges[0]
        # This is the same effective bound as ClipInfo.out_point.  It clamps
        # an explicit endpoint to known duration; it never turns an unknown or
        # empty range into a whole-clip span.
        end = min(raw_end, duration)
        return TimeSpan(start, end)
    if ranges:
        raise SequencePlanError(
            f"piece {index} has range material but target requests the whole clip"
        )
    return TimeSpan(Fraction(0), duration)


def _source_path(clip: Any, index: int) -> str:
    path = getattr(clip, "path", None)
    if path is None:
        raise SequencePlanError(f"piece {index} source path is unknown")
    value = str(path)
    if not value.strip() or value == ".":
        raise SequencePlanError(f"piece {index} source path is unknown")
    return value


def compile_sequence(
    working_output: Any,
    *,
    resolution: Resolution,
    revision: str,
) -> SequencePlan:
    """Compile one resolved ``WorkingOutput`` into an immutable 1x plan.

    ``resolution`` and ``revision`` are required rather than defaulted.  The
    caller therefore has to say that no Assembly gaps remain and has to own a
    revision token that later mappings can check.  The compiler verifies the
    ordered target-item/piece correspondence before it snapshots any timing.
    """
    if not isinstance(resolution, Resolution):
        raise TypeError("compile_sequence requires an explicit Resolution")
    if not resolution.successful:
        detail = ", ".join(resolution.gaps)
        raise UnresolvedSequenceError(
            f"cannot compile an unresolved sequence: {detail}"
        )
    sequence_revision = _revision(revision)
    items, pieces, provenance = _items_and_pieces(working_output)

    occurrences: list[SequenceOccurrence] = []
    boundaries = [0]
    cursor = Fraction(0)
    for index, (raw_item, piece, captured) in enumerate(
            zip(items, pieces, provenance)):
        item = _snapshot_item(raw_item, index)
        clip = getattr(piece, "clip", piece)
        if clip is None:
            raise SequencePlanError(f"piece {index} is unresolved")
        # The producer captured the dynamic ClipInfo identity before this pure
        # boundary.  Comparing that primitive here retains the ordered
        # Item/piece check without invoking ClipInfo.fingerprint (and its
        # filesystem-dependent Path.resolve) again.
        if (captured.fingerprint != item.fingerprint
                or captured.sid != item.sid):
            raise SequencePlanError(
                f"piece {index} provenance fingerprint/range does not correspond "
                "to its target Item"
            )
        source = _source_span(clip, item, index)
        assembled = TimeSpan(cursor, cursor + source.duration)
        output = TimeSpan(cursor, cursor + source.duration)
        cursor = output.end
        boundary = round_samples(cursor * OUTPUT_RATE)
        if boundary <= boundaries[-1]:
            raise SequencePlanError(
                f"piece {index} projects to an empty 48 kHz sample span"
            )
        boundaries.append(boundary)
        sample_span = SampleSpan(boundaries[-2], boundaries[-1], OUTPUT_RATE)
        occurrences.append(SequenceOccurrence(
            OccurrenceId(sequence_revision, index),
            item,
            _source_path(clip, index),
            source,
            assembled,
            output,
            sample_span,
        ))

    return SequencePlan(sequence_revision, tuple(occurrences), tuple(boundaries))
