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

"""The shortcuts dialog against the bindings it claims to describe.

`flightdvr/shortcuts.py` is a hand-written description of keys it does not
create, which is exactly the arrangement that let the README rot: it filed
`Delete` under the clip list when `Delete` only works in the queue, and listed
two window-wide keys when there are ten. Nothing about writing the list more
carefully this time would stop that happening again.

So these walk the real `QShortcut` and `QAction` objects on a built window and
compare both directions — a key that stops existing, and a key added without a
line in the dialog, each fail here.
"""

from __future__ import annotations

from html import escape

import pytest

from flightdvr.shortcuts import SHORTCUT_GROUPS


# Keys a group documents that no QShortcut on that group's widget provides.
# `Delete` is a branch in MainWindow.keyPressEvent guarded by
# queue_table.hasFocus(); `Space` on the clip list is Qt's own handling of a
# checkable row, which is precisely why the player's Space had to be scoped
# away from it. Both are real keys a pilot presses, so omitting them would
# repeat the gap this change exists to close.
#
# Per group rather than global, because Space is not exempt everywhere — it is
# a genuine QShortcut on the picture, and writing one blanket exemption would
# have stopped the comparison checking that.
PROVIDED_BY_QT = {
    "The clip list": {"Space"},
    "The picture": set(),
    "The export queue": {"Delete"},
    "Anywhere": set(),
}


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def window(qt_app):
    """Never shown. Nothing here is geometry, and these read the bindings a
    built window installed rather than anything it paints."""
    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow
    made = MainWindow(find_tools())
    yield made
    made.close()


def group(title: str):
    for candidate in SHORTCUT_GROUPS:
        if candidate.title == title:
            return candidate
    raise AssertionError(f"no group titled {title!r}")


def documented(title: str) -> set[str]:
    return {key for row in group(title).shortcuts for key in row.keys}


def installed_on(widget) -> set[str]:
    """The keys bound directly on one widget.

    `findChildren` reaches every descendant, so the parent check is what makes
    this "the picture's keys" rather than "the keys of everything under it".
    """
    from PySide6.QtGui import QShortcut
    return {s.key().toString() for s in widget.findChildren(QShortcut)
            if s.parent() is widget}


def menu_keys(window) -> set[str]:
    """Menu bindings, which are QAction shortcuts rather than QShortcut objects.

    Missing these is an easy way to write a comparison that passes while being
    blind to Ctrl+O, Ctrl+Shift+S and F1.
    """
    found = set()
    for menu_action in window.menuBar().actions():
        menu = menu_action.menu()
        if menu is None:
            continue
        for action in menu.actions():
            text = action.shortcut().toString()
            if text:
                found.add(text)
    return found


def by_shortcut_object(title: str) -> set[str]:
    """What a group claims, minus the keys Qt provides rather than this app."""
    return documented(title) - PROVIDED_BY_QT[title]


def test_the_dialog_lists_every_key_the_picture_actually_binds(window):
    """The README drifted from the bindings because nothing compared them. If
    a player key is added, renamed or dropped without touching the dialog, this
    is what says so."""
    assert by_shortcut_object("The picture") == installed_on(window.frame_view)


def test_the_dialog_lists_every_key_the_clip_list_actually_binds(window):
    """U/K/M/R come from REVIEW_KEYS, so a fifth review state would bind a key
    the dialog had never heard of."""
    assert by_shortcut_object("The clip list") == installed_on(window.table)


def test_the_dialog_lists_every_window_wide_key(window):
    """The section the README got most wrong: it named two of these and there
    are ten, seven of them QShortcuts and three menu actions."""
    assert by_shortcut_object("Anywhere") == (installed_on(window)
                                              | menu_keys(window))


def test_nothing_is_exempt_that_qt_does_not_actually_provide(window):
    """The exemptions are the one place this comparison can be talked out of
    failing, so they get checked too. Space is exempt on the clip list and must
    not be exempt on the picture, where it is a real shortcut object."""
    assert PROVIDED_BY_QT["The picture"] == set()
    assert PROVIDED_BY_QT["Anywhere"] == set()
    assert "Space" in installed_on(window.frame_view), (
        "Space is exempt on the clip list only because the picture owns it")
    assert "Space" not in installed_on(window.table)
    assert "Delete" not in (installed_on(window) | installed_on(window.table)
                            | installed_on(window.frame_view)
                            | menu_keys(window)), (
        "Delete became a real shortcut; the exemption should go, not stay")


def test_delete_is_documented_under_the_queue_and_not_the_clip_list(window):
    """The README filed Delete under the clip list. It is guarded by
    queue_table.hasFocus(), so it does nothing there — copying that mistake
    into the dialog would have taught it to a wider audience."""
    assert "Delete" in documented("The export queue")
    assert "Delete" not in documented("The clip list")


