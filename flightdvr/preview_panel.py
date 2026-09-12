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

"""Preview, transport and filmstrip widgets as one composed view."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from .music_panel import MusicPanel
from .player import FrameView
from .trim import TrimBar
from .widgets import INNER, TIGHT, PreviewPanel as AspectPreviewBox, dim

# Enough of the band to work in without the window demanding a screen it may
# not have. The rest scrolls; nothing is removed.
MUSIC_BAND_MINIMUM = 220


# What the sidebar says about where the keys are going.
#
# Not a substitute for the picture's focus ring — `FrameView.paintEvent` already
# draws one, and the F1 catalog already tells people to look for it. The ring
# says *where* the keys go; these say *what they do there*, which is the half
# #88 was missing: with the name field focused there was no way to know that
# Enter keeps a name and Escape puts the old one back, because neither did
# anything at all.
PICTURE_KEYS = "Silent · click the picture, then Space plays"
NAMING_KEYS = "Naming a range · Enter keeps it, Esc puts back the last one"


class RangeNameEdit(QLineEdit):
    """A name field you can leave deliberately, in both directions.

    Reported in #88: `I` and `O` typed into this box instead of setting trim
    points, and `Space` put a space in the name. Both are Qt behaving
    correctly — the preview shortcuts are scoped to the picture, so with focus
    here they do not fire, and the keys are text like any others.

    What was missing was a way out. There was no commit and no cancel, and the
    field committed on every keystroke, so a stray key was already in the
    session before anybody noticed. Enter keeps the name, Escape puts back the
    last one that was kept, and both hand the keys back to the picture.
    """

    cancelled = Signal()
    focus_changed = Signal(bool)          # True when this field has the keys

    def keyPressEvent(self, event):       # noqa: D102  (Qt entry point)
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):        # noqa: D102
        super().focusInEvent(event)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event):       # noqa: D102
        super().focusOutEvent(event)
        self.focus_changed.emit(False)


class PreviewView(QObject):
    """Build the two preview boxes and expose only user-action signals.

    The picture and filmstrip live in different parent layouts because the
    filmstrip needs the full window width. A QObject composes both without
    inventing a hidden container that would break that geometry.
    """

    frame_clicked = Signal()
    play_requested = Signal()
    grab_still_requested = Signal()
    set_in_requested = Signal()
    set_out_requested = Signal()
    reset_requested = Signal()
    playhead_moved = Signal(float)
    trim_changed = Signal(float, float)
    select_picked = Signal(int)
    select_added = Signal()
    select_removed = Signal()
    select_renamed = Signal(str)
    activity_accepted = Signal()
    track_requested = Signal()
    music_changed = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        # The last name that was actually kept, so Escape has something to put
        # back. Held here rather than read from the clip: this panel is given
        # names, it does not own them.
        self._committed_name = ""
        self.preview_box = self._build_preview_box()
        self.trim_band = self._build_trim_band()
        self.music_band = self._build_music_band()

    def _build_preview_box(self) -> QWidget:
        """The video and its transport, permanently visible."""
        box = AspectPreviewBox("Preview and trim")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(INNER, TIGHT, INNER, INNER)
        layout.setSpacing(INNER)

        self.frame_view = FrameView()
        self.frame_view.clicked.connect(lambda: self.frame_clicked.emit())
        layout.addWidget(self.frame_view, 1)

        # Beside the picture rather than under it. A 16:9 frame in a wide,
        # short box leaves this space without taking height from the clip list.
        layout.addWidget(self._build_sidebar())
        box.view = self.frame_view
        box.sidebar = self.sidebar
        return box

    def _build_sidebar(self) -> QWidget:
        side = self.sidebar = QWidget()
        side.setFixedWidth(190)
        column = QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(TIGHT)
        column.addStretch(1)

        # The position relays thirty times a second; the title changes once per
        # clip. Keeping them separate avoids relaying a long label every frame.
        self.trim_title = QLabel("Select a clip")
        self.trim_title.setWordWrap(True)
        column.addWidget(self.trim_title)

        self.trim_position = dim(QLabel(""))
        self.trim_position.setWordWrap(True)
        column.addWidget(self.trim_position)
        self.trim_summary = dim(QLabel(""))
        column.addWidget(self.trim_summary)
        column.addSpacing(TIGHT)

        # These change once per clip and deliberately stay out of the 30 Hz
        # playhead label update.
        self.clip_format = dim(QLabel(""))
        self.clip_format.setToolTip("Resolution, frame rate, codec and size")
        column.addWidget(self.clip_format)

        self.clip_date = dim(QLabel(""))
        self.clip_date.setToolTip(
            "The timestamp on the card, not when you flew. The Box Pro has no "
            "clock battery, so these are unreliable."
        )
        column.addWidget(self.clip_date)
        column.addSpacing(INNER)

        self.play_button = QPushButton("Play")
        self.play_button.setToolTip(
            "Play the highlighted clip here in the window.\n"
            "Space does the same once the picture has focus."
        )
        self.play_button.clicked.connect(lambda *_: self.play_requested.emit())
        column.addWidget(self.play_button)

        self.still_button = QPushButton("Grab still…")
        self.still_button.setEnabled(False)
        self.still_button.setToolTip(
            "Save the exact paused source frame as a full-resolution PNG.\n"
            "Pause or step to a real decoded frame first."
        )
        self.still_button.clicked.connect(
            lambda *_: self.grab_still_requested.emit())
        column.addWidget(self.still_button)

        trim_row = QHBoxLayout()
        trim_row.setContentsMargins(0, 0, 0, 0)
        trim_row.setSpacing(TIGHT)
        for text, requested, tip in (
            ("In", self.set_in_requested,
             "Start the export at the playhead  (I)"),
            ("Out", self.set_out_requested,
             "End the export at the playhead  (O)"),
            ("Reset", self.reset_requested, "Use the whole clip again"),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            # The keys go back to the picture afterwards. Qt leaves focus on a
            # clicked button, so on the base the next Space re-fired it: In,
            # then Space, moved the in point again — measured at 4.00 -> 8.50
            # in an isolated instance. Nobody pressing In means "In twice".
            button.clicked.connect(
                lambda *_, signal=requested: (signal.emit(),
                                              self.hand_keys_to_picture()))
            trim_row.addWidget(button)
        column.addLayout(trim_row)
        column.addStretch(1)

        self.focus_note = dim(QLabel(PICTURE_KEYS))
        self.focus_note.setWordWrap(True)
        keys = self.focus_note
        keys.setToolTip(
            "With the picture focused:\n"
            "Space or K — play or pause\n"
            "I / O — set the in / out point at the playhead\n"
            ", / . — previous / next source frame, with Shift ten\n"
            "Left / Right — move a second, with Shift five\n"
            "Home / End — jump to the in / out point\n"
            "Esc — stop"
        )
        column.addWidget(keys)

        self.trim_note = dim(QLabel(
            "Remux cuts at keyframes, so a trimmed rewrap can be a second out. "
            "The re-encoding presets are exact."
        ))
        self.trim_note.hide()
        column.addWidget(self.trim_note)

        return side

    def set_still_state(self, available: bool, running: bool = False,
                        cancelling: bool = False) -> None:
        """Make the button describe the one action it can take right now."""
        if cancelling:
            self.still_button.setText("Cancelling…")
            self.still_button.setEnabled(False)
        elif running:
            self.still_button.setText("Cancel still")
            self.still_button.setEnabled(True)
        else:
            self.still_button.setText("Grab still…")
            self.still_button.setEnabled(available)

    def show_activity(self, text: str, offer: str = "") -> None:
        """Say what the clip looks like, and offer a trim only if there is one.

        Nothing here ever changes a trim on its own. A wrong guess that silently
        cut footage would be worse than no guess at all, so the reading is a
        sentence and the action is a button.
        """
        self.activity_note.setText(text)
        self.activity_note.setVisible(bool(text))
        self.activity_button.setText(offer)
        self.activity_button.setVisible(bool(offer))

    def _build_selects_row(self) -> QHBoxLayout:
        """Which range is being edited, and how to add or drop one.

        Here rather than in the sidebar because it is about the ranges drawn
        directly below it, and because the sidebar is 190px and already full.
        Hidden entirely while a clip has one range or none: a card reviewed the
        way every version until now reviewed it should not grow a control it
        has no use for.
        """
        # "Range" is deliberately the word people see while Select and the
        # select_* identifiers remain internal. Renaming those would also mean
        # migrating the persisted "selects" session key for no user benefit.
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(TIGHT)

        self.select_label = dim(QLabel(""))
        self.select_label.setMinimumWidth(96)
        row.addWidget(self.select_label)

        self.select_name = RangeNameEdit()
        self.select_name.setPlaceholderText(
            "Name this range — launch, tree dive…")
        self.select_name.setMaximumWidth(280)
        self.select_name.setToolTip(
            "Enter keeps the name, Esc puts back the last one.\n"
            "Shown here and in the Assembly. It reaches the filename only "
            "when the clip has more than one range, which is what it has "
            "always done."
        )
        # Committed deliberately, not on every keystroke. `editingFinished`
        # covers Enter and clicking away; `returnPressed` additionally hands
        # the keys back, because staying in a text box after saying you are
        # finished is how `Space` ended up in a range name.
        self.select_name.editingFinished.connect(self._commit_name)
        self.select_name.returnPressed.connect(self._leave_name_field)
        self.select_name.cancelled.connect(self._cancel_name)
        self.select_name.focus_changed.connect(self._say_where_the_keys_are)
        row.addWidget(self.select_name)

        # What the recording looks like it spends its time doing, and an offer
        # to act on it. Beside the ranges rather than in the sidebar: the
        # sidebar is 190px and already full, and putting two more widgets there
        # clipped the lines above them.
        self.activity_note = dim(QLabel(""))
        # Explicitly unwrapped, and given room for the longest sentence it
        # produces. A label in a row with an expanding stretch gets handed its
        # minimum, and a wrapped one then makes the whole filmstrip box taller.
        self.activity_note.setWordWrap(False)
        self.activity_note.setMinimumWidth(340)
        self.activity_note.setToolTip(
            "Read from the filmstrip by comparing each second with the next. "
            "A stopped quad and a flying one look very different; a noisy feed "
            "can look like neither, and then this says so."
        )
        self.activity_note.hide()
        row.addWidget(self.activity_note)

        self.activity_button = QPushButton("")
        self.activity_button.setToolTip(
            "Sets the in and out points to the longest run of movement.\n"
            "Nothing is trimmed until you press this."
        )
        self.activity_button.clicked.connect(
            lambda *_: self.activity_accepted.emit())
        self.activity_button.hide()
        row.addWidget(self.activity_button)
        row.addStretch(1)

        add = QPushButton("Add range")
        add.setToolTip("Keep another range out of this clip  (N)")
        # The keys go back to the picture, like every other button here — and
        # to the *picture*, not to the new range's name field. `N` adds a range
        # from the picture, and landing in a text box would put the next Space
        # into the name, which is one of the things #88 reported.
        add.clicked.connect(
            lambda *_: (self.select_added.emit(),
                        self.hand_keys_to_picture()))
        row.addWidget(add)

        self.select_remove = QPushButton("Remove")
        self.select_remove.setToolTip("Drop the range being edited")
        self.select_remove.clicked.connect(
            lambda *_: (self.select_removed.emit(),
                        self.hand_keys_to_picture()))
        row.addWidget(self.select_remove)

        self.select_add = add
        # Everything except Add is about *which* of several ranges you are
        # editing, so none of it means anything until there are several.
        # The name is no longer in here: it follows `nameable` instead, so a
        # lone range can be named (#87).
        self._only_when_several = [self.select_label, self.select_remove]
        return row

    # -- who has the keys ------------------------------------------------------

    def hand_keys_to_picture(self) -> None:
        """Give focus back to the picture, where the trim keys live.

        Called after every pointer action in this panel. The shortcuts are
        deliberately scoped to the picture, so anything that leaves focus
        somewhere else leaves them switched off — which is what #88 reported
        from the other side: `Space` re-firing the In button it was still on.
        """
        self.frame_view.setFocus()

    def _say_where_the_keys_are(self, editing: bool) -> None:
        """One line naming the mode, and what its keys do.

        The picture has a focus ring of its own, so *where* the keys go is
        already visible. What was not was what Enter and Escape do once a name
        is being typed — which was nothing, before this.
        """
        self.focus_note.setText(NAMING_KEYS if editing else PICTURE_KEYS)

    # -- naming a range --------------------------------------------------------

    def _commit_name(self) -> None:
        """Keep what was typed. Called by Enter and by clicking away."""
        typed = self.select_name.text()
        if typed == self._committed_name:
            return
        self._committed_name = typed
        self.select_renamed.emit(typed)

    def _cancel_name(self) -> None:
        """Escape: put back the last kept name and hand the keys back."""
        self.select_name.setText(self._committed_name)
        self.hand_keys_to_picture()

    def _leave_name_field(self) -> None:
        """Enter: keep it, then stop being a text box.

        `returnPressed` arrives before `editingFinished` here, so the commit is
        explicit rather than relying on the order.
        """
        self._commit_name()
        self.hand_keys_to_picture()

    def show_selects(self, count: int, index: int, name: str,
                     nameable: bool = False) -> None:
        """Say which range is being edited, and hide what does not apply yet.

        Add stays whatever happens: it is how a second range comes to exist,
        and hiding it would leave the N key as the only way to reach a feature
        nobody would know was there.

        The name field now appears for a *single* range too (#87): a lone range
        could not be named, which left an Assembly row saying only the
        recording's filename and made ordering it harder than it needed to be.
        `nameable` is the caller's answer to "is there a real range here",
        because clearing a trim leaves an empty select behind and offering to
        name that would be naming something the export does not believe in.

        Which one of several, and dropping it, still appear only when there is
        more than one: "Range 1 of 1" says nothing, and removing the only range
        is what Reset already does.
        """
        several = count > 1
        for widget in self._only_when_several:
            widget.setVisible(several)
        self.select_name.setVisible(nameable)
        self.select_label.setText(
            f"Range {index + 1} of {count}" if several else "")
        self._committed_name = name
        if self.select_name.text() != name:
            self.select_name.setText(name)
            # Show the beginning of the name, not its tail. `setText` leaves the
            # cursor at the end, so a long one arrived reading "ve, second
            # attempt" — seen in the native shots, and worse now that a lone
            # range can carry a name nobody chose to abbreviate.
            self.select_name.setCursorPosition(0)

    def _build_music_band(self) -> QWidget:
        """The approved music band, under the filmstrip and collapsed by default.

        Collapsed means the body is hidden rather than merely disabled: the
        point of the toggle is the vertical space it gives back to the picture,
        and a disabled body still occupies its full height.
        """
        band = QGroupBox("Music")
        band.setCheckable(True)
        band.setChecked(False)
        layout = QVBoxLayout(band)
        layout.setContentsMargins(INNER, TIGHT, INNER, INNER)

        self.music_content = QWidget()
        body = QVBoxLayout(self.music_content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(INNER)

        chooser = QHBoxLayout()
        chooser.setSpacing(TIGHT)
        self.track_button = QPushButton("Choose track…")
        self.track_button.setToolTip(
            "Pick an audio file. It is read in the background; the passage and "
            "levels below can be set once it has been read."
        )
        self.track_button.clicked.connect(lambda: self.track_requested.emit())
        chooser.addWidget(self.track_button)
        self.track_status = dim(QLabel(""))
        self.track_status.setWordWrap(True)
        chooser.addWidget(self.track_status, 1)
        body.addLayout(chooser)

        # Said plainly rather than left to be discovered by pressing play. The
        # preview is the source picture and has no sound at all, so silence
        # here is not a fault in the music that was just chosen.
        self.music_silence_note = dim(QLabel(
            "The preview above is the source picture and has no sound. Music "
            "is heard in the finished file."
        ))
        self.music_silence_note.setWordWrap(True)
        body.addWidget(self.music_silence_note)

        self.music_panel = MusicPanel()
        self.music_panel.changed.connect(lambda: self.music_changed.emit())
        body.addWidget(self.music_panel)

        # Measured before it was built this way: the controls stack to a 625px
        # minimum, which made the whole window refuse to be shorter than
        # 1419px — taller than the 900px it opens at, so the picture could
        # never give the band its room. Scrolling bounds that without moving a
        # control or dropping a line of the explanatory text: the band still
        # expands downward as approved, and gives way when there is no room.
        self.music_body = QScrollArea()
        self.music_body.setWidget(self.music_content)
        self.music_body.setWidgetResizable(True)
        self.music_body.setFrameShape(QScrollArea.Shape.NoFrame)
        self.music_body.setMinimumHeight(MUSIC_BAND_MINIMUM)
        self.music_body.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.music_body)
        self.music_body.setVisible(False)
        band.toggled.connect(self.music_body.setVisible)
        return band

    def show_track_status(self, text: str) -> None:
        """What the acquisition is doing, in words a person can act on."""
        self.track_status.setText(text)

    def _build_trim_band(self) -> QWidget:
        """The full-width filmstrip directly under the preview it scrubs."""
        band = QGroupBox("Filmstrip")
        layout = QVBoxLayout(band)
        layout.setContentsMargins(INNER, TIGHT, INNER, INNER)
        layout.addLayout(self._build_selects_row())

        self.trim_bar = TrimBar()
        self.trim_bar.setToolTip(
            "Every keyframe in the clip, a second apart. Click to move the "
            "playhead, drag either end to set where the export starts and ends."
        )
        self.trim_bar.playhead_moved.connect(
            lambda seconds: self.playhead_moved.emit(seconds)
        )
        self.trim_bar.trim_changed.connect(
            lambda in_point, out_point: self.trim_changed.emit(
                in_point, out_point
            )
        )
        self.trim_bar.select_picked.connect(
            lambda index: self.select_picked.emit(index)
        )
        layout.addWidget(self.trim_bar)
        return band
