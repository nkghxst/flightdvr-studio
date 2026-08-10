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

"""What the keyboard does, grouped by what has to have focus for it to work.

Grouping is not presentation here, it is the content. Three of these keys mean
two different things depending on where you are — `K` is Keep in the clip list
and Play on the picture, and that ambiguity is deliberate, because the clip list
wants `Space` for ticking a row far more than the player wants it. A flat list
of keys would therefore print `K — play/pause` and be half wrong, which is worse
than the README it replaces.

The bindings themselves live where their slots do: window-wide ones in
`MainWindow._install_shortcuts`, the picture's in `_install_player_shortcuts`,
the review keys in `browser_panel.REVIEW_KEYS`, and `Delete` in
`MainWindow.keyPressEvent`. **This module does not create any of them** — it
only describes them, so it can drift, and the README already proved that a
hand-kept list will. `test_shortcuts.py` closes that hole by walking the real
`QShortcut` objects on a built window and failing if the two disagree in either
direction.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Shortcut:
    """One row: the keys that do it, and what they do.

    `keys` is a tuple because several rows are genuinely two bindings doing one
    job — `Space` and `K` are both play, and printing them as separate rows
    saying the same thing reads like a mistake.
    """

    keys: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class ShortcutGroup:
    """The keys that need one particular thing focused.

    `note` says how to get that focus. Without it the grouping tells you the
    keys are different in different places but not how to be in one of them.
    """

    title: str
    note: str
    shortcuts: tuple[Shortcut, ...]


SHORTCUT_GROUPS: tuple[ShortcutGroup, ...] = (
    ShortcutGroup(
        "The clip list",
        "Click a clip in the list on the left.",
        (
            Shortcut(("U",), "Mark unreviewed"),
            Shortcut(("K",), "Mark keep"),
            Shortcut(("M",), "Mark maybe"),
            Shortcut(("R",), "Mark reject"),
        ),
    ),
    ShortcutGroup(
        "The picture",
        "Click the video. Its focus ring tells you these are the keys you "
        "will get.",
        (
            Shortcut(("Space", "K"), "Play or pause"),
            Shortcut(("I",), "In point at the playhead"),
            Shortcut(("O",), "Out point at the playhead"),
            Shortcut(("N",), "Add another range"),
            Shortcut((",", "."), "Previous / next source frame"),
            Shortcut(("Shift+,", "Shift+."), "Ten frames"),
            Shortcut(("Left", "Right"), "Move a second"),
            Shortcut(("Shift+Left", "Shift+Right"), "Move five seconds"),
            Shortcut(("Home", "End"), "Jump to the in / out point"),
            Shortcut(("Esc",), "Stop"),
        ),
    ),
    ShortcutGroup(
        "The export queue",
        "Click a row in the queue at the bottom.",
        (
            Shortcut(("Delete",), "Drop the selected rows"),
        ),
    ),
    ShortcutGroup(
        "Anywhere",
        "No particular focus needed.",
        (
            Shortcut(("F5",), "Scan the source folder"),
            Shortcut(("Ctrl+A",), "Tick every clip"),
            Shortcut(("Ctrl+Shift+A",), "Untick every clip"),
            Shortcut(("Ctrl+P",), "Play the highlighted clip"),
            Shortcut(("Ctrl+Shift+P",), "Preview the highlighted clip"),
            Shortcut(("Ctrl+Return",), "Add the ticked clips to the queue"),
            Shortcut(("F9",), "Start the queue"),
            Shortcut(("Ctrl+O",), "Open a session"),
            Shortcut(("Ctrl+Shift+S",), "Save the session as…"),
            Shortcut(("F1",), "This list"),
        ),
    ),
)
