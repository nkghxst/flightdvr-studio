"""Stable range identity, which everything in #58 rests on.

An assembly is an ordered list of *ranges*, not of clips, so it needs a way to
say "this range" that survives the range being renamed, retrimmed, or having an
earlier sibling deleted. List position cannot do that: removing range 1 would
silently retarget an assembly item at range 2 to different footage, and the
export would be wrong in a way nobody could see until they watched it.

These tests state that contract before the assembly is built on it.
"""
import json
import tempfile

import pytest

from flightdvr.assembly_panel import ITEM_ROLE
from pathlib import Path as _Path

_TMP = [_Path(tempfile.mkdtemp())]


@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])

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


# --- the ordered list itself -------------------------------------------------

def _clip(tmp_path, name, ranges, sequence_size=1):
    from datetime import datetime

    from flightdvr.media import ClipInfo

    source = tmp_path / name
    source.write_bytes(b"x" * sequence_size)
    clip = ClipInfo(path=source, size=source.stat().st_size,
                    modified=datetime.fromtimestamp(source.stat().st_mtime),
                    duration=60.0, width=1280, height=720, fps=60.0)
    clip.selects = [Select(start, end, label) for start, end, label in ranges]
    return clip


def test_the_default_order_is_counter_then_along_each_recording(tmp_path):
    from flightdvr.assembly import absent, default_items, resolve

    second = _clip(tmp_path, "hdz_048.ts", [(5.0, 9.0, "late"), (1.0, 3.0, "early")])
    first = _clip(tmp_path, "hdz_047.ts", [(0.0, 2.0, "")], sequence_size=2)

    rows = resolve(default_items([second, first]), [second, first])

    assert not absent(rows)
    assert [(r.clip.path.name, r.select.name) for r in rows] == [
        ("hdz_047.ts", ""),
        ("hdz_048.ts", "early"),
        ("hdz_048.ts", "late"),
    ]


def test_an_assembly_survives_deleting_an_earlier_range_of_the_same_clip(tmp_path):
    """The defect the whole design exists to prevent.

    With positions as identity, removing range 1 would leave the item that
    named range 2 pointing at range 3 — an export that succeeds, plays, and
    contains footage nobody chose.
    """
    from flightdvr.assembly import Item, absent, present, resolve

    clip = _clip(tmp_path, "hdz_047.ts",
                 [(0.0, 2.0, "one"), (4.0, 6.0, "two"), (8.0, 10.0, "three")])
    wanted = Item(clip.fingerprint, clip.selects[2].sid)

    del clip.selects[0]
    rows = resolve([wanted], [clip])

    assert not absent(rows)
    assert rows[0].select.name == "three"


def test_material_that_has_gone_is_reported_rather_than_dropped(tmp_path):
    from flightdvr.assembly import Item, absent, present, resolve

    clip = _clip(tmp_path, "hdz_047.ts", [(0.0, 2.0, "kept")])
    deleted_range = Item(clip.fingerprint, "a-range-that-was-removed")
    absent_clip = Item("fp-not-on-this-card", "whatever")

    rows = resolve(
        [Item(clip.fingerprint, clip.selects[0].sid), deleted_range, absent_clip],
        [clip], names={"fp-not-on-this-card": "hdz_099.ts"})

    assert [r.select.name for r in present(rows)] == ["kept"]
    assert [r.item for r in absent(rows)] == [deleted_range, absent_clip]
    assert "hdz_099.ts" in rows[2].label()


def test_the_assembly_order_comes_back_after_a_save_and_reload(tmp_path):
    from flightdvr.assembly import Item, absent, present, resolve
    from flightdvr.session import Session

    clip = _clip(tmp_path, "hdz_047.ts", [(0.0, 2.0, "one"), (4.0, 6.0, "two")])
    chosen = [Item(clip.fingerprint, clip.selects[1].sid),
              Item(clip.fingerprint, clip.selects[0].sid)]

    session = Session(title="card", source=str(tmp_path))
    session.marks(clip.fingerprint, clip.path.name).selects = list(clip.selects)
    session.assembly = list(chosen)
    path = session.save(tmp_path / "card.flightdvr")

    again = Session.load(path)
    rows = resolve(again.assembly, [clip])

    assert not absent(rows)
    assert [r.select.name for r in rows] == ["two", "one"], "order not restored"


