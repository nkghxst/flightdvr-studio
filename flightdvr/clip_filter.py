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

"""Pure duration and review eligibility for the clip browser.

The browser owns the controls and the existing review/list filter. This module
only answers which objects are eligible for a view, so applying it cannot
delete a clip or change its review, ticks, ranges, queue entries or selection.

``ClipInfo.duration`` is zero when probing has no usable duration: the model
also starts at zero, ``media._to_float`` maps missing and malformed probe
values to zero, and the UI displays every non-positive duration as ``?``.
Consequently a positive finite number is known here; zero, negative, missing,
non-finite and otherwise malformed values are unknown.

An empty ``ClipFilter`` is the reset state and includes every clip, including
unknown-duration clips. Once a minimum or maximum is active, ``show_unknown``
decides whether unknown durations remain visible. ``review_filter`` is a
predicate supplied by the caller, which keeps this policy independent of the
browser's review constants and lets it compose with review or exported views.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import TypeVar


T = TypeVar("T")


def _normalise_bound(value: Real | None, name: str) -> float | None:
    """Validate a duration bound and store it as a plain finite float."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(
            f"{name} must be a finite non-negative number or None")
    seconds = float(value)
    if not isfinite(seconds) or seconds < 0:
        raise ValueError(
            f"{name} must be a finite non-negative number or None")
    return seconds


def _known_duration(value: object) -> float | None:
    """Return a usable duration, or ``None`` for the model's unknown values."""
    if value is None or isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(seconds) or seconds <= 0:
        return None
    return seconds


@dataclass(frozen=True)
class ClipFilter:
    """The view-only duration policy, optionally composed with review logic.

    Bounds are seconds and inclusive. The review predicate receives the clip
    object itself rather than a review string because the existing browser has
    both review-state and exported filters; the caller can supply either
    without this pure module importing UI or session code.
    """

    minimum: Real | None = None
    maximum: Real | None = None
    show_unknown: bool = False
    review_filter: Callable[[object], bool] | None = None

    def __post_init__(self) -> None:
        minimum = _normalise_bound(self.minimum, "minimum")
        maximum = _normalise_bound(self.maximum, "maximum")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("minimum duration cannot exceed maximum duration")
        if not isinstance(self.show_unknown, bool):
            raise TypeError("show_unknown must be a bool")
        if (self.review_filter is not None
                and not callable(self.review_filter)):
            raise TypeError("review_filter must be callable or None")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    @property
    def is_active(self) -> bool:
        """Whether a duration bound, rather than only review logic, is set."""
        return self.minimum is not None or self.maximum is not None

    def _duration_matches(self, value: object) -> bool:
        known = _known_duration(value)
        if known is None:
            # No duration bound is the reset state: it must not hide clips
            # merely because their probe could not provide a length.
            return not self.is_active or self.show_unknown
        return ((self.minimum is None or known >= self.minimum)
                and (self.maximum is None or known <= self.maximum))

    def matches(self, clip: object) -> bool:
        """Return whether ``clip`` may remain visible under this policy."""
        if not self._duration_matches(getattr(clip, "duration", None)):
            return False
        return self.review_filter is None or self.review_filter(clip)

    def apply(self, clips: Iterable[T]) -> list[T]:
        """Return eligible clips in input order without changing the inputs."""
        return [clip for clip in clips if self.matches(clip)]
