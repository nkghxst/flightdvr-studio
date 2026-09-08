"""Naming a lone range, and a rename you can leave (#87, #88).

Two reports that are one interaction. #87: the only range of a clip could not
be named, so an Assembly row said nothing but the recording's filename. #88:
`I` and `O` typed into the name box instead of setting trim points, `Space`
sometimes activated the In button it had left focus on, and there was no way to
cancel an edit.

They belong together because fixing #87 alone adds a text field to a panel that
already loses track of focus, which is the mechanism behind #88.

What is asserted here is what a headless test can settle: which controls are
offered, what reaches the model and when, and that the filename and the
Assembly say the right thing afterwards. **Where focus actually goes is proved
natively instead**, in an isolated instance, because this platform has no key
mapper and no real focus — the numbers from that run are in the PR.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flightdvr.media import ClipInfo, Select  # noqa: E402

# `isVisible()` answers False for every child of a window that was never shown,
# whatever its own flag says, so these read `isHidden()` — the flag this change
# actually sets. That a shown window really draws the field is in the native
# evidence, not here.


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(qt_app):
    from flightdvr.preview_panel import PreviewView

    made = PreviewView()
    yield made
    made.preview_box.deleteLater()
    made.trim_band.deleteLater()


def clip_with(ranges, name="hdz_047.ts", duration=60.0) -> ClipInfo:
    made = ClipInfo(path=Path(name), size=1, modified=datetime(2026, 7, 27),
                    duration=duration, width=1280, height=720, fps=60.0,
                    video_codec="hevc", audio_codec="aac")
    made.selects = [Select(a, b, n) for a, b, n in ranges]
    return made


# -- #87: the only range can be named -----------------------------------------


def test_a_lone_range_offers_its_name(view):
    """The report. One range and the name field was simply not there."""
    view.show_selects(1, 0, "", nameable=True)
    assert not view.select_name.isHidden()


def test_a_lone_range_does_not_offer_which_one_of_several(view):
    """"Range 1 of 1" says nothing, and Remove duplicates Reset."""
    view.show_selects(1, 0, "", nameable=True)
    assert view.select_label.isHidden()
    assert view.select_remove.isHidden()
    assert view.select_label.text() == ""


def test_several_ranges_still_offer_everything(view):
    view.show_selects(3, 1, "tree dive", nameable=True)
    assert not view.select_name.isHidden()
    assert not view.select_label.isHidden()
    assert not view.select_remove.isHidden()
    assert view.select_label.text() == "Range 2 of 3"
    assert view.select_name.text() == "tree dive"


def test_an_untrimmed_recording_offers_no_name(view):
    """There is no range to name, and inventing one is what #87 forbids.

    An untrimmed clip is referenced by an Assembly as the *recording*, not as a
    range of it. Offering a name here would either do nothing or quietly create
    the explicit range that reference deliberately is not.
    """
    view.show_selects(0, 0, "", nameable=False)
    assert view.select_name.isHidden()


def test_a_cleared_trim_is_not_nameable_either(view):
    """Clearing a trim leaves an empty select behind, not a range.

    `real_selects` is what tells them apart, and it is why the window passes a
    `nameable` answer rather than the raw count.
    """
    cleared = clip_with([(0.0, 0.0, "")])
    assert cleared.selects, "the placeholder select should still be there"
    assert not cleared.real_selects, "an empty select is not a range"


def test_the_window_asks_the_question_from_real_ranges(qt_app, monkeypatch):
    """The wiring, end to end, without asserting on a private call.

    `_show_selects` is what turns a clip into what the panel shows, so this
    drives it and reads the control.
    """
    from flightdvr.media import ToolsMissing, find_tools
    from flightdvr.ui import MainWindow

    try:
        tools = find_tools()
    except ToolsMissing:
        pytest.skip("needs ffmpeg to build a window")
    window = MainWindow(tools)
    try:
        trimmed = clip_with([(4.0, 9.0, "")])
        window._trim_clip = trimmed
        window._show_selects()
        assert not window.preview_view.select_name.isHidden()

        untrimmed = clip_with([])
        window._trim_clip = untrimmed
        window._show_selects()
        assert window.preview_view.select_name.isHidden()
    finally:
        window.close()


# -- #88: a rename you can leave ----------------------------------------------


def test_typing_does_not_reach_the_model(view):
    """The defect that made a stray key permanent.

    The field committed on `textEdited`, so `I`, `O` or a space typed by
    accident was in the session before anybody saw it. Nothing is kept until it
    is committed now.
    """
    emitted = []
    view.select_renamed.connect(emitted.append)
    view.show_selects(1, 0, "", nameable=True)

    view.select_name.setText("tri")
    assert emitted == [], "typing committed a name"


def test_enter_keeps_it_once(view):
    emitted = []
    view.select_renamed.connect(emitted.append)
    view.show_selects(1, 0, "", nameable=True)

    view.select_name.setText("tree dive")
    view.select_name.returnPressed.emit()
    assert emitted == ["tree dive"]

    # Committing the same text again says nothing, so an ordinary click away
    # after Enter does not repeat the write or touch the session twice.
    view.select_name.editingFinished.emit()
    assert emitted == ["tree dive"]


def test_escape_puts_back_the_last_kept_name(view):
    """Cancel, which did not exist. Escape used to do nothing at all."""
    emitted = []
    view.select_renamed.connect(emitted.append)
    view.show_selects(1, 0, "launch", nameable=True)

    view.select_name.setText("launch and then a stray key")
    view._cancel_name()

    assert view.select_name.text() == "launch"
    assert emitted == [], "cancelling committed something"


def test_escape_before_anything_was_kept_clears_back_to_empty(view):
    view.show_selects(1, 0, "", nameable=True)
    view.select_name.setText("ooops")
    view._cancel_name()
    assert view.select_name.text() == ""


def test_showing_a_range_resets_what_escape_would_restore(view):
    """Switching ranges must not let Escape paste the previous one's name."""
    view.show_selects(2, 0, "first", nameable=True)
    view.show_selects(2, 1, "second", nameable=True)

    view.select_name.setText("half typed")
    view._cancel_name()
    assert view.select_name.text() == "second"


