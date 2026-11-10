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

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QBoxLayout, QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QSpacerItem, QToolButton, QVBoxLayout, QWidget,
)

from .music_panel import MusicPanel
from .music_timeline import MusicEditor, MusicTimeline, Presentation
from .player import FrameView
from .range_lanes import RangeLanes
from .sequence_strip import SequenceStrip
from .trim import TrimBar
from .widgets import (INNER, TIGHT, ElidedLabel, PreviewPanel as AspectPreviewBox,
                      dim)

# Enough of the band to work in without the window demanding a screen it may
# not have. The rest scrolls; nothing is removed.
MUSIC_BAND_MINIMUM = 220
# Classic's band is the shallow one, under a picture and a list that already
# share the window. At its least it is the track row and the rest scrolls —
# measured natively, anything more grew a 1120x760 window with the list
# collapsed. At most the track and listening rows and a shallow lane: taller,
# and the band took the list's rows even where the picture had given the
# room (two rows to none at 1440x913, measured natively).
CLASSIC_MUSIC_MINIMUM = 24
CLASSIC_MUSIC_MAXIMUM = 120

# The controls column beside the picture. Classic's width, and Flow's: wider,
# so the key hint takes two lines rather than three and Play sits beside Grab
# still — the height a short Flow page cannot spare, without dropping a word.
SIDE_WIDTH = 190
FLOW_SIDE_WIDTH = 300
# Classic's compact column: Play, Sound and Grab still on one row at their
# natural widths (at 190 px Sound was cut to "S...d", natively). The picture
# is held to its floor and letterboxed whenever the column is compact, so the
# width comes out of black bars, not out of the picture.
COMPACT_SIDE_WIDTH = 256

class ControlsColumn(QWidget):
    """The column of controls beside the picture.

    Its width is fixed, so how tall its wrapping labels are is known. Qt's
    minimum hint measures them at the narrowest width the layout could take
    instead — which this column never has — and wraps them into lines they
    never occupy: 208px claimed for 176 of content, measured natively. In
    Flow it reports the height at its real width; Classic keeps Qt's answer.
    """

    def __init__(self) -> None:
        super().__init__()
        self.measured_at_width = False

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        hint = super().minimumSizeHint()
        layout = self.layout()
        if not self.measured_at_width or layout is None:
            return hint
        fixed = self.minimumWidth() == self.maximumWidth()
        width = self.maximumWidth() if fixed else self.width()
        if width <= 0:
            return hint
        at_width = layout.heightForWidth(width)
        if at_width <= 0:
            return hint
        # The layout's own size hint is already raised to that narrow-width
        # minimum, so the height at the real width is the whole answer.
        return QSize(hint.width(), at_width)


# Said whenever monitoring is not running for an ordinary reason.
# What the sound is doing when nothing is wrong. The preview used to say it
# had no sound at all; since the monitor it can, when asked, so the standing
# note says which of the two it is and how to change it.
QUIET_PREVIEW = ("Sound is off, so the preview plays with no sound. Turn "
                 "Sound on beside Play (or press M on the picture) to hear "
                 "this output; the finished file is not affected either way.")
LISTENING_PREVIEW = ("Sound is on for this output as it plays. The level here "
                     "is for monitoring; it does not change the file.")
# Kept for callers that only need the quiet wording.
SILENT_PREVIEW = QUIET_PREVIEW


# What the sidebar says about where the keys are going.
#
# Not a substitute for the picture's focus ring — `FrameView.paintEvent` already
# draws one, and the F1 catalog already tells people to look for it. The ring
# says *where* the keys go; these say *what they do there*, which is the half
# #88 was missing: with the name field focused there was no way to know that
# Enter keeps a name and Escape puts the old one back, because neither did
# anything at all.
# The Sound control's own label: on or off, and nothing else. What is heard,
# on which output, and why not, is the status line under it.
SOUND_LABEL = "Sound"
SOUND_ON_TIP = ("Sound is on for the preview (M with the picture focused).\n"
                "The arrow chooses what you hear. Nothing here changes the export.")
SOUND_OFF_TIP = ("Sound is off: the preview is muted (M with the picture focused).\n"
                 "The arrow chooses what you hear. Nothing here changes the export.")

