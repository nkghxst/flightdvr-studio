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

"""The session as the window uses it: mark a card, close it, find the work.

`test_session.py` covers the document. This covers the wiring, which is where
the promise is actually kept or broken — a session that round-trips perfectly
through JSON is worth nothing if nothing ever calls save, or if the scan that
follows loads over the top of it.

Every window here is built and closed for real, because the questions are about
what happens across a close.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from flightdvr import session as session_module
from flightdvr.media import ClipInfo


def a_clip(name: str, size: int = 599_189_652) -> ClipInfo:
    return ClipInfo(
        path=Path(name), size=size,
        modified=datetime(2025, 10, 8, 18, 39),
        duration=212.7, width=1280, height=720, fps=60.0,
        video_codec="hevc", audio_codec="aac",
        pix_fmt="yuvj420p", color_range="pc",
    )


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def sessions_home(tmp_path, monkeypatch):
    """Sessions live under the user's home. Point that somewhere disposable so
    a test run cannot read or write the real one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


@pytest.fixture
def card(tmp_path):
    """A folder standing in for a card. Only its path is used — the clips are
    handed to the window directly, so no media has to exist."""
    folder = tmp_path / "card"
    folder.mkdir()
    return folder


def open_window(app, card, clips):
    """A window looking at `card`, with `clips` already listed.

    This drives the real path — _add_clip then _scan_done — rather than setting
    self.clips, because loading the session is something _scan_done does and a
    shortcut would skip the thing under test.
    """
    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow

    window = MainWindow(find_tools())
    window.source_combo.insertItem(0, str(card), str(card))
    window.source_combo.setCurrentIndex(0)
    for clip in clips:
        window._add_clip(window._scan_generation, clip)
    window._scan_done(window._scan_generation, len(clips))
    app.processEvents()
    return window


def close(window):
    window.close()


# -- the promise --------------------------------------------------------------

def test_a_trim_survives_closing_the_window(app, sessions_home, card):
    """The whole point. Mark a clip, close, reopen the same folder, find it."""
    first = open_window(app, card, [a_clip("hdz_001.ts"), a_clip("hdz_002.ts")])
    first.clips[0].trim_in = 12.0
    first.clips[0].trim_out = 30.0
    first._touch_session()
    close(first)                                  # flushes on the way out

    again = open_window(app, card, [a_clip("hdz_001.ts"), a_clip("hdz_002.ts")])
    assert again.clips[0].trim_in == pytest.approx(12.0)
    assert again.clips[0].trim_out == pytest.approx(30.0)
    assert again.table.item(0, 5).text() == "U\n1 range"
    assert again.table.item(0, 5).toolTip() == (
        "Unreviewed · 1 saved range")
    assert not again.clips[1].is_trimmed, "a clip nobody marked came back marked"
    close(again)


def test_review_state_and_progress_survive_closing_the_window(app,
                                                              sessions_home,
                                                              card):
    """The state in the document matters only if the browser restores it and
    counts it when the card is opened again."""
    first = open_window(app, card, [a_clip("hdz_001.ts"), a_clip("hdz_002.ts")])
    first.table.setCurrentCell(0, 0)
    first.table.selectRow(0)
    first._set_review(session_module.MAYBE)
    close(first)

    again = open_window(app, card, [a_clip("hdz_001.ts"), a_clip("hdz_002.ts")])
    try:
        assert again.clips[0].review == session_module.MAYBE
        assert again.clips[1].review == session_module.UNREVIEWED
        assert again.browser_panel.review_count_label.text() == "1 of 2 reviewed"

        review_item = again.table.item(0, 5)
        assert review_item.text() == "M"
        assert review_item.toolTip() == "Maybe"
        again.browser_panel.review_filter.setCurrentIndex(
            again.browser_panel.review_filter.findData(session_module.MAYBE))
        assert not again.table.isRowHidden(0)
        assert again.table.isRowHidden(1)
    finally:
        close(again)


