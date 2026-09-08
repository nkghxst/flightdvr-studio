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

    plan = OutputPlan()
    target = OutputTarget.clip_or_range("clip-a")
    with pytest.raises(ValueError, match="unknown export preset"):
        plan.set_choices(target, "not-a-preset", ExportSettings())
    with pytest.raises(KeyError, match="not in this plan"):
        plan.select(target)


def test_music_choice_rejects_an_empty_path_without_claiming_it_exists():
    with pytest.raises(ValueError, match="cannot be empty"):
        MusicChoice("")