# Neither hint makes a claim about sound: whether the preview is heard is the
# listening row's to say. "Silent" here outlived the listening work and sat
# beside a ticked Listen box on the installed 2.0.0 candidate.
PICTURE_KEYS = "Click the picture, then Space plays"
# The same hint for an output's picture.
OUTPUT_PICTURE_KEYS = "Click the picture, then Space plays this output"
SOURCE_EDITS_ELSEWHERE = ("In, Out and Reset edit the recording's ranges on "
                          "Trim. This picture is the selected output.")
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


class _BandGrip(QWidget):
    """A handle for the Music band's depth, by mouse or keyboard."""

    requested = Signal(int)      # the body height asked for, in pixels
    STEP = 24

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Music band height")
        self.setToolTip("Drag up or down to make Music taller or shorter "
                        "(or focus it and use Up and Down).")
        self.body = lambda: None
        self._press = None

    def _height(self) -> int:
        body = self.body()
        return body.height() if body is not None else 0

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._press = (event.globalPosition().y(), self._height())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press is None:
            return
        y, height = self._press
        # Up makes it taller: the band grows into the room above it.
        self.requested.emit(int(height + (y - event.globalPosition().y())))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._press = None

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            delta = self.STEP if event.key() == Qt.Key.Key_Up else -self.STEP
            self.requested.emit(self._height() + delta)
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        from PySide6.QtGui import QPainter
        painter = QPainter(self)
        colour = self.palette().color(self.foregroundRole())
        painter.setPen(colour)
        middle = self.height() // 2
        for offset in (-4, 0, 4):
            painter.drawLine(5, middle + offset, self.width() - 5, middle + offset)
        if self.hasFocus():
            painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        painter.end()


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
    # Before the band's body is shown or hidden: showing it can resize the
    # window at once, and the window needs the size it had before that.
    music_band_changing = Signal(bool)
    music_focus_toggled = Signal(bool)
    music_depth_requested = Signal(int)
    listen_toggled = Signal(bool)
    listen_level_changed = Signal(int)
    listening_changed = Signal(str)
    restart_requested = Signal()
    sequence_scrub_requested = Signal(str, float)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        # The last name that was actually kept, so Escape has something to put
        # back. Held here rather than read from the clip: this panel is given
        # names, it does not own them.
        self._committed_name = ""
        self._classic_reach = CLASSIC_MUSIC_MAXIMUM
        self._flow_controls = False
        self._controls_below = False
        self._output_picture = False
        self.preview_box = self._build_preview_box()
        self.sequence_strip = SequenceStrip()
        self.sequence_strip.scrub_requested.connect(
            self.sequence_scrub_requested.emit)
        self.sequence_strip.hide()
        self.trim_band = self._build_trim_band()
        self.music_band = self._build_music_band()
        self._wire_sound_control()
        # The output strip is the output's picture on the output's clock. When
        # it is showing, the compact band need not show that picture again.
        self.sequence_strip.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 (Qt naming)
        if watched is self.sequence_strip and event.type() in (
                QEvent.Type.Show, QEvent.Type.Hide):
            self.music_timeline.set_picture_elsewhere(
                event.type() == QEvent.Type.Show)
        return False

    def _build_preview_box(self) -> QWidget:
        """The video and its transport, permanently visible."""
        box = AspectPreviewBox("Preview and trim")
        # Beside the picture, or under it: the same widgets either way.
        layout = self._box_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        box.setLayout(layout)
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
        side = self.sidebar = ControlsColumn()
        side.setFixedWidth(SIDE_WIDTH)
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
        # Held so Flow can close them up; Classic's are exactly addSpacing's.
        self._side_gaps = [QSpacerItem(0, TIGHT, QSizePolicy.Policy.Minimum,
                                       QSizePolicy.Policy.Fixed)]
        column.addItem(self._side_gaps[0])

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
        self._side_gaps.append(QSpacerItem(0, INNER, QSizePolicy.Policy.Minimum,
                                           QSizePolicy.Policy.Fixed))
        column.addItem(self._side_gaps[1])

        # Play and Grab still: one above the other in Classic, side by side in
        # Flow. Same widgets, same order, same spacing either way.
        self._side_actions = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self._side_actions.setContentsMargins(0, 0, 0, 0)
        self._side_actions.setSpacing(TIGHT)

        self.play_button = QPushButton("Play")
        self.play_button.setToolTip(
            "Play the highlighted clip here in the window.\n"
            "Space does the same once the picture has focus."
        )
        self.play_button.clicked.connect(lambda *_: self.play_requested.emit())

        # Sound for the preview, beside Play rather than inside Music:
        # hearing a recording needs no music chosen, and on the installed
        # 2.0.0 candidate the only way to listen was to open the music band.
        # One toggle (M with the picture focused), with what is heard — the
        # finished mix or the recording alone — on its menu. In Play's own
        # row at Play's height, so the column is no taller than it was: the
        # picture's measured fit depends on it.
        self.sound_button = QToolButton()
        self.sound_button.setCheckable(True)
        self.sound_button.setText(SOUND_LABEL)
        self.sound_button.setAccessibleName("Sound for the preview")
        self.sound_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.sound_button.setFixedHeight(self.play_button.sizeHint().height())
        self.sound_button.setToolTip(SOUND_OFF_TIP)
        sound_menu = QMenu(self.sound_button)
        self._sound_choices = QActionGroup(sound_menu)
        self._sound_choices.setExclusive(True)
        self.sound_actions: dict[str, QAction] = {}
        for data, text in (("mix", "Finished mix"), ("source", "Source only")):
            action = sound_menu.addAction(text)
            action.setCheckable(True)
            action.setData(data)
            self._sound_choices.addAction(action)
            self.sound_actions[data] = action
        self.sound_actions["mix"].setChecked(True)
        self.sound_button.setMenu(sound_menu)
        play_row = QHBoxLayout()
        play_row.setContentsMargins(0, 0, 0, 0)
        play_row.setSpacing(TIGHT)
        play_row.addWidget(self.play_button, 1)
        play_row.addWidget(self.sound_button)
        self._side_actions.addLayout(play_row)

        self.still_button = QPushButton("Grab still…")
        self.still_button.setEnabled(False)
        self.still_button.setToolTip(
            "Save the exact paused source frame as a full-resolution PNG.\n"
            "Pause or step to a real decoded frame first."
        )
        self.still_button.clicked.connect(
            lambda *_: self.grab_still_requested.emit())
        self._side_actions.addWidget(self.still_button)
        column.addLayout(self._side_actions)
        # What the sound is doing. Shown only while Sound is on, so a muted
        # preview costs the column nothing; the button's own state and
        # tooltip say muted.
        # One line beside the picture; the whole of it is the tooltip. Wrapped,
        # a reason took three lines of the column and set the picture's floor.
        self.sound_status = dim(ElidedLabel(""))
        self.sound_status.hide()
        column.addWidget(self.sound_status)

        trim_row = QHBoxLayout()
        trim_row.setContentsMargins(0, 0, 0, 0)
        trim_row.setSpacing(TIGHT)
        self._source_edits = []
        for text, requested, tip in (
            ("In", self.set_in_requested,
             "Start the export at the playhead  (I)"),
            ("Out", self.set_out_requested,
             "End the export at the playhead  (O)"),
            ("Reset", self.reset_requested, "Use the whole clip again"),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            self._source_edits.append((button, tip))
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

        return side

    def set_source_edits(self, applicable: bool) -> None:
        """In, Out and Reset act on the recording in source focus. Beside an
        output's picture that is not what is shown, so they are off and say
        where they work."""
        for button, tip in self._source_edits:
            button.setEnabled(bool(applicable))
            button.setToolTip(tip if applicable else SOURCE_EDITS_ELSEWHERE)

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
        self._editing_name = editing
        self.focus_note.setText(
            NAMING_KEYS if editing
            else OUTPUT_PICTURE_KEYS if self._output_picture else PICTURE_KEYS)

    def set_output_picture(self, bound: bool) -> None:
        """The picture is a selected output's, not the recording in focus.

        The column beside it then speaks for that output, and the controls
        that edit the focused recording's ranges say where they work instead
        of acting on a recording that is not the one shown.
        """
        bound = bool(bound)
        self._output_picture = bound
        self.set_source_edits(not bound)
        self._say_where_the_keys_are(getattr(self, "_editing_name", False))

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

    def _build_listening_row(self) -> QHBoxLayout:
        """Monitoring only, and no second transport.

        Play and Pause stay the picture's own button: there is one player and
        one clock, and a separate sound transport would be a second one to
        disagree with.
        """
        row = QHBoxLayout()
        row.setSpacing(TIGHT)

        self.listen_check = QCheckBox("Listen")
        self.listen_check.setToolTip(
            "Hear the mix while the preview plays. Off until you ask for it, "
            "and off again whenever the sound cannot be trusted."
        )
        self.listen_check.toggled.connect(
            lambda on: self.listen_toggled.emit(bool(on)))
        row.addWidget(self.listen_check)

        self.listening_combo = QComboBox()
        self.listening_combo.addItem("Finished mix", "mix")
        self.listening_combo.addItem("Source only", "source")
        self.listening_combo.currentIndexChanged.connect(
            lambda *_: self.listening_changed.emit(
                str(self.listening_combo.currentData())))
        row.addWidget(self.listening_combo)

        row.addWidget(dim(QLabel("Level")))
        self.listen_level = QSlider(Qt.Orientation.Horizontal)
        self.listen_level.setRange(0, 100)
        self.listen_level.setValue(25)
        self.listen_level.setMaximumWidth(120)
        self.listen_level.setToolTip(
            "How loud the monitoring is. It changes nothing about the export."
        )
        self.listen_level.valueChanged.connect(
            lambda value: self.listen_level_changed.emit(int(value)))
        row.addWidget(self.listen_level)

        self.restart_button = QPushButton("Start from the beginning")
        self.restart_button.setToolTip(
            "Back to the start of this output — the usual thing to want after "
            "changing a track."
        )
        self.restart_button.clicked.connect(
            lambda: self.restart_requested.emit())
        row.addWidget(self.restart_button)
        row.addStretch(1)
        return row

    def _wire_sound_control(self) -> None:
        """One state, two places to see it.

        The listening row in the music band stays the model the window
        already drives — `listen_check` and `listening_combo` — but is no
        longer shown there: two switches for the same sound is one too many.
        The Sound control beside Play drives that model and follows it.
        """
        self.listen_check.hide()
        self.listening_combo.hide()
        self.sound_button.toggled.connect(self._sound_toggled)
        self._sound_choices.triggered.connect(self._sound_choice_made)
        self.listen_check.toggled.connect(self._follow_listen_check)
        self.listening_combo.currentIndexChanged.connect(
            lambda *_: self._follow_listening_combo())

    def _sound_toggled(self, on: bool) -> None:
        self._show_sound_state(on)
        if self.listen_check.isChecked() != on:
            self.listen_check.setChecked(on)

    def _sound_choice_made(self, action: QAction) -> None:
        index = self.listening_combo.findData(action.data())
        if index >= 0 and index != self.listening_combo.currentIndex():
            self.listening_combo.setCurrentIndex(index)

    def _follow_listen_check(self, on: bool) -> None:
        if self.sound_button.isChecked() != on:
            blocked = self.sound_button.blockSignals(True)
            self.sound_button.setChecked(on)
            self.sound_button.blockSignals(blocked)
        self._show_sound_state(on)

    def _show_sound_state(self, on: bool) -> None:
        self.sound_button.setToolTip(SOUND_ON_TIP if on else SOUND_OFF_TIP)
        self.sound_status.setVisible(bool(on) and bool(self.sound_status.text()))

    def _follow_listening_combo(self) -> None:
        action = self.sound_actions.get(str(self.listening_combo.currentData()))
        if action is not None and not action.isChecked():
            action.setChecked(True)

    def toggle_sound(self) -> None:
        """M on the picture: the same as pressing the Sound button."""
        self.sound_button.toggle()

    def show_sound(self, text: str) -> None:
        """The one line that says what the preview's sound is doing."""
        self.sound_status.setText(text)
        self.sound_status.setToolTip(text)
        self.sound_status.setVisible(self.sound_button.isChecked() and bool(text))

    def show_monitoring(self, listening: bool, reason: str) -> None:
        """Say what the sound is doing, including when it is doing nothing.

        A reason replaces the standing note rather than sitting beside it:
        two lines about silence, one of them stale, is how a person stops
        reading either.
        """
        self.music_silence_note.setText(
            reason or (LISTENING_PREVIEW if listening else QUIET_PREVIEW))
        if bool(reason) != self._note_is_reason:
            self._note_is_reason = bool(reason)
            self._place_note()
        if reason:
            # What stops the music is never left scrolled out of the band,
            # wherever the band was scrolled to for the numbers. After the
            # layout has placed it: the note may just have moved.
            QTimer.singleShot(0, lambda: self.music_body.ensureWidgetVisible(
                self.music_silence_note, 0, 0))
        if self.listen_check.isChecked() != listening:
            blocked = self.listen_check.blockSignals(True)
            self.listen_check.setChecked(listening)
            self.listen_check.blockSignals(blocked)

    def _keep_reason_in_view(self, *_range) -> None:
        """Bring a refusal back into view when the band is resized after it
        was shown there. Measured natively at Classic 1120x760: Remux was
        revealed in the band's 54px, then a later relayout of the window
        (Remux's longer explanations taking height above the band) squeezed
        the band to 28px and left it 0 of 16px visible.
        A standing note, and a person's own scrolling, are left alone."""
        if self._note_is_reason:
            self.music_body.ensureWidgetVisible(self.music_silence_note, 0, 0)

    def set_flow_controls(self, flow: bool) -> None:
        """Arrange the controls beside the picture for Flow, or for Classic.

        Nothing is hidden either way. Flow's pages are short at the compact
        size — measured natively, Assemble needed 632px of a page's 556 — so
        the column is wider, the key hint wraps once instead of twice, Play
        sits beside Grab still and the two fixed gaps close up.
        """
        self._flow_controls = flow
        side = self.sidebar
        if not self._controls_below:
            side.setFixedWidth(self._side_width())
        side.measured_at_width = flow
        self._side_actions.setDirection(
            QBoxLayout.Direction.LeftToRight if flow
            else QBoxLayout.Direction.TopToBottom)
        for gap, size in zip(self._side_gaps, (TIGHT, INNER)):
            gap.changeSize(0, 0 if flow else size, QSizePolicy.Policy.Minimum,
                           QSizePolicy.Policy.Fixed)
        side.layout().invalidate()
        side.updateGeometry()

    def set_compact_controls(self, compact: bool) -> None:
        """Classic's controls column when Music needs the height.

        Only while the list has already folded for Music: the recording's
        format and date step aside (the list and its tooltip still have
        them), Play sits beside Grab still as it does in Flow, and the two
        fixed gaps close. Every control stays, and so does every line that
        says what is selected and where in it you are. Undone exactly.
        """
        compact = bool(compact)
        if compact == getattr(self, "_compact_controls", False):
            return
        self._compact_controls = compact
        for line in (self.clip_format, self.clip_date):
            line.setVisible(not compact)
        # The key hint is a line of the column too; compact, the picture
        # itself carries it as its tooltip (it is where the keys go).
        self.focus_note.setVisible(not compact)
        self.frame_view.setToolTip(
            f"{self.focus_note.text()}\n\n{self.focus_note.toolTip()}"
            if compact else "")
        side_by_side = compact or self._flow_controls
        self._side_actions.setDirection(
            QBoxLayout.Direction.LeftToRight if side_by_side
            else QBoxLayout.Direction.TopToBottom)
        for gap, size in zip(self._side_gaps, (TIGHT, INNER)):
            gap.changeSize(0, 0 if side_by_side else size,
                           QSizePolicy.Policy.Minimum,
                           QSizePolicy.Policy.Fixed)
        side = self.sidebar
        if not self._controls_below:
            side.setFixedWidth(self._side_width())
        side.layout().invalidate()
        side.updateGeometry()
        self.preview_box.updateGeometry()

    def _side_width(self) -> int:
        if self._flow_controls:
            return FLOW_SIDE_WIDTH
        if getattr(self, "_compact_controls", False):
            return COMPACT_SIDE_WIDTH
        return SIDE_WIDTH

    def set_controls_below(self, below: bool) -> None:
        """Put the controls column under the picture, or back beside it.

        For Flow's Browse, where the recordings list takes the width beside
        the picture: side by side the two needed 1178px of a compact page's
        794. Below, the column takes the picture's width instead of its own.
        """
        below = bool(below)
        self._controls_below = below
        side = self.sidebar
        self._box_layout.setDirection(
            QBoxLayout.Direction.TopToBottom if below
            else QBoxLayout.Direction.LeftToRight)
        if below:
            side.setMinimumWidth(0)
            side.setMaximumWidth(16777215)
        else:
            side.setFixedWidth(self._side_width())
        self.preview_box.set_controls_below(below)
        side.updateGeometry()
        self.preview_box.updateGeometry()

    def _build_music_band(self) -> QWidget:
        """The approved music band, under the filmstrip and collapsed by default.

        Collapsed means the body is hidden rather than merely disabled: the
        point of the toggle is the vertical space it gives back to the picture,
        and a disabled body still occupies its full height.
        """
        band = QGroupBox("Music")
        band.setCheckable(True)
        band.setChecked(False)
        # Flat, and with no padding of its own. Collapsed, this band is one
        # line the person can turn on; a framed box with margins around
        # nothing costs height the picture needs and buys no clarity. The
        # frame comes back with the contents.
        band.setFlat(True)
        layout = QVBoxLayout(band)
        layout.setContentsMargins(0, 0, 0, 0)

        self.music_content = QWidget()
        body = self._music_rows = QVBoxLayout(self.music_content)
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
        # Classic's band is shallow because it shares the window with the
        # list, the picture and the export column. Focus gives it the window
        # for as long as the music is being edited: the whole song, the
        # passage and fades at a usable depth, and the numbers. The filmstrip
        # stays; pressing it again puts everything back.
        self.focus_button = QPushButton("Focus")
        self.focus_button.setCheckable(True)
        self.focus_button.setToolTip(
            "Give the music the window while you edit it. The list, picture "
            "and export settings come back when you press it again.")
        self.focus_button.toggled.connect(
            lambda on: self.music_focus_toggled.emit(bool(on)))
        chooser.addWidget(self.focus_button)
        # Taller or shorter, with the picture and the export settings still
        # there: drag this up or down, or focus it and use Up and Down. In the
        # track row rather than a row of its own, so it costs no height.
        self.band_grip = _BandGrip()
        self.band_grip.requested.connect(
            lambda height: self.music_depth_requested.emit(int(height)))
        self.band_grip.body = lambda: self.music_body
        chooser.addWidget(self.band_grip)
        body.addLayout(chooser)

        # Said plainly rather than left to be discovered by pressing play. The
        # preview is the source picture and has no sound at all, so silence
        # here is not a fault in the music that was just chosen.
        body.addLayout(self._build_listening_row())

        # One line across the band, the whole of it in the tooltip: wrapped,
        # it cost the shallow band a second line at every size.
        self.music_silence_note = dim(ElidedLabel(SILENT_PREVIEW))
        body.addWidget(self.music_silence_note)
        self._note_is_reason = False
        self._arranged_as = None

        # One editor for every way the music is shown. The lanes are its
        # visual presentation; the numbers below are another view of the
        # same value, so a drag and a typed number cannot disagree.
        self.music_editor = MusicEditor(self)
        self.music_timeline = MusicTimeline(self.music_editor)
        self.music_timeline.more_button.toggled.connect(
            lambda _on: self._arrange_music())
        body.addWidget(self.music_timeline)

        self.music_panel = MusicPanel()
        self.music_panel.changed.connect(lambda: self.music_changed.emit())
        body.addWidget(self.music_panel)
        # Spare height goes below everything, not between the rows: spread
        # out, the rows pushed the song overview to the band's bottom edge.
        body.addStretch(1)

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
        # The range changes when the band's viewport or its contents are
        # resized, never when a person scrolls.
        self.music_body.verticalScrollBar().rangeChanged.connect(
            self._keep_reason_in_view)
        layout.addWidget(self.music_body)
        self.music_body.setVisible(False)
        band.toggled.connect(self.music_band_changing)
        band.toggled.connect(self.music_body.setVisible)
        self._music_band_layout = layout
        band.toggled.connect(lambda _on: self._music_band_margins())
        return band

    def _music_band_margins(self) -> None:
        """Open, a little air around the body; closed, none. Classic's
        shallow band spends no height on it: measured natively, those 10px
        were part of what grew a 1120x760 window."""
        shallow = (self.music_timeline.presentation is Presentation.CLASSIC)
        if self.music_band.isChecked() and not shallow:
            self._music_band_layout.setContentsMargins(0, TIGHT, 0, INNER)
        else:
            self._music_band_layout.setContentsMargins(0, 0, 0, 0)

    def set_music_presentation(self, presentation: Presentation) -> None:
        """Arrange the band for where it is shown. Chooses nothing, reads
        nothing: it only shows and hides."""
        self.music_timeline.set_presentation(presentation)
        if presentation is Presentation.CLASSIC:
            # The track row, measured rather than assumed: its height is
            # the font's and the style's.
            self.music_body.setMinimumHeight(max(
                CLASSIC_MUSIC_MINIMUM, self.track_button.sizeHint().height()))
            self.restore_classic_reach()
        else:
            self.music_body.setMinimumHeight(MUSIC_BAND_MINIMUM)
            self.music_body.setMaximumHeight(16777215)
        self._music_band_margins()
        self._arrange_music()

    def restore_classic_reach(self) -> None:
        """Let Classic's band have its approved depth again, after the list
        keeping a row held it down to its track row — or the room the window
        has decided to give it when the list is collapsed or Music is in
        focus."""
        self.music_body.setMaximumHeight(self._classic_reach)

    def essential_music_height(self) -> int:
        """The band's body down to the end of its listening row: Choose
        track, Focus and the grip, then Level. Measured off the rows as they
        are laid out, so it is the font's and the style's, not a guess."""
        content = self.music_content
        bottom = 0
        for widget in (self.track_button, self.focus_button, self.listen_level,
                       self.restart_button):
            if widget.isVisibleTo(content):
                rect = widget.geometry()
                corner = widget.mapTo(content, rect.bottomLeft() - rect.topLeft())
                bottom = max(bottom, corner.y() + 1)
        frame = self.music_body.frameWidth() * 2
        return bottom + frame if bottom else 0

    def set_classic_reach(self, deep: bool) -> None:
        """Whether Classic's band may grow past its shallow depth."""
        self._classic_reach = 16777215 if deep else CLASSIC_MUSIC_MAXIMUM
        self.restore_classic_reach()

    def set_classic_depth(self, height: int) -> None:
        """A depth the person chose for Classic's band: as tall as this when
        the window has the room, and never a demand on the window.

        The band keeps the shallow band's least as its minimum and takes the
        depth as its ceiling; its stretch (set by the window) lets it take
        free room up to it first. Set as min = max = depth, a depth fitted
        against a minimum that leaves out wrapped text grew the window on
        macOS and Ubuntu CI (791 to 810, 770 to 832 px; Sol, 9 October).
        """
        self._classic_reach = int(height)
        self.music_body.setMinimumHeight(max(
            CLASSIC_MUSIC_MINIMUM, self.track_button.sizeHint().height()))
        self.music_body.setMaximumHeight(int(height))

    def set_music_title(self, text: str) -> None:
        """Say which output the band edits, in its own heading."""
        self.music_band.setTitle(text)

    def show_music_focus(self, on: bool) -> None:
        if self.focus_button.isChecked() != bool(on):
            blocked = self.focus_button.blockSignals(True)
            self.focus_button.setChecked(bool(on))
            self.focus_button.blockSignals(blocked)

    def _place_note(self) -> None:
        """Classic's band is 120px: the track and listening rows and the
        shallow lane with its handles fill it. The standing listening note
        goes under the lane there; a refusal or a problem goes above it, so
        what stops the music is never below the band's fold. Everywhere else
        the note stays above the lanes."""
        rows, note = self._music_rows, self.music_silence_note
        below = (self.music_timeline.presentation is Presentation.CLASSIC
                 and not self._note_is_reason)
        rows.removeWidget(note)
        at = rows.indexOf(self.music_timeline)
        rows.insertWidget(at + 1 if below else at, note)

    def _arrange_music(self) -> None:
        # The numbers are behind More… in the compact presentation, and in
        # every other one they are simply there. Sound and If shorter are not
        # numbers: compact keeps them in view beside More…, as approved, and
        # they go back into their boxes everywhere else. Same widgets both
        # ways, so there is only ever one of each value.
        timeline, panel = self.music_timeline, self.music_panel
        arrangement = (timeline.presentation, timeline.shows_more)
        if arrangement != self._arranged_as:
            # A different arrangement starts at its top: the track and Listen
            # rows. Kept from the last one, the scroll left them above the
            # band's fold (natively, after More… closed and after Flow to
            # Classic). Only a real change does this, never a relayout.
            self._arranged_as = arrangement
            self.music_body.verticalScrollBar().setValue(0)
        if timeline.presentation is Presentation.COMPACT:
            row = timeline.more_row
            for index, widget in enumerate(panel.take_primary()):
                row.insertWidget(index, widget)
                widget.show()
        else:
            panel.restore_primary()
        panel.setVisible(timeline.shows_more)
        self._place_note()

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
        # Flow's Trim page adds one lane per range under the whole recording.
        # Hidden everywhere else, so Classic's filmstrip is exactly as it was.
        self.range_lanes = RangeLanes()
        self.range_lanes.follow(self.trim_bar)
        layout.addWidget(self.range_lanes)
        return band
