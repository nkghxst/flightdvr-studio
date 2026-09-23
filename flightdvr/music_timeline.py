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

"""Seeing the music and moving it: one editor, several ways to look at it.

`MusicEditor` holds what one output's music is showing and, during a gesture,
the draft being dragged. It is not an authority: the stored choice lives in
the window's `OutputPlan` (working) or a `Job` (submitted), and comes in
through `load`. Every presentation — Flow's lanes, the compact fold, Classic's
shallow band, a submitted job — calls the same few methods and listens to the
same signals, so they cannot disagree about a value.

Three clocks, each lane on exactly one:

- **output** (`OUTPUT_RATE` samples from the output's first sample): the
  picture lane, the music as it lands on the output, fades, repeats, playhead;
- **music** (the track's own native samples): the whole-song overview and the
  passage's start and end;
- **source** (the recording's seconds): only as the picture lane's frames,
  which are the recording's, placed on the output clock and labelled so.

`LiveMusicBinding` is what an edit does to the sound being listened to: a gain
or fade is applied in place, and undone in place on Escape; anything else is
left to the window to prepare again.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Callable

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from .audio_plan import OUTPUT_RATE, MusicChoice, SampleSpan
from .music_edit import (
    DEFAULT_FPS, EditKind, classify, music_view, native_at, pixel_to_sample,
    sample_to_pixel, step, valid_passage,
)
from .widgets import INNER, TIGHT, dim

HANDLE_REACH = 6          # pixels either side of a handle that pick it up


class Presentation(str, Enum):
    FULL = "full"                # Flow Music: every lane and the numbers
    COMPACT = "compact"          # Flow Music when short: lanes; More… for the rest
    CLASSIC = "classic"          # Classic's band: one shallow lane over numbers
    SUBMITTED = "submitted"      # a queued job: shown, never edited


# -- the one working value ----------------------------------------------------

class MusicEditor(QObject):
    """What one output's music shows, and the draft of a gesture on it.

    Signals are only for things a person did. `load` and `set_envelope` are
    quiet: showing a value is not choosing it.
    """

    view_changed = Signal()               # repaint: nothing was chosen
    drafted = Signal(object, object)      # (choice, EditKind) mid-gesture
    committed = Signal(object, object)    # (choice, EditKind) a real edit
    cancelled = Signal(object)            # the stored choice, put back

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stored = MusicChoice()
        self._draft: MusicChoice | None = None
        # Whether a gesture is under way, kept apart from what it is pinned
        # to: a pin can legitimately be None (nothing distinguishes outputs).
        self._active = False
        self._pin = None
        self.pin_source: Callable[[], object] = lambda: None
        self.output_samples = 0
        self.fps: Fraction = DEFAULT_FPS
        self.envelope = None
        self.envelope_note = ""
        self.strip = None
        self.strip_origin = 0.0          # source seconds at output sample 0
        self.occurrences: tuple = ()     # an Assembly's (start, end, label)
        self.playhead: int | None = None
        self.read_only = False
        self.editable = True
        self.label = ""

    # -- what is shown ---------------------------------------------------------

    @property
    def choice(self) -> MusicChoice:
        """The value on screen: the draft during a gesture, else the stored."""
        return self._draft if self._draft is not None else self._stored

    @property
    def stored(self) -> MusicChoice:
        return self._stored

    @property
    def gesture_active(self) -> bool:
        return self._active

    def load(self, choice: MusicChoice, *, output_samples: int,
             fps: Fraction = DEFAULT_FPS, label: str = "",
             read_only: bool = False, editable: bool = True) -> None:
        """Show a choice. Emits no edit: nobody has chosen anything.

        Loading another value abandons any gesture without putting anything
        back — the value it would restore belongs to what was showing before.
        """
        self._active = False
        self._pin = None
        self._draft = None
        self._stored = choice
        self.output_samples = max(0, int(output_samples))
        self.fps = fps
        self.label = label
        self.read_only = read_only
        self.editable = editable and not read_only
        self.view_changed.emit()

    def set_envelope(self, envelope, note: str = "") -> None:
        """The track's waveform, if there is one. Quiet."""
        self.envelope = envelope
        self.envelope_note = note
        self.view_changed.emit()

    def set_picture(self, strip=None, origin: float = 0.0,
                    occurrences: tuple = ()) -> None:
        """Existing frames or an Assembly's spans, for the picture lane."""
        self.strip = strip
        self.strip_origin = origin
        self.occurrences = tuple(occurrences)
        self.view_changed.emit()

    def set_playhead(self, output_sample: int | None) -> None:
        self.playhead = output_sample
        self.view_changed.emit()

    # -- gestures --------------------------------------------------------------

    def begin_gesture(self) -> bool:
        """Start dragging (or holding a key). Pins what is being edited."""
        if not self.editable:
            return False
        self._pin = self.pin_source()
        self._draft = self._stored
        self._active = True
        return True

    def _pin_holds(self) -> bool:
        if not self._active:
            return False
        if self.pin_source() != self._pin:
            # The output, its track or its material changed underneath the
            # gesture. Its draft and its way back both belong to what was
            # there before; neither is applied to what is there now.
            self.invalidate_gesture()
            return False
        return True

    def draft(self, choice: MusicChoice) -> None:
        if not self._pin_holds() or choice == self._draft:
            return
        self._draft = choice
        self.drafted.emit(choice, classify(self._stored, choice))
        self.view_changed.emit()

    def end_gesture(self) -> None:
        """Let go: the draft becomes one edit, or nothing if nothing moved."""
        if not self._pin_holds():
            return
        choice = self._draft
        self._active = False
        self._pin = None
        self._draft = None
        kind = classify(self._stored, choice)
        if kind is EditKind.NONE:
            self.view_changed.emit()
            return
        self._stored = choice
        self.committed.emit(choice, kind)
        self.view_changed.emit()

    def cancel_gesture(self) -> None:
        """Escape: back to the stored value, and say so, so what is heard can
        go back too. Only while the gesture's output is still the one here."""
        if not self._pin_holds():
            return
        moved = self._draft != self._stored
        self._active = False
        self._pin = None
        self._draft = None
        if moved:
            self.cancelled.emit(self._stored)
        self.view_changed.emit()

    def invalidate_gesture(self) -> None:
        """Drop a gesture without restoring anything anywhere."""
        if not self._active and self._draft is None:
            return
        self._active = False
        self._pin = None
        self._draft = None
        self.view_changed.emit()

    def commit(self, choice: MusicChoice) -> None:
        """A discrete edit (a typed number, a mode): one edit, straight away."""
        if not self.editable or self.gesture_active:
            return
        kind = classify(self._stored, choice)
        if kind is EditKind.NONE:
            return
        self._stored = choice
        self.committed.emit(choice, kind)
        self.view_changed.emit()


