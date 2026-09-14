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

from datetime import datetime
from fractions import Fraction
from pathlib import Path

import pytest

from flightdvr.assembly import Item
from flightdvr.media import ClipInfo, Select
from flightdvr.output_plan import OutputTarget, WorkingOutput
from flightdvr.sequence_plan import (
    OccurrenceId,
    Resolution,
    SequenceLocation,
    SequencePlanError,
    SequenceTerminal,
    StaleSequenceRevision,
    TimeSpan,
    UnresolvedSequenceError,
    compile_sequence,
)


_MODIFIED = datetime(2026, 9, 14, 12, 0)


def clip(name: str, duration, span=None, sid: str = "") -> ClipInfo:
    found = ClipInfo(
        path=Path(name), size=1024, modified=_MODIFIED, duration=duration,
        width=1280, height=720, fps=60.0, video_codec="hevc",
    )
    if span is not None:
        start, end = span
        found.selects = [Select(start, end, sid=sid)]
    return found


def item_for(found: ClipInfo, sid: str = "") -> Item:
    return Item(found.fingerprint, sid)


def output(items, pieces) -> WorkingOutput:
    return WorkingOutput(
        OutputTarget.assembly(items), tuple(pieces), joined=True,
    )


def compile_output(working, revision: str = "r1"):
    return compile_sequence(
        working, resolution=Resolution.success(), revision=revision,
    )


def test_repeated_occurrences_keep_order_seams_and_explicit_inverse_identity():
    """A repeated source range is three occurrences, not one deduplicated item."""
    a = clip("A.ts", 20, (10, 13), sid="a-range")
    b = clip("B.ts", 20, (2, 4), sid="b-range")
    items = [item_for(a, "a-range"), item_for(b, "b-range"),
             item_for(a, "a-range")]
    plan = compile_output(output(items, [a, b, a]))

    assert [found.item for found in plan.occurrences] == items
    assert [found.occurrence.ordinal for found in plan.occurrences] == [0, 1, 2]
    assert [(found.source.start, found.source.end) for found in plan.occurrences] == [
        (Fraction(10), Fraction(13)),
        (Fraction(2), Fraction(4)),
        (Fraction(10), Fraction(13)),
    ]
    assert [(found.assembled.start, found.assembled.end)
            for found in plan.occurrences] == [
        (Fraction(0), Fraction(3)),
        (Fraction(3), Fraction(5)),
        (Fraction(5), Fraction(8)),
    ]
    assert plan.sample_boundaries == (0, 144_000, 240_000, 384_000)

    at_b = plan.locate_output(3)
    assert isinstance(at_b, SequenceLocation)
    assert at_b.occurrence == plan.occurrences[1].id
    assert (at_b.source, at_b.assembled, at_b.output) == (
        Fraction(2), Fraction(3), Fraction(3))

    at_second_a = plan.output_to_source(5)
    assert isinstance(at_second_a, SequenceLocation)
    assert at_second_a.occurrence == plan.occurrences[2].id
    assert at_second_a.source == 10

    terminal = plan.locate_output(8)
    assert isinstance(terminal, SequenceTerminal)
    assert terminal.is_terminal
    assert (terminal.assembled, terminal.output, terminal.sample) == (
        Fraction(8), Fraction(8), 384_000)

    # The same source position is only meaningful with the occurrence that
    # selected it; both inverse answers are intentionally different.
    assert plan.source_to_output(plan.occurrences[0].id, 10) == 0
    assert plan.source_to_output(plan.occurrences[2].id, 10) == 5
    with pytest.raises(SequencePlanError, match="outside the occurrence"):
        plan.source_to_output(plan.occurrences[0].id, 13)


def test_nonzero_source_origin_maps_without_substituting_file_zero():
    found = clip("offset.ts", 30, (12, 18), sid="offset-range")
    plan = compile_output(output([item_for(found, "offset-range")], [found]))

    at_start = plan.locate_output(0)
    at_five = plan.locate_output(5)
    assert isinstance(at_start, SequenceLocation)
    assert isinstance(at_five, SequenceLocation)
    assert (at_start.source, at_start.output) == (Fraction(12), Fraction(0))
    assert (at_five.source, at_five.output) == (Fraction(17), Fraction(5))
    assert plan.source_to_output(plan.occurrences[0].id, 12) == 0
    assert plan.source_to_output(plan.occurrences[0].id, 17) == 5
    assert isinstance(plan.locate_output(6), SequenceTerminal)
    with pytest.raises(SequencePlanError, match="outside the occurrence"):
        plan.source_to_output(plan.occurrences[0].id, 18)


def test_cumulative_half_up_rounding_uses_boundaries_not_rounded_durations():
    """Three 1000.5-sample spans must end at 0/1001/2001/3002."""
    duration = Fraction(2001, 96_000)
    pieces = [clip(f"sample-{index}.ts", duration) for index in range(3)]
    plan = compile_output(output([item_for(found) for found in pieces], pieces))

    assert plan.sample_boundaries == (0, 1001, 2001, 3002)
    assert [found.sample_span.samples for found in plan.occurrences] == [
        1001, 1000, 1001,
    ]
    assert plan.total_samples == 3002


