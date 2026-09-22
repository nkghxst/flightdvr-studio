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

"""The stage policy, with no window anywhere near it.

Which stages exist, which can honestly be offered, where Back and Next go, and
what an unreadable stored value means. All of it answerable without Qt, which
is the point of keeping it apart from the widgets that obey it.
"""

from __future__ import annotations

import pytest

from flightdvr.flow_layout import (
    Clock, Domain, OccurrenceSource, SelectedContext,
    material_revision, nothing_selected, occurrences_of,
    STAGE_ORDER, Mode, Stage, first_stage, mode_from_stored, neighbours,
    offered_stages, stage_from_stored, title,
)


def test_the_order_is_the_approved_one():
    """From `flow_user.py:36` of the approved revision, not from the issue's
    candidate list, which #84 itself calls provisional."""
    assert [stage.value for stage in STAGE_ORDER] == [
        "browse", "trim", "assemble", "music", "output", "queue"]


def test_every_stage_has_a_name_to_show():
    for stage in STAGE_ORDER:
        assert title(stage)


# -- which mode ----------------------------------------------------------------

def test_a_stored_mode_is_honoured():
    assert mode_from_stored("flow") is Mode.FLOW
    assert mode_from_stored("classic") is Mode.CLASSIC
    assert mode_from_stored(" FLOW ") is Mode.FLOW


@pytest.mark.parametrize("stored", [None, "", "docked", "3", 7, object()])
def test_anything_unreadable_opens_classic(stored):
    """#84: Classic is the default and the fallback. An unknown stored value
    is a window that opens the way it always did, not an error at somebody."""
    assert mode_from_stored(stored) is Mode.CLASSIC


# -- which stages can honestly be offered --------------------------------------

def test_only_what_is_built_is_offered_and_always_in_order():
    built = {Stage.QUEUE, Stage.BROWSE, Stage.OUTPUT}
    assert offered_stages(built) == (Stage.BROWSE, Stage.OUTPUT, Stage.QUEUE)


def test_a_stage_with_nothing_behind_it_is_left_out_not_shown_empty():
    """An empty page a stage bar advertises says the feature is there and
    broken. A gap says it is not there yet, which is true."""
    assert Stage.ASSEMBLE not in offered_stages(
        {Stage.BROWSE, Stage.TRIM, Stage.MUSIC})


def test_offering_nothing_is_a_state_and_not_a_crash():
    assert offered_stages(set()) == ()
    assert first_stage(()) is None


def test_strings_are_accepted_where_stages_are():
    assert offered_stages({"browse", "queue"}) == (Stage.BROWSE, Stage.QUEUE)


# -- back and next -------------------------------------------------------------

def test_back_and_next_walk_the_offered_stages():
    offered = offered_stages({Stage.BROWSE, Stage.TRIM, Stage.QUEUE})
    assert neighbours(Stage.TRIM, offered) == (Stage.BROWSE, Stage.QUEUE)


def test_the_ends_have_nothing_beyond_them():
    offered = offered_stages({Stage.BROWSE, Stage.TRIM})
    assert neighbours(Stage.BROWSE, offered) == (None, Stage.TRIM)
    assert neighbours(Stage.TRIM, offered) == (Stage.BROWSE, None)


def test_next_skips_a_stage_that_is_not_built_rather_than_stopping_there():
    """Trim to Music, because Assemble is not offered in this slice."""
    offered = offered_stages({Stage.TRIM, Stage.MUSIC})
    assert neighbours(Stage.TRIM, offered)[1] is Stage.MUSIC


def test_a_stage_that_is_no_longer_offered_leads_nowhere():
    offered = offered_stages({Stage.BROWSE})
    assert neighbours(Stage.ASSEMBLE, offered) == (None, None)


def test_a_single_offered_stage_has_no_neighbours():
    offered = offered_stages({Stage.OUTPUT})
    assert neighbours(Stage.OUTPUT, offered) == (None, None)


# -- which stage to open on ----------------------------------------------------

def test_a_remembered_stage_is_reopened():
    offered = offered_stages({Stage.BROWSE, Stage.OUTPUT})
    assert stage_from_stored("output", offered) is Stage.OUTPUT


def test_a_remembered_stage_that_is_gone_falls_back_to_the_first():
    """A build where a stage stopped being offered must not open on nothing."""
    offered = offered_stages({Stage.BROWSE, Stage.OUTPUT})
    assert stage_from_stored("assemble", offered) is Stage.BROWSE
    assert stage_from_stored("nonsense", offered) is Stage.BROWSE
    assert stage_from_stored(None, offered) is Stage.BROWSE


def test_with_nothing_offered_there_is_no_stage_to_open():
    assert stage_from_stored("browse", ()) is None


# -- the one selected context every page reads ---------------------------------
#
# No window is built here on purpose. W5's picture recipe consumes this same
# value, and a contract that needed a QApplication to be checked would be a
# contract W5 could only test by building the thing it is meant to be
# independent of.