# -- what an edit does to the sound -------------------------------------------

class LiveMusicBinding:
    """Carry gains and fades to monitoring in place; put them back on Escape.

    `plan_for(target, choice)` resolves what monitoring would play for that
    choice, or None if it cannot. `monitor` is the transport: anything with
    `update_parameters(target, plan) -> bool`. Structure is not this class's
    business — a structural edit returns False from `committed`, and the
    window prepares the stream again.
    """

    def __init__(self, monitor, plan_for) -> None:
        self.monitor = monitor
        self.plan_for = plan_for

    def _apply(self, target, choice) -> bool:
        if target is None or self.monitor is None:
            return False
        plan = self.plan_for(target, choice)
        if plan is None:
            return False
        return bool(self.monitor.update_parameters(target, plan))

    def drafted(self, target, choice, kind) -> None:
        if kind is EditKind.PARAMETER:
            self._apply(target, choice)

    def cancelled(self, target, stored) -> None:
        # The stored value is what was playing before the gesture: gains and
        # fades are all a draft could have changed in the stream, so applying
        # the stored ones puts back exactly what was heard.
        self._apply(target, stored)

    def committed(self, target, choice, kind) -> bool:
        """True if the edit was a parameter and needs nothing more."""
        if kind is not EditKind.PARAMETER:
            return False
        self._apply(target, choice)
        return True


