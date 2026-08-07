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

"""Telling flying from crashed, and knowing when it cannot be told.

The shapes tested here come from real cards rather than from imagination — the
first version of this module was built against invented curves, passed fourteen
tests, and did not work on a single real clip. The numbers in these tests are
the ones measured on `G:\\movies`.

The two that matter most are at the end: a clip whose feed is too noisy to
measure must say so rather than claim it flew throughout, and nothing anywhere
may turn any of this into a trim on its own.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from flightdvr.motion import (
    MIN_FLYING, MIN_STILL, STILL, UNREADABLE_FLOOR, Activity, segment,
)


def curve(*runs: tuple[float, int], jitter: float = 0.0, seed: int = 7):
    """A difference curve from (level, seconds) runs, at one value a second."""
    rng = random.Random(seed)
    out: list[float] = []
    for level, length in runs:
        out += [max(0.0, level + rng.uniform(-jitter, jitter))
                for _ in range(length)]
    return out


def seconds(count: int) -> list[float]:
    return [float(n) for n in range(count + 1)]


# -- flying and crashed ---------------------------------------------------------

def test_a_flight_then_a_crash_is_split():
    """The shape of hdz_018: about two and a half minutes of flying, then the
    quad in the grass and the pilot walking over."""
    values = curve((11.0, 140), (1.0, 60), jitter=1.0)
    still, flying = segment(values, seconds(len(values)))

    assert len(flying) == 1 and len(still) == 1
    assert flying[0][0] == pytest.approx(0.0)
    assert flying[0][1] == pytest.approx(140, abs=3)
    assert still[0][0] == pytest.approx(140, abs=3)


def test_several_flights_in_one_recording():
    """Older footage holds more than one arm, fly, land, disarm cycle."""
    values = curve((11.0, 40), (1.0, 20), (11.0, 50), (1.0, 15), (11.0, 30),
                   jitter=1.0)
    still, flying = segment(values, seconds(len(values)))
    assert len(flying) == 3
    assert len(still) == 2


def test_a_hover_is_not_a_landing():
    """A couple of still seconds mid-flight is pointing at the sky, not a
    crash, and splitting a flight in two over it would be wrong."""
    values = curve((11.0, 60), (1.0, MIN_STILL - 1), (11.0, 60), jitter=1.0)
    still, flying = segment(values, seconds(len(values)))
    assert still == []
    assert len(flying) == 1


def test_picking_the_quad_up_is_not_a_flight():
    values = curve((1.0, 40), (11.0, MIN_FLYING - 1), (1.0, 40), jitter=0.5)
    still, flying = segment(values, seconds(len(values)))
    assert flying == []


def test_a_clip_that_never_stops_is_all_flying():
    values = curve((12.0, 200), jitter=2.0)
    still, flying = segment(values, seconds(len(values)))
    assert still == []
    assert len(flying) == 1


def test_nothing_to_segment():
    assert segment([], []) == ([], [])
    assert segment([1.0, 2.0], []) == ([], [])


# -- what the numbers are for ---------------------------------------------------

def test_a_long_flight_then_a_crash_reads_as_worth_watching():
    """hdz_018: 225 seconds, crash at about 140."""
    made = Activity(duration=225.0, still=[(141.0, 188.0)],
                    flying=[(0.0, 141.0)], quietest=0.7)
    assert made.longest_flight == pytest.approx(141.0)
    assert made.flying_share > 0.6
    assert made.flights == 1


def test_a_short_flight_then_a_crash_reads_as_not_worth_it():
    """hdz_016: 71 seconds, of which half is the quad lying in the grass."""
    made = Activity(duration=71.0, still=[(31.0, 43.0), (49.0, 58.0)],
                    flying=[(0.0, 31.0)], quietest=0.9)
    assert made.longest_flight == pytest.approx(31.0)
    assert made.still_seconds == pytest.approx(21.0)
    assert made.flying_share < 0.5


def test_the_longest_flight_is_not_the_total():
    """Two thirty-second flights are not the same clip as one of sixty, and
    the totals cannot tell them apart."""
    split = Activity(duration=120.0, still=[(30.0, 60.0)],
                     flying=[(0.0, 30.0), (60.0, 90.0)], quietest=1.0)
    whole = Activity(duration=120.0, still=[],
                     flying=[(0.0, 60.0)], quietest=1.0)
    assert split.flying_seconds == whole.flying_seconds
    assert split.longest_flight < whole.longest_flight


def test_shares_of_an_empty_clip_do_not_divide_by_zero():
    assert Activity(duration=0.0, still=[], flying=[], quietest=0.0).flying_share == 0.0


# -- the two that matter --------------------------------------------------------

def test_a_feed_too_noisy_to_measure_says_so():
    """hdz_004 has a noise floor of 5.5 where other clips sit near 1.0, so no
    still time can be seen in it.

    Reporting "no crashes" would be a claim the measurement cannot support.
    Reporting "cannot tell" is the truth, and the caller has to be able to tell
    the two apart."""
    assert UNREADABLE_FLOOR > STILL, (
        "a clip whose quietest second is below the still threshold is "
        "measurable by definition"
    )
    noisy = Activity(duration=228.0, still=[], flying=[(0.0, 228.0)],
                     quietest=5.5, readable=False)
    quiet = Activity(duration=249.0, still=[(151.0, 163.0)],
                     flying=[(0.0, 151.0)], quietest=1.0, readable=True)
    assert not noisy.readable and quiet.readable


def test_nothing_anywhere_turns_this_into_a_trim():
    """The property that makes describing a recording acceptable at all.

    A wrong reading offered is ignored. A wrong reading applied is somebody's
    crash landing missing from their export, found later or never.
    """
    import re

    root = Path(__file__).resolve().parents[1] / "flightdvr"
    offenders = []
    for source in root.glob("*.py"):
        for line in source.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#"):
                continue
            if re.search(r"trim_(in|out)\s*=.*\b(activity|flying|segment)\b",
                         line):
                offenders.append(f"{source.name}: {line.strip()}")
    assert not offenders, (
        f"movement is being applied as a trim rather than offered: {offenders}"
    )


# -- reading it off real frames -------------------------------------------------

def test_differences_are_one_per_gap(tmp_path):
    from PySide6.QtGui import QImage
    from flightdvr.motion import frame_differences

    frames = []
    for n, shade in enumerate((0, 0, 255, 255)):
        image = QImage(16, 16, QImage.Format.Format_RGB32)
        image.fill(shade << 16 | shade << 8 | shade)
        path = tmp_path / f"f_{n:04d}.jpg"
        image.save(str(path))
        frames.append(path)

    values = frame_differences(frames)
    assert len(values) == 3
    assert values[0] < values[1] and values[2] < values[1]


def test_a_frame_that_will_not_load_is_not_movement(tmp_path):
    from flightdvr.motion import frame_differences
    broken = tmp_path / "f_0000.jpg"
    broken.write_bytes(b"not a jpeg")
    assert frame_differences([broken]) == []


def test_no_filmstrip_means_no_answer():
    from flightdvr.motion import activity
    from flightdvr.trim import Filmstrip
    assert activity(Filmstrip()) is None
    assert activity(Filmstrip([Path("a.jpg")], [0.0])) is None


# -- what the window is offered, and what it must never do --------------------

def test_nothing_applies_a_suggestion_on_its_own():
    """The rule the whole feature rests on, checked structurally rather than
    by reading. A wrong guess that silently trimmed footage would be far worse
    than no guess, so the only caller of `suggestion` outside this module must
    be the handler behind the button."""
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "flightdvr" / "ui.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    callers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "suggestion":
            for enclosing in ast.walk(tree):
                if (isinstance(enclosing, ast.FunctionDef)
                        and enclosing.lineno <= node.lineno <= enclosing.end_lineno):
                    callers.add(enclosing.name)
    assert callers <= {"_activity_ready", "_accept_activity"}, (
        f"a suggestion is read somewhere that could apply it: {callers}")


def test_a_reading_that_cannot_be_trusted_offers_nothing():
    found = Activity(duration=240.0, still=[], flying=[(10.0, 200.0)],
                     quietest=9.0, readable=False)
    assert found.suggestion is None
    assert "no reading" in found.describe(str)


def test_a_short_flight_is_not_worth_trimming_to():
    """Twenty seconds of flying then four minutes of grass is a clip to skip,
    not one to cut down — offering a trim would imply it is worth keeping."""
    found = Activity(duration=240.0, still=[(20.0, 240.0)],
                     flying=[(0.0, 20.0)], quietest=1.0)
    assert found.suggestion is None


def test_a_clip_that_flew_throughout_is_not_offered_a_trim():
    """There is nothing to cut, so an offer would be noise."""
    found = Activity(duration=240.0, still=[], flying=[(0.0, 240.0)],
                     quietest=1.0)
    assert found.suggestion is None


def test_the_longest_flight_is_what_gets_offered():
    """Not the first, and not the total: the number that decides whether a clip
    is worth opening is the longest continuous run."""
    found = Activity(duration=300.0,
                     still=[(40.0, 90.0), (250.0, 300.0)],
                     flying=[(0.0, 40.0), (90.0, 250.0)], quietest=1.0)
    assert found.suggestion == (90.0, 250.0)


def test_the_description_says_how_many_flights_when_there_are_several():
    found = Activity(duration=300.0, still=[(40.0, 90.0)],
                     flying=[(0.0, 40.0), (90.0, 250.0)], quietest=1.0)
    said = found.describe(lambda s: f"{s:.0f}s")
    assert "2 flights" in said
    assert "longest 160s" in said
