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

"""Which view the window is in, and which stage of Flow it is showing.

Policy only. Nothing here imports Qt, holds a widget or knows that a window
exists: it answers what the order is, which stages can honestly be offered
yet, and where Back and Next go. `ui.py` owns every widget, the stacking and
the reparenting, and asks these questions rather than deciding them itself —
so the order can be tested without building a window, and the window has one
place to change when a stage becomes real.

The stage order is not invented here. It is the order of the approved Flow
user revision, `flow_user.py:36`:

    STAGES = ["Browse", "Trim", "Assemble", "Music", "Output", "Queue"]
"""

from __future__ import annotations

from enum import Enum


class Mode(str, Enum):
    """How the same session is being presented."""

    CLASSIC = "classic"
    FLOW = "flow"


class Stage(str, Enum):
    """One page of Flow, in the approved order."""

    BROWSE = "browse"
    TRIM = "trim"
    ASSEMBLE = "assemble"
    MUSIC = "music"
    OUTPUT = "output"
    QUEUE = "queue"


# The approved order, and the only place it is written down.
STAGE_ORDER: tuple[Stage, ...] = (
    Stage.BROWSE, Stage.TRIM, Stage.ASSEMBLE, Stage.MUSIC, Stage.OUTPUT,
    Stage.QUEUE,
)

TITLES: dict[Stage, str] = {
    Stage.BROWSE: "Browse",
    Stage.TRIM: "Trim",
    Stage.ASSEMBLE: "Assemble",
    Stage.MUSIC: "Music",
    Stage.OUTPUT: "Output",
    Stage.QUEUE: "Queue",
}


def mode_from_stored(value) -> Mode:
    """The mode a stored setting asks for, or Classic.

    Classic is the default and the fallback, per issue #84: an unreadable,
    unknown or missing value is not an error to report at somebody, it is a
    window that opens the way it always did.
    """
    try:
        return Mode(str(value).strip().lower())
    except (ValueError, AttributeError, TypeError):
        return Mode.CLASSIC


def offered_stages(built) -> tuple[Stage, ...]:
    """The stages that can honestly be shown, in order.

    A stage with nothing behind it yet is left out rather than offered empty.
    An empty page that a stage bar advertises is worse than a stage bar with a
    gap in it: the first says the feature is there and broken, the second says
    it is not there yet — and this slice is a foundation, not six finished
    pages.
    """
    chosen = {Stage(stage) for stage in built}
    return tuple(stage for stage in STAGE_ORDER if stage in chosen)


def first_stage(offered) -> Stage | None:
    ordered = tuple(offered)
    return ordered[0] if ordered else None


def neighbours(current: Stage, offered) -> tuple[Stage | None, Stage | None]:
    """What Back and Next reach from here, skipping what is not built.

    `None` on either side means the button is at an end and has nothing to do,
    which the window shows as disabled rather than as a button that silently
    does nothing.
    """
    ordered = tuple(offered)
    if current not in ordered:
        return None, None
    index = ordered.index(current)
    back = ordered[index - 1] if index > 0 else None
    forward = ordered[index + 1] if index + 1 < len(ordered) else None
    return back, forward


def stage_from_stored(value, offered) -> Stage | None:
    """The stage a stored setting asks for, if it is still one we offer."""
    ordered = tuple(offered)
    try:
        stage = Stage(str(value).strip().lower())
    except (ValueError, AttributeError, TypeError):
        return first_stage(ordered)
    return stage if stage in ordered else first_stage(ordered)


def title(stage: Stage) -> str:
    return TITLES[Stage(stage)]