# -- painting helpers ------------------------------------------------------------

def _colours(widget: QWidget):
    palette = widget.palette()
    return (palette.color(QPalette.ColorRole.Highlight),
            palette.color(QPalette.ColorRole.Mid),
            palette.color(QPalette.ColorRole.WindowText),
            palette.color(QPalette.ColorRole.Base))


def _bin_extent(envelope, native: int) -> tuple[float, float] | None:
    """The waveform's extent at a track sample, across channels."""
    if envelope is None:
        return None
    if not envelope.coverage_start <= native < envelope.coverage_end:
        return None
    bins = envelope.bins
    span = envelope.coverage_end - envelope.coverage_start
    index = min(len(bins) - 1,
                (native - envelope.coverage_start) * len(bins) // span)
    item = bins[index]
    return min(item.minimum), max(item.maximum)


def _hatch(painter: QPainter, rect: QRectF, colour: QColor) -> None:
    brush = QBrush(colour, Qt.BrushStyle.BDiagPattern)
    painter.fillRect(rect, brush)


class _Lane(QWidget):
    """A lane on one clock, with the editor's value and optional handles."""

    height_hint = 56

    def __init__(self, editor: MusicEditor, parent=None) -> None:
        super().__init__(parent)
        self.editor = editor
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(self.height_hint)
        self.setMaximumHeight(self.height_hint)
        editor.view_changed.connect(self.update)
        self._grab: str | None = None
        self.active = self.handles()[0] if self.handles() else None

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(200, self.height_hint)

    # Subclasses: the lane's clock, handles and how a handle moves.
    def span(self) -> SampleSpan | None:
        return None

    def rate(self) -> int:
        return OUTPUT_RATE

    def handles(self) -> tuple[str, ...]:
        return ()

    def handle_sample(self, name: str) -> int | None:
        return None

    def moved(self, choice: MusicChoice, name: str, sample: int) -> MusicChoice:
        return choice

    # -- input -----------------------------------------------------------------

    def _x(self, sample: int) -> float:
        span = self.span()
        return 0.0 if span is None else sample_to_pixel(sample, self.width(), span)

    def _handle_at(self, x: float) -> str | None:
        best, reach = None, HANDLE_REACH + 1
        for name in self.handles():
            at = self.handle_sample(name)
            if at is None:
                continue
            distance = abs(self._x(at) - x)
            if distance < reach:
                best, reach = name, distance
        return best

    def mousePressEvent(self, event) -> None:  # noqa: N802
        name = self._handle_at(event.position().x())
        if name is None or not self.editor.begin_gesture():
            return super().mousePressEvent(event)
        self._grab = name
        self.active = name
        self.setFocus()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        span = self.span()
        if self._grab is None or span is None:
            return
        sample = pixel_to_sample(event.position().x(), self.width(), span)
        self.editor.draft(self.moved(self.editor.choice, self._grab, sample))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._grab is None:
            return
        self._grab = None
        self.editor.end_gesture()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._grab = None
            self.editor.cancel_gesture()
            return
        if key == Qt.Key.Key_Tab and self.handles():
            names = self.handles()
            self.active = names[(names.index(self.active) + 1) % len(names)]
            self.update()
            return
        if key not in (Qt.Key.Key_Left, Qt.Key.Key_Right) or self.active is None:
            return super().keyPressEvent(event)
        if not self.editor.gesture_active and not self.editor.begin_gesture():
            return
        at = self.handle_sample(self.active)
        if at is None:
            return
        direction = 1 if key == Qt.Key.Key_Right else -1
        seconds = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        moved = step(at, direction, self.rate(), self.editor.fps, seconds=seconds)
        self.editor.draft(self.moved(self.editor.choice, self.active, moved))

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        # A held key repeats presses and releases; only the real release is
        # the end of the gesture, so a held key is one edit.
        if event.isAutoRepeat():
            return
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self._grab is None:
            self.editor.end_gesture()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        if self._grab is None and self.editor.gesture_active:
            self.editor.end_gesture()
        super().focusOutEvent(event)

    def _paint_handles(self, painter: QPainter, colour: QColor) -> None:
        for name in self.handles():
            at = self.handle_sample(name)
            if at is None:
                continue
            x = self._x(at)
            size = 7 if name == self.active and self.hasFocus() else 5
            painter.setPen(QPen(colour, 1))
            painter.setBrush(colour if name == self.active else Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(x - size / 2, 1, size, size))


class PictureLane(_Lane):
    """The recording's own frames placed on the output clock. Nothing drawn
    here is made from the music, and nothing is decoded for it."""

    height_hint = 40

    def span(self):
        count = self.editor.output_samples
        return SampleSpan(0, count, OUTPUT_RATE) if count > 0 else None

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        accent, mid, text, base = _colours(self)
        rect = QRectF(self.rect())
        painter.fillRect(rect, base)
        span = self.span()
        editor = self.editor
        if span is not None and editor.occurrences:
            for number, (start, end, label) in enumerate(editor.occurrences):
                x0, x1 = self._x(start), self._x(end)
                shade = QColor(mid)
                shade.setAlpha(90 if number % 2 else 150)
                painter.fillRect(QRectF(x0, 0, x1 - x0, rect.height()), shade)
                painter.setPen(text)
                painter.drawText(QRectF(x0 + 3, 0, x1 - x0 - 6, rect.height()),
                                 int(Qt.AlignmentFlag.AlignVCenter), label)
        elif span is not None and editor.strip:
            frames = editor.strip.frames
            columns = max(1, int(rect.width() // 48))
            width = rect.width() / columns
            for column in range(columns):
                seconds = editor.strip_origin + (
                    (column + 0.5) * width / rect.width()) * (
                        span.samples / OUTPUT_RATE)
                path = editor.strip.frame_at(seconds)
                if path is None:
                    continue
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    painter.drawPixmap(QRectF(column * width, 0, width, rect.height()),
                                       pixmap, QRectF(pixmap.rect()))
        if editor.playhead is not None and span is not None:
            painter.setPen(QPen(text, 1))
            x = self._x(editor.playhead)
            painter.drawLine(QPointF(x, 0), QPointF(x, rect.height()))
        painter.end()


class OutputMusicLane(_Lane):
    """The music as the finished output carries it, on the output clock.

    Repeats, the fitted fades, a partial last repeat and planned silence are
    drawn from the resolver's own answer. The fade handles move the request;
    where the export fits a shorter fade, both are marked.
    """

    height_hint = 64

    def handles(self):
        return ("fade_in", "fade_out")

    def span(self):
        count = self.editor.output_samples
        return SampleSpan(0, count, OUTPUT_RATE) if count > 0 else None

    def handle_sample(self, name):
        choice, count = self.editor.choice, self.editor.output_samples
        if music_view(choice, count) is None:
            return None
        if name == "fade_in":
            return min(count, choice.fade_in_samples)
        return max(0, count - choice.fade_out_samples)

    def moved(self, choice, name, sample):
        from dataclasses import replace
        count = self.editor.output_samples
        sample = min(max(0, sample), count)
        if name == "fade_in":
            return replace(choice, fade_in_samples=sample)
        return replace(choice, fade_out_samples=count - sample)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        accent, mid, text, base = _colours(self)
        rect = QRectF(self.rect())
        painter.fillRect(rect, base)
        editor = self.editor
        choice = editor.choice
        view = music_view(choice, editor.output_samples)
        middle = rect.height() / 2
        if view is None:
            painter.setPen(mid)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter),
                             "No music on this output" if choice.asset is None
                             and choice.track is None else "Music not read yet")
            painter.end()
            return
        span = self.span()
        width = int(rect.width())
        unknown = editor.envelope is None
        for x in range(width):
            output = pixel_to_sample(x + 0.5, width, span)
            native = native_at(choice, min(output, span.end - 1), view)
            if native is None:
                continue                              # planned silence
            extent = None if unknown else _bin_extent(editor.envelope, native)
            if extent is None:
                continue
            gain = 1.0
            if view.fade_in_effective and output < view.fade_in_effective:
                gain = output / view.fade_in_effective
            if view.fade_out_effective and (
                    view.audible_samples - output < view.fade_out_effective):
                gain = min(gain, (view.audible_samples - output)
                           / view.fade_out_effective)
            low, high = extent
            painter.setPen(accent)
            painter.drawLine(QPointF(x, middle - high * gain * middle),
                             QPointF(x, middle - low * gain * middle))
        if unknown:
            _hatch(painter, rect, mid)
            painter.setPen(text)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter),
                             editor.envelope_note or "Waveform not available yet")
        if view.silence_from is not None:
            x0 = self._x(view.silence_from)
            painter.setPen(mid)
            painter.drawText(QRectF(x0 + 4, 0, rect.width() - x0 - 4, rect.height()),
                             int(Qt.AlignmentFlag.AlignVCenter), "silence")
        dashed = QPen(text, 1, Qt.PenStyle.DashLine)
        painter.setPen(dashed)
        for start in list(view.repeat_starts())[1:]:
            x = self._x(start)
            painter.drawLine(QPointF(x, 0), QPointF(x, rect.height()))
        # The fitted fades, solid; a request longer than fits, a tick.
        painter.setPen(QPen(text, 1))
        x_in = self._x(view.fade_in_effective)
        painter.drawLine(QPointF(0, rect.height()), QPointF(x_in, 0))
        x_out = self._x(view.audible_samples - view.fade_out_effective)
        x_end = self._x(view.audible_samples)
        painter.drawLine(QPointF(x_out, 0), QPointF(x_end, rect.height()))
        for requested, effective, from_end in (
                (view.fade_in_requested, view.fade_in_effective, False),
                (view.fade_out_requested, view.fade_out_effective, True)):
            if requested != effective:
                at = (view.audible_samples - min(requested, view.audible_samples)
                      if from_end else min(requested, view.output_samples))
                x = self._x(at)
                painter.setPen(QPen(accent, 1, Qt.PenStyle.DotLine))
                painter.drawLine(QPointF(x, 0), QPointF(x, rect.height()))
        if editor.playhead is not None:
            painter.setPen(QPen(text, 1))
            x = self._x(editor.playhead)
            painter.drawLine(QPointF(x, 0), QPointF(x, rect.height()))
        if editor.editable:
            self._paint_handles(painter, accent)
        painter.end()


