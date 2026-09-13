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