# --- the four blockers Sol found ---------------------------------------------

def test_a_gap_between_two_items_keeps_its_place_through_the_panel(qt_app):
    """Sol's finding 2, pinned across the whole round trip.

    The panel drew resolved rows and then missing ones, so an interleaved gap
    sank to the bottom — and `items()` reads the displayed order, which is what
    gets persisted. Opening a card with a gap in the middle silently rewrote
    the order it was stored in, and the assembly still looked healthy.
    """
    from flightdvr.assembly import Item, resolve
    from flightdvr.assembly_panel import AssemblyPanel

    here = _clip(_TMP[0], "hdz_047.ts", [(0.0, 5.0, "one")])
    there = _clip(_TMP[0], "hdz_048.ts", [(0.0, 5.0, "three")])
    stored = [Item(here.fingerprint, here.selects[0].sid),
              Item("fp-gone", "a-range-that-went"),
              Item(there.fingerprint, there.selects[0].sid)]

    panel = AssemblyPanel()
    panel.show_rows(resolve(stored, [here, there], names={"fp-gone": "hdz_099.ts"}))

    assert "missing" in panel.list.item(1).text(), [
        panel.list.item(n).text() for n in range(panel.list.count())]
    assert panel.items() == stored, "the panel rewrote the stored order"

    # And it still holds after an ordinary edit, which is what gets saved.
    panel.list.item(2).setSelected(True)
    panel._move(-1)
    assert [i.sid for i in panel.items()] == [
        stored[0].sid, stored[2].sid, stored[1].sid]


def test_a_whole_recording_row_still_exports_after_the_clip_gains_a_range(tmp_path):
    """Sol's finding 3. `for_export()` expands a clip into its selects, so a
    row naming the whole recording vanished the moment somebody trimmed it."""
    from flightdvr.assembly import Item, export_piece, resolve

    clip = _clip(tmp_path, "hdz_047.ts", [])
    whole = Item(clip.fingerprint)

    rows = resolve([whole], [clip])
    assert not rows[0].missing
    assert export_piece(rows[0]).selects == []

    clip.selects = [Select(4.0, 9.0, "added later")]
    rows = resolve([whole], [clip])
    assert not rows[0].missing, "the whole-recording row lost its material"
    piece = export_piece(rows[0])
    assert piece.selects == [], "a whole-recording row exported only a range"


def test_export_piece_does_not_share_the_select_with_the_clip(tmp_path):
    """The copying discipline per_select_clips has, kept here.

    A queued job holds its clip until it runs, so a shared range meant editing
    a trim afterwards silently changed an export already waiting in the queue.
    """
    from flightdvr.assembly import Item, export_piece, resolve

    clip = _clip(tmp_path, "hdz_047.ts", [(1.0, 4.0, "one")])
    piece = export_piece(resolve([Item(clip.fingerprint, clip.selects[0].sid)],
                                 [clip])[0])

    clip.selects[0].start = 99.0
    assert piece.selects[0].start == 1.0, "the queued piece followed a later edit"
    assert piece.selects[0].sid == clip.selects[0].sid


# --- #77: drag to reorder ----------------------------------------------------

