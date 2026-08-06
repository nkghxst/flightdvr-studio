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


# -- getting the work back onto the clips ---------------------------------------

def test_remembered_trims_come_back_onto_the_clips():
    from flightdvr.session import apply_to

    session = Session()
    session.marks(clip().fingerprint).selects = [Select(45.0, 150.5)]

    found = clip()
    assert apply_to(session, [found]) == 1
    assert found.trim_in == pytest.approx(45.0)
    assert found.trim_out == pytest.approx(150.5)


def test_a_clip_nothing_was_decided_about_is_left_alone():
    from flightdvr.session import apply_to

    found = clip()
    assert apply_to(Session(), [found]) == 0
    assert found.trim_in == 0.0 and found.trim_out == 0.0


def test_trims_are_recorded_back_into_the_session():
    from flightdvr.session import capture_from

    session = Session()
    trimmed = clip()
    trimmed.trim_in, trimmed.trim_out = 45.0, 150.5
    capture_from(session, [trimmed])

    kept = session.marks(trimmed.fingerprint).selects
    assert [(s.start, s.end) for s in kept] == [(45.0, 150.5)]


def test_resetting_a_clip_clears_the_mark_rather_than_storing_the_whole_thing():
    """Otherwise Reset leaves behind a range covering everything, which looks
    like a decision somebody made rather than the absence of one."""
    from flightdvr.session import capture_from

    session = Session()
    marked = clip()
    marked.trim_in, marked.trim_out = 45.0, 150.5
    capture_from(session, [marked])

    marked.trim_in = marked.trim_out = 0.0
    capture_from(session, [marked])
    assert session.marks(marked.fingerprint).selects == []


def test_capturing_a_trim_leaves_the_other_selects_alone():
    """Until #15 only the first is editable; the rest must survive a round trip
    through a version that cannot see them."""
    from flightdvr.session import capture_from

    session = Session()
    edited = clip()
    session.marks(edited.fingerprint).selects = [
        Select(1.0, 2.0), Select(60.0, 70.0, "tree dive"),
    ]
    edited.trim_in, edited.trim_out = 45.0, 150.5
    capture_from(session, [edited])

    kept = session.marks(edited.fingerprint).selects
    assert [(s.start, s.end, s.name) for s in kept] == [
        (45.0, 150.5, ""), (60.0, 70.0, "tree dive"),
    ]


# -- footage that is not there any more -----------------------------------------

def test_marked_clips_missing_from_a_scan_are_reported():
    """Either the file moved or the card was rewritten. The fingerprint cannot
    tell those apart and does not need to — what matters is being able to say
    "nine clips you marked are not in this folder" rather than dropping the
    work silently, which is what happens when nobody looks."""
    from flightdvr.session import missing_from

    session = Session()
    session.marks("here", "hdz_001.ts").review = KEEP
    session.marks("gone", "hdz_002.ts").review = MAYBE
    session.marks("untouched", "hdz_003.ts")        # nothing decided

    absent = missing_from(session, present={"here"})
    assert [m.name for m in absent] == ["hdz_002.ts"]


def test_nothing_is_reported_missing_when_everything_is_there():
    from flightdvr.session import missing_from
    session = Session()
    session.marks("a").review = KEEP
    assert missing_from(session, present={"a", "b"}) == []


# -- one session per source folder ----------------------------------------------

def test_each_source_folder_gets_its_own_session(tmp_path, monkeypatch):
    """Review a card, then another, then come back to the first: the first
    one's work has to still be there."""
    from flightdvr import session as module
    monkeypatch.setattr(module, "sessions_dir", lambda: tmp_path)

    assert module.autosave_path(r"G:\movies") != module.autosave_path(r"H:\movies")


def test_the_same_folder_gets_the_same_session_however_it_is_spelled(tmp_path,
                                                                     monkeypatch):
    """Windows and macOS treat these as one folder, so the app must too."""
    from flightdvr import session as module
    monkeypatch.setattr(module, "sessions_dir", lambda: tmp_path)

    assert module.autosave_path(r"G:\Movies") == module.autosave_path(r"g:\movies")


def test_reopening_a_source_finds_the_work_left_there(tmp_path, monkeypatch):
    """The whole point, and also the crash recovery: the autosave on disk is
    always the last complete state, so there is nothing separate to recover."""
    from flightdvr import session as module
    monkeypatch.setattr(module, "sessions_dir", lambda: tmp_path)

    first = module.for_source(r"G:\movies")
    first.marks("fp1", "hdz_001.ts").review = KEEP
    first.save()

    again = module.for_source(r"G:\movies")
    assert again.marks("fp1").review == KEEP
    assert again.source == r"G:\movies"


def test_an_untouched_source_starts_a_new_session(tmp_path, monkeypatch):
    from flightdvr import session as module
    monkeypatch.setattr(module, "sessions_dir", lambda: tmp_path)

    fresh = module.for_source(tmp_path / "some card")
    assert fresh.clips == {}
    assert fresh.title == "some card"


# -- the recent list ------------------------------------------------------------

def test_recent_sessions_are_most_recent_first(tmp_path, monkeypatch):
    from flightdvr import session as module
    monkeypatch.setattr(module, "sessions_dir", lambda: tmp_path)

    for name in ("one", "two", "three"):
        s = Session(title=name, path=tmp_path / f"{name}{'.flightdvr.json'}")
        module.remember(s)

    assert [r.title for r in module.recent_sessions()] == ["three", "two", "one"]


def test_reopening_a_session_moves_it_up_rather_than_repeating_it(tmp_path,
                                                                  monkeypatch):
    from flightdvr import session as module
    monkeypatch.setattr(module, "sessions_dir", lambda: tmp_path)

    a = Session(title="a", path=tmp_path / "a.flightdvr.json")
    b = Session(title="b", path=tmp_path / "b.flightdvr.json")
    module.remember(a)
    module.remember(b)
    module.remember(a)

    titles = [r.title for r in module.recent_sessions()]
    assert titles == ["a", "b"]


def test_the_recent_list_does_not_grow_without_limit(tmp_path, monkeypatch):
    from flightdvr import session as module
    monkeypatch.setattr(module, "sessions_dir", lambda: tmp_path)

    for n in range(module.RECENT_LIMIT + 6):
        module.remember(Session(title=f"s{n}",
                                path=tmp_path / f"s{n}{'.flightdvr.json'}"))

    assert len(module.recent_sessions()) == module.RECENT_LIMIT


def test_an_unreadable_recent_list_is_simply_empty(tmp_path, monkeypatch):
    """It is a convenience. Losing it should cost nothing at all."""
    from flightdvr import session as module
    monkeypatch.setattr(module, "sessions_dir", lambda: tmp_path)
    (tmp_path / "recent.json").write_text("{{{ not json")
    assert module.recent_sessions() == []


def test_a_recent_entry_labels_itself_from_whatever_it_has(tmp_path):
    from flightdvr.session import Recent
    assert Recent(path="x", title="Hampstead Heath").label == "Hampstead Heath"
    assert Recent(path="x", source=r"G:\movies").label == "movies"
    assert Recent(path=str(tmp_path / "card.flightdvr.json")).label.startswith("card")
