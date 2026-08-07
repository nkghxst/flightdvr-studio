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

"""Which parts of a recording are flying, from frames decoded anyway.

The filmstrip already pulls out every keyframe — about one a second — so
working out when the picture stops moving costs one pass over files already on
disk. Nothing is decoded twice for this.

**What a still stretch means here is a crash.** These goggles keep recording
after the quad is down, so a run of frames that barely change is the quad in
the grass and the pilot walking over to it. That is the useful signal, and it
is not the one this started out looking for: the first version of this module
tried to find the bench time before take-off, and measurement said there is
none — the DVR records from arming, so the footage is moving from the first
frame. That finding is in issue #17 and worth not rediscovering.

What the numbers support:

* **How much of a clip is flying.** Four minutes of flight then a crash is
  worth watching; twenty seconds then a crash is not, and on a card of a
  hundred and twenty recordings that distinction is most of the triage.
* **Where the flying parts are.** One recording can hold several arm, fly,
  land, disarm cycles, so "the flight" is often several of them.

Everything here describes; nothing decides. No caller may turn these into a
trim without a person agreeing to it, for the same reason as always — a wrong
guess offered is ignored, a wrong guess applied is somebody's crash landing
missing from their export.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

# Frames are compared at this size rather than their own. Small enough that
# sensor noise, OSD digits ticking over and compression artefacts average away;
# large enough that a quad moving does not.
COMPARE_WIDTH = 32
COMPARE_HEIGHT = 18

# Below this, one second to the next, nothing happened. Chosen by measurement
# rather than taste: on real cards a stationary quad sits at 0.7-2.5 and flight
# runs 9-17, so this sits in the gap with room either side.
STILL = 3.0

# A stop shorter than this is a hover, a slow turn, or a moment pointing at the
# sky — not a crash. Keyframes are a second apart, so this is four seconds.
MIN_STILL = 4

# Likewise for the flying side: a couple of seconds of movement in the middle
# of a crash is the pilot picking the quad up, not a flight.
MIN_FLYING = 4

# If even the quietest second of a clip is this busy, the feed's own noise is
# louder than the thing being measured and no still time can be detected. Such
# a clip gets an honest "cannot tell" rather than "flew throughout".
#
# Measured across fourteen recordings on a real card: readable clips scored
# 0.59-3.81 at their quietest and unreadable ones 5.26-9.03, so this sits in a
# gap with room either side. Five of the fourteen fell above it — more than the
# "one in four" this was first written for, and worth knowing before treating a
# refusal as unusual.
UNREADABLE_FLOOR = 4.0

# Below this there is nothing worth offering to trim to. Twenty seconds of
# flying followed by four minutes of grass is a clip to skip, not one to cut
# down — and suggesting a trim would imply it is worth keeping.
MIN_SUGGESTION = 30.0


@dataclass
class Activity:
    """What a recording spends its time doing, as far as this can tell."""

    duration: float
    still: list[tuple[float, float]]
    flying: list[tuple[float, float]]
    quietest: float
    readable: bool = True

    @property
    def still_seconds(self) -> float:
        return sum(b - a for a, b in self.still)

    @property
    def flying_seconds(self) -> float:
        return sum(b - a for a, b in self.flying)

    @property
    def flying_share(self) -> float:
        return self.flying_seconds / self.duration if self.duration else 0.0

    @property
    def longest_flight(self) -> float:
        """The number that decides whether a clip is worth opening.

        A clip with four minutes of continuous flying and a crash at the end is
        worth watching. One with twenty seconds of flying and four minutes of
        grass is not, however similar the totals look.
        """
        return max((b - a for a, b in self.flying), default=0.0)

    @property
    def flights(self) -> int:
        """Roughly how many arm, fly, land cycles are in here.

        Roughly, because a long enough hover reads as a landing. Older footage
        in particular holds several cycles in one recording.
        """
        return len(self.flying)

    @property
    def suggestion(self) -> tuple[float, float] | None:
        """The longest run of flying, when trimming to it would drop anything.

        Nothing is applied from this — it is what a button offers, never what
        the app does on its own. A wrong guess that silently trimmed footage
        would be far worse than no guess, and the whole point of the still
        detection is that it is a reading rather than a fact.

        None when the reading cannot be trusted, when there is no still time to
        cut, or when the longest flight is so short that what is left would not
        be worth keeping either.
        """
        if not self.readable or not self.flying:
            return None
        longest = max(self.flying, key=lambda span: span[1] - span[0])
        if longest[1] - longest[0] < MIN_SUGGESTION:
            return None
        if longest[1] - longest[0] >= self.duration - MIN_STILL:
            return None                     # nothing worth cutting
        return longest

    def describe(self, human) -> str:
        """One line for the sidebar, saying only what was actually measured."""
        if not self.readable:
            return ("This feed is too noisy to tell flying from stopped — "
                    "no reading")
        if not self.flying:
            return "Nothing here looks like flying"

        # The two numbers that decide anything, and nothing else. How many
        # times it flew, and how long the best run was — a clip with four
        # minutes of flying and a crash at the end is worth watching, one with
        # twenty seconds and four minutes of grass is not, and the totals do
        # not tell those apart. Short enough to sit on one line, too.
        if self.flights > 1:
            return (f"{self.flights} flights · longest "
                    f"{human(self.longest_flight)} of {human(self.duration)}")
        if self.still_seconds > MIN_STILL:
            return (f"{human(self.longest_flight)} moving "
                    f"of {human(self.duration)}")
        return f"moving throughout, {human(self.duration)}"


def _runs(flags: list[bool], want: bool, minimum: int) -> list[tuple[int, int]]:
    """Index spans where `flags` holds `want` for at least `minimum` in a row."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for i, value in enumerate(flags + [not want]):
        if value == want:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= minimum:
                spans.append((start, i))
            start = None
    return spans


