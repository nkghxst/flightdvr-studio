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

"""Export controls and the settings they represent."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDateEdit, QFileDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QRadioButton, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from .format import (
    DEFAULT_TEMPLATE, TEMPLATE_FIELDS, BadTemplate, UnknownTemplateField,
    expand_template,
)
from .media import ClipInfo
from .presets import (
    COLOUR_MODES, EDIT_CODECS, PRESET_ORDER, PRESETS, QUALITY_LEVELS,
    SOCIAL_QUALITY_LEVELS, SPEEDS, ExportSettings, edit_bitrate_mbps,
)
from .widgets import INNER, dim

# Offered as downscale targets when the footage is taller than they are. Every
# HDZero goggle records .ts the same way, but not at the same size: the Box Pro
# does 720p60, other modes do 720p90 and 1080p30, and the Goggle 2 goes to 1080p.
# Nothing here is assumed about the source; the choices are built from the clips.
RESOLUTION_STEPS = [1440, 1080, 720, 540, 480, 360]
FPS_STEPS = [90, 60, 50, 30, 25]


class ExportPanel(QWidget):
    """Own the export widgets and expose their state as values and signals.

    MainWindow used to receive roughly thirty widget attributes from five
    builder methods. That made every export option part of the window's API.
    This panel is the seam: callers ask for an ``ExportSettings`` value and
    respond to a small set of changes instead of reaching into its controls.
    """

    preset_changed = Signal(str)
    settings_changed = Signal()
    output_changed = Signal()
    date_changed = Signal()
    add_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._ready = False
        self._build()
        self._ready = True
        self._on_preset_changed()
        self._on_colour_changed()
        self._on_quality_changed()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroller.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # The Windows 11 style hides scrollbars until you hover, which made it
        # look as though content had simply vanished. Give it a solid width.
        scroller.verticalScrollBar().setStyleSheet(
            "QScrollBar:vertical { width: 12px; }"
        )
        scroller.setWidget(self._build_controls())
        layout.addWidget(scroller, 1)
        layout.addWidget(self._build_actions())
        self.setMinimumWidth(330)

    def _build_controls(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        preset_box = QGroupBox("Export preset")
        preset_layout = QVBoxLayout(preset_box)
        # Only the chosen preset explains itself. Showing all four descriptions
        # at once filled the panel and pushed the Output box and the Add button
        # off the bottom of the window.
        button_row = QHBoxLayout()
        self.preset_group = QButtonGroup(self)
        self.preset_buttons: dict[str, QRadioButton] = {}
        for key in PRESET_ORDER:
            button = QRadioButton(PRESETS[key].label)
            button.setToolTip(PRESETS[key].blurb)
            self.preset_group.addButton(button)
            self.preset_buttons[key] = button
            button_row.addWidget(button)
            button.toggled.connect(self._on_preset_changed)
        button_row.addStretch(1)
        preset_layout.addLayout(button_row)
        self.preset_help = dim(QLabel())
        preset_layout.addWidget(self.preset_help)
        self.preset_buttons["master"].setChecked(True)
        layout.addWidget(preset_box)

        self.options_stack = QStackedWidget()
        builders = {
            "edit": self._build_edit_options,
            "master": self._build_master_options,
            "social": self._build_social_options,
            "upload": self._build_upload_options,
            "remux": self._build_remux_options,
            "slowmo": self._build_slowmo_options,
        }
        for key in PRESET_ORDER:
            self.options_stack.addWidget(builders[key]())
        layout.addWidget(self.options_stack)

        colour_box = QGroupBox("Colour")
        colour_layout = QVBoxLayout(colour_box)
        self.colour_combo = QComboBox()
        for key, label, _ in COLOUR_MODES:
            self.colour_combo.addItem(label, key)
        self.colour_combo.currentIndexChanged.connect(self._on_colour_changed)
        colour_layout.addWidget(self.colour_combo)
        self.colour_help = dim(QLabel())
        colour_layout.addWidget(self.colour_help)
        layout.addWidget(colour_box)

        out_box = QGroupBox("Output")
        out_layout = QVBoxLayout(out_box)
        row = QHBoxLayout()
        self.out_edit = QComboBox()
        self.out_edit.setEditable(True)
        self.out_edit.setMinimumWidth(240)
        row.addWidget(self.out_edit, 1)
        pick = QPushButton("…")
        pick.setFixedWidth(34)
        pick.clicked.connect(self._browse_output)
        row.addWidget(pick)
        out_layout.addLayout(row)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self.template_edit = QLineEdit(DEFAULT_TEMPLATE)
        self.template_edit.setToolTip(
            "Fields: " + "  ".join(f"{{{f}}}" for f in TEMPLATE_FIELDS)
            + "\nAn empty field takes its separator with it, so one template "
              "covers a lone range and a named one out of several."
        )
        self.template_edit.textEdited.connect(lambda *_: self._show_example())
        name_row.addWidget(self.template_edit, 1)
        out_layout.addLayout(name_row)

        # The rendered example is the whole point of the control. A template
        # language nobody can see the output of is a way to discover at export
        # time that every file is called the same thing.
        self.template_example = dim(QLabel(""))
        out_layout.addWidget(self.template_example)

        self.subfolder_check = QCheckBox("Put each preset in its own subfolder")
        self.subfolder_check.setChecked(True)
        self.subfolder_check.toggled.connect(
            lambda *_: self.output_changed.emit()
        )
        out_layout.addWidget(self.subfolder_check)

        self.audio_check = QCheckBox("Keep the audio track")
        self.audio_check.setChecked(True)
        self.audio_check.setToolTip(
            "DVR audio is mostly motor noise and wind. Dropping it makes a "
            "small file smaller, and there is less to distract an editor."
        )
        self.audio_check.toggled.connect(
            lambda *_: self.settings_changed.emit()
        )
        out_layout.addWidget(self.audio_check)

        self.join_check = QCheckBox("Join the ticked clips into one file")
        self.join_check.setToolTip(
            "Useful when the DVR split a single flight across several recordings"
        )
        self.join_check.toggled.connect(
            lambda *_: self.settings_changed.emit()
        )
        out_layout.addWidget(self.join_check)

        date_row = QHBoxLayout()
        self.date_check = QCheckBox("Start filenames with the flight date")
        self.date_check.toggled.connect(self._on_date_toggled)
        date_row.addWidget(self.date_check)
        self.export_date = QDateEdit(QDate.currentDate())
        self.export_date.setCalendarPopup(True)
        self.export_date.setDisplayFormat("dd MMM yyyy")
        self.export_date.setEnabled(False)
        self.export_date.dateChanged.connect(lambda *_: self.date_changed.emit())
        date_row.addWidget(self.export_date)
        date_row.addStretch(1)
        out_layout.addLayout(date_row)

        self.date_help = dim(QLabel(
            "Exports are named after the clip, which carries no usable date. "
            "Tick this to get 2026-07-27_hdz_022_master.mp4 instead."
        ))
        out_layout.addWidget(self.date_help)
        layout.addWidget(out_box)

        layout.addStretch(1)
        return panel

    def _build_actions(self) -> QWidget:
        """Keep the estimate and Add button below the scrolling controls."""
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, INNER, 0, 0)

        self.estimate_label = QLabel()
        self.estimate_label.setWordWrap(True)
        layout.addWidget(self.estimate_label)

        add = QPushButton("Add to queue")
        add.setMinimumHeight(30)
        add.clicked.connect(lambda *_: self.add_requested.emit())
        layout.addWidget(add)
        return box

    def _build_edit_options(self) -> QWidget:
        box = QWidget()
        form = QFormLayout(box)
        self.edit_codec_combo = QComboBox()
        for key, (label, _, _, _) in EDIT_CODECS.items():
            # The GB/hour figure depends on the footage, so it is filled in by
            # refresh_source_options once there are clips to measure.
            self.edit_codec_combo.addItem(label, key)
        self.edit_codec_combo.currentIndexChanged.connect(
            lambda *_: self.settings_changed.emit()
        )
        form.addRow("Codec:", self.edit_codec_combo)
        form.addRow(dim(QLabel(
            "Mezzanine codecs are deliberately large. They decode a frame at a "
            "time, so scrubbing is instant and the free DaVinci Resolve can read "
            "them, which it cannot do with the original HEVC."
        )))
        return box

    def _build_upload_options(self) -> QWidget:
        box = QWidget()
        form = QFormLayout(box)

        self.upload_height = QComboBox()
        self.upload_height.currentIndexChanged.connect(
            lambda *_: self.settings_changed.emit()
        )
        self.upload_height.setToolTip(
            "The resolution you hand the platform, not the resolution the "
            "goggles recorded."
        )
        form.addRow("Upload at:", self.upload_height)

        self.upload_quality = QComboBox()
        for crf, name, _ in QUALITY_LEVELS:
            self.upload_quality.addItem(name, crf)
        self.upload_quality.setCurrentIndex(1)
        self.upload_quality.currentIndexChanged.connect(self._on_quality_changed)
        form.addRow("Quality:", self.upload_quality)

        self.upload_quality_help = dim(QLabel())
        form.addRow(self.upload_quality_help)

        self.upload_speed = QComboBox()
        self.upload_speed.addItems(SPEEDS)
        self.upload_speed.setCurrentText("slow")
        form.addRow("Encoder effort:", self.upload_speed)

        # Two labels rather than one paragraph: Qt underestimates the height of
        # a wrapped label containing newlines, and the text gets clipped.
        form.addRow(dim(QLabel(
            "These sites re-encode everything you send them and decide how much "
            "bitrate to spend based on the resolution you arrived at. Sending "
            "1080p buys a bigger allowance than sending 720p, so more of your "
            "footage survives their encode."
        )))
        form.addRow(dim(QLabel(
            "It does not add detail the goggles never recorded. The picture is "
            "the same; the difference is how kindly the platform treats it."
        )))
        return box

    def _build_master_options(self) -> QWidget:
        box = QWidget()
        form = QFormLayout(box)

        self.master_quality = QComboBox()
        for crf, name, _ in QUALITY_LEVELS:
            self.master_quality.addItem(name, crf)
        self.master_quality.setCurrentIndex(1)
        self.master_quality.currentIndexChanged.connect(self._on_quality_changed)
        form.addRow("Quality:", self.master_quality)

        self.master_quality_help = dim(QLabel())
        form.addRow(self.master_quality_help)

        self.master_speed = QComboBox()
        self.master_speed.addItems(SPEEDS)
        self.master_speed.setCurrentText("slow")
        self.master_speed.setToolTip(
            "How long the encoder thinks about each frame. Slower gives a "
            "smaller file at the same quality; it does not change how it looks."
        )
        form.addRow("Encoder effort:", self.master_speed)

        self.gpu_check = QCheckBox("Use hardware encoding")
        self.gpu_check.setEnabled(False)
        self.gpu_check.setText("Use hardware encoding (checking…)")
        self.gpu_check.toggled.connect(lambda *_: self.settings_changed.emit())
        form.addRow(self.gpu_check)

        self.gpu_help = dim(QLabel(
            "Hands the encoding to a dedicated chip on your graphics card "
            "instead of the processor. Roughly three times faster, slightly "
            "larger for the same quality."
        ))
        form.addRow(self.gpu_help)
        return box

    def _build_social_options(self) -> QWidget:
        box = QWidget()
        form = QFormLayout(box)
        self.social_mode = QComboBox()
        self.social_mode.addItem("Target a file size", "size")
        self.social_mode.addItem("Target a quality", "quality")
        self.social_mode.currentIndexChanged.connect(self._on_social_mode)
        form.addRow("Mode:", self.social_mode)

        self.social_size = QSpinBox()
        self.social_size.setRange(4, 2000)
        self.social_size.setValue(45)
        self.social_size.setSuffix(" MB")
        self.social_size.valueChanged.connect(
            lambda *_: self.settings_changed.emit()
        )
        form.addRow("File size:", self.social_size)

        self.social_quality = QComboBox()
        for crf, name, _ in SOCIAL_QUALITY_LEVELS:
            self.social_quality.addItem(name, crf)
        self.social_quality.setCurrentIndex(1)
        self.social_quality.currentIndexChanged.connect(self._on_quality_changed)
        form.addRow("Quality:", self.social_quality)

        self.social_quality_help = dim(QLabel())
        form.addRow(self.social_quality_help)

        # Both of these are rebuilt from whatever is ticked; see
        # refresh_source_options. Nothing about the source size is hardcoded.
        self.social_height = QComboBox()
        self.social_height.addItem("Keep original", 0)
        self.social_height.currentIndexChanged.connect(
            lambda *_: self.settings_changed.emit()
        )
        form.addRow("Resolution:", self.social_height)

        self.social_fps = QComboBox()
        self.social_fps.addItem("Keep original", 0)
        self.social_fps.currentIndexChanged.connect(
            lambda *_: self.settings_changed.emit()
        )
        form.addRow("Frame rate:", self.social_fps)

        form.addRow(dim(QLabel(
            "Targeting a size uses a two-pass encode, which lands within a few "
            "percent of the number you ask for."
        )))
        self._on_social_mode()
        return box

    def _build_remux_options(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        # Two labels rather than one with a blank line in it: Qt underestimates
        # the height a wrapped label needs when the text contains newlines, and
        # the last lines get clipped.
        layout.addWidget(dim(QLabel(
            "Nothing to configure. The video and audio are copied across "
            "untouched, so this finishes almost instantly and loses nothing."
        )))
        layout.addWidget(dim(QLabel(
            "Because the video stays HEVC, the free DaVinci Resolve still will "
            "not read it. Use Edit for anything going on a timeline."
        )))
        layout.addStretch(1)
        return box

    def _build_slowmo_options(self) -> QWidget:
        box = QWidget()
        form = QFormLayout(box)

        self.slow_quality = QComboBox()
        for crf, name, _ in QUALITY_LEVELS:
            self.slow_quality.addItem(name, crf)
        self.slow_quality.setCurrentIndex(1)
        self.slow_quality.currentIndexChanged.connect(self._on_quality_changed)
        form.addRow("Quality:", self.slow_quality)

        self.slow_quality_help = dim(QLabel())
        form.addRow(self.slow_quality_help)

        # Says the rates rather than only the ratio. "Half speed" leaves a pilot
        # working out what their own footage becomes, and the output rate is the
        # thing that decides whether an editor will take it.
        form.addRow(dim(QLabel(
            "Every frame you recorded is kept and shown for twice as long, so "
            "nothing is invented between them. A 60 fps recording becomes 30 "
            "fps and runs twice as long; 90 becomes 45."
        )))

        # The one preset that overrides Keep audio, so it says so where the
        # decision is made rather than leaving the tickbox looking effective.
        form.addRow(dim(QLabel(
            "Sound is left out. Slowed motor noise drops an octave and is not "
            "worth having, and keeping it at normal speed over video that now "
            "runs twice as long would drift apart by the length of the clip."
        )))
        return box

    def _combo_settings(self) -> list[tuple[str, QComboBox]]:
        """Combos whose choice is carried in the item data."""
        return [
            ("colour", self.colour_combo),
            ("edit_codec", self.edit_codec_combo),
            ("master_quality", self.master_quality),
            ("slow_quality", self.slow_quality),
            ("social_mode", self.social_mode),
            ("social_quality", self.social_quality),
            ("social_height", self.social_height),
            ("social_fps", self.social_fps),
            ("upload_height", self.upload_height),
            ("upload_quality", self.upload_quality),
        ]

    def _text_combo_settings(self) -> list[tuple[str, QComboBox]]:
        """Combos built from plain strings, which carry no item data at all."""
        return [("master_speed", self.master_speed),
                ("upload_speed", self.upload_speed)]

    def _check_settings(self) -> list[tuple[str, QCheckBox]]:
        return [
            ("subfolders", self.subfolder_check),
            ("keep_audio", self.audio_check),
            ("use_gpu", self.gpu_check),
        ]

    # There is one definition of "the export settings" — `capture` — and both
    # the settings store and the session document use it. A session that
    # serialised these separately would be a second list of keys to keep in
    # step with this panel, and the two would part company the first time a
    # control was added.

    def capture(self) -> dict:
        """Every export choice, as plain values."""
        values = {
            "output_dir": self.out_edit.currentText(),
            "template": self.template(),
            "preset": self.preset_key(),
            "social_size_mb": self.social_size.value(),
        }
        for key, combo in self._combo_settings():
            values[key] = combo.currentData()
        for key, combo in self._text_combo_settings():
            values[key] = combo.currentText()
        for key, check in self._check_settings():
            values[key] = check.isChecked()
        return values

    def apply(self, values: dict) -> None:
        """Put stored choices back. A key that is absent leaves its control be.

        Values arrive as strings from QSettings and as real types from a
        session's JSON, so every read here copes with both rather than
        assuming which side it was called from.
        """
        out = values.get("output_dir")
        if out:
            if self.out_edit.findText(str(out)) < 0:
                self.out_edit.addItem(str(out))
            self.out_edit.setCurrentText(str(out))

        template = values.get("template")
        if template:
            self.template_edit.setText(str(template))

        preset = values.get("preset")
        if preset in self.preset_buttons:
            self.preset_buttons[preset].setChecked(True)

        for key, combo in self._combo_settings():
            saved = values.get(key)
            if saved is None:
                continue
            # Combo data is int for some, str for others; try both.
            index = combo.findData(saved)
            if index < 0:
                try:
                    index = combo.findData(int(saved))
                except (TypeError, ValueError):
                    index = -1
            if index < 0:
                index = combo.findText(str(saved))
            if index >= 0:
                combo.setCurrentIndex(index)

        for key, combo in self._text_combo_settings():
            saved = values.get(key)
            if saved is not None and combo.findText(str(saved)) >= 0:
                combo.setCurrentText(str(saved))

        for key, check in self._check_settings():
            saved = values.get(key)
            if saved is not None:
                check.setChecked(str(saved).lower() in ("true", "1"))

        size = values.get("social_size_mb")
        if size is not None:
            try:
                self.social_size.setValue(int(size))
            except (TypeError, ValueError):
                pass

        self._on_preset_changed()
        self._on_colour_changed()
        self._on_quality_changed()

    def restore(self, store: QSettings) -> None:
        stored = {key: store.value(key, None) for key in self.capture()}
        stored = {key: value for key, value in stored.items()
                  if value is not None}
        stored.setdefault("output_dir", str(Path.home() / "Videos" / "FPV"))
        stored.setdefault("preset", "master")
        stored.setdefault("social_size_mb", 45)
        self.apply(stored)

    def save(self, store: QSettings) -> None:
        for key, value in self.capture().items():
            store.setValue(key, value)

    def preset_key(self) -> str:
        for key, button in self.preset_buttons.items():
            if button.isChecked():
                return key
        return "master"

    def settings(self, hw_encoder: str) -> ExportSettings:
        """Return one value object; callers do not need to know our widgets."""
        return ExportSettings(
            colour=self.colour_combo.currentData(),
            edit_codec=self.edit_codec_combo.currentData(),
            master_crf=self.master_quality.currentData(),
            master_speed=self.master_speed.currentText(),
            slow_crf=self.slow_quality.currentData(),
            social_mode=self.social_mode.currentData(),
            social_size_mb=self.social_size.value(),
            social_crf=self.social_quality.currentData(),
            social_height=self.social_height.currentData(),
            social_fps=self.social_fps.currentData(),
            upload_height=self.upload_height.currentData() or 1080,
            upload_crf=self.upload_quality.currentData(),
            upload_speed=self.upload_speed.currentText(),
            use_gpu=self.gpu_check.isChecked() and self.gpu_check.isEnabled(),
            hw_encoder=hw_encoder,
            keep_audio=self.audio_check.isChecked(),
        )

    def output_text(self) -> str:
        return self.out_edit.currentText()

    def template(self) -> str:
        """The name template, falling back rather than exporting under one name.

        An empty box means the default, not "call every file nothing". The
        queue refuses a template it cannot expand, but the value read here is
        the one stored in a session, and a session should never carry a state
        that cannot produce a filename.
        """
        typed = self.template_edit.text().strip()
        return typed or DEFAULT_TEMPLATE

    def _show_example(self) -> None:
        """Render the template against a clip nobody has to own.

        Shown as it is typed, because a template language whose output you
        cannot see until export time is how every file ends up called the same
        thing. An unusable template says so here rather than at the queue.
        """
        try:
            rendered = expand_template(self.template(), {
                "date": "2026-07-04",
                "session": "Hampstead Heath",
                "clip": "hdz_048",
                "range": "Launch",
                "range_number": "2",
                "preset": PRESETS[self.preset_key()].suffix.lstrip("_"),
            })
        except (UnknownTemplateField, BadTemplate) as exc:
            self.template_example.setText(str(exc))
            return
        extension = PRESETS[self.preset_key()].extension
        self.template_example.setText(f"e.g. {rendered}{extension}")

    def subfolders_enabled(self) -> bool:
        return self.subfolder_check.isChecked()

    def join_enabled(self) -> bool:
        return self.join_check.isChecked()

    def flight_date(self) -> date | None:
        if not self.date_check.isChecked():
            return None
        return self.export_date.date().toPython()

    def qdate(self) -> QDate:
        return self.export_date.date()

    def set_flight_date(self, chosen: date) -> None:
        self.export_date.setDate(QDate(chosen))
        self.date_check.setChecked(True)

    def set_estimate(self, text: str) -> None:
        self.estimate_label.setText(text)

    def set_hardware(self, found) -> None:
        if not found:
            self.gpu_check.setChecked(False)
            self.gpu_check.setEnabled(False)
            self.gpu_check.setText("Hardware encoding unavailable on this PC")
            self.gpu_help.setText(
                "No usable hardware encoder was found, so exports will use the "
                "processor. Nothing is missing: the result is the same, and "
                "generally a little smaller for the same quality."
            )
            return
        _, label = found
        self.gpu_check.setEnabled(True)
        self.gpu_check.setText(f"Use hardware encoding ({label})")
        self.gpu_help.setText(
            f"Hands the encoding to the video chip on your {label} hardware "
            "instead of the processor. Roughly three times faster, and slightly "
            "larger for the same quality. Detected by running a test encode, so "
            "it is known to work on this machine."
        )

    def refresh_source_options(self, clips: list[ClipInfo]) -> None:
        """Rebuild resolution, frame-rate and codec choices from the clips."""
        heights = sorted({c.height for c in clips if c.height})
        rates = sorted({round(c.fps) for c in clips if c.fps})

        top_height = heights[-1] if heights else 0
        top_rate = rates[-1] if rates else 0
        mixed_height = len(heights) > 1
        mixed_rate = len(rates) > 1

        keep_height = "Keep original"
        if top_height and not mixed_height:
            keep_height = f"Keep original ({top_height}p)"
        elif mixed_height:
            keep_height = "Keep original (mixed sizes)"

        options = [(keep_height, 0)]
        for step in RESOLUTION_STEPS:
            if top_height and step < top_height:
                options.append((f"Downscale to {step}p", step))
        self._repopulate(self.social_height, options)

        # Upload is the one preset that goes the other way. Everything from the
        # source height upwards is offered, because the whole point of it is to
        # arrive in a higher resolution tier than the footage was recorded in.
        upload_options = []
        for step in sorted(RESOLUTION_STEPS):
            if not top_height or step < top_height:
                continue
            if step == top_height:
                upload_options.append((f"Keep {step}p", step))
            else:
                upload_options.append((f"Upscale to {step}p", step))
        if not upload_options:
            upload_options = [(keep_height, 0)]
        self._repopulate(self.upload_height, upload_options)

        keep_rate = "Keep original"
        if top_rate and not mixed_rate:
            keep_rate = f"Keep original ({top_rate} fps)"
        elif mixed_rate:
            keep_rate = "Keep original (mixed rates)"

        options = [(keep_rate, 0)]
        for step in FPS_STEPS:
            if top_rate and step < top_rate:
                options.append((f"{step} fps", step))
        self._repopulate(self.social_fps, options)
        self._refresh_codec_labels(clips)

    @staticmethod
    def _repopulate(combo: QComboBox,
                    options: list[tuple[str, int]]) -> None:
        """Replace a combo's contents, keeping its choice if it survives."""
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for text, value in options:
            combo.addItem(text, value)
        index = combo.findData(previous)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _refresh_codec_labels(self, clips: list[ClipInfo]) -> None:
        """Show mezzanine sizes for the footage in hand, not for 720p60."""
        sample = max(
            clips,
            key=lambda c: c.width * c.height * (c.fps or 60),
            default=None,
        )
        previous = self.edit_codec_combo.currentData()
        self.edit_codec_combo.blockSignals(True)
        self.edit_codec_combo.clear()
        for key, (label, _, _, _) in EDIT_CODECS.items():
            if sample is not None and sample.width:
                gb_per_hour = edit_bitrate_mbps(key, sample) * 450 / 1000
                self.edit_codec_combo.addItem(
                    f"{label}  ~{gb_per_hour:.0f} GB/hour", key
                )
            else:
                self.edit_codec_combo.addItem(label, key)
        index = self.edit_codec_combo.findData(previous)
        self.edit_codec_combo.setCurrentIndex(index if index >= 0 else 0)
        self.edit_codec_combo.blockSignals(False)

    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose where exports go")
        if folder:
            self.out_edit.insertItem(0, folder)
            self.out_edit.setCurrentIndex(0)

    def _on_preset_changed(self) -> None:
        if not self._ready:
            return
        key = self.preset_key()
        self.preset_help.setText(PRESETS[key].blurb)
        self.options_stack.setCurrentIndex(PRESET_ORDER.index(key))
        self.colour_combo.setEnabled(key != "remux")
        # Off for Remux, which copies the streams and cannot be told otherwise,
        # and off for Slow motion, which always drops the sound. A ticked box
        # that changes nothing is a promise the export does not keep — the file
        # arrives silent while the interface said the audio was being kept.
        self.audio_check.setEnabled(key not in ("remux", "slowmo"))
        self._on_colour_changed()
        self._on_quality_changed()
        # The example carries the preset's own suffix and container, so it goes
        # stale the moment the preset changes rather than when the template does.
        self._show_example()
        self.preset_changed.emit(key)

    def _on_colour_changed(self) -> None:
        if not self._ready:
            return
        if self.preset_key() == "remux":
            # The help text stays legible even though the control above it is
            # not available, because it is explaining why.
            self.colour_help.setText(
                "Remux copies the video stream as it is, so there is no colour "
                "processing to choose."
            )
        else:
            key = self.colour_combo.currentData()
            for mode_key, _, description in COLOUR_MODES:
                if mode_key == key:
                    self.colour_help.setText(description)
                    break
        self.settings_changed.emit()

    def _on_quality_changed(self) -> None:
        if not self._ready:
            return
        crf = self.master_quality.currentData()
        for value, _, description in QUALITY_LEVELS:
            if value == crf:
                self.master_quality_help.setText(f"{description}  (CRF {value})")
                break
        crf = self.social_quality.currentData()
        for value, _, description in SOCIAL_QUALITY_LEVELS:
            if value == crf:
                self.social_quality_help.setText(f"{description}  (CRF {value})")
                break
        crf = self.upload_quality.currentData()
        for value, _, description in QUALITY_LEVELS:
            if value == crf:
                self.upload_quality_help.setText(f"{description}  (CRF {value})")
                break
        crf = self.slow_quality.currentData()
        for value, _, description in QUALITY_LEVELS:
            if value == crf:
                self.slow_quality_help.setText(f"{description}  (CRF {value})")
                break
        self.settings_changed.emit()

    def _on_date_toggled(self, checked: bool) -> None:
        self.export_date.setEnabled(checked)
        self.date_changed.emit()

    def _on_social_mode(self) -> None:
        by_size = self.social_mode.currentData() == "size"
        self.social_size.setEnabled(by_size)
        self.social_quality.setEnabled(not by_size)
        self.settings_changed.emit()
