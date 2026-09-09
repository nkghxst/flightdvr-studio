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

"""The controls that edit one output's export audio, and nothing else.

This is a widget over `audio_plan.MusicChoice`. It loads a choice, shows it,
and hands an edited one back. It is deliberately not wired to anything — no
window, player, session or `OutputPlan` — so the same component can serve
Classic and Flow when the integration lease arrives.

Five things the merged contract decides, and this panel therefore does not:

- **Fades are integer samples at `OUTPUT_RATE`.** Seconds are a display
  conversion; the stored value stays samples, so nothing is lost to rounding
  on the way through a spin box.
- **`resolve_audio_plan` clamps overlapping fades**, in `_effective_fades`.
  What the person asked for and what the export can fit are different numbers,
  and this shows and returns the *request*. Re-deriving the clamped value here
  would silently rewrite it and the next capture would persist the rewrite.
- **Levels are `Fraction`.** A percent box is display only. Storing a float
  would drift a value the export uses exactly.
- **`AudioAsset` needs a full SHA-256 and a decoded sample count**, which means
  probing. Probing does not belong on the UI thread and is not in this lease,
  so this panel never builds one: an asset is injected. Until it is, a Replace
  choice is legitimately track-only, which the contract already models as
  unresolved.
- **A passage is a `SampleSpan` on the asset's own rate.** Without an asset
  there is no clock to express it on, so the passage controls stay disabled
  rather than inventing one.

There is no mute or volume here. `MusicChoice` has no field for either,
because monitoring is not an export choice — so there is nothing to leak
because there is nowhere to put it. Listening belongs to the shared transport,
which does not exist yet, and this panel draws no playback control that would
not work.
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QSpinBox, QVBoxLayout, QWidget,
)

from .audio_plan import (
    OUTPUT_RATE, AudioAsset, AudioMode, MusicChoice, SampleSpan,
    ShortTrackPolicy,
)
from .widgets import INNER, TIGHT, dim


# The words are the approved design's, kept rather than trimmed to icons: what
# a mode does to the finished file is the whole decision being made here.
MODE_LABELS: list[tuple[AudioMode, str, str]] = [
    (AudioMode.ORIGINAL, "Original audio",
     "Keep the recording's own sound."),
    (AudioMode.NO_SOUND, "No sound",
     "Write no audio track at all."),
    (AudioMode.REPLACE, "Replace with music",
     "The music replaces the recording's sound."),
    (AudioMode.MIX, "Mix music with original",
     "Both are heard; the recording sits under the music."),
]

SHORT_TRACK_LABELS: list[tuple[ShortTrackPolicy, str]] = [
    (ShortTrackPolicy.LOOP, "Loop"),
    (ShortTrackPolicy.PLAY_ONCE, "Play once"),
]

# S2 exports music for one 1x range on Master only. Anything else raises in
# `resolve_audio_plan`, so the panel says so rather than offering controls that
# would fail at commit time.
SUPPORTED_PRESET = "master"


def seconds_of(samples: int, rate: int = OUTPUT_RATE) -> float:
    """Samples to seconds, for display only."""
    return samples / rate if rate else 0.0


def samples_of(seconds: float, rate: int = OUTPUT_RATE) -> int:
    """Seconds back to whole samples, the unit the contract stores."""
    return max(0, round(float(seconds) * rate))


class MusicPanel(QWidget):
    """Edit one output's `MusicChoice`.

    `load` shows a choice without claiming the person made it; `capture`
    returns what they did make. `changed` fires only for real edits, so a
    caller can save on it without a programmatic load writing back over the
    value it just supplied.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # Set while widgets are being written to from code. Every handler
        # checks it, because a QComboBox emits on setCurrentIndex just as
        # loudly as it does on a click, and a caller that saves on `changed`
        # would then persist a value nobody chose.
        self._loading = False
        self._choice = MusicChoice()
        self._asset: AudioAsset | None = None
        self._supported = True
        # Samples, kept beside the widgets rather than read back out of them.
        #
        # A two-decimal box cannot hold a sample. One sample at 44.1 kHz is
        # 0.0000227 s, which displays as 0.00 and reads back as zero; 440 999
        # samples displays as 10.00 and reads back as 441 000. Reading the
        # widget at capture therefore rewrote values nobody had touched. These
        # are the values; the boxes show them, and only a real edit moves them.
        self._fade_in_samples = MusicChoice().fade_in_samples
        self._fade_out_samples = MusicChoice().fade_out_samples
        self._passage: SampleSpan | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(INNER)

        self.target_label = QLabel("No output selected")
        outer.addWidget(self.target_label)
        self.scope_note = dim(QLabel("Music applies to this output only."))
        self.scope_note.setWordWrap(True)
        outer.addWidget(self.scope_note)

        self.unsupported_label = QLabel("")
        self.unsupported_label.setWordWrap(True)
        self.unsupported_label.hide()
        outer.addWidget(self.unsupported_label)

        outer.addWidget(self._build_sound_box())
        outer.addWidget(self._build_passage_box())
        outer.addWidget(self._build_levels_box())
        outer.addStretch(1)

        self._show_choice()

    # -- construction ---------------------------------------------------------

    def _build_sound_box(self) -> QWidget:
        box = QGroupBox("Sound")
        form = QFormLayout(box)
        form.setSpacing(TIGHT)

        self.mode_combo = QComboBox()
        for mode, label, _ in MODE_LABELS:
            self.mode_combo.addItem(label, mode)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("Sound:", self.mode_combo)

        self.mode_help = dim(QLabel(""))
        self.mode_help.setWordWrap(True)
        form.addRow(self.mode_help)

        # The track is shown, never chosen here: choosing one means a file
        # dialog and a probe, which are the next lease.
        self.track_label = QLabel("No track chosen")
        self.track_label.setWordWrap(True)
        form.addRow("Track:", self.track_label)

        self.asset_note = dim(QLabel(""))
        self.asset_note.setWordWrap(True)
        form.addRow(self.asset_note)
        return box

    def _build_passage_box(self) -> QWidget:
        box = self.passage_box = QGroupBox("The passage used")
        form = QFormLayout(box)
        form.setSpacing(TIGHT)

        self.passage_start = QDoubleSpinBox()
        self.passage_start.setSuffix(" s")
        self.passage_start.setDecimals(2)
        self.passage_start.setMaximum(0.0)
        self.passage_start.valueChanged.connect(self._on_passage_changed)
        form.addRow("Starts at:", self.passage_start)

        self.passage_end = QDoubleSpinBox()
        self.passage_end.setSuffix(" s")
        self.passage_end.setDecimals(2)
        self.passage_end.setMaximum(0.0)
        self.passage_end.valueChanged.connect(self._on_passage_changed)
        form.addRow("Ends at:", self.passage_end)

        self.short_track_combo = QComboBox()
        for policy, label in SHORT_TRACK_LABELS:
            self.short_track_combo.addItem(label, policy)
        self.short_track_combo.currentIndexChanged.connect(self._on_edited)
        form.addRow("If shorter:", self.short_track_combo)

        self.passage_note = dim(QLabel(
            "The passage needs the track's own sample clock, so it can only be "
            "set once the track has been read."))
        self.passage_note.setWordWrap(True)
        form.addRow(self.passage_note)
        return box

    def _build_levels_box(self) -> QWidget:
        box = QGroupBox("Levels and fades")
        form = QFormLayout(box)
        form.setSpacing(TIGHT)

        self.music_level = QSpinBox()
        self.music_level.setRange(0, 100)
        self.music_level.setSuffix(" %")
        self.music_level.valueChanged.connect(self._on_edited)
        form.addRow("Music level:", self.music_level)

        self.dvr_level = QSpinBox()
        self.dvr_level.setRange(0, 100)
        self.dvr_level.setSuffix(" %")
        self.dvr_level.valueChanged.connect(self._on_edited)
        form.addRow("Recording level:", self.dvr_level)

        fades = QHBoxLayout()
        self.fade_in = QDoubleSpinBox()
        self.fade_in.setSuffix(" s")
        self.fade_in.setDecimals(2)
        self.fade_in.setMaximum(3600.0)
        self.fade_in.valueChanged.connect(self._on_fade_changed)
        fades.addWidget(QLabel("Fade in:"))
        fades.addWidget(self.fade_in)

        self.fade_out = QDoubleSpinBox()
        self.fade_out.setSuffix(" s")
        self.fade_out.setDecimals(2)
        self.fade_out.setMaximum(3600.0)
        self.fade_out.valueChanged.connect(self._on_fade_changed)
        fades.addWidget(QLabel("Fade out:"))
        fades.addWidget(self.fade_out)
        fades.addStretch(1)
        form.addRow(fades)

        self.fade_note = dim(QLabel(
            "These are what you are asking for. A pair longer than the finished "
            "output is shortened when the export is built; the request is kept "
            "as you set it."))
        self.fade_note.setWordWrap(True)
        form.addRow(self.fade_note)
        return box

    # -- the interface the integration lease will use -------------------------

    def load(self, choice: MusicChoice, *, target: str = "",
             preset_key: str = SUPPORTED_PRESET, joined: bool = False,
             bundle: bool = False) -> None:
        """Show a choice. Emits nothing: the person has not chosen anything.

        The context arrives with the choice because whether music is offerable
        at all is a property of the target, not of the choice.
        """
        if not isinstance(choice, MusicChoice):
            raise TypeError("load needs a MusicChoice")
        self._choice = choice
        self._asset = choice.asset
        self.set_context(target=target, preset_key=preset_key, joined=joined,
                         bundle=bundle)
        self._show_choice()

    def capture(self) -> MusicChoice:
        """The edited choice, built through the frozen type.

        `MusicChoice.__post_init__` is the gate: if the widgets describe
        something the contract refuses, this raises here rather than producing
        a value that fails later at export.
        """
        mode = self._mode()
        if mode in (AudioMode.ORIGINAL, AudioMode.NO_SOUND):
            # Those two carry no track, asset or passage at all, and the
            # contract refuses them together rather than ignoring them.
            return MusicChoice(mode=mode)
        return MusicChoice(
            track=self._choice.track,
            mode=mode,
            asset=self._asset,
            passage=self._passage if self._asset is not None else None,
            short_track=self.short_track_combo.currentData(),
            music_level=Fraction(self.music_level.value(), 100),
            dvr_level=Fraction(self.dvr_level.value(), 100),
            fade_in_samples=self._fade_in_samples,
            fade_out_samples=self._fade_out_samples,
        )

    def set_asset(self, asset: AudioAsset | None) -> None:
        """Supply a validated track, read somewhere that may block.

        The panel never probes or hashes: it is handed the result. Injecting
        one turns the passage controls on, because only then is there a sample
        clock to express a passage on.
        """
        if asset is not None and not isinstance(asset, AudioAsset):
            raise TypeError("set_asset needs an AudioAsset or None")
        self._asset = asset
        if asset is not None and self._choice.track is None:
            self._choice = replace(self._choice, track=asset.track)
        self._show_choice()

    def set_context(self, *, target: str = "",
                    preset_key: str = SUPPORTED_PRESET, joined: bool = False,
                    bundle: bool = False) -> None:
        """Name the output, and say whether music can be exported for it."""
        self.target_label.setText(
            f"Editing: {target}" if target else "No output selected")
        reason = self._refusal(preset_key, joined, bundle)
        self._supported = reason == ""
        self.unsupported_label.setText(reason)
        self.unsupported_label.setVisible(bool(reason))
        self._apply_enabled()

    @staticmethod
    def _refusal(preset_key: str, joined: bool, bundle: bool) -> str:
        """The same refusals `resolve_audio_plan` makes, said in advance.

        Worded as what is not built yet rather than as an error, because the
        person has not done anything wrong by selecting an Assembly.
        """
        if bundle:
            return ("Music is not exported for a delivery bundle yet. Each "
                    "member would need its own choice.")
        if joined:
            return ("Music is not exported for an Assembly yet. Only a single "
                    "range can carry it.")
        if preset_key != SUPPORTED_PRESET:
            return (f"Music is not exported for the {preset_key} preset yet. "
                    "Master is the one that carries it.")
        return ""

    # -- showing and reading the widgets --------------------------------------

    def _mode(self) -> AudioMode:
        """The chosen mode, as the enum rather than as whatever Qt returns.

        `AudioMode` is a `str` Enum, and a QVariant round trip hands the plain
        string back: `currentData() is AudioMode.MIX` is False while `==` is
        True. Normalising here once means no caller has to know that, and the
        identity comparison that quietly disabled the recording level cannot
        come back.
        """
        return AudioMode(self.mode_combo.currentData())


    def _passage_from_widgets(self) -> SampleSpan | None:
        """What the boxes are asking for, read only when somebody edits them.

        Never called on the load path: the boxes cannot express the value they
        were given, so reading them there would quantise it.
        """
        if self._asset is None:
            return None
        rate = self._asset.sample_rate
        start = samples_of(self.passage_start.value(), rate)
        end = samples_of(self.passage_end.value(), rate)
        end = min(end, self._asset.decoded_samples)
        if end <= start:
            return None
        return SampleSpan(start, end, rate)

    def _show_choice(self) -> None:
        """Write the current choice into the widgets, quietly."""
        was = self._loading
        self._loading = True
        try:
            choice = self._choice
            # The values, taken from the choice. The boxes below only display
            # them, and cannot be asked for them back.
            self._fade_in_samples = choice.fade_in_samples
            self._fade_out_samples = choice.fade_out_samples
            self._passage = choice.passage
            if self._passage is None and self._asset is not None:
                # A whole track is what the boxes will show, so it is what a
                # capture should return; anything else would disagree with what
                # is on screen.
                self._passage = SampleSpan(0, self._asset.decoded_samples,
                                           self._asset.sample_rate)
            mode = choice.mode or AudioMode.ORIGINAL
            index = self.mode_combo.findData(mode)
            if index >= 0:
                self.mode_combo.setCurrentIndex(index)
            self.mode_help.setText(dict(
                (m, help_text) for m, _, help_text in MODE_LABELS).get(mode, ""))

            self.track_label.setText(
                str(choice.track) if choice.track else "No track chosen")

            if self._asset is None:
                self.asset_note.setText(
                    "This track has not been read yet, so its length and "
                    "passage are not known here."
                    if choice.track else "")
                self.passage_start.setMaximum(0.0)
                self.passage_end.setMaximum(0.0)
                self.passage_start.setValue(0.0)
                self.passage_end.setValue(0.0)
            else:
                asset = self._asset
                length = seconds_of(asset.decoded_samples, asset.sample_rate)
                self.asset_note.setText(
                    f"{length:.2f} s · {asset.sample_rate} Hz · "
                    f"{asset.channels} channel"
                    + ("s" if asset.channels != 1 else ""))
                for spin in (self.passage_start, self.passage_end):
                    spin.setMaximum(length)
                passage = choice.passage
                self.passage_start.setValue(
                    seconds_of(passage.start, passage.rate) if passage else 0.0)
                self.passage_end.setValue(
                    seconds_of(passage.end, passage.rate) if passage else length)

            policy = self.short_track_combo.findData(choice.short_track)
            if policy >= 0:
                self.short_track_combo.setCurrentIndex(policy)

            self.music_level.setValue(round(float(choice.music_level) * 100))
            self.dvr_level.setValue(round(float(choice.dvr_level) * 100))
            self.fade_in.setValue(seconds_of(choice.fade_in_samples))
            self.fade_out.setValue(seconds_of(choice.fade_out_samples))
        finally:
            self._loading = was
        self._apply_enabled()

    def _apply_enabled(self) -> None:
        """What can be edited, given the mode, the asset and the context."""
        mode = self._mode()
        musical = mode in (AudioMode.REPLACE, AudioMode.MIX)
        self.mode_combo.setEnabled(self._supported)
        self.passage_box.setEnabled(self._supported and musical
                                    and self._asset is not None)
        self.passage_note.setVisible(self._asset is None)
        self.music_level.setEnabled(self._supported and musical)
        # The recording's own level only means something when it is still
        # audible, which is Mix and nothing else.
        self.dvr_level.setEnabled(self._supported and mode is AudioMode.MIX)
        for spin in (self.fade_in, self.fade_out):
            spin.setEnabled(self._supported and musical)
        self.short_track_combo.setEnabled(self._supported and musical)

    # -- edits ----------------------------------------------------------------

    def _on_mode_changed(self, *_args) -> None:
        self._apply_enabled()
        mode = self._mode()
        self.mode_help.setText(dict(
            (m, help_text) for m, _, help_text in MODE_LABELS).get(mode, ""))
        self._on_edited()

    def _on_fade_changed(self, *_args) -> None:
        """A person moving a fade box sets that fade, and only that one.

        Reading both boxes here would quantise the one nobody touched: its
        exact sample value is not expressible in two decimals, so it would come
        back rounded as a side effect of editing its neighbour.
        """
        if self._loading:
            return
        if self.sender() is self.fade_out:
            self._fade_out_samples = samples_of(self.fade_out.value())
        else:
            self._fade_in_samples = samples_of(self.fade_in.value())
        self._on_edited()

    def _on_passage_changed(self, *_args) -> None:
        if self._loading:
            return
        # One control, two ends: an end at or before the start is not a
        # passage, so the other end moves visibly rather than the value being
        # rejected after the fact.
        moved_start = self.sender() is self.passage_start
        nudged = False
        if self.passage_end.value() <= self.passage_start.value():
            other = self.passage_end if moved_start else self.passage_start
            was = self._loading
            self._loading = True
            try:
                if other is self.passage_end:
                    other.setValue(min(other.maximum(),
                                       self.passage_start.value() + 0.01))
                else:
                    other.setValue(max(0.0, self.passage_end.value() - 0.01))
            finally:
                self._loading = was
            nudged = True
        self._passage = self._passage_after_edit(moved_start, nudged)
        self._on_edited()

    def _passage_after_edit(self, moved_start: bool,
                            nudged: bool) -> SampleSpan | None:
        """Take the edited end from its box, and keep the other one exact.

        Re-reading both would round the end nobody touched, which is the same
        defect as the fades: its sample value is not expressible in the two
        decimals the box has. The other end is only re-read when the pairing
        above actually moved it.
        """
        if self._asset is None:
            return None
        rate = self._asset.sample_rate
        held = self._passage
        start = (samples_of(self.passage_start.value(), rate)
                 if moved_start or nudged or held is None else held.start)
        end = (samples_of(self.passage_end.value(), rate)
               if not moved_start or nudged or held is None else held.end)
        end = min(end, self._asset.decoded_samples)
        if end <= start:
            return None
        return SampleSpan(start, end, rate)

    def _on_edited(self, *_args) -> None:
        if self._loading:
            return
        self.changed.emit()