class SongOverview(_Lane):
    """The whole track on its own clock, with the passage the output uses."""

    height_hint = 44

    def handles(self):
        return ("start", "end")

    def span(self):
        asset = self.editor.choice.asset
        if asset is None:
            return None
        return SampleSpan(0, asset.decoded_samples, asset.sample_rate)

    def rate(self):
        asset = self.editor.choice.asset
        return asset.sample_rate if asset is not None else OUTPUT_RATE

    def handle_sample(self, name):
        passage = self.editor.choice.passage
        if passage is None:
            return None
        return passage.start if name == "start" else passage.end

    def moved(self, choice, name, sample):
        from dataclasses import replace
        asset, passage = choice.asset, choice.passage
        if asset is None or passage is None:
            return choice
        total = asset.decoded_samples
        if name == "start":
            start, end = sample, passage.end
            start = min(start, end - 1)
        else:
            start, end = passage.start, max(sample, passage.start + 1)
        start, end = valid_passage(start, end, total)
        return replace(choice, passage=SampleSpan(start, end, passage.rate))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        accent, mid, text, base = _colours(self)
        rect = QRectF(self.rect())
        painter.fillRect(rect, base)
        span = self.span()
        editor = self.editor
        if span is None:
            painter.setPen(mid)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter),
                             "The song appears here once it has been read")
            painter.end()
            return
        passage = editor.choice.passage
        if passage is not None:
            shade = QColor(accent)
            shade.setAlpha(60)
            x0, x1 = self._x(passage.start), self._x(passage.end)
            painter.fillRect(QRectF(x0, 0, x1 - x0, rect.height()), shade)
        middle = rect.height() / 2
        if editor.envelope is None:
            _hatch(painter, rect, mid)
        else:
            width = int(rect.width())
            painter.setPen(text)
            for x in range(width):
                native = pixel_to_sample(x + 0.5, width, span)
                extent = _bin_extent(editor.envelope, min(native, span.end - 1))
                if extent is None:
                    continue
                low, high = extent
                painter.drawLine(QPointF(x, middle - high * middle),
                                 QPointF(x, middle - low * middle))
        if editor.editable:
            self._paint_handles(painter, accent)
        painter.end()


