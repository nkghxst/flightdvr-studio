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

"""The desktop window."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import (
    QPoint, QRect, QRectF, QSettings, Qt, QThread, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPalette,
    QPen, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QSplitter, QTableWidget, QToolButton, QVBoxLayout, QWidget,
)

from . import scan
from .browser_panel import BrowserPanel
from .external import DESKTOP_OPEN, PLAYER_PATHS, find_player, reveal
from .export_panel import ExportPanel, FPS_STEPS, RESOLUTION_STEPS
from .format import (
    _clip_set_id, existing_ancestor, human_duration, human_size, natural_key,
    output_key, work_dir,
)
from .jobs import ExportWorker, Job, JobStatus, write_concat_file
from .media import (
    ClipInfo, Tools, available_encoders, detect_hardware_encoder, probe,
    stop_process,
)
from .presets import (
    ExportSettings, describe_join_problems, estimate_output_size, join_problems,
    output_path,
)
from .player import PreviewPlayer
from .preview_panel import PreviewView
from .queue_panel import QueuePanel
from .session import (
    SUFFIX as SESSION_SUFFIX, Session, apply_to, capture_from, for_source,
    missing_from, recent_sessions, remember,
)
from .thumbs import THUMB_WIDTH, ThumbnailLoader
from .trim import Filmstrip, FilmstripLoader
from .widgets import (
    EDGE, GAP, INNER, MIN_LIST_HEIGHT, MIN_THUMB_WIDTH, MIN_VISIBLE_CLIPS,
    TIGHT, DriveCombo, SortItem, _default_window_size, app_icon, dim, resource,
)
from .workers import CopyDialog, CopyWorker, HardwareProbe, ScanWorker

APP_NAME = "FlightDVR Studio"
APP_TAGLINE = "Browse, trim and convert HDZero goggle DVR footage"
ORG = "FlightDVR Studio"
COPYRIGHT_HOLDER = "Isadu Nkemi"

# Probing is I/O bound on a card reader, so a few at once helps a lot; beyond
# about four the reader becomes the limit and it gets slower again.
PROBE_WORKERS = 4















class MainWindow(QMainWindow):
    def __init__(self, tools: Tools):
        super().__init__()
        self.tools = tools
        self.settings_store = QSettings(ORG, APP_NAME)
        self.clips: list[ClipInfo] = []
        self.clip_by_path: dict[str, ClipInfo] = {}
        self.jobs: list[Job] = []
        self.worker: ExportWorker | None = None
        self.scan_worker: ScanWorker | None = None
        self._scan_generation = 0
        # Workers asked to stop that may still be finishing a probe. Held only
        # so a running QThread is not collected out from under itself.
        self._retired_scans: list[ScanWorker] = []
        self.copy_worker: CopyWorker | None = None
        self.update_check = None
        self.encoders = available_encoders(tools)
        self.hw_encoder = ""
        self.hw_label = ""
        self._expected = 0
        self._queue_started = 0.0
        self._queue_total = 1.0
        self._queue_done = 0.0
        self.splitter: QSplitter | None = None
        self._trim_clip: ClipInfo | None = None
        self._strip: Filmstrip = Filmstrip()
        self._strip_loader: FilmstripLoader | None = None
        # Same reason as _retired_scans: a running QThread that gets collected
        # takes its decode down with it.
        self._retired_strips: list[FilmstripLoader] = []
        self._strip_generation = 0
        # Guards handlers that fire while the window is still being assembled.
        self._ready = False

        # The decisions made about the folder currently open. None until a
        # scan has said which folder that is.
        self.session: Session | None = None
        # A session opened by name, waiting for the scan of its folder to
        # finish so there are clips to put it onto.
        self._pending_session: Session | None = None
        # Trims arrive continuously while a filmstrip handle is dragged. The
        # session is written after the dragging stops rather than during it,
        # which is the difference between one write and several hundred.
        self._session_timer = QTimer(self)
        self._session_timer.setSingleShot(True)
        self._session_timer.setInterval(1500)
        self._session_timer.timeout.connect(self._write_session)

        self.thumbs = ThumbnailLoader(tools, self)
        self.thumbs.ready.connect(self._thumb_ready)

        self._select_timer = QTimer(self)
        self._select_timer.setSingleShot(True)
        self._select_timer.setInterval(250)
        self._select_timer.timeout.connect(self._load_selected_clip)

        self.player = PreviewPlayer(tools, self)
        self.player.frame_ready.connect(self._preview_frame_ready)
        self.player.state_changed.connect(self._preview_state_changed)
        self.player.failed.connect(self._preview_failed)
        self.player.ended.connect(self._preview_ended)

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(*_default_window_size())
        self._build()
        self._restore()
        self._ready = True
        self._relayout()
        self._on_preset_changed()
        self._refresh_drives()
        self._update_estimate()

        # Finding a working hardware encoder means running one, so it happens
        # off the UI thread and the checkbox switches on when the answer lands.
        self.hw_probe = HardwareProbe(tools, self)
        self.hw_probe.result.connect(self._hardware_found)
        self.hw_probe.start()

        self._start_update_check()

    # -- construction ---------------------------------------------------------

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(EDGE, EDGE, EDGE, EDGE)
        outer.setSpacing(INNER)

        self._build_menus()
        outer.addWidget(self._build_update_bar())
        outer.addLayout(self._build_source_bar())

        splitter = self.splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left_column())

        splitter.addWidget(self._build_export_panel())

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([720, 500])
        # Widening the left column makes the picture usefully taller, so this
        # is the control for trading list height against picture size.
        splitter.splitterMoved.connect(lambda *_: self._relayout())
        outer.addWidget(splitter, 1)

        # With no frames around them, the gaps are what say the picture and the
        # filmstrip are one thing and the queue is another. Tight above the
        # filmstrip, loose above the queue.
        outer.addWidget(self._build_trim_band())
        outer.addSpacing(GAP - INNER)
        outer.addWidget(self._build_queue())
        self._install_shortcuts()
        self.statusBar().showMessage(f"{APP_TAGLINE}   ·   ffmpeg: {self.tools.ffmpeg}")

    def _build_update_bar(self) -> QWidget:
        """A quiet line offering a newer release. Hidden until there is one.

        Deliberately not a dialog. Nobody opened this app to be interrupted by
        a box about software; the offer can sit there until it is convenient.
        """
        bar = self.update_bar = QWidget()
        bar.hide()
        row = QHBoxLayout(bar)
        row.setContentsMargins(INNER, TIGHT, TIGHT, TIGHT)
        row.setSpacing(INNER)

        self.update_label = QLabel("")
        self.update_label.setOpenExternalLinks(True)
        self.update_label.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(self.update_label, 1)

        dismiss = QPushButton("Dismiss")
        dismiss.setFlat(True)
        dismiss.clicked.connect(bar.hide)
        row.addWidget(dismiss)
        return bar

    def _start_update_check(self) -> None:
        """Look for a newer release, at most once a day, if that is wanted.

        Everything about this is quiet: no request when it is turned off, no
        message when it finds nothing, and no message when it fails. Somebody
        flying with no signal is not having a problem.
        """
        from . import __version__
        from .updates import UpdateCheck, should_check

        if not self.settings_store.value("check_for_updates", True, type=bool):
            return

        stamp = self.settings_store.value("last_update_check", "", type=str)
        try:
            last = datetime.fromisoformat(stamp) if stamp else None
        except ValueError:
            last = None
        if not should_check(last, datetime.now()):
            return

        self.settings_store.setValue("last_update_check",
                                     datetime.now().isoformat(timespec="seconds"))
        self.update_check = UpdateCheck(__version__, self)
        self.update_check.found.connect(self._update_available)
        self.update_check.start()

    def _update_available(self, version: str, page: str) -> None:
        self.update_label.setText(
            f"<b>Version {version} is available.</b> "
            f'<a href="{page}">See what changed</a>'
        )
        self.update_bar.show()

    def _show_about(self) -> None:
        self._build_about_box().exec()

    def _build_about_box(self) -> QMessageBox:
        """The legal notice GPL v3 section 5(d) asks an interactive program to show.

        It has to state the copyright, disclaim warranty, say the work may be
        redistributed under the licence, and say how to read the licence.

        Built here and shown by the caller, so a test can read what it says
        without a modal dialog to dismiss.
        """
        from . import __version__
        from .media import is_bundled, packaged_file
        from .updates import PROJECT_PAGE

        # Each package format puts the licence somewhere different, and only
        # the Windows installer carries its own ffmpeg. Saying otherwise in a
        # notice whose whole job is accuracy would be a poor look.
        licence = packaged_file("LICENSE")
        where = (f"The full licence is in <code>{licence}</code>, or at "
                 if licence else "The full licence is at ")

        if is_bundled(self.tools.ffmpeg):
            ffmpeg_line = ("Bundles <b>FFmpeg</b> (GPL v3) and uses <b>Qt</b> "
                           "via PySide6 (LGPL v3).")
        else:
            ffmpeg_line = ("Uses the <b>FFmpeg</b> installed on this system "
                           "(GPL) and <b>Qt</b> via PySide6 (LGPL v3).")

        box = QMessageBox(self)
        box.setWindowTitle(f"About {APP_NAME}")
        box.setIconPixmap(app_icon().pixmap(64, 64))
        box.setText(f"<b>{APP_NAME}</b> {__version__}<br>{APP_TAGLINE}")
        box.setInformativeText(
            f"Copyright © 2026 {COPYRIGHT_HOLDER}<br><br>"
            "This program comes with <b>absolutely no warranty</b>. It is free "
            "software, and you are welcome to redistribute it under the terms of "
            "the GNU General Public License, version 3 or later.<br><br>"
            f"{where}"
            "<a href='https://www.gnu.org/licenses/gpl-3.0.html'>gnu.org</a>.<br><br>"
            f"{ffmpeg_line} Qt's own licence is in "
            "<code>LICENSE.LGPL-3.0.txt</code>. See THIRD-PARTY-NOTICES.md for "
            "versions, origins and the offer of source.<br><br>"
            # The source itself, which is the thing the licence above is
            # promising. A notice that says you may redistribute the program
            # is more use when it also says where the program is.
            f"Source, releases and issues: <a href='{PROJECT_PAGE}'>"
            "github.com/nkghxst/flightdvr-studio</a><br><br>"
            "Not affiliated with or endorsed by HDZero."
        )
        box.setTextFormat(Qt.TextFormat.RichText)
        # A link nothing opens is decoration. QMessageBox does not turn this on
        # for you, so the gnu.org link above has never done anything either.
        for label in box.findChildren(QLabel):
            label.setOpenExternalLinks(True)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextBrowserInteraction)

        # The only network access this program makes, so the switch for it
        # belongs next to the statement of what the program is.
        updates = QCheckBox("Check for new versions")
        updates.setChecked(
            self.settings_store.value("check_for_updates", True, type=bool)
        )
        updates.setToolTip(
            "One request a day to this project's releases page on GitHub, to "
            "see whether a newer version exists. Nothing is downloaded or "
            "installed, and nothing about you or your footage is sent."
        )
        updates.toggled.connect(
            lambda on: self.settings_store.setValue("check_for_updates", on)
        )
        box.setCheckBox(updates)
        return box

    def _install_shortcuts(self) -> None:
        """Keyboard equivalents for the things you do on every card."""
        bindings = [
            ("F5", self._scan),
            ("Ctrl+A", self._select_all),
            ("Ctrl+Shift+A", self._select_none),
            ("Ctrl+P", self._play_selected),
            ("Ctrl+Shift+P", self._preview_selected),
            ("Ctrl+Return", self._add_to_queue),
            ("F9", self._start),
        ]
        for keys, slot in bindings:
            QShortcut(QKeySequence(keys), self, activated=slot)
        self._install_player_shortcuts()

    def _install_player_shortcuts(self) -> None:
        """The playback keys, scoped to the picture rather than the window.

        Space is the reason. The clip list uses it to tick the highlighted row,
        which is worth more than anything a window-wide binding could do with
        it, so these only fire when the video has focus — and the focus ring on
        it is what tells you which of the two you are about to get.
        """
        bindings = [
            ("Space", self._toggle_play),
            ("K", self._toggle_play),
            ("I", self._set_in),
            ("O", self._set_out),
            ("Left", lambda: self._nudge(-1.0)),
            ("Right", lambda: self._nudge(1.0)),
            ("Shift+Left", lambda: self._nudge(-5.0)),
            ("Shift+Right", lambda: self._nudge(5.0)),
            ("Home", lambda: self._jump(self.trim_bar.in_point)),
            ("End", lambda: self._jump(self.trim_bar.out_point)),
            ("Esc", self._stop_preview),
        ]
        for keys, slot in bindings:
            shortcut = QShortcut(QKeySequence(keys), self.frame_view,
                                 activated=slot)
            shortcut.setContext(
                Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def _build_source_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Source:"))

        self.source_combo = DriveCombo()
        self.source_combo.setMinimumWidth(360)
        self.source_combo.setEditable(True)
        self.source_combo.about_to_show.connect(self._refresh_drives)
        row.addWidget(self.source_combo, 1)

        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_source)
        row.addWidget(browse)

        detect = QPushButton("Find SD card")
        detect.setToolTip("Look for a removable drive containing HDZero recordings")
        detect.clicked.connect(self._detect_card)
        row.addWidget(detect)

        self.recursive_check = QCheckBox("Include subfolders")
        self.recursive_check.setChecked(True)
        row.addWidget(self.recursive_check)

        self.scan_button = QPushButton("Scan")
        self.scan_button.setDefault(True)
        self.scan_button.clicked.connect(self._scan)
        row.addWidget(self.scan_button)

        self.copy_button = QPushButton("Copy originals to library…")
        self.copy_button.setToolTip(
            "Copies the untouched .ts recordings off the card into dated folders.\n"
            "No conversion happens — for that, use the export presets."
        )
        self.copy_button.clicked.connect(self._copy_to_library)
        row.addWidget(self.copy_button)
        return row

    def _build_clip_table(self) -> QWidget:
        panel = self.browser_panel = BrowserPanel(self)
        panel.open_external_requested.connect(self._preview_selected)
        panel.select_all_requested.connect(self._select_all)
        panel.select_none_requested.connect(self._select_none)
        panel.item_changed.connect(self._on_item_changed)
        panel.item_activated.connect(self._play_item)
        panel.selection_changed.connect(self._on_clip_selected)
        return panel

    # Compatibility views for the established MainWindow API. They keep
    # integrations and UI tests working without making the widgets
    # MainWindow-owned again.
    @property
    def table(self) -> QTableWidget:
        return self.browser_panel.table

    @property
    def clip_count_label(self) -> QLabel:
        return self.browser_panel.clip_count_label

    @property
    def warning_label(self) -> QLabel:
        return self.browser_panel.warning_label

    @property
    def preview_button(self) -> QPushButton:
        return self.browser_panel.preview_button

    def _build_left_column(self) -> QWidget:
        """The clip list above the player.

        Not a splitter, deliberately. How tall the preview is worth being is
        decided by how wide its aspect-aware box is, so there is nothing to
        drag: the handle could only choose how much black to look at. Widening
        the left column is what makes the picture bigger, and the clip list
        takes everything the picture cannot use.
        """
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(INNER)
        table = self._build_clip_table()
        table.setMinimumHeight(MIN_LIST_HEIGHT)
        layout.addWidget(table, 1)
        layout.addWidget(self._build_preview_panel())
        return column

    def _build_preview_panel(self) -> QWidget:
        view = self.preview_view = PreviewView(self)
        view.frame_clicked.connect(self._focus_player)
        view.play_requested.connect(self._toggle_play)
        view.set_in_requested.connect(self._set_in)
        view.set_out_requested.connect(self._set_out)
        view.reset_requested.connect(self._reset_trim)
        view.playhead_moved.connect(self._on_playhead)
        view.trim_changed.connect(self._on_trim_changed)
        return view.preview_box

    # Compatibility views for the established MainWindow API. PreviewView
    # owns the widgets; window workflows still address them by their old names.
    @property
    def preview_box(self) -> QWidget:
        return self.preview_view.preview_box

    @property
    def frame_view(self) -> QWidget:
        return self.preview_view.frame_view

    @property
    def preview_sidebar(self) -> QWidget:
        return self.preview_view.sidebar

    @property
    def trim_title(self) -> QLabel:
        return self.preview_view.trim_title

    @property
    def trim_position(self) -> QLabel:
        return self.preview_view.trim_position

    @property
    def trim_summary(self) -> QLabel:
        return self.preview_view.trim_summary

    @property
    def clip_format(self) -> QLabel:
        return self.preview_view.clip_format

    @property
    def clip_date(self) -> QLabel:
        return self.preview_view.clip_date

    @property
    def play_button(self) -> QPushButton:
        return self.preview_view.play_button

    @property
    def trim_note(self) -> QLabel:
        return self.preview_view.trim_note

    @property
    def trim_band(self) -> QWidget:
        return self.preview_view.trim_band

    @property
    def trim_bar(self) -> QWidget:
        return self.preview_view.trim_bar

    def _build_trim_band(self) -> QWidget:
        return self.preview_view.trim_band

    def _build_export_panel(self) -> QWidget:
        panel = self.export_panel = ExportPanel(self)
        panel.preset_changed.connect(self._on_preset_changed)
        panel.settings_changed.connect(self._update_estimate)
        panel.output_changed.connect(self._on_output_changed)
        panel.date_changed.connect(self._on_date_changed)
        panel.add_requested.connect(self._add_to_queue)
        return panel

    def _build_queue(self) -> QWidget:
        panel = self.queue_panel = QueuePanel(self)
        panel.start_requested.connect(self._start)
        panel.cancel_requested.connect(self._cancel)
        panel.remove_requested.connect(self._remove_selected_jobs)
        panel.clear_requested.connect(self._clear_queue)
        panel.reveal_requested.connect(
            lambda: reveal(Path(self.export_panel.output_text()))
        )
        panel.about_requested.connect(self._show_about)
        panel.item_activated.connect(self._open_finished_job)
        return panel

    def _on_queue_toggled(self, open_: bool) -> None:
        self.queue_panel._on_toggled(open_)

    def _open_queue(self) -> None:
        self.queue_panel.open_queue()

    def _queue_summary(self) -> str:
        """What the strip says when it is closed, and while it is open."""
        return self.queue_panel.summary(self.jobs)

    @property
    def queue_toggle(self) -> QToolButton:
        return self.queue_panel.toggle

    @property
    def queue_body(self) -> QWidget:
        return self.queue_panel.body

    @property
    def queue_table(self) -> QTableWidget:
        return self.queue_panel.table

    @property
    def overall_bar(self) -> QProgressBar:
        return self.queue_panel.overall_bar

    @property
    def overall_label(self) -> QLabel:
        return self.queue_panel.overall_label

    @property
    def start_button(self) -> QPushButton:
        return self.queue_panel.start_button

    @property
    def cancel_button(self) -> QPushButton:
        return self.queue_panel.cancel_button

    # -- settings -------------------------------------------------------------

    # -- sessions -------------------------------------------------------------

    def _build_menus(self) -> None:
        """A menu for the session, because that is where people look for it.

        Everything else in this window is a button, and a session deliberately
        is not: it autosaves, so the common case needs no action at all. What
        the menu holds is the uncommon cases — naming one, and getting back to
        one from another folder.
        """
        menu = self.menuBar().addMenu("&Session")

        self.session_save_as = menu.addAction("Save session as…")
        self.session_save_as.setShortcut("Ctrl+Shift+S")
        self.session_save_as.triggered.connect(self._save_session_as)

        open_action = menu.addAction("Open session…")
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_session)

        self.recent_menu = menu.addMenu("Recent sessions")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)

    def _fill_recent_menu(self) -> None:
        """Built when opened rather than kept in step.

        The list changes on disk whenever any window opens a session, so a menu
        built once at startup is wrong the moment there are two of them.
        """
        self.recent_menu.clear()
        entries = [r for r in recent_sessions() if r.exists]
        if not entries:
            nothing = self.recent_menu.addAction("Nothing yet")
            nothing.setEnabled(False)
            return
        for entry in entries:
            action = self.recent_menu.addAction(entry.label)
            action.setToolTip(entry.source or entry.path)
            action.triggered.connect(
                lambda *_, path=entry.path: self._open_session_file(Path(path))
            )

    def _adopt_session(self, found: Session) -> None:
        """Put a session's marks onto the clips on screen, and say what it did.

        Silent on a session that gave nothing back — an empty one is the normal
        state of a folder opened for the first time, and announcing it every
        time would train people to ignore the line that also reports losses.
        """
        self.session = found
        self._pending_session = None
        # Only once there is a file. A folder opened for the first time has a
        # session with a path and nothing written at it yet, and listing that
        # under "recent" offers a door that opens onto nothing.
        if found.path is not None and found.path.exists():
            remember(found)
        self._show_session_title()

        restored = apply_to(found, self.clips)
        for clip in self.clips:
            self._mark_trim_in_table(clip)
        if self._trim_clip is not None:
            self._load_selected_clip()
        self._update_estimate()

        notes = []
        if restored:
            notes.append(f"{restored} trim{'' if restored == 1 else 's'} "
                         "restored from your last visit")

        # Named, not counted: "9 clips are missing" is a puzzle, and the whole
        # point of keeping the name alongside the fingerprint is to answer it.
        gone = missing_from(found, {c.fingerprint for c in self.clips})
        if gone:
            listed = ", ".join(m.name for m in gone[:3] if m.name)
            more = f" and {len(gone) - 3} more" if len(gone) > 3 else ""
            notes.append(
                f"{len(gone)} marked clip{'' if len(gone) == 1 else 's'} "
                f"not in this folder ({listed}{more}) — moved, or rewritten by "
                "the goggles since you marked them")
        if notes:
            self.statusBar().showMessage("  ·  ".join(notes), 12000)

    def _show_session_title(self) -> None:
        if self.session is None or self.session.is_empty():
            self.setWindowTitle(APP_NAME)
            return
        self.setWindowTitle(f"{self.session.title or 'Session'} — {APP_NAME}")

    def _touch_session(self) -> None:
        """Something was decided. Write it, once the deciding has stopped."""
        if self.session is not None:
            self._session_timer.start()

    def _write_session(self) -> None:
        if self.session is None:
            return
        capture_from(self.session, self.clips)
        try:
            self.session.save()
        except OSError as problem:
            # Not a dialog. Losing marks matters, but interrupting a review to
            # say so — possibly repeatedly — would cost more than it saves.
            self.statusBar().showMessage(
                f"Could not save the session: {problem}", 8000)
            return
        # Here rather than on opening, so the recent list only ever names files
        # that are actually there.
        remember(self.session)
        self._show_session_title()

    def _flush_session(self) -> None:
        """Write now rather than when the timer says so."""
        if self._session_timer.isActive():
            self._session_timer.stop()
        self._write_session()

    def _save_session_as(self) -> None:
        if self.session is None:
            QMessageBox.information(
                self, "No session yet",
                "Scan a folder first — a session belongs to the clips in it.")
            return
        self._flush_session()

        suggested = (self.session.title
                     or Path(self.session.source).name or "session")
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save session as", str(Path.home() / f"{suggested}{SESSION_SUFFIX}"),
            f"FlightDVR sessions (*{SESSION_SUFFIX})")
        if not chosen:
            return

        target = Path(chosen)
        self.session.title = target.name.replace(SESSION_SUFFIX, "")
        try:
            self.session.save(target)
        except OSError as problem:
            QMessageBox.warning(self, "Could not save", str(problem))
            return
        remember(self.session)
        self._show_session_title()
        self.statusBar().showMessage(f"Session saved to {target}", 8000)

    def _open_session(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Open session", str(Path.home()),
            f"FlightDVR sessions (*{SESSION_SUFFIX})")
        if chosen:
            self._open_session_file(Path(chosen))

    def _open_session_file(self, path: Path) -> None:
        """Open a session, and offer to go to the folder it was made from.

        Opening one while looking at a different card would otherwise apply
        marks to clips they were never about — the fingerprints would not match
        and nothing would happen, which looks like the file being broken.
        """
        found = Session.load(path)
        source = Path(found.source) if found.source else None
        here = self._source_path()

        if source and source.exists() and source != here:
            answer = QMessageBox.question(
                self, "Different folder",
                f"That session was made from\n{source}\n\n"
                f"Scan there now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if answer == QMessageBox.StandardButton.Yes:
                self._flush_session()
                # Held rather than assigned: the scan that follows ends in
                # _scan_done, which would otherwise load the folder's own
                # autosave over the top of the file just opened.
                self._pending_session = found
                self.source_combo.insertItem(0, str(source), str(source))
                self.source_combo.setCurrentIndex(0)
                self._scan()
                return

        self._flush_session()
        self._adopt_session(found)

    def _restore(self) -> None:
        store = self.settings_store
        self.export_panel.restore(store)

        geometry = store.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        state = store.value("splitter")
        if state and self.splitter is not None:
            self.splitter.restoreState(state)

        # The flight date is deliberately not remembered: it belongs to the
        # footage in front of you, and a stale one would mislabel a new card.

    def _save(self) -> None:
        store = self.settings_store
        self.export_panel.save(store)
        store.setValue("geometry", self.saveGeometry())
        if self.splitter is not None:
            store.setValue("splitter", self.splitter.saveState())

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if (event.key() == Qt.Key.Key_Delete
                and self.queue_table.hasFocus()):
            self._remove_selected_jobs()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        """Everything whose size depends on another widget's size.

        Called on a window resize and whenever a splitter moves, because both
        change how much room the clip list and the picture have without either
        of them being resized directly.
        """
        # Deferred, because the preview's height is settled by Qt's layout pass
        # and the clip list's viewport still reports the size it is about to
        # stop being. Sizing thumbnails against that gave rows too tall for the
        # list they ended up in. The frame is deferred for the same reason.
        QTimer.singleShot(0, self._sync_thumbnail_size)

    def _sync_thumbnail_size(self) -> None:
        """Fit the thumbnails to the space the list actually has.

        Spare width goes to a bigger picture rather than an empty filename
        column, capped at the width thumbnails are generated at since scaling
        past that only blurs them.

        Bounded by the height as well, which it was not: sized on width alone
        at a normal window width the rows came out 141 px tall, so the list
        showed two clips however much vertical space it was given. Whichever
        bound is tighter wins, and the thumbnails still grow when the list is
        given room.
        """
        if not self._ready:
            return
        self.browser_panel.sync_thumbnail_size()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._save()
        # Before the threads are stopped: a trim set in the last second and a
        # half is still sitting on the debounce timer, and closing the window
        # is exactly when someone expects their work to have been kept.
        self._flush_session()
        # First, because it is the one holding a decoder open on the card.
        self.player.shutdown()
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(4000)
        # Retired scans are included: one can still be inside a probe, and a
        # QThread destroyed while running takes the process with it.
        if self.update_check and self.update_check.isRunning():
            self.update_check.wait(2000)
        for thread in [self.hw_probe, self.scan_worker, self.copy_worker,
                       *self._retired_scans]:
            if thread and thread.isRunning():
                thread.stop()
                thread.wait(2000)
        for loader in [self._strip_loader, *self._retired_strips]:
            if loader and loader.isRunning():
                loader.stop()
                loader.wait(2000)
        self.thumbs.shutdown()
        super().closeEvent(event)

    # -- hardware -------------------------------------------------------------

    def _hardware_found(self, found) -> None:
        self.export_panel.set_hardware(found)
        if found:
            self.hw_encoder, self.hw_label = found

    # -- source handling ------------------------------------------------------

    def _refresh_drives(self) -> None:
        """Rebuild the drive list, keeping whatever is currently typed in."""
        current = self.source_combo.currentText()
        current_data = self.source_combo.currentData()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()

        remembered = self.settings_store.value("source_dir", "")
        if remembered and Path(str(remembered)).exists():
            self.source_combo.addItem(str(remembered), str(remembered))
        for drive in scan.list_drives():
            self.source_combo.addItem(drive.description, str(drive.path))

        if current:
            self.source_combo.setCurrentText(current)
            if current_data:
                index = self.source_combo.findData(current_data)
                if index >= 0:
                    self.source_combo.setCurrentIndex(index)
        self.source_combo.blockSignals(False)

    def _source_path(self) -> Path | None:
        data = self.source_combo.currentData()
        text = data or self.source_combo.currentText()
        if not text:
            return None
        path = Path(str(text))
        return path if path.exists() else None

    def _browse_source(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder of recordings")
        if folder:
            self.source_combo.insertItem(0, folder, folder)
            self.source_combo.setCurrentIndex(0)
            self.settings_store.setValue("source_dir", folder)
            self._scan()

    def _detect_card(self) -> None:
        card = scan.detect_card()
        if card is None:
            QMessageBox.information(
                self, "No card found",
                "No removable drive with recordings on it turned up.\n\n"
                "Check the card is inserted, or use Browse to point at a folder.",
            )
            return
        self.source_combo.insertItem(0, f"{card}  (detected card)", str(card))
        self.source_combo.setCurrentIndex(0)
        self._scan()

    def _scan(self) -> None:
        folder = self._source_path()
        if folder is None:
            QMessageBox.warning(self, "Nothing to scan", "Pick a folder or drive first.")
            return
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.stop()
            self.scan_worker.wait(1500)

        self.settings_store.setValue("source_dir", str(folder))
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.clips.clear()
        self.clip_by_path.clear()
        self.warning_label.hide()
        self.scan_button.setEnabled(False)
        self._expected = 0
        self.clip_count_label.setText(f"Scanning {folder}…")

        # Thumbnails read from the same card as the probing does; letting them
        # compete is what made listing a full card take minutes.
        self.thumbs.clear()
        self.thumbs.pause()

        # A worker that was asked to stop may still be inside a probe, so the
        # old one is left to finish in its own time and simply ignored. Keeping
        # a reference stops Python collecting a running QThread.
        self._scan_generation += 1
        if self.scan_worker and self.scan_worker.isRunning():
            self._retired_scans.append(self.scan_worker)
        self._retired_scans = [w for w in self._retired_scans if w.isRunning()]

        self.scan_worker = ScanWorker(
            self.tools, folder, self.recursive_check.isChecked(),
            self._scan_generation, self,
        )
        self.scan_worker.counted.connect(self._scan_counted)
        self.scan_worker.found.connect(self._add_clip)
        self.scan_worker.done.connect(self._scan_done)
        self.scan_worker.start()

    def _is_current_scan(self, generation: int) -> bool:
        """Whether a signal belongs to the scan now on screen."""
        return generation == self._scan_generation

    def _scan_counted(self, generation: int, total: int) -> None:
        if not self._is_current_scan(generation):
            return
        self._expected = total
        self.clip_count_label.setText(f"Reading {total} clips…")

    def _scan_done(self, generation: int, count: int) -> None:
        if not self._is_current_scan(generation):
            return
        self.scan_button.setEnabled(True)
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.SortOrder.AscendingOrder)
        # Deferred, like the other callers: the columns that size to their
        # contents have not done so yet, so the name column's width is still
        # whatever it was before there were any rows. Sizing against that left
        # the thumbnail too wide and every clip listed as "hdz_00…".
        QTimer.singleShot(0, self._sync_thumbnail_size)
        self._refresh_export_markers()
        self._update_counts()
        if count == 0:
            self.clip_count_label.setText("No readable video files found here")

        message = scan.timestamps_are_unreliable([c.modified for c in self.clips])
        if message:
            self.warning_label.setText(message)
            self.warning_label.show()

        # After the clips exist, because a session is only meaningful applied
        # to them — and after sorting, so the rows it marks are the final ones.
        source = self._source_path()
        if source is not None:
            opened, self._pending_session = self._pending_session, None
            if opened is None and self._is_open_for(source):
                # Scanning the same folder again keeps the session already
                # open. Without this, pressing Scan after opening a session by
                # name quietly swapped it for the folder's own autosave.
                opened = self.session
            self._adopt_session(opened or for_source(source))

    def _is_open_for(self, source: Path) -> bool:
        return (self.session is not None
                and os.path.normcase(self.session.source)
                == os.path.normcase(str(source)))

        self.thumbs.resume()

    def _add_clip(self, generation: int, clip: ClipInfo) -> None:
        # A worker from an earlier scan can still be finishing probes it had
        # already started, and its clips do not belong in this list.
        if not self._is_current_scan(generation):
            return
        self.clips.append(clip)
        self.clip_by_path[str(clip.path)] = clip

        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = SortItem(clip.path.name, natural_key(clip.path.name))
        name_item.setFlags(name_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        name_item.setCheckState(Qt.CheckState.Unchecked)
        name_item.setData(Qt.ItemDataRole.UserRole, str(clip.path))
        tip = [str(clip.path), clip.format_detail]
        if clip.is_full_range:
            tip.append("Full-range recording: levels correction applies")
        if clip.error:
            tip.append(f"Warning: {clip.error}")
        name_item.setToolTip("\n".join(tip))
        self.table.setItem(row, 0, name_item)

        # Sort keys are the underlying numbers, so the columns order correctly
        # rather than alphabetically by their formatted text.
        self.table.setItem(row, 1, SortItem(clip.duration_label, clip.duration))
        self.table.setItem(row, 2, SortItem(clip.size_label, clip.size))
        self.table.setItem(row, 3, SortItem(
            clip.modified.strftime("%d %b %Y  %H:%M"), clip.modified.timestamp()
        ))
        self.table.setItem(row, 4, SortItem(clip.format_label, clip.format_label))

        self.table.setRowHeight(row, self.table.iconSize().height() + 6)
        self.thumbs.request(clip)

        if self._expected:
            self.clip_count_label.setText(
                f"Reading {len(self.clips)} of {self._expected} clips…"
            )

    def _thumb_ready(self, clip_path: str, thumb_path: str) -> None:
        pixmap = QPixmap(thumb_path)
        if pixmap.isNull():
            return
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == clip_path:
                item.setIcon(QIcon(pixmap))
                break

    # -- selection ------------------------------------------------------------

    def _set_all(self, state: Qt.CheckState) -> None:
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(state)
        self.table.blockSignals(False)
        self._update_counts()

    def _select_all(self) -> None:
        self._set_all(Qt.CheckState.Checked)

    def _select_none(self) -> None:
        self._set_all(Qt.CheckState.Unchecked)

    def _on_item_changed(self, _item) -> None:
        self._update_counts()

    def selected_clips(self) -> list[ClipInfo]:
        """Ticked clips, in the order the table is currently showing them.

        Looked up by path rather than by row index, because the table can be
        sorted and row order stops matching the order clips were added in.
        """
        chosen = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                clip = self.clip_by_path.get(item.data(Qt.ItemDataRole.UserRole))
                if clip is not None:
                    chosen.append(clip)
        return chosen

    def _update_counts(self) -> None:
        if not self._ready:
            return
        total = len(self.clips)
        picked = len(self.selected_clips())
        self.clip_count_label.setText(f"{total} clips found, {picked} ticked")
        self._refresh_output_options()
        self._update_estimate()

    # -- options that depend on the footage -----------------------------------

    def _relevant_clips(self) -> list[ClipInfo]:
        return self.selected_clips() or self.clips

    def _refresh_output_options(self) -> None:
        """Rebuild the resolution, frame rate and codec choices from the clips.

        Different HDZero goggles record at different sizes, so offering a fixed
        "Keep 720p" is wrong the moment someone points this at 1080p footage.
        """
        self.export_panel.refresh_source_options(self._relevant_clips())

    # -- trimming -------------------------------------------------------------

    def _on_clip_selected(self) -> None:
        """Wait a moment before loading, in case more rows are coming.

        The panel is always open now, so holding the down arrow used to walk
        the list and start a whole filmstrip extraction for every row it passed
        through. Each one is a full decode pass of a clip nobody stopped on.
        """
        if not self._ready:
            return
        self._select_timer.start()

    def _load_selected_clip(self) -> None:
        """Load the highlighted clip into the player and the filmstrip."""
        self._select_timer.stop()
        clip = self._highlighted_clip()
        if clip is None or clip.duration <= 0:
            return
        if self._trim_clip is not None and clip.path == self._trim_clip.path:
            return

        self._trim_clip = clip
        # Static for as long as this clip is the one loaded, so it is written
        # here rather than alongside the playhead.
        self.clip_format.setText(f"{clip.format_label} · {clip.size_label}")
        self.clip_format.setToolTip(f"{clip.format_detail}\n{clip.size_label}")
        self.clip_date.setText(clip.modified.strftime("%d %b %Y  %H:%M"))

        # Whatever was playing is a different clip now.
        self.player.load(clip, position=clip.trim_in)
        if clip.width and clip.height:
            self.frame_view.set_aspect(clip.width / clip.height)
            # A 4:3 clip wants a different height from a 16:9 one.
            self.preview_box.updateGeometry()
        self.trim_bar.set_clip(clip.duration, clip.trim_in, clip.out_point)
        self.trim_bar.set_strip(Filmstrip())
        self._strip = Filmstrip()
        self.frame_view.set_message("reading frames…")
        self._update_trim_labels()

        # Stopped, then held rather than dropped: browsing the list starts
        # one of these per clip, and each is a full decode pass competing for
        # the same card. Letting the last reference to a running QThread go is
        # how Qt takes the process down with it.
        if self._strip_loader and self._strip_loader.isRunning():
            self._strip_loader.stop()
            self._retired_strips.append(self._strip_loader)
        self._retired_strips = [t for t in self._retired_strips
                                if t.isRunning()]
        self._strip_generation += 1
        self._strip_loader = FilmstripLoader(self.tools, clip,
                                             self._strip_generation, self)
        self._strip_loader.ready.connect(self._strip_ready)
        self._strip_loader.start()

    def _strip_ready(self, generation: int, clip_path: str, strip) -> None:
        """Take a filmstrip only from the extraction that is current.

        The path alone does not settle it. Select A, then B, then A again, and
        the first extraction of A can finish after the second has started —
        same path, older frames, and it would have been accepted.
        """
        if generation != self._strip_generation:
            return
        if self._trim_clip is None or str(self._trim_clip.path) != clip_path:
            return
        if not strip:
            if not self.player.is_playing:
                self.frame_view.set_message("no frames")
            return
        self.trim_bar.set_strip(strip)
        self._strip = strip
        self._show_frame(self.trim_bar.playhead)

    def _show_frame(self, seconds: float) -> None:
        """Paint the filmstrip still nearest a moment.

        Refuses while the player is running. Without that, every press of I
        repaints a second-old keyframe over the live video — the playhead moves
        for reasons other than scrubbing now, and each of them used to land
        here.
        """
        if self.player.is_playing:
            return
        frame = self._strip.frame_at(seconds) if self._strip else None
        if frame is None:
            return
        image = QImage(str(frame))
        if not image.isNull():
            self.frame_view.set_image(image)

    def _on_playhead(self, seconds: float) -> None:
        """The filmstrip was clicked or dragged."""
        self.player.seek(seconds)
        self._show_frame(seconds)
        self._update_trim_labels()

    # -- playing --------------------------------------------------------------

    def _focus_player(self) -> None:
        """The keys only work when the picture has focus, so anything that
        means "I am working in the player now" has to move it there."""
        self.frame_view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _toggle_play(self) -> None:
        clip = self._trim_clip
        if clip is None:
            self._load_selected_clip()
            clip = self._trim_clip
        if clip is None:
            self.statusBar().showMessage("Click a clip in the list first", 4000)
            return
        self._focus_player()
        self.player.toggle(self.frame_view.width())

    def _stop_preview(self) -> None:
        self.player.stop()
        self._show_frame(self.trim_bar.playhead)

    def _nudge(self, seconds: float) -> None:
        """Step the playhead.

        A second, not a frame, even when paused: what a paused player shows is
        the nearest filmstrip keyframe, and those are a second apart, so a
        single-frame step would move the playhead without changing the picture.
        """
        self._jump(self.trim_bar.playhead + seconds)

    def _jump(self, seconds: float) -> None:
        clip = self._trim_clip
        if clip is None:
            return
        seconds = max(0.0, min(seconds, clip.duration))
        self.player.seek(seconds)
        self.trim_bar.set_playhead(seconds)
        self._show_frame(seconds)
        self._update_trim_labels()

    def _preview_frame_ready(self, image, seconds: float) -> None:
        self.frame_view.set_image(image)
        # From the frame that was painted, not from the clock, so that pressing
        # I always means the picture on screen.
        self.trim_bar.set_playhead(seconds)
        self._update_trim_labels()

    def _preview_state_changed(self, playing: bool) -> None:
        self.play_button.setText("Pause" if playing else "Play")

    def _preview_failed(self, message: str) -> None:
        self.frame_view.set_message("could not play this clip")
        self.statusBar().showMessage(f"Preview: {message}", 8000)
        self._show_frame(self.trim_bar.playhead)

    def _preview_ended(self) -> None:
        self._show_frame(self.trim_bar.playhead)

    def _on_trim_changed(self, in_point: float, out_point: float) -> None:
        clip = self._trim_clip
        if clip is None:
            return
        clip.trim_in = in_point if in_point > 0.01 else 0.0
        clip.trim_out = out_point if out_point < clip.duration - 0.01 else 0.0
        self._show_frame(self.trim_bar.playhead)
        self._update_trim_labels()
        self._mark_trim_in_table(clip)
        self._update_estimate()
        self._touch_session()

    def _set_in(self) -> None:
        if self._trim_clip is None:
            return
        self.trim_bar.in_point = min(self.trim_bar.playhead,
                                     self.trim_bar.out_point - 0.5)
        self.trim_bar.update()
        self._on_trim_changed(self.trim_bar.in_point, self.trim_bar.out_point)

    def _set_out(self) -> None:
        if self._trim_clip is None:
            return
        self.trim_bar.out_point = max(self.trim_bar.playhead,
                                      self.trim_bar.in_point + 0.5)
        self.trim_bar.update()
        self._on_trim_changed(self.trim_bar.in_point, self.trim_bar.out_point)

    def _reset_trim(self) -> None:
        clip = self._trim_clip
        if clip is None:
            return
        clip.trim_in = clip.trim_out = 0.0
        self.trim_bar.set_clip(clip.duration, 0.0, clip.duration)
        self._update_trim_labels()
        self._mark_trim_in_table(clip)
        self._update_estimate()
        # Clearing a trim is a decision too. Closing the window would capture
        # it anyway, since the session is written from the clips as they stand
        # — what this buys is that it survives the app dying instead.
        self._touch_session()

    def _update_trim_labels(self) -> None:
        clip = self._trim_clip
        if clip is None:
            return
        self.trim_title.setText(clip.path.name)
        self.trim_position.setText(
            f"{human_duration(self.trim_bar.playhead)}"
            f" of {human_duration(clip.duration)}"
        )
        kept = self.trim_bar.out_point - self.trim_bar.in_point
        if clip.is_trimmed:
            self.trim_summary.setText(
                f"keeping {human_duration(kept)} of {human_duration(clip.duration)}"
            )
        else:
            self.trim_summary.setText(f"whole clip, {human_duration(clip.duration)}")

    def _mark_trim_in_table(self, clip: ClipInfo) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            name = self.table.item(row, 0)
            if item is None or name is None:
                continue
            if name.data(Qt.ItemDataRole.UserRole) != str(clip.path):
                continue
            if clip.is_trimmed:
                item.setText(f"{clip.duration_label}  ✂ {clip.trim_label}")
            else:
                item.setText(clip.duration_label)
            break

    # -- preview --------------------------------------------------------------

    def _highlighted_clip(self) -> ClipInfo | None:
        row = self.table.currentRow()
        if row < 0:
            ticked = self.selected_clips()
            return ticked[0] if ticked else None
        item = self.table.item(row, 0)
        if item is None:
            return None
        return self.clip_by_path.get(item.data(Qt.ItemDataRole.UserRole))

    def _play_selected(self) -> None:
        """Ctrl+P: play here, rather than handing the file to another program."""
        self._load_selected_clip()
        self._toggle_play()

    def _preview_selected(self) -> None:
        clip = self._highlighted_clip()
        if clip is None:
            QMessageBox.information(
                self, "Nothing to open",
                "Click a clip in the list first. Double-clicking one plays it "
                "here instead of opening it elsewhere.",
            )
            return
        self._open_externally(clip.path)

    def _play_item(self, item) -> None:
        """Double-click: play it here.

        Plays rather than toggles. Double-clicking a clip that happens to be
        running would otherwise pause it, which is not what a double-click
        means anywhere else. Handing the file to another program is still
        there, on the button and on Ctrl+Shift+P.
        """
        clip = self.clip_by_path.get(
            self.table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        )
        if clip is None:
            return
        self._load_selected_clip()
        self._focus_player()
        self.player.play(self.frame_view.width())

    def _open_externally(self, path: Path) -> None:
        """Play the file, preferring a player that can actually decode it."""
        player = find_player()
        try:
            if player is not None:
                subprocess.Popen([str(player), str(path)])
            elif os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen([DESKTOP_OPEN, str(path)])
        except OSError as exc:
            QMessageBox.warning(
                self, "Could not open the clip",
                f"{path.name} could not be opened.\n\n{exc}\n\n"
                "Install VLC or mpv, which both play these files. Windows "
                "Media Player is registered for .ts but usually cannot decode "
                "the HEVC video inside. The Remux preset also rewraps a clip to "
                ".mp4 in seconds without re-encoding it.",
            )
            return
        where = player.name if player else "your default player"
        self.statusBar().showMessage(f"Opening {path.name} in {where}…", 4000)

    # -- options --------------------------------------------------------------

    def _preset_key(self) -> str:
        return self.export_panel.preset_key()

    def _on_preset_changed(self, key: str | None = None) -> None:
        if not self._ready:
            return
        key = key or self._preset_key()
        self.trim_note.setVisible(key == "remux")
        self._refresh_export_markers()
        self._update_estimate()

    def _on_date_changed(self) -> None:
        self._retarget_pending()
        self._refresh_export_markers()

    def _on_output_changed(self) -> None:
        self._retarget_pending()
        self._refresh_export_markers()

    def _refresh_export_markers(self) -> None:
        """Flag clips that already have an export for the current settings.

        On a card holding 122 recordings, knowing what is left to do matters
        more than anything else in the list.
        """
        if not self._ready or not self.clips:
            return
        out_dir = Path(self.export_panel.output_text().strip() or ".")
        key = self._preset_key()
        subfolders = self.export_panel.subfolders_enabled()
        stamp = self.flight_date()

        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                continue
            clip = self.clip_by_path.get(item.data(Qt.ItemDataRole.UserRole))
            if clip is None:
                continue
            target = output_path(out_dir, clip.stem, key, subfolders, stamp)
            done = target.exists() and target.stat().st_size > 0
            item.setText(f"{clip.path.name}    ✓ exported" if done else clip.path.name)
            if done:
                item.setToolTip(f"{clip.path}\nAlready exported to {target}")
        self.table.blockSignals(False)

    def _retarget_pending(self) -> None:
        """Re-apply output settings to jobs that have not started yet.

        Output paths are worked out when a job is queued. Changing the flight
        date afterwards used to leave those jobs named the old way with no
        indication, which read as the setting simply not working.
        """
        if not self._ready or not self.jobs:
            return
        stamp = self.flight_date()
        for job in self.jobs:
            job.retarget(stamp)
        self._rebuild_queue()

    def flight_date(self) -> date | None:
        """The date to stamp on output, or None to leave names alone."""
        return self.export_panel.flight_date()

    def current_settings(self) -> ExportSettings:
        return self.export_panel.settings(self.hw_encoder)

    def _update_estimate(self) -> None:
        if not self._ready:
            return
        clips = self.selected_clips()
        if not clips:
            self.export_panel.set_estimate(
                "Tick some clips to see an estimated output size."
            )
            return
        settings = self.current_settings()
        key = self._preset_key()
        total = sum(estimate_output_size(c, key, settings) for c in clips)

        if self.export_panel.join_enabled() and len(clips) > 1:
            if key == "social" and settings.social_mode == "size":
                total = settings.social_size_mb * 1024 * 1024
            summary = f"1 joined file, about {human_size(total)}"
        else:
            summary = f"{len(clips)} files, about {human_size(total)} in total"

        runtime = human_duration(sum(c.duration for c in clips))
        self.export_panel.set_estimate(f"{summary}  ·  {runtime} of footage")

    # -- queue ----------------------------------------------------------------

    def _add_to_queue(self) -> None:
        clips = self.selected_clips()
        if not clips:
            QMessageBox.warning(self, "Nothing ticked", "Tick at least one clip first.")
            return
        out_dir = Path(self.export_panel.output_text().strip())
        if not str(out_dir).strip():
            QMessageBox.warning(self, "No output folder", "Choose where the exports should go.")
            return

        key = self._preset_key()
        settings = self.current_settings()
        subfolders = self.export_panel.subfolders_enabled()
        stamp = self.flight_date()

        # Two jobs writing the same filename would just overwrite each other.
        already = {
            output_key(j.out_path) for j in self.jobs
            if j.status in (JobStatus.PENDING, JobStatus.RUNNING)
        }
        before = len(self.jobs)

        if self.export_panel.join_enabled() and len(clips) > 1:
            # Joined in DVR counter order: the file timestamps cannot be trusted.
            ordered = sorted(clips, key=lambda c: (c.sequence, natural_key(c.path.name)))

            # Refused rather than exported wrongly. A join built from mismatched
            # clips does not fail; it produces a file that is silent after the
            # first clip, or the wrong length, and looks like it worked.
            # Remux is stricter, and has to be: it copies the streams rather
            # than rebuilding them, so it cannot reconcile anything that
            # differs and cannot cut inside a clip at all.
            problems = join_problems(ordered, re_encoding=key != "remux")
            if problems:
                QMessageBox.warning(self, "These clips cannot be joined",
                                    describe_join_problems(ordered, problems))
                return

            stem = f"{ordered[0].stem}_joined"
            target = output_path(out_dir, stem, key, subfolders, stamp)
            if output_key(target) not in already:
                # The list is written only once the target is accepted, and its
                # name covers every clip in it. Writing it first, under a name
                # taken from the first clip alone, meant queueing a+c after a+b
                # was rejected as a duplicate yet still rewrote a+b's list, so
                # the job on screen exported clips it did not name.
                concat = write_concat_file(ordered, work_dir(),
                                           f"{stem}_{_clip_set_id(ordered)}")
                self.jobs.append(Job(ordered, key, settings, target, concat_file=concat,
                                     out_dir=out_dir, stem=stem, subfolders=subfolders))
        else:
            for clip in clips:
                target = output_path(out_dir, clip.stem, key, subfolders, stamp)
                if output_key(target) in already:
                    continue
                already.add(output_key(target))
                self.jobs.append(Job([clip], key, settings, target,
                                     out_dir=out_dir, stem=clip.stem,
                                     subfolders=subfolders))

        added = len(self.jobs) - before
        skipped = (
            1 if self.export_panel.join_enabled() and len(clips) > 1 else len(clips)
        ) - added
        self._rebuild_queue()
        note = f"{added} queued"
        if skipped > 0:
            note += f", {skipped} already in the queue"
        self.statusBar().showMessage(note, 5000)

    def _rebuild_queue(self) -> None:
        # The single funnel for anything that changes the queue, so this is
        # where the strip learns what to say and when to open itself.
        self.queue_panel.rebuild(self.jobs)

    def _open_finished_job(self, item) -> None:
        """Double-clicking a finished row opens what it produced."""
        row = item.row()
        if not (0 <= row < len(self.jobs)):
            return
        job = self.jobs[row]
        if job.status is JobStatus.DONE and job.out_path.exists():
            self._open_externally(job.out_path)
        elif job.out_path.parent.exists():
            reveal(job.out_path.parent)

    def _start(self) -> None:
        pending = [j for j in self.jobs if j.status == JobStatus.PENDING]
        if not pending:
            QMessageBox.information(self, "Queue empty", "Add some clips to the queue first.")
            return
        if self.worker and self.worker.isRunning():
            return
        if not self._confirm_overwrites(pending):
            return
        pending = [j for j in self.jobs if j.status == JobStatus.PENDING]
        if not pending:
            return
        if not self._confirm_free_space(pending):
            return

        # So Cancel is never behind a collapsed panel at the moment it is
        # wanted. Closing it again from here on is a deliberate act.
        self._open_queue()

        # Progress is weighted by footage length rather than job count, because
        # a five minute clip is not the same amount of work as a one minute one.
        self._queue_started = time.monotonic()
        self._queue_total = sum(j.total_duration for j in pending) or 1.0
        self._queue_done = 0.0
        self._update_overall(0.0)

        self.worker = ExportWorker(self.tools, self.jobs, work_dir(), self)
        self.worker.job_started.connect(self._job_started)
        self.worker.job_progress.connect(self._job_progress)
        self.worker.job_finished.connect(self._job_finished)
        self.worker.queue_finished.connect(self._queue_finished)
        self.queue_panel.set_running(True)
        self.worker.start()

    def _update_overall(self, done_seconds: float) -> None:
        fraction = max(0.0, min(1.0, done_seconds / self._queue_total))
        elapsed = time.monotonic() - self._queue_started
        text = f"elapsed {human_duration(elapsed)}"
        # Wait until there is enough done for the rate to mean anything.
        if fraction > 0.02 and elapsed > 3:
            remaining = elapsed / fraction - elapsed
            text += f"  ·  about {human_duration(remaining)} left"
        self.queue_panel.set_overall(fraction, text)

    def _confirm_overwrites(self, pending: list[Job]) -> bool:
        """Nothing gets overwritten without being asked about first."""
        clashes = [j for j in pending if j.out_path.exists()]
        if not clashes:
            return True

        names = "\n".join(f"   {j.out_path.name}" for j in clashes[:8])
        if len(clashes) > 8:
            names += f"\n   …and {len(clashes) - 8} more"

        box = QMessageBox(self)
        box.setWindowTitle("Files already exist")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            f"{len(clashes)} of these {len(pending)} exports would replace a file "
            "that is already there:"
        )
        box.setInformativeText(names)
        skip = box.addButton("Skip those", QMessageBox.ButtonRole.AcceptRole)
        overwrite = box.addButton("Overwrite", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(skip)
        box.exec()

        clicked = box.clickedButton()
        if clicked is overwrite:
            return True
        if clicked is skip:
            for job in clashes:
                job.status = JobStatus.SKIPPED
                job.message = "output already exists"
            self._rebuild_queue()
            return True
        return False

    def _confirm_free_space(self, pending: list[Job]) -> bool:
        """A full card of mezzanine exports runs to hundreds of gigabytes."""
        needed = 0
        for job in pending:
            if job.preset_key == "remux":
                needed += sum(c.size for c in job.clips)
            else:
                needed += sum(
                    estimate_output_size(c, job.preset_key, job.settings)
                    for c in job.clips
                )
        target = existing_ancestor(pending[0].out_path.parent)
        available = scan.free_space(target)
        if not available or needed < available * 0.95:
            return True

        answer = QMessageBox.warning(
            self, "Not enough space",
            f"These exports come to roughly {human_size(needed)}, but only "
            f"{human_size(available)} is free on {target.anchor or target}.\n\n"
            "Export anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _cancel(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.statusBar().showMessage("Cancelling…", 3000)

    # -- queue editing --------------------------------------------------------

    def _removable_rows(self, rows: list[int]) -> list[int]:
        return [r for r in rows if 0 <= r < len(self.jobs)
                and self.jobs[r].status is not JobStatus.RUNNING]

    @staticmethod
    def _withdraw(job: Job) -> None:
        """Tell a running worker to pass over a job dropped from the queue.

        The worker holds its own list and skips anything that is not pending,
        so marking the job is what stops it. Removing it from the window's list
        alone did not: clearing the queue mid-export left the worker encoding
        jobs that were no longer on screen.
        """
        if job.status is JobStatus.PENDING:
            job.status = JobStatus.SKIPPED
            job.message = "removed from the queue"

    def _remove_selected_jobs(self) -> None:
        rows = {i.row() for i in self.queue_table.selectedIndexes()}
        removable = self._removable_rows(sorted(rows))
        if not removable:
            QMessageBox.information(
                self, "Nothing to remove",
                "Select the queue rows you want to drop. A job that is already "
                "encoding has to be cancelled instead.",
            )
            return
        for row in sorted(removable, reverse=True):
            self._withdraw(self.jobs[row])
            del self.jobs[row]
        self._rebuild_queue()
        self.statusBar().showMessage(f"Removed {len(removable)} from the queue", 4000)

    def _clear_queue(self) -> None:
        keep = [j for j in self.jobs if j.status is JobStatus.RUNNING]
        dropped = [j for j in self.jobs if j.status is not JobStatus.RUNNING]
        if not dropped:
            return
        for job in dropped:
            self._withdraw(job)
        self.jobs = keep
        self._rebuild_queue()
        self.statusBar().showMessage(f"Cleared {len(dropped)} from the queue", 4000)

    def _reported_job(self, index: int):
        """Resolve a worker's index to (job, row), either of which may be gone.

        The index refers to the worker's own list, not to what is on screen.
        Using it to subscript self.jobs meant that removing a queued row while
        an export was running updated the wrong progress bar, or raised
        IndexError once the list had grown shorter than the worker's.
        """
        jobs = self.worker.jobs if self.worker else self.jobs
        if not 0 <= index < len(jobs):
            return None, None
        job = jobs[index]
        row = next((i for i, queued in enumerate(self.jobs) if queued is job), None)
        return job, row

    def _job_started(self, index: int) -> None:
        _, row = self._reported_job(index)
        if row is None:
            return
        self.queue_panel.mark_started(row)

    def _job_progress(self, index: int, fraction: float, speed: str) -> None:
        job, row = self._reported_job(index)
        if job is None:
            return
        job.progress = fraction
        if row is not None:
            self.queue_panel.mark_progress(row, fraction, speed)
        self._update_overall(self._queue_done + fraction * job.total_duration)

    def _job_finished(self, index: int, ok: bool, message: str) -> None:
        job, row = self._reported_job(index)
        if job is None:
            return
        if row is not None:
            self.queue_panel.mark_finished(row, ok, job.status, message)
        self._queue_done += job.total_duration
        self._update_overall(self._queue_done)

    def _queue_finished(self, completed: int, failed: int) -> None:
        self.queue_panel.set_running(False)
        # Newly written files mean more clips can be marked as done.
        self._refresh_export_markers()
        note = f"Finished: {completed} exported"
        if failed:
            note += f", {failed} failed"
        self.statusBar().showMessage(note, 10000)

        total = time.monotonic() - self._queue_started
        self.queue_panel.finish_overall(
            f"finished in {human_duration(total)}"
        )

    # -- ingest ---------------------------------------------------------------

    def _copy_to_library(self) -> None:
        clips = self.selected_clips()
        if not clips:
            QMessageBox.warning(
                self, "Nothing ticked",
                "Tick the clips you want copied off the card first.",
            )
            return

        needed = sum(c.size for c in clips)
        dialog = CopyDialog(len(clips), needed, self.export_panel.qdate(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # One flight date for the session: setting it here fills it in for
        # exports too, so both routes off the card end up labelled the same.
        self.export_panel.set_flight_date(dialog.flight_date)

        base = dialog.folder
        if not str(base).strip():
            return

        available = scan.free_space(existing_ancestor(base))
        if available and needed > available:
            QMessageBox.warning(
                self, "Not enough space",
                f"That copy needs {human_size(needed)} but only "
                f"{human_size(available)} is free on the destination.",
            )
            return

        self.copy_button.setEnabled(False)
        self.copy_worker = CopyWorker(
            [c.path for c in clips], base, dialog.flight_date, self
        )
        self.copy_worker.progress.connect(
            lambda done, total, name: self.statusBar().showMessage(
                f"Copying {done + 1}/{total}: {name}" if name else "Finishing copy…"
            )
        )
        self.copy_worker.done.connect(
            lambda written, problems: self._copy_done(base, written, problems)
        )
        self.copy_worker.start()

    def _copy_done(self, base: Path, written: list, problems: list) -> None:
        self.copy_button.setEnabled(True)
        text = f"Copied {len(written)} clips into {base}."
        if problems:
            text += "\n\nProblems:\n" + "\n".join(problems[:10])
        QMessageBox.information(self, "Copy finished", text)
        self.statusBar().showMessage(f"Copied {len(written)} clips", 8000)


def _say(text: str) -> None:
    """print() that survives a windowed build, which has no stdout attached."""
    try:
        print(text)
    except (AttributeError, OSError, ValueError):
        pass


def _describe_environment() -> tuple[str, int]:
    """Report what a packaged build can actually see, and an exit code.

    This is what `--check` runs. It exists so a build can be proved working
    without a display or a person: CI runs it against the AppImage and the .app
    to confirm Qt's platform plugin loaded and ffmpeg resolved. It is also the
    first thing to ask someone whose install will not start.
    """
    from . import __version__
    from .media import ToolsMissing, find_tools, is_bundled, packaged_file

    lines = [f"{APP_NAME} {__version__}",
             f"Python {sys.version.split()[0]} on {sys.platform}"]
    code = 0

    # Each package format puts these somewhere different, and About shows the
    # path. Reporting it here means a build that cannot find its own licence
    # is visible in CI rather than only to whoever opens the dialog.
    for name in ("LICENSE", "LICENSE.LGPL-3.0.txt"):
        found = packaged_file(name)
        lines.append(f"{name:<22} {found if found else 'NOT FOUND'}")
        if found is None:
            code = 5

    # Constructing the application proves Qt's platform plugin loaded, which is
    # the part of a packaged build most likely to be broken.
    try:
        app = QApplication.instance() or QApplication([])
        from PySide6.QtCore import qVersion
        lines.append(f"Qt {qVersion()} on the {app.platformName()} plugin")
    except Exception as exc:              # noqa: BLE001 — report, never raise
        lines.append(f"Qt failed to start: {exc}")
        code = 4

    try:
        tools = find_tools()
        origin = "bundled" if is_bundled(tools.ffmpeg) else "from this system"
        lines.append(f"ffmpeg  {tools.ffmpeg}  ({origin})")
        lines.append(f"ffprobe {tools.ffprobe}")
    except ToolsMissing as exc:
        lines.append(str(exc).replace("\n\n", " "))
        code = code or 3

    return "\n".join(lines), code


def launch(argv: list[str] | None = None) -> int:
    from .media import ToolsMissing, find_tools

    args = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args:
        from . import __version__
        _say(f"{APP_NAME} {__version__}")
        return 0
    if "--check" in args:
        report, code = _describe_environment()
        _say(report)
        return code

    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG)
    app.setWindowIcon(app_icon())
    try:
        tools = find_tools()
    except ToolsMissing as exc:
        QMessageBox.critical(None, "ffmpeg not found", str(exc))
        return 2
    window = MainWindow(tools)
    window.show()
    return app.exec()
