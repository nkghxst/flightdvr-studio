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

"""What a session promises: that the work survives closing the window.

No Qt and no ffmpeg here. A session is a document, and the things that can go
wrong with it — losing the decisions, keeping the wrong ones, half-writing the
file — are all reachable without either.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from flightdvr.media import ClipInfo
from flightdvr.session import (
    KEEP, MAYBE, REJECT, SCHEMA, UNREVIEWED, ClipMarks, Select, Session,
)


def clip(name="hdz_022.ts", size=599_189_652, modified=None) -> ClipInfo:
    return ClipInfo(
        path=Path(name), size=size,
        modified=modified or datetime(2025, 10, 8, 18, 39),
        duration=212.7, width=1280, height=720, fps=60.0,
        video_codec="hevc", audio_codec="aac", color_range="pc",
    )


# -- what identifies a recording ----------------------------------------------

def test_the_same_file_is_the_same_recording():
    assert clip().fingerprint == clip().fingerprint


def test_a_rewritten_card_is_not_the_same_recording():
    """The whole reason the fingerprint is not the filename. Cards get reused,
    and the counter starts again at hdz_000 — attaching last week's trim points
    to this week's footage would be worse than remembering nothing."""
    original = clip(size=599_189_652, modified=datetime(2025, 10, 8, 18, 39))
    rewritten = clip(size=412_004_811, modified=datetime(2026, 8, 6, 11, 20))
    assert original.path == rewritten.path
    assert original.fingerprint != rewritten.fingerprint


def test_a_file_edited_in_place_is_not_the_same_recording():
    """Same name, same size, different modification time."""
    before = clip(modified=datetime(2025, 10, 8, 18, 39))
    after = clip(modified=datetime(2025, 10, 8, 19, 2))
    assert before.fingerprint != after.fingerprint


def test_the_filmstrip_cache_uses_the_same_identity():
    """One idea of what a recording is, not two that can disagree."""
    from flightdvr.trim import _key
    assert _key(clip()) != _key(clip(size=1))


# -- keeping what was decided --------------------------------------------------

def test_a_session_round_trips(tmp_path):
    session = Session(title="Hampstead Heath", source=r"G:\movies")
    marks = session.marks(clip().fingerprint, name="hdz_022.ts")
    marks.selects = [Select(45.0, 150.5, "tree dive"), Select(180.0, 200.0)]
    marks.review = KEEP
    marks.note = "the one worth posting"
    marks.exported = [r"D:\FPV\Exports\hdz_022_master.mp4"]

    written = session.save(tmp_path / f"heath{'.flightdvr.json'}")
    read = Session.load(written)

    assert read.title == "Hampstead Heath"
    assert read.source == r"G:\movies"
    restored = read.marks(clip().fingerprint)
    assert restored.review == KEEP
    assert restored.note == "the one worth posting"
    assert restored.exported == [r"D:\FPV\Exports\hdz_022_master.mp4"]
    assert [(s.start, s.end, s.name) for s in restored.selects] == [
        (45.0, 150.5, "tree dive"), (180.0, 200.0, ""),
    ]


def test_a_session_reopened_on_a_rewritten_card_offers_nothing(tmp_path):
    """The promise the fingerprint makes, end to end."""
    session = Session(source=r"G:\movies")
    session.marks(clip().fingerprint, "hdz_022.ts").review = KEEP
    read = Session.load(session.save(tmp_path / "s.flightdvr.json"))

    rewritten = clip(size=412_004_811, modified=datetime(2026, 8, 6))
    assert read.marks(rewritten.fingerprint).review == UNREVIEWED


def test_clips_nobody_decided_anything_about_are_not_stored(tmp_path):
    """Walking a 122-clip card should not write 122 empty records."""
    session = Session()
    for n in range(122):
        session.marks(clip(f"hdz_{n:03d}.ts").fingerprint, f"hdz_{n:03d}.ts")
    session.marks(clip("hdz_007.ts").fingerprint).review = MAYBE

    stored = json.loads(session.save(tmp_path / "s.flightdvr.json").read_text())
    assert len(stored["clips"]) == 1