def test_resetting_a_trim_is_remembered_as_a_decision(app, sessions_home, card):
    """The subtle one. Clearing a trim has to be stored as cleared, or the next
    visit puts back the trim that was just removed."""
    first = open_window(app, card, [a_clip("hdz_001.ts")])
    first.clips[0].trim_in, first.clips[0].trim_out = 12.0, 30.0
    first._touch_session()
    close(first)

    second = open_window(app, card, [a_clip("hdz_001.ts")])
    assert second.clips[0].is_trimmed                     # as established above
    second._trim_clip = second.clips[0]
    second.trim_bar.set_clip(second.clips[0].duration, 0.0,
                             second.clips[0].duration)
    second._reset_trim()
    close(second)

    third = open_window(app, card, [a_clip("hdz_001.ts")])
    assert not third.clips[0].is_trimmed, "the reset was forgotten"
    close(third)


def test_another_folder_does_not_inherit_this_ones_marks(app, sessions_home,
                                                         card, tmp_path):
    """One session per source, so reviewing a second card starts clean."""
    first = open_window(app, card, [a_clip("hdz_001.ts")])
    first.clips[0].trim_in, first.clips[0].trim_out = 5.0, 20.0
    first._touch_session()
    close(first)

    elsewhere = tmp_path / "other-card"
    elsewhere.mkdir()
    other = open_window(app, elsewhere, [a_clip("hdz_001.ts")])
    assert not other.clips[0].is_trimmed
    close(other)


def test_a_rewritten_card_inherits_nothing(app, sessions_home, card):
    """DVR counters wrap, so the same filename comes round again on footage
    that has nothing to do with the old marks. Identity is the fingerprint."""
    first = open_window(app, card, [a_clip("hdz_001.ts")])
    first.clips[0].trim_in, first.clips[0].trim_out = 5.0, 20.0
    first._touch_session()
    close(first)

    rewritten = a_clip("hdz_001.ts", size=123_456_789)     # same name, new file
    again = open_window(app, card, [rewritten])
    assert not again.clips[0].is_trimmed
    close(again)


def test_clips_that_have_gone_are_named_not_counted(app, sessions_home, card):
    """"Nine clips are missing" is a puzzle. The names are why the session
    stores them alongside the fingerprint."""
    first = open_window(app, card, [a_clip("hdz_001.ts"), a_clip("hdz_002.ts")])
    for clip in first.clips:
        clip.trim_in, clip.trim_out = 3.0, 40.0
    first._touch_session()
    close(first)

    again = open_window(app, card, [a_clip("hdz_002.ts")])   # 001 is gone
    said = again.statusBar().currentMessage()
    assert "hdz_001.ts" in said, said
    assert "not in this folder" in said, said
    close(again)


# -- writing behaviour --------------------------------------------------------

def test_dragging_a_handle_does_not_write_a_file_per_frame(app, sessions_home,
                                                           card):
    """Trims arrive continuously while a handle is dragged. The debounce is the
    difference between one write and several hundred."""
    window = open_window(app, card, [a_clip("hdz_001.ts")])
    window._trim_clip = window.clips[0]
    window.trim_bar.set_clip(window.clips[0].duration, 0.0,
                             window.clips[0].duration)

    for tenth in range(30):
        window._on_trim_changed(tenth / 10.0, 200.0)
    assert window.session is not None
    assert not window.session.path.exists(), "wrote while the drag was going on"

    close(window)                                  # the flush on the way out
    assert window.session.path.exists(), "never wrote at all"


def test_the_work_is_on_disk_before_the_window_closes(app, sessions_home, card):
    """What the debounce timer is actually for.

    Closing captures everything anyway — the session is written from the clips
    as they stand, so a close records the final state whether or not anything
    asked it to. The timer exists for the close that never comes: a crash, a
    flat battery, a task manager. So this asserts the file appears while the
    window is still open, and never closes it.
    """
    window = open_window(app, card, [a_clip("hdz_001.ts")])
    window._trim_clip = window.clips[0]
    window.trim_bar.set_clip(window.clips[0].duration, 0.0,
                             window.clips[0].duration)
    window._session_timer.setInterval(10)          # the wait, not the logic

    window._on_trim_changed(12.0, 30.0)
    assert window._session_timer.isActive(), "nothing was scheduled"

    deadline = time.monotonic() + 5.0
    while not window.session.path.exists() and time.monotonic() < deadline:
        app.processEvents()

    assert window.session.path.exists(), (
        "the trim never reached disk without closing the window")
    stored = json.loads(window.session.path.read_text(encoding="utf-8"))
    only = next(iter(stored["clips"].values()))
    assert only["selects"][0]["start"] == pytest.approx(12.0)


