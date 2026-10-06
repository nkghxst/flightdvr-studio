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

"""Which range is being edited, when an empty placeholder is in the list.

`current` counts every select; the places that name the edited range used to
index the filtered `real_selects` with it, and named the next range along.
No Qt.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from flightdvr.media import ClipInfo, Select, editing_range
from flightdvr.output_plan import piece_label, target_for_piece

PLACEHOLDER = Select(0.0, 0.0, sid="placeholder")
A = Select(2.0, 6.0, "A", sid="r-a")
B = Select(8.0, 12.0, "B", sid="r-b")
C = Select(14.0, 16.0, "C", sid="r-c")


def a_clip(selects, current) -> ClipInfo:
    return ClipInfo(path=Path("hdz_001.ts"), size=1, modified=datetime(2025, 10, 8),
                    duration=60.0, selects=list(selects), current=current)


@pytest.mark.parametrize("selects, current, expected", [
    ([PLACEHOLDER, A, B], 1, "r-a"),       # the case that named B
    ([PLACEHOLDER, A, B], 2, "r-b"),
    ([PLACEHOLDER, A, B], 0, "r-a"),       # editing the placeholder itself
    ([A, PLACEHOLDER], 1, "r-a"),
    ([A, PLACEHOLDER, B], 1, "r-a"),
    ([A, B, C], 2, "r-c"),                 # unchanged without a placeholder
    ([A, B], 5, "r-b"),
    ([PLACEHOLDER], 0, None),              # the whole recording
    ([], 0, None),
])
def test_the_edited_range_is_found_in_the_list_current_counts(
        selects, current, expected):
    clip = a_clip(selects, current)
    found = editing_range(clip)
    assert (found.sid if found else None) == expected
    target = target_for_piece(clip)
    assert target.items[0].sid == (expected or "")
    label = piece_label(clip)
    assert label == ("hdz_001.ts" if expected is None
                     else f"hdz_001.ts · {found.name}")