# -- the presentations ---------------------------------------------------------

class MusicTimeline(QWidget):
    """The lanes for one editor, arranged for where they are shown.

    Switching presentation, and More…, only show and hide lanes: nothing is
    chosen, read or rebuilt.
    """

    def __init__(self, editor: MusicEditor, parent=None) -> None:
        super().__init__(parent)
        self.editor = editor
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(TIGHT)

        self.output_caption = QLabel("<b>Output time</b>")
        layout.addWidget(self.output_caption)
        self.picture_caption = dim(QLabel(
            "Picture — the recording's frames, placed on the output's time"))
        layout.addWidget(self.picture_caption)
        self.picture = PictureLane(editor)
        layout.addWidget(self.picture)
        self.music_caption = dim(QLabel("Music — as the finished output carries it"))
        layout.addWidget(self.music_caption)
        self.music = OutputMusicLane(editor)
        layout.addWidget(self.music)
        self.legend = dim(QLabel(
            "Drag a fade handle, or select this lane and use the arrow keys "
            "(Shift for whole seconds). Esc puts a drag back."))
        self.legend.setWordWrap(True)
        layout.addWidget(self.legend)

        more_row = QHBoxLayout()
        more_row.setSpacing(INNER)
        self.more_button = QPushButton("More…")
        self.more_button.setCheckable(True)
        self.more_button.toggled.connect(self._show_more)
        more_row.addWidget(self.more_button)
        self.more_note = dim(QLabel(
            "The whole song and the numbers are behind More… at this size."))
        more_row.addWidget(self.more_note, 1)
        layout.addLayout(more_row)

        self.song_caption = QLabel("<b>Music time</b> — the whole track")
        layout.addWidget(self.song_caption)
        self.song = SongOverview(editor)
        layout.addWidget(self.song)
        self.song_note = dim(QLabel(""))
        layout.addWidget(self.song_note)
        editor.view_changed.connect(self._say_song)

        self._presentation = Presentation.FULL
        self._more = False
        self.set_presentation(Presentation.FULL)

    @property
    def presentation(self) -> Presentation:
        return self._presentation

    def set_presentation(self, presentation: Presentation) -> None:
        self._presentation = Presentation(presentation)
        self._arrange()

    def _show_more(self, on: bool) -> None:
        self._more = on
        self._arrange()

    def _arrange(self) -> None:
        mode = self._presentation
        compact = mode is Presentation.COMPACT
        classic = mode is Presentation.CLASSIC
        folded = compact and not self._more
        for widget in (self.output_caption, self.picture_caption, self.picture):
            widget.setVisible(not classic)
        for widget in (self.more_button, self.more_note):
            widget.setVisible(compact)
        self.more_note.setVisible(folded)
        for widget in (self.song_caption, self.song, self.song_note):
            widget.setVisible(not classic and not folded)
        self.legend.setVisible(mode is not Presentation.SUBMITTED)

    @property
    def shows_more(self) -> bool:
        return self._presentation is not Presentation.COMPACT or self._more

    def _say_song(self) -> None:
        choice = self.editor.choice
        asset, passage = choice.asset, choice.passage
        if asset is None or passage is None:
            self.song_note.setText("")
            return
        rate = asset.sample_rate
        self.song_caption.setText(
            f"<b>Music time</b> — the whole track, "
            f"{asset.decoded_samples / rate:.1f} s")
        self.song_note.setText(
            f"The output uses {passage.start / rate:.3f}–{passage.end / rate:.3f} s "
            "of the song, shaded.")