def test_an_unwritable_session_says_so_and_carries_on(app, sessions_home, card,
                                                      monkeypatch):
    """A full disk or a read-only home must not take the window down, and must
    not be a dialog either — this can fire repeatedly during a review."""
    window = open_window(app, card, [a_clip("hdz_001.ts")])
    window.clips[0].trim_in, window.clips[0].trim_out = 1.0, 2.0

    def refuse(*_args, **_kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(type(window.session), "save", refuse)
    window._write_session()                        # must not raise
    assert "Could not save" in window.statusBar().currentMessage()
    close(window)


# -- naming and reopening -----------------------------------------------------

def test_saving_under_a_name_keeps_the_marks(app, sessions_home, card,
                                             tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from flightdvr.session import SUFFIX, Session

    window = open_window(app, card, [a_clip("hdz_001.ts")])
    window.clips[0].trim_in, window.clips[0].trim_out = 8.0, 44.0
    window._touch_session()

    named = tmp_path / f"hampstead-heath{SUFFIX}"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(named), "")))
    window._save_session_as()
    close(window)

    assert named.exists()
    read = Session.load(named)
    assert read.title == "hampstead-heath"
    assert len(read.clips) == 1
    only = next(iter(read.clips.values()))
    assert only.selects[0].start == pytest.approx(8.0)


def test_a_folder_nobody_marked_is_not_offered_as_recent(app, sessions_home,
                                                         card):
    """A session exists for every folder that gets scanned, but until something
    is decided there is no file — and a recent entry pointing at nothing is a
    door that opens onto a blank."""
    window = open_window(app, card, [a_clip("hdz_001.ts")])
    window._fill_recent_menu()
    assert [a.text() for a in window.recent_menu.actions()] == ["Nothing yet"]
    close(window)


def test_the_recent_menu_lists_a_card_once_it_has_been_marked(app,
                                                              sessions_home,
                                                              card):
    window = open_window(app, card, [a_clip("hdz_001.ts")])
    window.clips[0].trim_in, window.clips[0].trim_out = 4.0, 9.0
    window._touch_session()
    window._flush_session()

    window._fill_recent_menu()
    labels = [a.text() for a in window.recent_menu.actions()]
    assert labels != ["Nothing yet"], labels
    assert any(card.name in text for text in labels), labels
    close(window)


def test_opening_a_session_does_not_survive_the_scan_that_follows(
        app, sessions_home, card, tmp_path, monkeypatch):
    """Opening a named session then scanning its folder must apply the file
    that was opened, not the folder's own autosave."""
    from PySide6.QtWidgets import QMessageBox
    from flightdvr.session import SUFFIX, Session

    # An autosave for the card, saying one thing.
    stale = open_window(app, card, [a_clip("hdz_001.ts")])
    stale.clips[0].trim_in, stale.clips[0].trim_out = 1.0, 2.0
    stale._touch_session()
    close(stale)

    # A named session for the same card, saying another.
    named_session = session_module.for_source(card)
    marks = named_session.marks(a_clip("hdz_001.ts").fingerprint, "hdz_001.ts")
    marks.selects = [session_module.Select(60.0, 90.0)]
    named_session.title = "the good one"
    named = named_session.save(tmp_path / f"good{SUFFIX}")

    window = open_window(app, card, [a_clip("hdz_001.ts")])
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k:
                                     QMessageBox.StandardButton.Yes))
    window._open_session_file(Path(named))
    # The scan is asynchronous; drive the completion the way it arrives.
    for clip in [a_clip("hdz_001.ts")]:
        window._add_clip(window._scan_generation, clip)
    window._scan_done(window._scan_generation, 1)
    app.processEvents()

    assert window.clips[0].trim_in == pytest.approx(60.0), (
        "the folder's autosave loaded over the session that was opened")
    close(window)


# -- what Codex's review of #22 found -----------------------------------------

def test_scanning_resumes_the_thumbnail_loader(app, sessions_home, card):
    """_scan pauses the loader and _scan_done resumes it. A method definition
    got inserted between them, so resume() ended up unreachable inside the new
    method and every thumbnail after a scan was held indefinitely."""
    window = MainWindow_for(card)
    window.thumbs.pause()
    assert window.thumbs._paused
    window._scan_done(window._scan_generation, 0)
    assert not window.thumbs._paused, "thumbnails never start loading again"
    close(window)


