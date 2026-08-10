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

import pytest

from flightdvr.shortcuts import SHORTCUT_GROUPS


# `Delete` is the one binding with nothing to introspect: it is a branch in
# MainWindow.keyPressEvent guarded by queue_table.hasFocus(), not a QShortcut.
# It is checked by behaviour further down instead, and named here so the
# comparison below can say why it is exempt rather than quietly skipping it.
NOT_AN_OBJECT = {"Delete"}


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


def test_the_dialog_lists_every_key_the_picture_actually_binds(window):
    """The README drifted from the bindings because nothing compared them. If
    a player key is added, renamed or dropped without touching the dialog, this
    is what says so."""
    assert documented("The picture") == installed_on(window.frame_view)


def test_the_dialog_lists_every_key_the_clip_list_actually_binds(window):
    """U/K/M/R come from REVIEW_KEYS, so a fifth review state would bind a key
    the dialog had never heard of."""
    assert documented("The clip list") == installed_on(window.table)


def test_the_dialog_lists_every_window_wide_key(window):
    """The section the README got most wrong: it named two of these and there
    are ten, seven of them QShortcuts and three menu actions."""
    assert documented("Anywhere") == installed_on(window) | menu_keys(window)


def test_the_only_undocumented_binding_is_the_one_with_no_object(window):
    """Delete is a keyPressEvent branch rather than a shortcut object, so it is
    exempt from the comparisons above by name. If it ever becomes a real
    QShortcut this fails, and the exemption should go rather than grow."""
    every_documented = {key for g in SHORTCUT_GROUPS for row in g.shortcuts
                        for key in row.keys}
    every_installed = (installed_on(window) | menu_keys(window)
                       | installed_on(window.frame_view)
                       | installed_on(window.table))
    assert every_documented - every_installed == NOT_AN_OBJECT


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
                    assert f"<code>{key}</code>" in text, (
                        f"{key} is missing from the dialog")
    finally:
        dialog.deleteLater()


def _all_label_text(dialog) -> str:
    from PySide6.QtWidgets import QLabel
    return " ".join(label.text() for label in dialog.findChildren(QLabel))