def test_the_review_count_is_what_was_reviewed():
    session = Session()
    for state in (KEEP, MAYBE, REJECT, UNREVIEWED):
        session.marks(f"fp-{state or 'none'}").review = state
    assert session.reviewed_count() == 3


# -- surviving things going wrong ----------------------------------------------

def test_writing_is_atomic(tmp_path):
    """A session is written after every change, so a crash lands in the middle
    of one sooner or later. A half-written file is worse than a stale one."""
    target = tmp_path / "s.flightdvr.json"
    session = Session(title="first")
    session.save(target)

    session.title = "second"
    session.save(target)

    assert json.loads(target.read_text())["title"] == "second"
    assert not list(tmp_path.glob("*.part")), "a temporary was left behind"


def test_an_unreadable_session_opens_empty_rather_than_raising(tmp_path):
    """Losing the decisions is bad. Refusing to open the app because of them
    would be worse."""
    broken = tmp_path / "broken.flightdvr.json"
    broken.write_text("{not json at all")
    session = Session.load(broken)
    assert session.clips == {}
    assert session.path == broken


def test_a_missing_session_opens_empty(tmp_path):
    session = Session.load(tmp_path / "never-existed.flightdvr.json")
    assert session.clips == {}


def test_a_session_from_a_file_that_is_not_a_session(tmp_path):
    odd = tmp_path / "list.flightdvr.json"
    odd.write_text("[1, 2, 3]")
    assert Session.load(odd).clips == {}


# -- the format changing under it ----------------------------------------------

def test_the_schema_version_is_written(tmp_path):
    session = Session()
    stored = json.loads(session.save(tmp_path / "s.flightdvr.json").read_text())
    assert stored["schema"] == SCHEMA


def test_a_session_with_no_schema_still_reads(tmp_path):
    """Anything written before the numbering existed."""
    early = tmp_path / "early.flightdvr.json"
    early.write_text(json.dumps({
        "title": "before schemas",
        "clips": {"abc123": {"name": "hdz_001.ts", "review": "keep"}},
    }))
    session = Session.load(early)
    assert session.title == "before schemas"
    assert session.marks("abc123").review == KEEP


def test_a_session_from_a_newer_version_reads_what_it_can(tmp_path):
    """Refusing to open would lose everything; the fields this version knows
    are still the fields this version knows."""
    later = tmp_path / "later.flightdvr.json"
    later.write_text(json.dumps({
        "schema": SCHEMA + 5,
        "title": "from the future",
        "clips": {"abc": {"name": "hdz_001.ts", "review": "keep",
                          "selects": [{"start": 1.0, "end": 2.0}],
                          "mood": "triumphant"}},
    }))
    session = Session.load(later)
    assert session.title == "from the future"
    assert session.marks("abc").selects[0].end == 2.0


def test_a_review_state_this_version_does_not_know_reads_as_unreviewed(tmp_path):
    odd = tmp_path / "odd.flightdvr.json"
    odd.write_text(json.dumps({
        "schema": SCHEMA,
        "clips": {"abc": {"review": "brilliant"}},
    }))
    assert Session.load(odd).marks("abc").review == UNREVIEWED


# -- selects -------------------------------------------------------------------

def test_a_select_knows_how_long_it_is():
    assert Select(45.0, 150.5).duration == pytest.approx(105.5)


def test_a_backwards_select_has_no_duration_rather_than_a_negative_one():
    assert Select(150.0, 45.0).duration == 0.0


def test_marks_with_nothing_in_them_are_falsey():
    assert not ClipMarks("abc")
    assert ClipMarks("abc", review=KEEP)
    assert ClipMarks("abc", selects=[Select(0, 1)])
    assert ClipMarks("abc", note="hm")