def test_the_sidebar_says_which_mode_the_keys_are_in(view):
    """The picture has a focus ring; what it cannot say is what Enter does.

    `FrameView.paintEvent` draws a ring when it has focus, so where the keys go
    is already visible. The sentence carries the half that was missing: with a
    name being typed, that Enter keeps it and Escape puts back the last one.
    """
    from flightdvr.preview_panel import NAMING_KEYS, PICTURE_KEYS

    view._say_where_the_keys_are(True)
    assert view.focus_note.text() == NAMING_KEYS
    assert "Enter" in NAMING_KEYS and "Esc" in NAMING_KEYS

    view._say_where_the_keys_are(False)
    assert view.focus_note.text() == PICTURE_KEYS


def test_escape_is_swallowed_rather_than_typed(qt_app, view):
    """The field has to answer Escape itself; nothing else was listening."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    cancels = []
    view.select_name.cancelled.connect(lambda: cancels.append(1))
    view.show_selects(1, 0, "", nameable=True)
    view.select_name.setText("half typed")
    QTest.keyClick(view.select_name, Qt.Key.Key_Escape)
    assert cancels == [1]


# -- what the name changes, and what it deliberately does not -----------------


def test_a_named_lone_range_still_exports_to_the_same_filename():
    """#87 is explicit: showing a name does not authorise renaming exports.

    `select_stem` suppresses the range fields for a lone range, so the file a
    person already has keeps the name it already had. Asserted against the real
    naming path rather than described.
    """
    from flightdvr.format import DEFAULT_TEMPLATE, expand_template, export_fields

    unnamed = clip_with([(4.0, 9.0, "")])
    named = clip_with([(4.0, 9.0, "launch")])

    def stem(clip):
        piece = clip.for_export()[0]
        return expand_template(DEFAULT_TEMPLATE,
                               export_fields(piece, 0, 1, "_master"))

    assert stem(named) == stem(unnamed) == "hdz_047_master"


def test_a_named_range_out_of_several_still_reaches_the_filename():
    """The other half: naming has always mattered when there are several."""
    from flightdvr.format import DEFAULT_TEMPLATE, expand_template, export_fields

    clip = clip_with([(1.0, 5.0, "launch"), (10.0, 14.0, "tree dive")])
    pieces = clip.for_export()
    stems = [expand_template(DEFAULT_TEMPLATE,
                             export_fields(p, i, len(pieces), "_master"))
             for i, p in enumerate(pieces)]
    assert stems == ["hdz_047_1_launch_master", "hdz_047_2_tree-dive_master"]


def test_the_assembly_shows_a_lone_range_by_name():
    """#87's other half, and it needed no assembly change at all.

    `Row.label()` already prefers the range's name whenever there is one, with
    no condition on how many ranges the clip has. Naming was the only thing
    missing.
    """
    from flightdvr.assembly import Item, resolve

    clip = clip_with([(4.0, 9.0, "tree dive")])
    row = resolve([Item(clip.fingerprint, clip.real_selects[0].sid)], [clip])[0]
    assert row.label() == "hdz_047.ts  ·  tree dive"


def test_a_whole_recording_reference_is_not_turned_into_a_named_range():
    """The identity limitation, asserted rather than left to be discovered.

    An assembly item with an empty `sid` means "this recording". It resolves to
    a select built at read time, which carries no name — so a whole-recording
    row cannot show one, and naming cannot quietly convert it. That is the
    behaviour #87 asks for, and the cost is that this row stays plain.
    """
    from flightdvr.assembly import Item, resolve

    clip = clip_with([(4.0, 9.0, "tree dive")])
    whole = resolve([Item(clip.fingerprint)], [clip])[0]

    assert whole.whole_clip
    assert whole.label() == "hdz_047.ts", "a whole-recording row took a name"
    assert whole.select.sid == "", "the reference gained a range identity"


# -- it survives the things that move ranges around ---------------------------


def test_a_name_survives_a_session_round_trip(tmp_path):
    from flightdvr.session import Session, capture_from

    clip = clip_with([(4.0, 9.0, "tree dive")])
    session = Session(title="card")
    capture_from(session, [clip])
    reopened = Session.load(session.save(tmp_path / "card.flightdvr"))

    marks = reopened.clips[clip.fingerprint]
    assert [s.name for s in marks.selects] == ["tree dive"]


def test_a_name_survives_adding_and_removing_a_second_range(qt_app):
    """The interaction #87 names: a second range appears, then goes again."""
    from flightdvr.media import ToolsMissing, find_tools
    from flightdvr.ui import MainWindow

    try:
        tools = find_tools()
    except ToolsMissing:
        pytest.skip("needs ffmpeg to build a window")
    window = MainWindow(tools)
    try:
        clip = clip_with([(4.0, 9.0, "launch")])
        window._trim_clip = clip
        window.trim_bar.set_clip(clip.duration, 4.0, 9.0)
        window._show_selects()

        window._add_select()
        assert len(clip.selects) == 2
        assert clip.selects[0].name == "launch"

        window._remove_select()
        assert len(clip.selects) == 1
        assert clip.selects[0].name == "launch", "the survivor lost its name"
        assert not window.preview_view.select_name.isHidden()
    finally:
        window.close()


def test_the_name_belongs_to_the_range_not_to_its_position(qt_app):
    """Names and stable ids stay separate, which is what #87 asks for."""
    from flightdvr.media import ToolsMissing, find_tools
    from flightdvr.ui import MainWindow

    try:
        tools = find_tools()
    except ToolsMissing:
        pytest.skip("needs ffmpeg to build a window")
    window = MainWindow(tools)
    try:
        clip = clip_with([(1.0, 5.0, "first"), (10.0, 14.0, "second")])
        sids = [s.sid for s in clip.selects]
        window._trim_clip = clip
        window._show_selects()

        clip.current = 0
        window._remove_select()

        assert [s.name for s in clip.selects] == ["second"]
        assert [s.sid for s in clip.selects] == [sids[1]], (
            "removing a range moved another range's identity")
    finally:
        window.close()