def test_whole_recording_ranges_and_repeated_same_source_items_stay_distinct():
    """Whole, range-one and range-two references retain their own spans."""
    whole = clip("same.ts", 20)
    first = clip("same.ts", 20, (1, 3), sid="first")
    second = clip("same.ts", 20, (4, 6), sid="second")
    items = [item_for(whole), item_for(first, "first"),
             item_for(second, "second"), item_for(first, "first")]
    plan = compile_output(output(items, [whole, first, second, first]))

    assert len(plan.occurrences) == 4
    assert [found.sid for found in plan.occurrences] == [
        "", "first", "second", "first",
    ]
    assert [(found.source.start, found.source.end)
            for found in plan.occurrences] == [
        (Fraction(0), Fraction(20)),
        (Fraction(1), Fraction(3)),
        (Fraction(4), Fraction(6)),
        (Fraction(1), Fraction(3)),
    ]


def test_resolution_success_and_gaps_are_explicit_and_gaps_refuse_compilation():
    found = clip("resolved.ts", 5)
    working = output([item_for(found)], [found])

    with pytest.raises(TypeError, match="resolution"):
        compile_sequence(working, revision="r1")
    with pytest.raises(UnresolvedSequenceError, match="missing row"):
        compile_sequence(working, resolution=Resolution.failed("missing row"),
                         revision="r1")
    with pytest.raises(ValueError, match="must name its gaps"):
        Resolution(False, ())
    with pytest.raises(ValueError, match="cannot contain gaps"):
        Resolution(True, ("missing row",))


def test_reference_and_piece_order_must_match_without_dropping_unresolved_input():
    first = clip("first.ts", 5)
    second = clip("second.ts", 5)
    good_items = [item_for(first), item_for(second)]

    with pytest.raises(SequencePlanError, match="one-to-one"):
        compile_output(output(good_items, [first]))
    with pytest.raises(SequencePlanError, match="fingerprint"):
        compile_output(output([Item("not-the-piece", "")], [first]))
    with pytest.raises(SequencePlanError, match="unresolved"):
        compile_output(output([item_for(first)], [None]))


@pytest.mark.parametrize(
    "found, item, message",
    [
        (clip("missing-duration.ts", None), None, "unknown"),
        (clip("zero-duration.ts", 0), None, "positive"),
        (clip("negative-duration.ts", -1), None, "positive"),
        (clip("negative-range.ts", 20, (-1, 4), sid="negative"),
         "negative", "invalid"),
        (clip("empty-range.ts", 20, (5, 5), sid="empty"),
         "empty", "invalid"),
        (clip("nonfinite-range.ts", 20, (float("nan"), 5), sid="nan"),
         "nan", "finite"),
    ],
)
def test_unknown_negative_or_empty_source_material_refuses_instead_of_using_whole_clip(
        found, item, message):
    reference = item_for(found, item) if item is not None else item_for(found)
    with pytest.raises(SequencePlanError, match=message):
        compile_output(output([reference], [found]))


def test_range_identity_mismatch_never_falls_back_to_the_whole_clip():
    found = clip("range.ts", 20, (4, 8), sid="actual")
    asks_for_other = Item(found.fingerprint, "other")
    asks_for_whole = Item(found.fingerprint, "")

    with pytest.raises(SequencePlanError, match="does not correspond"):
        compile_output(output([asks_for_other], [found]))
    with pytest.raises(SequencePlanError, match="range material"):
        compile_output(output([asks_for_whole], [found]))


def test_source_endpoint_is_clamped_to_known_duration_but_unknown_or_empty_stays_refused():
    found = clip("clamped.ts", 5, (4, 10), sid="clamped")
    plan = compile_output(output([item_for(found, "clamped")], [found]))
    assert plan.occurrences[0].source == TimeSpan(Fraction(4), Fraction(5))

    tiny = clip("subsample.ts", Fraction(1, 96_000_000))
    with pytest.raises(SequencePlanError, match="empty 48 kHz"):
        compile_output(output([item_for(tiny)], [tiny]))


def test_compilation_snapshots_mutable_clip_values_and_rejects_old_revision_ids():
    found = clip("before.ts", 20, (2, 5), sid="stable")
    first = compile_output(output([item_for(found, "stable")], [found]),
                           revision="r1")
    old_id = first.occurrences[0].id
    old_path = first.occurrences[0].source_path
    old_fingerprint = first.occurrences[0].fingerprint

    found.path = Path("after.ts")
    found.duration = 40
    found.selects[0].start = 11
    found.selects[0].end = 17

    assert first.occurrences[0].source == TimeSpan(Fraction(2), Fraction(5))
    assert first.occurrences[0].source_path == old_path == "before.ts"
    assert first.occurrences[0].fingerprint == old_fingerprint

    second = compile_output(
        output([item_for(found, "stable")], [found]), revision="r2")
    with pytest.raises(StaleSequenceRevision, match="stale occurrence revision"):
        second.get_occurrence(old_id)
    with pytest.raises(StaleSequenceRevision, match="stale occurrence revision"):
        second.source_to_output(old_id, 2)
    assert second.occurrences[0].source == TimeSpan(Fraction(11), Fraction(17))
    assert second.occurrences[0].id == OccurrenceId("r2", 0)


def test_sequence_values_are_frozen_and_mapping_boundaries_are_half_open():
    found = clip("frozen.ts", 4)
    plan = compile_output(output([item_for(found)], [found]))

    with pytest.raises(AttributeError):
        plan.occurrences = ()
    with pytest.raises(SequencePlanError, match="outside the sequence"):
        plan.locate_output(-1)
    with pytest.raises(SequencePlanError, match="outside the sequence"):
        plan.locate_output(4.0001)
    with pytest.raises(SequencePlanError, match="outside the occurrence"):
        plan.locate_source(plan.occurrences[0].id, 4)
