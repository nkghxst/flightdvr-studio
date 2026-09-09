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

"""How much room the clip list gets, and how the length filter is worded.

Everything here is a plain value, so the policy can be tested without a window
and the window is left holding widgets rather than arithmetic.

The one thing to understand before changing any of it: **the picture's height
follows its width**. `widgets.PreviewPanel.resizeEvent` sets its own height to
`useful_height(width)`, and the clip list above it takes whatever is left.

The obvious lever is therefore the horizontal splitter — and it was measured
giving nothing. At the sizes this window opens at the left column is already at
its own minimum width, so there is no width to trade, and on a window wide
enough for it to work all it would do is widen the export column. So no mode
moves the split. Expanded asks the preview for a height ceiling instead
(`PreviewPanel.set_height_cap`), which is the one thing that does buy the list
room; the shares below exist to record that decision, not to act on it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from .clip_filter import ClipFilter


class BrowserMode(str, Enum):
    """How much of the window the clip list is asking for."""

    COLLAPSED = "collapsed"
    NORMAL = "normal"
    EXPANDED = "expanded"

    @property
    def label(self) -> str:
        return {"collapsed": "Collapsed", "normal": "Normal",
                "expanded": "Expanded"}[self.value]


# The left column's share of the splitter in each mode.
#
# Normal is today's default expressed as a share: `ui.py` opens the splitter at
# [720, 500], and 720 / 1220 is 0.59. The other two are that number moved in
# the direction the mode is asking for, because width is the only thing that
# changes the picture's height.
NORMAL_LEFT_SHARE = 0.59
# Also the same as Normal. Narrowing the left column is the textbook way to
# make the picture shorter, and it was measured giving nothing: at the sizes
# this window opens at, the left column is already at its own minimum width, so
# `setSizes` has nothing to take. On a window wide enough for it to work, all
# it would do is widen the export column, which is simulating an expansion
# rather than performing one. `PreviewPanel`'s height cap is what actually
# buys the list its height.
EXPANDED_LEFT_SHARE = 0.59
# The same as Normal, deliberately. Widening the left column here does make the
# picture bigger, but the width comes out of the export column, and at 386 px
# its help text starts being cut off rather than wrapping. Collapsing the list
# is not worth truncating the panel that explains the preset.
#
# The height the list gives up is not recoverable either: `PreviewPanel`'s
# clamp reserves `MIN_LIST_HEIGHT` for a list that is no longer on screen. That
# clamp is out of scope here, so Collapsed's benefit is that the list is out of
# the way, and the reclaimed height is recorded as a known limitation rather
# than quietly taken from somewhere it hurts.
COLLAPSED_LEFT_SHARE = 0.59

DEFAULT_MODE = BrowserMode.NORMAL


def split_sizes(mode: BrowserMode, total: int, minimum_right: int = 330,
                minimum_left: int = 360) -> tuple[int, int]:
    """Splitter sizes for a mode: today, the same split for all three.

    Kept as a function rather than deleted because it is the place the decision
    is recorded — no mode is allowed to move the split, so none of them can pay
    for itself out of the export column.

    `minimum_right` is the export panel's own minimum width, which it sets on
    itself; passing it in keeps this module free of that import. A total too
    small for both minimums gives the right column its minimum and the left
    whatever is honestly left, which is what the splitter would do anyway.
    """
    if total <= 0:
        return (0, 0)
    share = {
        BrowserMode.COLLAPSED: COLLAPSED_LEFT_SHARE,
        BrowserMode.NORMAL: NORMAL_LEFT_SHARE,
        BrowserMode.EXPANDED: EXPANDED_LEFT_SHARE,
    }[mode]
    left = round(total * share)
    left = min(left, max(0, total - minimum_right))
    left = max(left, min(minimum_left, total))
    return (left, max(0, total - left))


@dataclass(frozen=True)
class ClassicLayout:
    """What the View menu and the browser's own toggles agree on."""

    browser: BrowserMode = DEFAULT_MODE
    queue_open: bool = False

    @classmethod
    def default(cls) -> "ClassicLayout":
        """What Restore default layout restores."""
        return cls()

    def with_browser(self, mode: BrowserMode) -> "ClassicLayout":
        return replace(self, browser=mode)

    def with_queue(self, open_: bool) -> "ClassicLayout":
        return replace(self, queue_open=bool(open_))

    @property
    def list_visible(self) -> bool:
        return self.browser is not BrowserMode.COLLAPSED


# -- the length filter ---------------------------------------------------------

# A spin box shows its `specialValueText` at its minimum, so zero is how both
# bounds say "not set". No separate tick box, and no sentinel a user can type.
UNSET = 0


def bounds(minimum: int, maximum: int) -> tuple[int | None, int | None]:
    """Turn the two spin box values into bounds, in seconds.

    A maximum below an active minimum is raised to meet it rather than being
    applied as an empty range: the pair is one control, and a filter that can
    hide everything while both boxes look reasonable is a trap.
    """
    low = None if minimum <= UNSET else int(minimum)
    high = None if maximum <= UNSET else int(maximum)
    if low is not None and high is not None and high < low:
        high = low
    return (low, high)


def length_filter(minimum: int, maximum: int, show_unknown: bool,
                  review_filter=None) -> ClipFilter:
    """The merged #96 policy, composed with whatever the Show box is doing.

    The review predicate is passed straight through as `ClipFilter`'s own
    `review_filter`, so a clip has to pass both and neither filter has to know
    about the other.
    """
    low, high = bounds(minimum, maximum)
    return ClipFilter(minimum=low, maximum=high,
                      show_unknown=bool(show_unknown),
                      review_filter=review_filter)


def clock(seconds: int) -> str:
    """m:ss, for a bound that is spoken as a length rather than a number."""
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def bound_text(minimum: int, maximum: int, show_unknown: bool) -> str:
    """The active length rule, in words, or an empty string when it is reset."""
    low, high = bounds(minimum, maximum)
    parts = []
    if low is not None and high is not None:
        parts.append(f"{clock(low)} to {clock(high)}")
    elif low is not None:
        parts.append(f"{clock(low)} or longer")
    elif high is not None:
        parts.append(f"{clock(high)} or shorter")
    if not show_unknown:
        parts.append("unknown lengths hidden")
    return " · ".join(parts)


def hidden_summary(total: int, shown: int, by_length: int) -> str:
    """What is not on screen, and how much of that the length rule did.

    `by_length` is counted against the review filter alone, so the two numbers
    add up for someone reading them: everything hidden, and the part of it this
    control is responsible for.
    """
    hidden = max(0, total - shown)
    if hidden == 0:
        return f"{total} shown"
    if by_length <= 0:
        return f"{shown} of {total} shown · {hidden} hidden"
    if by_length >= hidden:
        return (f"{shown} of {total} shown · {hidden} hidden by length")
    return (f"{shown} of {total} shown · {hidden} hidden, "
            f"{by_length} of them by length")
