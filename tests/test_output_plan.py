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

from pathlib import Path

import pytest

from flightdvr.assembly import Item
from flightdvr.output_plan import MusicChoice, OutputPlan, OutputTarget
from flightdvr.presets import ExportSettings


def test_two_targets_keep_distinct_choices_across_selection_round_trips():
    """Switching views used to be the planned alias risk for shared state."""
    range_target = OutputTarget.clip_or_range("clip-a", "range-a")
    assembly_target = OutputTarget.assembly([
        Item("clip-a", "range-a"), Item("clip-b", "range-b")])
    caller = ExportSettings(social_size_mb=25)
    plan = OutputPlan()

    plan.set_choices(range_target, "social", caller,
                     MusicChoice(Path("launch.wav")))
    caller.social_size_mb = 60
    plan.set_choices(assembly_target, "master", caller,
                     MusicChoice(Path("cruise.wav")))

    first = plan.select(range_target)
    assert first.settings.social_size_mb == 25
    assert first.music.track == Path("launch.wav")

    second = plan.select(assembly_target)
    assert second.settings.social_size_mb == 60
    assert second.music.track == Path("cruise.wav")

    assert plan.select(range_target) == first
    assert plan.targets == (range_target, assembly_target)
    assert plan.selected_target is range_target


def test_returned_choices_cannot_mutate_the_plan_or_another_target():
    """A presentation edits through set_choices, not a shared settings alias."""
    first_target = OutputTarget.clip_or_range("clip-a", "range-a")
    second_target = OutputTarget.clip_or_range("clip-b", "range-b")
    plan = OutputPlan()
    plan.set_choices(first_target, "social", ExportSettings(social_crf=20))
    plan.set_choices(second_target, "social", ExportSettings(social_crf=26))

    returned = plan.get(first_target)
    returned.settings.social_crf = 51

    assert plan.get(first_target).settings.social_crf == 20
    assert plan.get(second_target).settings.social_crf == 26


def test_target_identity_uses_existing_references_not_mutable_source_lists():
    items = [Item("clip-a", "range-a"), Item("clip-b", "range-b")]
    target = OutputTarget.assembly(items)

    items.reverse()
    items.append(Item("clip-c", "range-c"))

    assert target.items == (
        Item("clip-a", "range-a"), Item("clip-b", "range-b"))
    assert OutputTarget.clip_or_range("clip-a", "range-a").items == (
        Item("clip-a", "range-a"),)


def test_invalid_targets_and_unknown_presets_fail_before_entering_the_plan():
    with pytest.raises(ValueError, match="at least one"):
        OutputTarget.assembly([])
    with pytest.raises(ValueError, match="exactly one"):
        OutputTarget((Item("a"), Item("b")))
    with pytest.raises(ValueError, match="fingerprints"):
        OutputTarget.clip_or_range("")
    with pytest.raises(ValueError, match="fingerprints"):
        OutputTarget.clip_or_range("clip-a", sid=object())

    plan = OutputPlan()
    target = OutputTarget.clip_or_range("clip-a")
    with pytest.raises(ValueError, match="unknown export preset"):
        plan.set_choices(target, "not-a-preset", ExportSettings())
    with pytest.raises(KeyError, match="not in this plan"):
        plan.select(target)


def test_music_choice_rejects_an_empty_path_without_claiming_it_exists():
    with pytest.raises(ValueError, match="cannot be empty"):
        MusicChoice("")


# -- what the queue would build (#84 sidebar) ----------------------------------

def a_piece(name: str, ranges=(), current: int = 0):
    """A resolved export piece, shaped the way `for_export` leaves one."""
    from datetime import datetime
    from pathlib import Path

    from flightdvr.media import ClipInfo, Select

    clip = ClipInfo(
        path=Path(name), size=1024, modified=datetime(2025, 10, 8, 18, 39),
        duration=30.0, width=1280, height=720, fps=60.0, video_codec="hevc",
        audio_codec="aac", pix_fmt="yuvj420p", color_range="pc",
    )
    clip.selects = [Select(start, end, label, sid=sid)
                    for start, end, label, sid in ranges]
    clip.current = current
    return clip


def test_a_whole_recording_is_one_output_with_no_range_id():
    from flightdvr.output_plan import working_outputs

    outputs = working_outputs([a_piece("hdz_001.ts")])
    assert len(outputs) == 1
    assert outputs[0].target.items[0].sid == ""
    assert outputs[0].label == "hdz_001.ts"