def an_occurrence(ordinal=0, fingerprint="fp-a", sid="r-1", path="A.ts"):
    return OccurrenceSource(ordinal, fingerprint, sid, path)


def a_working(occurrences=(), **kwargs):
    chosen = tuple(occurrences) or (an_occurrence(),)
    fields = dict(
        domain=Domain.WORKING,
        revision=material_revision(Domain.WORKING, chosen),
        target=object(),
        occurrences=chosen,
        preset_key="master",
    )
    fields.update(kwargs)
    return SelectedContext(**fields)


def test_an_unresolved_context_must_say_why_in_words():
    with pytest.raises(ValueError):
        SelectedContext(domain=Domain.NOTHING, revision="r")
    said = nothing_selected("choose a recording or an output")
    assert said.refusal
    assert not said.resolved and not said.editable


def test_an_output_needs_its_exact_target_and_a_job_needs_its_identity():
    with pytest.raises(ValueError):
        SelectedContext(domain=Domain.WORKING, revision="r")
    with pytest.raises(ValueError):
        SelectedContext(domain=Domain.SUBMITTED, revision="r", target=object())
    with pytest.raises(ValueError):
        SelectedContext(domain=Domain.SOURCE, revision="r")


def test_only_a_working_output_is_editable():
    assert a_working().editable
    assert not SelectedContext(
        domain=Domain.SUBMITTED, revision="r", target=object(),
        submitted="job-1").editable
    assert not SelectedContext(
        domain=Domain.SOURCE, revision="r", source_path="A.ts").editable
    assert not nothing_selected("nothing here").editable


def test_an_ordinary_single_range_output_still_keeps_the_output_clock():
    """The trap this property exists for: one range is still an output, and
    its first sample is output zero while the recording is at 12 seconds."""
    assert a_working().clock is Clock.OUTPUT
    assert SelectedContext(
        domain=Domain.SUBMITTED, revision="r", target=object(),
        submitted="job-1").clock is Clock.OUTPUT
    assert SelectedContext(
        domain=Domain.SOURCE, revision="r",
        source_path="A.ts").clock is Clock.SOURCE


def test_the_revision_moves_for_material_and_not_for_an_edit():
    first = (an_occurrence(0, "fp-a", "r-1", "A.ts"),
             an_occurrence(1, "fp-b", "r-2", "B.ts"))
    reordered = (an_occurrence(0, "fp-b", "r-2", "B.ts"),
                 an_occurrence(1, "fp-a", "r-1", "A.ts"))
    retrimmed = (an_occurrence(0, "fp-a", "r-9", "A.ts"),
                 an_occurrence(1, "fp-b", "r-2", "B.ts"))
    moved = (an_occurrence(0, "fp-a", "r-1", "MOVED.ts"),
             an_occurrence(1, "fp-b", "r-2", "B.ts"))

    base = material_revision(Domain.WORKING, first)
    assert material_revision(Domain.WORKING, reordered) != base, "reorder"
    assert material_revision(Domain.WORKING, retrimmed) != base, "retrim"
    assert material_revision(Domain.WORKING, moved) != base, "source changed"
    assert material_revision(Domain.WORKING, first) == base, "not stable"

    # Settings and music are edits to the same material. Fencing a decoder for
    # them would restart the picture every time somebody moved a slider.
    plain = a_working(first, preset_key="master", settings="A", music="X")
    edited = a_working(first, preset_key="social", settings="B", music="Y")
    assert edited.revision == plain.revision


def test_a_callback_from_superseded_material_is_not_ours():
    first = a_working((an_occurrence(0, "fp-a", "r-1", "A.ts"),))
    second = a_working((an_occurrence(0, "fp-a", "r-9", "A.ts"),))

    assert first.same_material(first)
    assert not first.same_material(second)
    assert not first.same_material(None)


def test_the_occurrence_snapshot_is_values_and_not_a_live_alias():
    """Held by value so material cannot change under a context that calls
    itself immutable."""
    class Mutable:
        def __init__(self):
            self.id = type("Id", (), {"ordinal": 0})()
            self.fingerprint = "fp-a"
            self.sid = "r-1"
            self.source_path = "A.ts"

    live = Mutable()
    plan = type("Plan", (), {"occurrences": (live,)})()
    snapshot = occurrences_of(plan)
    live.sid = "r-changed"
    live.source_path = "OTHER.ts"

    assert snapshot[0].sid == "r-1"
    assert snapshot[0].source_path == "A.ts"


def test_a_snapshot_that_does_not_match_its_sequence_is_refused():
    plan = type("Plan", (), {"occurrences": (object(), object())})()
    with pytest.raises(ValueError):
        a_working((an_occurrence(),), sequence=plan)


def test_occurrences_of_nothing_is_empty_rather_than_an_error():
    assert occurrences_of(None) == ()