def test_delete_only_drops_queue_rows_while_the_queue_has_focus(window,
                                                                monkeypatch):
    """The behavioural half of the exemption above: Delete has no shortcut
    object to introspect, so its scope is only ever a claim unless something
    presses it."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    dropped = []
    monkeypatch.setattr(window, "_remove_selected_jobs",
                        lambda: dropped.append(True))
    press = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Delete,
                      Qt.KeyboardModifier.NoModifier)

    monkeypatch.setattr(type(window.queue_table), "hasFocus",
                        lambda self: False)
    window.keyPressEvent(press)
    assert dropped == [], "Delete dropped queue rows without the queue focused"

    monkeypatch.setattr(type(window.queue_table), "hasFocus",
                        lambda self: True)
    window.keyPressEvent(press)
    assert dropped == [True]


def test_space_really_does_tick_a_clip_in_the_list(window):
    """The behavioural half of the other exemption. Nothing in this app binds
    Space on the list — it is Qt toggling a checkable row — so the dialog's
    claim is only as good as a real key press proving it."""
    from datetime import datetime
    from pathlib import Path

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from flightdvr.media import ClipInfo

    window._add_clip(window._scan_generation, ClipInfo(
        path=Path("hdz_047.ts"), size=599_189_652,
        modified=datetime(2025, 10, 8, 18, 39),
        duration=240.0, width=1280, height=720, fps=60.0,
        video_codec="hevc", audio_codec="aac",
        pix_fmt="yuvj420p", color_range="pc",
    ))
    table = window.table
    item = table.item(0, 0)
    assert item.flags() & Qt.ItemFlag.ItemIsUserCheckable, (
        "the row is not checkable, so Space could not tick it whatever the "
        "dialog says")

    table.setCurrentCell(0, 0)
    before = item.checkState()
    QApplication.sendEvent(table, QKeyEvent(
        QKeyEvent.Type.KeyPress, Qt.Key.Key_Space,
        Qt.KeyboardModifier.NoModifier, " "))
    assert item.checkState() != before, (
        "Space did not tick the highlighted clip, which the dialog promises")


def test_k_is_deliberately_two_different_keys(window):
    """The reason the dialog is grouped at all. A flat list would have to print
    one meaning of K and be wrong about the other, which is the specific defect
    the grouping exists to avoid."""
    assert "K" in documented("The clip list")
    assert "K" in documented("The picture")
    assert "K" in installed_on(window.table)
    assert "K" in installed_on(window.frame_view)
    assert "K" not in installed_on(window), (
        "a window-wide K would make both meanings ambiguous and Qt would fire "
        "neither")


def described(keys: str) -> str:
    for g in SHORTCUT_GROUPS:
        for row in g.shortcuts:
            if keys in row.keys:
                return row.description
    raise AssertionError(f"{keys} is not documented")


def test_the_ticking_keys_say_visible_because_they_skip_hidden_rows(window):
    """The defect a review caught in this dialog, kept caught.

    `_set_all` skips hidden rows, so with a review filter on, Ctrl+A ticks what
    is on screen and nothing else — there is a test elsewhere named for the day
    that broke. This list said "every clip" anyway, because the comparison in
    this file checks which keys exist and has nothing to say about whether the
    sentence beside one is true.

    Tying the wording to the buttons is the narrow fix available: they are the
    same action described twice, so the two can at least not disagree.
    """
    from PySide6.QtWidgets import QPushButton

    tips = {button.text(): button.toolTip()
            for button in window.browser_panel.findChildren(QPushButton)}

    assert tips["All"] == described("Ctrl+A"), (
        "the All button and Ctrl+A describe one action and have drifted apart")
    assert tips["None"] == described("Ctrl+Shift+A"), (
        "the None button and Ctrl+Shift+A describe one action and have "
        "drifted apart")
    assert "visible" in described("Ctrl+A"), (
        "_set_all skips hidden rows; a description without 'visible' is wrong "
        "whenever a filter is on")


def test_delete_is_described_the_same_way_the_queue_button_describes_it(window):
    """The other place the app already says what a key does. Same reasoning as
    above: one action, two descriptions, and no way for them to disagree."""
    from PySide6.QtWidgets import QPushButton

    tips = [b.toolTip() for b in window.queue_panel.findChildren(QPushButton)
            if b.toolTip().startswith("Drop the selected rows")]
    assert tips, "the queue's Remove button no longer explains itself"
    assert tips[0].startswith(described("Delete")), (
        "the Remove tooltip and the Delete row have drifted apart")


def test_the_keycap_fill_follows_the_theme_instead_of_a_fixed_grey(qt_app):
    """A grey chosen against a light window is a near-white block on a dark
    one: measured, the dark theme's own text on a fixed #d0d0d0 chip comes out
    at 1.54:1, which is unreadable. Deriving it gives 10.7:1.

    So the fill has to move toward the text colour in both directions, and this
    fails if anyone replaces it with a literal.
    """
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QLabel

    from flightdvr.widgets import key_fill

    def fill_for(window: str, text: str) -> QColor:
        label = QLabel()
        palette = label.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(window))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(text))
        label.setPalette(palette)
        return QColor(key_fill(label))

    light = fill_for("#f3f3f3", "#000000")
    dark = fill_for("#1e1e1e", "#ffffff")

    assert light.lightness() < QColor("#f3f3f3").lightness(), (
        "on a light window the chip must be darker than the page")
    assert dark.lightness() > QColor("#1e1e1e").lightness(), (
        "on a dark window the chip must be lighter than the page")
    assert light != dark, "the fill is not reading the palette at all"


def test_every_documented_key_says_what_it_does():
    """A row with keys and no description is a row that teaches nothing."""
    for g in SHORTCUT_GROUPS:
        assert g.note, f"{g.title} does not say how to focus it"
        for row in g.shortcuts:
            assert row.keys, f"a row in {g.title} has no keys"
            assert row.description.strip(), f"{row.keys} has no description"


def test_the_dialog_shows_every_group_and_row(window):
    """Built rather than described: the data could be right while the dialog
    silently rendered half of it."""
    dialog = window._build_shortcuts_dialog()
    try:
        text = _all_label_text(dialog)
        for g in SHORTCUT_GROUPS:
            assert g.title in text, f"{g.title} is missing from the dialog"
            for row in g.shortcuts:
                assert row.description in text, (
                    f"{row.description} is missing from the dialog")
                for key in row.keys:
                    assert f"&nbsp;{escape(key)}&nbsp;" in text, (
                        f"{key} is missing from the dialog")
    finally:
        dialog.deleteLater()


def _all_label_text(dialog) -> str:
    from PySide6.QtWidgets import QLabel
    return " ".join(label.text() for label in dialog.findChildren(QLabel))