def test_a_trim_survives_pressing_scan_immediately_after_it(app, sessions_home,
                                                            card, tmp_path):
    """The write is debounced by a second and a half, and _scan clears the
    clips it would have been written from. Trim, then Scan straight away, and
    the only copy was discarded."""
    window = open_window(app, card, [a_clip("hdz_001.ts")])
    window.clips[0].trim_in, window.clips[0].trim_out = 8.0, 44.0
    window._touch_session()                       # pending, not yet written

    window._scan()                                # user presses Scan at once
    app.processEvents()
    close(window)

    assert session_module.for_source(card).clips, "the trim was thrown away"


def test_opening_a_session_replaces_what_is_on_screen(app, sessions_home, card,
                                                      tmp_path):
    """apply_to only touches clips the new session knows about, so a switch
    used to merge: trims from the old one survived and were then written into
    the file that had just been opened and had never heard of them."""
    from flightdvr.session import SUFFIX, Session

    foreign = Session(source=str(tmp_path / "not-here"), title="somewhere else",
                      path=tmp_path / f"foreign{SUFFIX}")
    foreign.save()

    window = open_window(app, card, [a_clip("hdz_001.ts")])
    window.clips[0].trim_in, window.clips[0].trim_out = 8.0, 44.0
    window.clips[0].review = session_module.KEEP
    window._open_session_file(foreign.path)
    close(window)

    assert not Session.load(foreign.path).clips, (
        "decisions from the previous session leaked into the opened one")
    assert not window.clips[0].is_trimmed, "the old trim stayed on screen"
    assert window.clips[0].review == session_module.UNREVIEWED, (
        "the old review state stayed on screen")


def test_opening_a_session_clears_every_select_not_just_the_edited_one(
        app, sessions_home, card, tmp_path):
    """trim_in and trim_out are a view onto the select being edited, so
    clearing them leaves the rest of the list on the clip — the same leak one
    range further along."""
    from flightdvr.media import Select
    from flightdvr.session import SUFFIX, Session

    foreign = Session(source=str(card), title="somewhere else",
                      path=tmp_path / f"foreign2{SUFFIX}")
    foreign.save()

    window = open_window(app, card, [a_clip("hdz_001.ts")])
    window.clips[0].selects = [Select(10, 40, "one"), Select(90, 120, "two"),
                               Select(200, 230, "three")]
    window._open_session_file(foreign.path)

    assert window.clips[0].real_selects == [], (
        "selects beyond the edited one survived the switch")
    close(window)
    assert not Session.load(foreign.path).clips


def MainWindow_for(card):
    """A window pointed at a folder, with no clips and no scan run."""
    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow
    made = MainWindow(find_tools())
    made.source_combo.insertItem(0, str(card), str(card))
    made.source_combo.setCurrentIndex(0)
    return made


# -- export settings travel with the session (#36) ----------------------------

def test_each_card_remembers_its_own_preset(app, sessions_home, card, tmp_path):
    """Two cards, deliberately.

    A single card proves nothing: the preset also lives in QSettings, so it
    comes back on its own and the test passes with the session doing nothing at
    all. The first version of this did exactly that. Two folders with different
    presets can only work if the setting travels with the session, because
    QSettings holds one.
    """
    other = tmp_path / "second-card"
    other.mkdir()

    first = open_window(app, card, [a_clip("hdz_001.ts")])
    first.export_panel.preset_buttons["social"].setChecked(True)
    first.clips[0].trim_in, first.clips[0].trim_out = 10.0, 40.0
    first._touch_session()
    close(first)

    second = open_window(app, other, [a_clip("hdz_009.ts")])
    second.export_panel.preset_buttons["upload"].setChecked(True)
    second.clips[0].trim_in, second.clips[0].trim_out = 5.0, 25.0
    second._touch_session()
    close(second)

    back = open_window(app, card, [a_clip("hdz_001.ts")])
    assert back.export_panel.preset_key() == "social", (
        "the second card's preset followed us back to the first")
    close(back)