def test_three_ranges_are_three_outputs_each_with_its_own_identity():
    """`for_export` hands three one-select pieces, so this hands three
    outputs — one per piece, keyed by that piece's range."""
    from flightdvr.output_plan import working_outputs

    pieces = [
        a_piece("hdz_001.ts", ranges=[(1.0, 5.0, "one", "r-1")]),
        a_piece("hdz_001.ts", ranges=[(6.0, 9.0, "two", "r-2")]),
        a_piece("hdz_001.ts", ranges=[(11.0, 14.0, "", "r-3")]),
    ]
    outputs = working_outputs(pieces)

    assert [o.target.items[0].sid for o in outputs] == ["r-1", "r-2", "r-3"]
    assert len({o.target for o in outputs}) == 3
    assert outputs[0].label.endswith("· one")
    assert outputs[2].label == "hdz_001.ts", "an unnamed range invented a name"


def test_retrimming_a_range_does_not_change_its_identity():
    """The card's content changes; the thing its music hangs on does not."""
    from flightdvr.output_plan import working_outputs

    before = working_outputs([a_piece("hdz_001.ts",
                                      ranges=[(1.0, 5.0, "one", "r-1")])])
    after = working_outputs([a_piece("hdz_001.ts",
                                     ranges=[(2.5, 8.0, "one", "r-1")])])
    assert before[0].target == after[0].target


def test_renaming_a_range_changes_the_label_and_not_the_identity():
    from flightdvr.output_plan import working_outputs

    before = working_outputs([a_piece("hdz_001.ts",
                                      ranges=[(1.0, 5.0, "one", "r-1")])])
    after = working_outputs([a_piece("hdz_001.ts",
                                     ranges=[(1.0, 5.0, "renamed", "r-1")])])
    assert before[0].target == after[0].target
    assert before[0].label != after[0].label


def test_an_assembly_is_one_output_over_its_whole_ordered_run():
    """One job, so one output — and its pieces are the run, in order."""
    from flightdvr.output_plan import working_outputs

    pieces = [
        a_piece("hdz_001.ts", ranges=[(1.0, 5.0, "one", "r-1")]),
        a_piece("hdz_002.ts"),
    ]
    outputs = working_outputs(pieces, joined=True)

    assert len(outputs) == 1
    assert outputs[0].joined
    assert outputs[0].target.is_assembly
    assert len(outputs[0].pieces) == 2
    assert [item.sid for item in outputs[0].target.items] == ["r-1", ""]


def test_an_assembly_keeps_material_nobody_ticked_and_repeated_ranges():
    """Assembly rows are resolved from the list, not from the ticks, and the
    same range may appear more than once. Both have to survive."""
    from flightdvr.output_plan import working_outputs

    twice = a_piece("hdz_001.ts", ranges=[(1.0, 5.0, "one", "r-1")])
    pieces = [twice, a_piece("hdz_009.ts"), twice]
    outputs = working_outputs(pieces, joined=True)

    assert len(outputs[0].pieces) == 3, "a repeated occurrence was collapsed"
    assert [item.fingerprint for item in outputs[0].target.items].count(
        twice.fingerprint) == 2


def test_nothing_resolved_is_no_outputs_rather_than_an_empty_one():
    from flightdvr.output_plan import working_outputs

    assert working_outputs([]) == []
    assert working_outputs([], joined=True) == []
    assert working_outputs([None]) == []


def test_a_piece_with_no_fingerprint_is_skipped_not_invented():
    """No identity is made up for something that cannot supply one."""
    from types import SimpleNamespace

    from flightdvr.output_plan import target_for_piece, working_outputs

    assert target_for_piece(SimpleNamespace(fingerprint="")) is None
    assert working_outputs([SimpleNamespace(fingerprint="")]) == []


def test_a_wrapped_piece_resolves_to_the_same_identity_as_a_bare_one():
    """The bundle path wraps its recording in `.clip`; one identity either
    way, or the same output would be keyed two ways."""
    from types import SimpleNamespace

    from flightdvr.output_plan import target_for_piece

    bare = a_piece("hdz_001.ts", ranges=[(1.0, 5.0, "one", "r-1")])
    wrapped = SimpleNamespace(clip=bare)
    assert target_for_piece(wrapped) == target_for_piece(bare)