def _panel_with(rows_spec, qt_app):
    """A panel holding present rows, and a missing one where asked."""
    from flightdvr.assembly import Item, resolve
    from flightdvr.assembly_panel import AssemblyPanel

    here = _clip(_TMP[0], "hdz_047.ts",
                 [(0.0, 5.0, "one"), (6.0, 9.0, "two"), (10.0, 14.0, "three")])
    stored = []
    for spec in rows_spec:
        if spec == "gap":
            stored.append(Item("fp-gone", "a-range-that-went"))
        else:
            stored.append(Item(here.fingerprint, here.selects[spec].sid))
    panel = AssemblyPanel()
    panel.show_rows(resolve(stored, [here], names={"fp-gone": "hdz_099.ts"}))
    return panel, stored


def _through_qt_mime(panel, rows):
    """Encode rows the way a drag does, and decode them into an empty list.

    This is the step that carries the risk and the only one worth simulating.
    Hand-rolling a whole move would test the arithmetic in this helper rather
    than anything in the application — `dropMimeData` on a list overwrites
    rather than inserts, which is exactly the sort of detail a fake gets
    wrong. Whether Qt accepts the drop event needs a live drag source and is
    checked natively instead.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidget

    model = panel.list.model()
    mime = model.mimeData([model.index(r, 0) for r in rows])

    landing = QListWidget()
    landing.model().dropMimeData(mime, Qt.DropAction.CopyAction, 0, 0,
                                 landing.rootIndex())
    return [landing.item(r).data(ITEM_ROLE) for r in range(landing.count())]


def test_a_dragged_row_keeps_the_exact_item_it_pointed_at(qt_app):
    """The whole risk of this feature.

    A drag round-trips each row through Qt's mime encoding. If the stored
    reference does not survive, the row still reads `hdz_047.ts · one` while
    pointing at nothing, and nobody finds out until the export.
    """
    panel, stored = _panel_with([0, 1, 2], qt_app)

    carried = _through_qt_mime(panel, [2])

    assert carried == [stored[2]], carried
    assert carried[0].sid == stored[2].sid
    assert carried[0].fingerprint == stored[2].fingerprint


def test_a_multi_row_drag_carries_every_reference_in_order(qt_app):
    panel, stored = _panel_with([0, 1, 2], qt_app)

    carried = _through_qt_mime(panel, [1, 2])

    assert carried == [stored[1], stored[2]], carried
    assert len({i.sid for i in carried}) == 2, "a row was duplicated"


def test_a_completed_drop_reports_the_order_once(qt_app):
    """The wiring we own: one finished drop, one order_changed.

    Qt moves list rows by inserting and then removing, so `rowsMoved` never
    fires and `rowsInserted`/`rowsRemoved` would report one drag twice — with
    a half-finished order visible in between.
    """
    panel, _ = _panel_with([0, 1, 2], qt_app)
    changes = []
    panel.order_changed.connect(lambda: changes.append(panel.items()))

    panel.list.dropped.emit()

    assert len(changes) == 1, f"order_changed fired {len(changes)} times"
    assert len(changes[0]) == 3


def test_a_missing_row_cannot_be_dragged_or_carried(qt_app):
    """Clearing ItemIsEnabled leaves ItemIsDragEnabled set, so a gap could be
    picked up and moved away from the position that is the only remaining
    evidence of where it belonged."""
    from PySide6.QtCore import Qt

    panel, stored = _panel_with([0, "gap", 1], qt_app)
    gap = panel.list.item(1)

    assert not (gap.flags() & Qt.ItemFlag.ItemIsDragEnabled)
    assert not (gap.flags() & Qt.ItemFlag.ItemIsEnabled)
    # And it still holds the reference it always did, in its stored place.
    assert panel.items()[1] == stored[1]


def test_the_list_offers_internal_move_without_losing_the_keyboard(qt_app):
    from PySide6.QtWidgets import QAbstractItemView

    panel, stored = _panel_with([0, 1, 2], qt_app)

    assert panel.list.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
    # The accessible route still works exactly as it did.
    panel.list.item(2).setSelected(True)
    panel._move(-1)
    assert [i.sid for i in panel.items()] == [
        stored[0].sid, stored[2].sid, stored[1].sid]