def test_the_join_order_records_only_the_clips_that_were_marked(app,
                                                                sessions_home,
                                                                card):
    first = open_window(app, card,
                        [a_clip("hdz_001.ts"), a_clip("hdz_002.ts")])
    first.clips[1].trim_in, first.clips[1].trim_out = 5.0, 20.0
    first._touch_session()
    first._flush_session()

    stored = session_module.for_source(card)
    assert stored.join_order == [first.clips[1].fingerprint]
    close(first)


def test_a_card_with_no_stored_settings_leaves_the_panel_alone(app,
                                                               sessions_home,
                                                               card):
    """Opening a fresh folder must not reset the choices already on screen."""
    window = open_window(app, card, [a_clip("hdz_001.ts")])
    window.export_panel.preset_buttons["upload"].setChecked(True)

    window._adopt_session(session_module.for_source(card))
    assert window.export_panel.preset_key() == "upload"
    close(window)


def test_the_assembly_order_decides_the_queue(app, sessions_home, card):
    """What replaced the remembered join order.

    `join_order` could reverse two clips and nothing more: it stored one
    position per clip, so two ranges of a single recording always came out in
    source order however anybody wanted them. The assembly stores a position
    per *range*, which is why this asserts the jobs rather than the list.
    """
    from PySide6.QtCore import Qt

    first, second = a_clip("hdz_001.ts"), a_clip("hdz_002.ts")
    window = open_window(app, card, [first, second])
    window.export_panel.out_edit.setCurrentText(str(sessions_home / "out"))

    for row in range(window.table.rowCount()):
        window.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
    window._fill_assembly()

    # Deliberately the reverse of counter order, which is what makes this
    # distinguishable from the default at all.
    panel = window.export_panel.assembly_panel
    panel.list.item(1).setSelected(True)
    panel._move(-1)

    window.jobs.clear()
    window._add_to_queue()

    assert len(window.jobs) == 1, [j.out_path.name for j in window.jobs]
    names = [c.path.name for c in window.jobs[0].clips]
    assert names == ["hdz_002.ts", "hdz_001.ts"], names
    close(window)


def test_an_assembly_naming_footage_that_has_gone_queues_nothing(
        app, sessions_home, card, monkeypatch):
    """The failure the old order could not even represent.

    A stored order was a list of fingerprints used for sorting, so a clip that
    had gone simply did not sort. An assembly names material, and material that
    is not here has to stop the export rather than shorten it — a join quietly
    one piece short produces a file that plays perfectly and is not the one
    anybody asked for.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox

    from flightdvr.assembly import Item

    one, two = a_clip("hdz_001.ts"), a_clip("hdz_002.ts")
    window = open_window(app, card, [one, two])
    window.export_panel.out_edit.setCurrentText(str(sessions_home / "out"))

    for row in range(window.table.rowCount()):
        window.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
    window._fill_assembly()
    window._store_assembly(
        window.export_panel.assembly_panel.items()
        + [Item("a-clip-that-is-not-on-this-card", "")])

    said = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: said.append(a[1]))
    window.jobs.clear()
    window._add_to_queue()

    assert window.jobs == [], "queued an assembly it could not fulfil"
    assert said, "queued nothing and said nothing"
    close(window)




def test_a_reopened_session_brings_the_assembly_back_in_order(
        app, sessions_home, card):
    """A list you have to rebuild every time you open the card is not a list.

    Ordered by range id rather than by position, so this also proves the
    reference survives the round trip through the file: the clips are rescanned
    from disk, and the items still find the ranges they named.
    """
    from PySide6.QtCore import Qt

    one, two = a_clip("hdz_001.ts"), a_clip("hdz_002.ts")
    window = open_window(app, card, [one, two])
    for row in range(window.table.rowCount()):
        window.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
    window._fill_assembly()

    panel = window.export_panel.assembly_panel
    panel.list.item(1).setSelected(True)
    panel._move(-1)
    window._capture_assembly()
    window._write_session()
    close(window)

    again = open_window(app, card, [a_clip("hdz_001.ts"), a_clip("hdz_002.ts")])
    rows = again.export_panel.assembly_panel
    assert rows.list.count() == 2, "the assembly did not come back"
    assert "hdz_002" in rows.list.item(0).text(), rows.list.item(0).text()
    assert "hdz_001" in rows.list.item(1).text(), rows.list.item(1).text()
    close(again)
