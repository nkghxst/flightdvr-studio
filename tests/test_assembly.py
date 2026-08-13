"""Stable range identity, which everything in #58 rests on.

An assembly is an ordered list of *ranges*, not of clips, so it needs a way to
say "this range" that survives the range being renamed, retrimmed, or having an
earlier sibling deleted. List position cannot do that: removing range 1 would
silently retarget an assembly item at range 2 to different footage, and the
export would be wrong in a way nobody could see until they watched it.

These tests state that contract before the assembly is built on it.
"""
import json

from flightdvr.media import Select
from flightdvr.session import SCHEMA, Session


def _schema_2_document() -> dict:
    """A session as 1.5 wrote them: ranges with no identity of their own."""
    return {
        "schema": 2,
        "title": "card",
        "source": "D:/movies",
        "join_order": ["fp-second", "fp-first"],
        "clips": {
            "fp-first": {
                "fingerprint": "fp-first",
                "name": "hdz_047.ts",
                "review": "keep",
                "selects": [
                    {"start": 1.0, "end": 4.0, "name": "gap"},
                    {"start": 9.0, "end": 12.0, "name": "tree"},
                ],
            },
            "fp-second": {
                "fingerprint": "fp-second",
                "name": "hdz_048.ts",
                "review": "",
                "selects": [{"start": 0.0, "end": 6.0, "name": ""}],
            },
        },
    }


def _load(tmp_path, document: dict) -> Session:
    path = tmp_path / "card.flightdvr"
    path.write_text(json.dumps(document), encoding="utf-8")
    return Session.load(path)


def test_migrating_a_1_5_session_gives_every_range_an_id_and_moves_nothing(tmp_path):
    session = _load(tmp_path, _schema_2_document())

    first = session.clips["fp-first"].selects
    second = session.clips["fp-second"].selects

    assert [s.sid for s in first] != ["", ""], "ranges must come back with ids"
    assert all(s.sid for s in first + second)
    assert len({s.sid for s in first + second}) == 3, "ids must not collide"

    # The migration assigns identity. It must not also reorder anything, or a
    # card reviewed last week would come back joined in a different order.
    assert [(s.start, s.end, s.name) for s in first] == [
        (1.0, 4.0, "gap"), (9.0, 12.0, "tree")]
    assert session.join_order == ["fp-second", "fp-first"]


def test_an_id_survives_renaming_and_retrimming_the_range(tmp_path):
    session = _load(tmp_path, _schema_2_document())
    ranges = session.clips["fp-first"].selects
    original = ranges[1].sid

    ranges[1].name = "tree dive, second attempt"
    ranges[1].start = 8.25
    ranges[1].end = 13.5

    assert ranges[1].sid == original


def test_a_new_range_cannot_reuse_the_id_of_a_deleted_one(tmp_path):
    """Position is not identity, and neither is "next free number"."""
    session = _load(tmp_path, _schema_2_document())
    marks = session.clips["fp-first"]
    retired = [s.sid for s in marks.selects]

    del marks.selects[0]
    marks.selects.append(Select(20.0, 24.0, "landing"))

    assert marks.selects[-1].sid not in retired


def test_ids_survive_a_save_and_reload(tmp_path):
    session = _load(tmp_path, _schema_2_document())
    before = [s.sid for s in session.clips["fp-first"].selects]

    path = tmp_path / "saved.flightdvr"
    session.save(path)
    again = Session.load(path)

    assert [s.sid for s in again.clips["fp-first"].selects] == before
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == SCHEMA


def test_an_id_survives_the_round_trip_through_a_scanned_clip(tmp_path):
    """The one that matters: reopening a card must not mint new identity.

    `apply_to` and `capture_from` rebuild Select objects rather than sharing
    them, deliberately, so that editing a trim cannot rewrite the stored one
    behind the session's back. Copying start, end and name alone would give
    every range a fresh id on every reopen, and an assembly saved yesterday
    would point at nothing today — while looking perfectly healthy.
    """
    from datetime import datetime

    from flightdvr.media import ClipInfo
    from flightdvr.session import apply_to, capture_from

    source = tmp_path / "hdz_047.ts"
    source.write_bytes(b"not really video")
    clip = ClipInfo(path=source, size=source.stat().st_size,
                    modified=datetime.fromtimestamp(source.stat().st_mtime),
                    duration=30.0, width=1280, height=720, fps=60.0)

    document = _schema_2_document()
    document["clips"][clip.fingerprint] = document["clips"].pop("fp-first")
    session = _load(tmp_path, document)
    stored = [s.sid for s in session.clips[clip.fingerprint].selects]
    assert len(stored) == 2

    apply_to(session, [clip])
    assert [s.sid for s in clip.selects] == stored, "reopening minted new ids"

    clip.selects[0].name = "renamed after reopening"
    capture_from(session, [clip])
    assert [s.sid for s in session.clips[clip.fingerprint].selects] == stored
