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

"""The verdict is a pure function, so the awkward cases can just be written out.

Every case here is a shape the 6 August 2026 outage actually produced. The last
one is the shape that made this file necessary: the tool reported "not usable"
for half an hour after Actions came back, because two poisoned runs were still
queued behind runs that were starting and passing normally.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from check_ci import verdict                                      # noqa: E402

CLEAR = {"reachable": True, "component": "operational"}
BROKEN = {"reachable": True, "component": "major_outage"}


def run(status: str, minutes_ago: float, conclusion: str | None = None) -> dict:
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    stamp = when.isoformat().replace("+00:00", "Z")
    return {"status": status, "conclusion": conclusion,
            "createdAt": stamp, "updatedAt": stamp}


def test_a_run_executing_now_settles_it():
    usable, notes = verdict(BROKEN, [run("in_progress", 2)])
    assert usable, notes


def test_nothing_moving_for_hours_is_not_usable():
    usable, notes = verdict(CLEAR, [run("queued", 400), run("queued", 300)])
    assert not usable
    assert "no runner has picked them up" in " ".join(notes)


def test_unreachable_api_is_treated_as_broken():
    usable, notes = verdict(CLEAR, None)
    assert not usable


def test_a_clear_page_with_no_runs_at_all_is_believed_but_hedged():
    usable, notes = verdict(CLEAR, [])
    assert usable
    assert "unproven" in " ".join(notes)


def test_the_same_silence_with_a_broken_page_is_not():
    assert not verdict(BROKEN, [])[0]


def test_leftover_queued_runs_do_not_veto_newer_runs_that_passed():
    """The shape that made this file necessary.

    Two jobs from the outage sat queued for hours and no runner would ever
    claim them. Meanwhile three newer runs started and went green. The tool
    said "NOT usable" — reading the debris and ignoring the evidence.
    """
    recent = [
        run("completed", 5, "success"),
        run("completed", 40, "success"),
        run("completed", 65, "success"),
        run("queued", 340),
        run("queued", 470),
    ]
    usable, notes = verdict(CLEAR, recent)
    assert usable, notes
    assert "leftovers" in " ".join(notes), notes


def test_but_stale_queued_runs_still_count_when_nothing_newer_moved():
    """The distinction has to cut both ways, or it is just optimism."""
    recent = [
        run("queued", 20),
        run("queued", 340),
        run("completed", 600, "success"),      # older than the stuck ones
    ]
    usable, notes = verdict(CLEAR, recent)
    assert not usable, notes