def segment(values: list[float], times: list[float],
            still: float = STILL) -> tuple[list, list]:
    """Split a difference curve into still spans and flying spans, in seconds.

    Pure, and takes the numbers rather than the pictures, so every awkward
    shape is testable without decoding anything.
    """
    if not values or not times:
        return [], []

    def seconds(spans):
        out = []
        for a, b in spans:
            start = times[min(a, len(times) - 1)]
            end = times[min(b, len(times) - 1)]
            if end > start:
                out.append((start, end))
        return out

    quiet = [v < still for v in values]
    stopped = _runs(quiet, True, MIN_STILL)

    # Flying is worked out from the stops that were *accepted*, not from the
    # raw quiet flags. Otherwise a two-second stillness — too short to be a
    # landing, and correctly not reported as one — still punches a hole through
    # the flight either side of it, and one flight is reported as two. A hover
    # belongs to the flight around it.
    settled = [False] * len(values)
    for a, b in stopped:
        for i in range(a, b):
            settled[i] = True

    return seconds(stopped), seconds(_runs(settled, False, MIN_FLYING))


def _thumbnail(path: Path) -> bytes | None:
    """One frame, small and grey, as plain bytes to compare."""
    image = QImage(str(path))
    if image.isNull():
        return None
    small = image.scaled(
        COMPARE_WIDTH, COMPARE_HEIGHT,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    ).convertToFormat(QImage.Format.Format_Grayscale8)
    return bytes(small.constBits())


def frame_differences(frames: list[Path]) -> list[float]:
    """How much changed between each frame and the one before it.

    One value per gap, so `values[i]` is the step from `frames[i]` to
    `frames[i + 1]`. A frame that will not load contributes nothing rather than
    a spike, because a missing thumbnail is not movement.
    """
    values: list[float] = []
    previous = None
    for path in frames:
        current = _thumbnail(path)
        if current is None:
            continue
        if previous is not None and len(previous) == len(current):
            total = sum(abs(a - b) for a, b in zip(previous, current))
            values.append(total / len(current))
        previous = current
    return values


def activity(strip) -> Activity | None:
    """What this recording spends its time doing, or None without a filmstrip.

    `readable` is False when the feed's own noise is louder than the difference
    between a flying quad and a crashed one. Such a clip reports no still time
    because none can be seen, which is not the same as there being none, and
    the caller has to be able to tell those apart.
    """
    if not strip or len(strip.frames) < MIN_STILL + MIN_FLYING:
        return None

    values = frame_differences(strip.frames)
    if not values:
        return None

    ordered = sorted(values)
    quietest = ordered[max(0, len(ordered) // 20)]        # 5th percentile
    still, flying = segment(values, strip.times)

    return Activity(
        duration=strip.times[-1] if strip.times else 0.0,
        still=still,
        flying=flying,
        quietest=quietest,
        readable=quietest < UNREADABLE_FLOOR,
    )
