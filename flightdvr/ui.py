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
    QDate, QSettings, QSize, Qt, QThread, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QIcon, QImage, QKeySequence, QPalette, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QDateEdit, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QRadioButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget,
)

from . import scan
from .jobs import ExportWorker, Job, JobStatus, write_concat_file
from .media import (
    ClipInfo, Tools, available_encoders, detect_hardware_encoder, probe,
)
from .presets import (
    COLOUR_MODES, EDIT_CODECS, PRESET_ORDER, PRESETS, QUALITY_LEVELS,
    SOCIAL_QUALITY_LEVELS, SPEEDS, ExportSettings, describe_join_problems,
    edit_bitrate_mbps, estimate_output_size, join_problems, output_path,
)
from .player import FrameView, PreviewPlayer
from .thumbs import THUMB_WIDTH, ThumbnailLoader
from .trim import Filmstrip, FilmstripLoader, TrimBar

APP_NAME = "FlightDVR Studio"
APP_TAGLINE = "Browse, trim and convert HDZero goggle DVR footage"
ORG = "FlightDVR Studio"
COPYRIGHT_HOLDER = "Isadu Nkemi"

# Offered as downscale targets when the footage is taller than they are. Every
# HDZero goggle records .ts the same way, but not at the same size: the Box Pro
# does 720p60, other modes do 720p90 and 1080p30, and the Goggle 2 goes to 1080p.
# Nothing here is assumed about the source; the choices are built from the clips.
RESOLUTION_STEPS = [1440, 1080, 720, 540, 480, 360]
FPS_STEPS = [90, 60, 50, 30, 25]

# Probing is I/O bound on a card reader, so a few at once helps a lot; beyond
# about four the reader becomes the limit and it gets slower again.
PROBE_WORKERS = 4


def resource(name: str) -> Path:
    return Path(__file__).resolve().parent / "resources" / name


def app_icon() -> QIcon:
    path = resource("icon.ico")
    return QIcon(str(path)) if path.exists() else QIcon()


def dim(label: QLabel, strength: float = 0.34) -> QLabel:
    """Mute a label so it reads as secondary text without becoming unreadable.

    The obvious approach, `color: palette(mid)`, is close to invisible against a
    dark theme: on the Windows 11 dark style it resolves to #282828 on a #1e1e1e
    window, a contrast ratio of 1.13:1. Blending the real text colour toward the
    real background colour gives about 7.9:1 and works in either theme.
    """
    palette = label.palette()
    text = palette.color(QPalette.ColorRole.WindowText)
    back = palette.color(QPalette.ColorRole.Window)
    blended = QColor(
        round(text.red() * (1 - strength) + back.red() * strength),
        round(text.green() * (1 - strength) + back.green() * strength),
        round(text.blue() * (1 - strength) + back.blue() * strength),
    )
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                  QPalette.ColorGroup.Disabled):
        palette.setColor(group, QPalette.ColorRole.WindowText, blended)
    label.setPalette(palette)
    label.setWordWrap(True)
    # A wrapped QLabel reports the height it needs for its current width, but a
    # vertical layout will happily give it less and clip the last lines when the
    # panel narrows. MinimumExpanding makes the layout grow the label instead.
    label.setSizePolicy(QSizePolicy.Policy.Preferred,
                        QSizePolicy.Policy.MinimumExpanding)
    return label


def human_size(num_bytes: float) -> str:
    if num_bytes <= 0:
        return "-"
    mb = num_bytes / (1024 * 1024)
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def human_duration(seconds: float) -> str:
    """Runtime in units people actually use, not decimal minutes."""
    total = int(round(seconds))
    if total < 60:
        return f"{total} sec"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} hr {minutes:02d} min"
    return f"{minutes} min {secs:02d} sec"


def natural_key(text: str) -> str:
    """Sort key where digit runs compare numerically (hdz_9 before hdz_112)."""
    return re.sub(r"\d+", lambda m: m.group().zfill(12), text.lower())


def output_key(path: Path) -> str:
    """One name per file, for spotting two jobs aimed at the same place.

    Absolute and case-folded, because Windows and macOS treat hdz_001.mp4 and
    HDZ_001.mp4 as one file. Comparing the paths as written meant two jobs from
    differently-cased folders queued happily and the second silently overwrote
    the first, without the overwrite prompt appearing.
    """
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    return os.path.normcase(str(resolved))


def existing_ancestor(path: Path) -> Path:
    """The nearest folder that exists, so free space can be measured.

    Looking only at the immediate parent meant a destination two levels below
    anything that existed skipped the capacity check altogether: disk_usage()
    failed, the failure came back as zero, and zero reads as "no warning".
    """
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def _clip_set_id(clips) -> str:
    """A short identifier for exactly this set of clips and their trims.

    Concat lists were named after the first clip alone, so two different joins
    beginning with the same recording shared one file and overwrote each other.
    """
    material = "|".join(
        f"{c.path}:{c.trim_in:.3f}:{c.trim_out:.3f}" for c in clips
    )
    return hashlib.sha1(material.encode("utf-8", "replace")).hexdigest()[:8]


def work_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "flightdvr"
    path.mkdir(parents=True, exist_ok=True)
    return path


PLAYER_PATHS = [
    Path(r"C:\Program Files\VideoLAN\VLC\vlc.exe"),
    Path(r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"),
    Path(r"C:\Program Files\mpv\mpv.exe"),
    # macOS keeps the real executable inside the app bundle; nothing lands on
    # PATH when these are installed by dragging them to Applications.
    Path("/Applications/VLC.app/Contents/MacOS/VLC"),
    Path("/Applications/IINA.app/Contents/MacOS/IINA"),
    Path("/Applications/mpv.app/Contents/MacOS/mpv"),
]

# macOS has no xdg-open. `open` is the equivalent and resolves app bundles.
DESKTOP_OPEN = "open" if sys.platform == "darwin" else "xdg-open"

# Every gap in the window is one of these four, and which one says how related
# the two things either side of it are. Before this they were a dozen different
# literals chosen a panel at a time, so the spacing varied in ways that meant
# nothing and grouped nothing.
EDGE = 10       # the window's own margin
GAP = 12        # between regions that have nothing to do with each other
INNER = 6       # between parts of one region
TIGHT = 4       # between controls that belong together

# How many clips the list should manage to show before thumbnails start giving
# up size for it. Sized on width alone the rows came out 141 px tall, which is
# two clips on a normal window whatever height the list was given.
MIN_VISIBLE_CLIPS = 4

# The clip list never gives up more than this to the picture, however wide the
# left column is dragged.
MIN_LIST_HEIGHT = 150


class PreviewPanel(QWidget):
    """The preview, as tall as its picture can fill and no taller.

    Not a titled group box, and neither is the filmstrip below it. They are one
    thing — a picture and the strip you scrub it with — and a frame around each
    says they are two. The clip list above them has no frame either, so the
    whole left column reads as one column rather than a stack of panels. The
    export settings on the right keep theirs, because those genuinely are
    separate groups of unrelated switches.

    One frame around both was tried and taken out again. Qt has no L-shaped
    frame, so it had to be a widget outside the layout overlapping its own
    siblings, and a widget outside the layout is a widget that can be in the
    wrong place. Proximity groups them well enough.

    A 16:9 frame in a box of any other shape letterboxes: past `width / aspect`
    every extra pixel of height is a black bar, and short of it every missing
    pixel is black down the sides. There is exactly one right height and it
    follows from the width, so this is `heightForWidth` rather than something
    recomputed in a resize handler — Qt solves it during layout, and the answer
    stops depending on which resize happened to fire first.

    Everything the picture cannot use goes to the clip list above it, which is
    why there is no splitter here: a handle could only choose how much black to
    look at. Widening the left column is what makes the picture bigger.
    """

    def __init__(self):
        super().__init__()
        self.view: FrameView | None = None
        self.sidebar: QWidget | None = None

    def useful_height(self, width: int) -> int:
        """How tall this is worth being at a given width.

        Measured off the picture where possible rather than derived from the
        margins: a group box's title and frame cost more height than
        contentsMargins reports, and deriving it left the picture twenty
        pixels short of filling the width.
        """
        if self.view is None or self.sidebar is None:
            return self.minimumHeight()
        margins = self.contentsMargins()
        spacing = self.layout().spacing() if self.layout() else 0
        # Both measured off the picture once there is one, so the answer holds
        # whatever the style charges for a group box's title and frame.
        inset = (self.width() - self.view.width() if self.view.width() > 0
                 else margins.left() + margins.right()
                 + self.sidebar.sizeHint().width() + spacing)
        chrome = (self.height() - self.view.height() if self.view.height() > 0
                  else margins.top() + margins.bottom())
        picture = round(max(1, width - inset) / self.view.aspect)
        # The sidebar is taller than the picture at small sizes, and clipping
        # the buttons off the bottom is a worse trade than a black bar.
        floor = self.sidebar.sizeHint().height()
        return max(picture, floor) + chrome

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Take the height its width has earned, and no more.

        Driven by this widget's own width because that is the only input:
        setting the height cannot change it, so this settles in one pass. Doing
        it from the window's resize handler instead made the answer depend on
        which resize Qt happened to deliver first, and the picture came out
        two thirds of the width it could have had.
        """
        super().resizeEvent(event)
        wanted = self.useful_height(self.width())
        # Never at the cost of the clip list disappearing entirely.
        parent = self.parentWidget()
        if parent is not None:
            wanted = min(wanted, max(1, parent.height() - MIN_LIST_HEIGHT))
        if self.height() != wanted:
            self.setFixedHeight(wanted)


def find_player() -> Path | None:
    """A player known to cope with HEVC inside MPEG-TS.

    Windows associates `.ts` with Media Player, which opens the file and then
    often cannot decode it, so a player that definitely works is preferred when
    one is installed.

    On Linux the usual players are on PATH, and if neither is installed the
    caller falls back to xdg-open, which reaches a Flatpak player through the
    desktop association where exec'ing a binary would not. macOS installs them
    as app bundles instead, so the paths above are checked first there.
    """
    for path in PLAYER_PATHS:
        if path.exists():
            return path
    for name in ("vlc", "mpv", "mplayer", "totem"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def reveal(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen([DESKTOP_OPEN, str(path)])
    except OSError:
        pass


class SortItem(QTableWidgetItem):
    """Table cell that sorts on a supplied key rather than on its text."""

    SORT_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, text: str, key):
        super().__init__(text)
        self.setData(self.SORT_ROLE, key)

    def __lt__(self, other):
        mine = self.data(self.SORT_ROLE)
        theirs = other.data(self.SORT_ROLE) if isinstance(other, QTableWidgetItem) else None
        if mine is None or theirs is None:
            return super().__lt__(other)
        try:
            return mine < theirs
        except TypeError:
            return str(mine) < str(theirs)


class DriveCombo(QComboBox):
    """Source picker that re-reads the drive list every time it is opened.

    Enumerating once at startup meant a card inserted afterwards never
    appeared, which is exactly when you want to see it.
    """

    about_to_show = Signal()

    def showPopup(self) -> None:  # noqa: N802 (Qt naming)
        self.about_to_show.emit()
        super().showPopup()


class ScanWorker(QThread):
    """Finds and probes clips without blocking the window."""

    # Every signal carries the scan it belongs to. A probe can sit in ffprobe
    # for a long time on a slow card, and stopping a worker only asks it to
    # finish early — it cannot interrupt a call already in progress. Starting
    # a new scan therefore leaves the old worker alive, and it used to arrive
    # later with `done` and re-enable the window in the middle of the new one.
    found = Signal(int, object)
    counted = Signal(int, int)
    done = Signal(int, int)

    def __init__(self, tools: Tools, folder: Path, recursive: bool,
                 generation: int = 0, parent=None):
        super().__init__(parent)
        self.tools = tools
        self.folder = folder
        self.recursive = recursive
        self.generation = generation
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        paths = scan.find_clips(self.folder, self.recursive)
        self.counted.emit(self.generation, len(paths))
        count = 0
        with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
            futures = {pool.submit(probe, self.tools, path): path for path in paths}
            try:
                for future in as_completed(futures):
                    if self._stop:
                        break
                    try:
                        clip = future.result()
                    except Exception:  # pragma: no cover - a single bad file
                        continue
                    if clip.error and not clip.width:
                        continue
                    self.found.emit(self.generation, clip)
                    count += 1
            finally:
                # cancel_futures drops the ones not started; the context manager
                # still waits for any probe already inside ffprobe, which is why
                # the window cannot rely on this worker being finished.
                if self._stop:
                    pool.shutdown(wait=False, cancel_futures=True)
        self.done.emit(self.generation, count)


class HardwareProbe(QThread):
    """Works out which hardware encoder, if any, this machine can really use."""

    result = Signal(object)

    def __init__(self, tools: Tools, parent=None):
        super().__init__(parent)
        self.tools = tools

    def run(self) -> None:
        try:
            self.result.emit(detect_hardware_encoder(self.tools))
        except Exception:  # pragma: no cover - never block startup on this
            self.result.emit(None)


class CopyWorker(QThread):
    progress = Signal(int, int, str)
    done = Signal(list, list)

    def __init__(self, sources, base, flight_date, parent=None):
        super().__init__(parent)
        self.sources, self.base = sources, base
        self.flight_date = flight_date
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        def report(done: int, total: int, name: str) -> bool:
            self.progress.emit(done, total, name)
            return not self._stop

        written, problems = scan.copy_clips(
            self.sources, self.base, True, True, self.flight_date, report
        )
        self.done.emit(written, problems)


class CopyDialog(QDialog):
    """Asks where the clips go and, crucially, when they were actually flown."""

    def __init__(self, count: int, total_bytes: int, initial: QDate, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Copy originals to library")
        self.setMinimumWidth(470)

        layout = QVBoxLayout(self)
        headline = QLabel(
            f"Copying {count} original .ts recordings ({human_size(total_bytes)}) "
            "off the card, exactly as they were filmed."
        )
        headline.setWordWrap(True)
        layout.addWidget(headline)
        layout.addWidget(dim(QLabel(
            "Nothing is converted here. Use the export presets for that."
        )))

        form = QFormLayout()
        row = QHBoxLayout()
        self.folder_edit = QComboBox()
        self.folder_edit.setEditable(True)
        self.folder_edit.addItem(str(Path.home() / "Videos" / "FPV"))
        row.addWidget(self.folder_edit, 1)
        pick = QPushButton("…")
        pick.setFixedWidth(34)
        pick.clicked.connect(self._browse)
        row.addWidget(pick)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Library folder:", holder)

        self.date_edit = QDateEdit(initial)
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd MMM yyyy")
        form.addRow("Flight date:", self.date_edit)
        layout.addLayout(form)

        layout.addWidget(dim(QLabel(
            "Clips are filed into a folder named after the flight date and get "
            "that date added to their filenames."
        )))
        layout.addWidget(dim(QLabel(
            "The date has to be entered by hand because the goggles cannot keep "
            "time. There is a socket for a CR2032 on the board but no cell "
            "fitted, so the clock restarts from the same value on every "
            "power-up and stamps every recording with it."
        )))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Copy recordings into…")
        if folder:
            self.folder_edit.insertItem(0, folder)
            self.folder_edit.setCurrentIndex(0)

    @property
    def folder(self) -> Path:
        return Path(self.folder_edit.currentText().strip())

    @property
    def flight_date(self) -> date:
        return self.date_edit.date().toPython()


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
        # Guards handlers that fire while the window is still being assembled.
        self._ready = False

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
        self.resize(1240, 900)
        self._build()
        self._restore()
        self._ready = True
        self._relayout()
        self._on_preset_changed()
        self._on_colour_changed()
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

        outer.addWidget(self._build_update_bar())
        outer.addLayout(self._build_source_bar())

        splitter = self.splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left_column())

        # The export settings scroll, so nothing is ever cut off, but the
        # estimate and the Add button sit below the scroll area and stay put.
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroller.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # The Windows 11 style hides scrollbars until you hover, which made it
        # look as though content had simply vanished. Give it a solid width.
        scroller.verticalScrollBar().setStyleSheet(
            "QScrollBar:vertical { width: 12px; }"
        )
        scroller.setWidget(self._build_export_panel())
        right_layout.addWidget(scroller, 1)
        right_layout.addWidget(self._build_export_actions())

        right.setMinimumWidth(330)
        splitter.addWidget(right)

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
        """The legal notice GPL v3 section 5(d) asks an interactive program to show.

        It has to state the copyright, disclaim warranty, say the work may be
        redistributed under the licence, and say how to read the licence.
        """
        from . import __version__
        from .media import is_bundled, packaged_file

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
            "Not affiliated with or endorsed by HDZero."
        )
        box.setTextFormat(Qt.TextFormat.RichText)

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
        box.exec()

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
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        self.clip_count_label = QLabel("No clips loaded")
        header.addWidget(self.clip_count_label)
        header.addStretch(1)
        self.preview_button = QPushButton("Open in player…")
        self.preview_button.setToolTip(
            "Hand the highlighted clip to your usual video player.\n"
            "Double-clicking a row plays it here instead."
        )
        self.preview_button.clicked.connect(self._preview_selected)
        header.addWidget(self.preview_button)
        for text, slot in (("All", self._select_all), ("None", self._select_none)):
            button = QPushButton(text)
            button.setFixedWidth(58)
            button.clicked.connect(slot)
            header.addWidget(button)
        layout.addLayout(header)

        self.warning_label = dim(QLabel())
        self.warning_label.hide()
        layout.addWidget(self.warning_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Clip", "Length", "Size", "Card date", "Format"]
        )
        self.table.horizontalHeaderItem(3).setToolTip(
            "The timestamp on the card, not when you flew. The Box Pro has no "
            "clock battery, so these are unreliable."
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setIconSize(QSize(120, 68))
        self.table.setSortingEnabled(True)
        head = self.table.horizontalHeader()
        # The name column takes the slack, but the thumbnail grows into it, so
        # extra width buys a bigger preview rather than empty space.
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            head.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        head.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        head.setToolTip("Click a column heading to sort by it")
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemDoubleClicked.connect(self._play_item)
        self.table.itemSelectionChanged.connect(self._on_clip_selected)
        layout.addWidget(self.table, 1)
        return box

    def _build_left_column(self) -> QWidget:
        """The clip list above the player.

        Not a splitter, deliberately. How tall the preview is worth being is
        decided by how wide it is — see PreviewPanel — so there is nothing to
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
        """The video and its transport.

        Permanent, where trimming used to be behind an unticked box. Nobody
        ticks a box to find out what is behind it, so the feature this app
        exists for was hidden from everyone who had not been told about it.
        """
        box = self.preview_box = PreviewPanel()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(INNER)

        self.frame_view = FrameView()
        self.frame_view.clicked.connect(self._focus_player)
        layout.addWidget(self.frame_view, 1)

        # Beside the picture rather than under it. A 16:9 frame in a wide, short
        # box letterboxes to a third of the width, and that empty space is the
        # only place these can go without taking height from the clip list.
        layout.addWidget(self._build_preview_sidebar())

        box.view = self.frame_view
        box.sidebar = self.preview_sidebar
        return box

    def _build_preview_sidebar(self) -> QWidget:
        side = self.preview_sidebar = QWidget()
        side.setFixedWidth(190)
        column = QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(TIGHT)
        column.addStretch(1)

        # The name and the position are separate labels because the position
        # changes thirty times a second and the name changes once a clip. One
        # short label relaying beats one long one doing it.
        self.trim_title = QLabel("Select a clip")
        self.trim_title.setWordWrap(True)
        column.addWidget(self.trim_title)

        self.trim_position = dim(QLabel(""))
        column.addWidget(self.trim_position)

        self.trim_summary = dim(QLabel(""))
        column.addWidget(self.trim_summary)
        column.addSpacing(TIGHT)

        # What you are looking at, which until now was only readable as columns
        # in the list. Two lines rather than three, and the buttons share a row:
        # this column's height is the floor on how short the panel can get, so
        # every line here is one the picture cannot have on a narrow window.
        # These change once a clip, so they are set where the clip is loaded
        # rather than in _update_trim_labels, which runs on every painted frame.
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
        self.play_button.clicked.connect(self._toggle_play)
        column.addWidget(self.play_button)

        trim_row = QHBoxLayout()
        trim_row.setContentsMargins(0, 0, 0, 0)
        trim_row.setSpacing(TIGHT)
        for text, slot, tip in (
            ("In", self._set_in, "Start the export at the playhead  (I)"),
            ("Out", self._set_out, "End the export at the playhead  (O)"),
            ("Reset", self._reset_trim, "Use the whole clip again"),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            trim_row.addWidget(button)
        column.addLayout(trim_row)

        # Labels and buttons stay together as one block, centred. Pinning the
        # labels to the top and the buttons to the bottom left a hole down the
        # middle of the column.
        column.addStretch(1)

        # Silence is said out loud so its absence is not filed as a bug. Audio
        # would need a second pipe, a second clock and an output device, and
        # DVR sound is motor whine — an in point is found by eye.
        keys = dim(QLabel("Silent · click the picture, then Space plays"))
        keys.setToolTip(
            "With the picture focused:\n"
            "Space or K — play or pause\n"
            "I / O — set the in / out point at the playhead\n"
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

    def _build_trim_band(self) -> QWidget:
        """The filmstrip, full width, directly under the preview it scrubs.

        Full width is not decoration. On a three and a half minute clip it is
        5.8 pixels per second of footage against 3.4 in the left column alone,
        which is the difference when you are dragging an out point onto a
        particular moment.
        """
        band = self.trim_band = QWidget()
        layout = QVBoxLayout(band)
        layout.setContentsMargins(0, 0, 0, 0)

        self.trim_bar = TrimBar()
        self.trim_bar.setToolTip(
            "Every keyframe in the clip, a second apart. Click to move the "
            "playhead, drag either end to set where the export starts and ends."
        )
        self.trim_bar.playhead_moved.connect(self._on_playhead)
        self.trim_bar.trim_changed.connect(self._on_trim_changed)
        layout.addWidget(self.trim_bar)
        return band

    def _build_export_panel(self) -> QWidget:
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
        self.options_stack.addWidget(self._build_edit_options())
        self.options_stack.addWidget(self._build_master_options())
        self.options_stack.addWidget(self._build_social_options())
        self.options_stack.addWidget(self._build_upload_options())
        self.options_stack.addWidget(self._build_remux_options())
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

        self.subfolder_check = QCheckBox("Put each preset in its own subfolder")
        self.subfolder_check.setChecked(True)
        self.subfolder_check.toggled.connect(self._on_output_changed)
        out_layout.addWidget(self.subfolder_check)

        self.audio_check = QCheckBox("Keep the audio track")
        self.audio_check.setChecked(True)
        self.audio_check.setToolTip(
            "DVR audio is mostly motor noise and wind. Dropping it makes a "
            "small file smaller, and there is less to distract an editor."
        )
        self.audio_check.toggled.connect(self._update_estimate)
        out_layout.addWidget(self.audio_check)

        self.join_check = QCheckBox("Join the ticked clips into one file")
        self.join_check.setToolTip(
            "Useful when the DVR split a single flight across several recordings"
        )
        self.join_check.toggled.connect(self._update_estimate)
        out_layout.addWidget(self.join_check)

        date_row = QHBoxLayout()
        self.date_check = QCheckBox("Start filenames with the flight date")
        self.date_check.toggled.connect(self._on_date_toggled)
        date_row.addWidget(self.date_check)
        self.export_date = QDateEdit(QDate.currentDate())
        self.export_date.setCalendarPopup(True)
        self.export_date.setDisplayFormat("dd MMM yyyy")
        self.export_date.setEnabled(False)
        self.export_date.dateChanged.connect(self._retarget_pending)
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

    def _build_export_actions(self) -> QWidget:
        """Estimate and Add button, kept out of the scroll area.

        These are the things you always need to reach, so they stay pinned to
        the bottom of the panel however far the settings above are scrolled.
        """
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, INNER, 0, 0)

        self.estimate_label = QLabel()
        self.estimate_label.setWordWrap(True)
        layout.addWidget(self.estimate_label)

        add = QPushButton("Add to queue")
        add.setMinimumHeight(30)
        add.clicked.connect(self._add_to_queue)
        layout.addWidget(add)
        return box

    def _build_edit_options(self) -> QWidget:
        box = QWidget()
        form = QFormLayout(box)
        self.edit_codec_combo = QComboBox()
        for key, (label, _, _, _) in EDIT_CODECS.items():
            # The GB/hour figure depends on the footage, so it is filled in by
            # _refresh_output_options once there are clips to measure.
            self.edit_codec_combo.addItem(label, key)
        self.edit_codec_combo.currentIndexChanged.connect(self._update_estimate)
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
        self.upload_height.currentIndexChanged.connect(self._update_estimate)
        self.upload_height.setToolTip(
            "The resolution you hand the platform, not the resolution the "
            "goggles recorded."
        )
        form.addRow("Upload at:", self.upload_height)

        self.upload_quality = QComboBox()
        for crf, name, _ in QUALITY_LEVELS:
            self.upload_quality.addItem(name, crf)
        self.upload_quality.setCurrentIndex(1)          # High
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
        self.master_quality.setCurrentIndex(1)          # High
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
        self.gpu_check.toggled.connect(self._update_estimate)
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
        self.social_size.valueChanged.connect(self._update_estimate)
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
        # _refresh_output_options. Nothing about the source size is hardcoded.
        self.social_height = QComboBox()
        self.social_height.addItem("Keep original", 0)
        self.social_height.currentIndexChanged.connect(self._update_estimate)
        form.addRow("Resolution:", self.social_height)

        self.social_fps = QComboBox()
        self.social_fps.addItem("Keep original", 0)
        self.social_fps.currentIndexChanged.connect(self._update_estimate)
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

    def _build_queue(self) -> QWidget:
        """A one-line strip that opens into the queue when there is one.

        Closed to start with, because an empty queue was taking two hundred
        pixels of height off a window where the clip list and the picture are
        both short of it. Not a saved setting: jobs do not survive a session
        either, so "closed" and "closed unless there is something in it" are
        the same thing on startup.
        """
        box = QWidget()
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(TIGHT)

        header = QHBoxLayout()
        self.queue_toggle = QToolButton()
        self.queue_toggle.setText("Queue — empty")
        self.queue_toggle.setCheckable(True)
        self.queue_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.queue_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.queue_toggle.setAutoRaise(True)
        self.queue_toggle.toggled.connect(self._on_queue_toggled)
        header.addWidget(self.queue_toggle)

        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, 1000)
        self.overall_bar.setValue(0)
        self.overall_bar.setTextVisible(True)
        self.overall_bar.setFormat("idle")
        self.overall_bar.setMinimumWidth(220)
        header.addWidget(self.overall_bar, 1)
        self.overall_label = QLabel("")
        self.overall_label.setMinimumWidth(210)
        header.addWidget(self.overall_label)
        header.addSpacing(GAP)

        # These two stay on the header rather than inside the body. They are
        # the reason the whole panel cannot simply be hidden: About carries the
        # GPL and LGPL notices, and a licence you can only reach by opening a
        # queue you have no jobs in is not much of a notice.
        open_out = QPushButton("Open output folder")
        open_out.clicked.connect(lambda: reveal(Path(self.out_edit.currentText())))
        header.addWidget(open_out)

        about = QPushButton("About")
        about.setToolTip("Version, licence and attribution")
        about.clicked.connect(self._show_about)
        header.addWidget(about)
        outer.addLayout(header)

        body = self.queue_body = QWidget()
        body.hide()
        outer.addWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)

        self.queue_table = QTableWidget(0, 4)
        self.queue_table.setHorizontalHeaderLabels(["Clip", "Preset", "Progress", "Status"])
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.queue_table.itemDoubleClicked.connect(self._open_finished_job)
        self.queue_table.setMaximumHeight(150)
        head = self.queue_table.horizontalHeader()
        # Filenames are short; the progress bar is the thing worth watching, so
        # it gets the width rather than the name column.
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.queue_table)

        row = QHBoxLayout()
        self.start_button = QPushButton("Start export")
        self.start_button.clicked.connect(self._start)
        row.addWidget(self.start_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        row.addWidget(self.cancel_button)
        remove = QPushButton("Remove selected")
        remove.setToolTip("Drop the selected rows. Delete key does the same.")
        remove.clicked.connect(self._remove_selected_jobs)
        row.addWidget(remove)

        clear = QPushButton("Clear queue")
        clear.setToolTip("Empty the queue. Anything currently encoding carries on.")
        clear.clicked.connect(self._clear_queue)
        row.addWidget(clear)
        row.addStretch(1)

        layout.addLayout(row)
        return box

    def _on_queue_toggled(self, open_: bool) -> None:
        self.queue_body.setVisible(open_)
        self.queue_toggle.setArrowType(
            Qt.ArrowType.DownArrow if open_ else Qt.ArrowType.RightArrow)

    def _open_queue(self) -> None:
        if not self.queue_toggle.isChecked():
            self.queue_toggle.setChecked(True)

    def _queue_summary(self) -> str:
        """What the strip says when it is closed, and while it is open."""
        if not self.jobs:
            return "Queue — empty"
        counts: dict[JobStatus, int] = {}
        for job in self.jobs:
            counts[job.status] = counts.get(job.status, 0) + 1
        order = [JobStatus.RUNNING, JobStatus.PENDING, JobStatus.DONE,
                 JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.SKIPPED]
        parts = [f"{counts[status]} {status.value.lower()}"
                 for status in order if counts.get(status)]
        return "Queue — " + ", ".join(parts)

    # -- settings -------------------------------------------------------------

    def _combo_settings(self) -> list[tuple[str, QComboBox]]:
        """Combos whose choice is carried in the item data."""
        return [
            ("colour", self.colour_combo),
            ("edit_codec", self.edit_codec_combo),
            ("master_quality", self.master_quality),
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

    def _restore(self) -> None:
        store = self.settings_store
        last_out = store.value("output_dir", str(Path.home() / "Videos" / "FPV"))
        self.out_edit.addItem(str(last_out))

        preset = store.value("preset", "master")
        if preset in self.preset_buttons:
            self.preset_buttons[preset].setChecked(True)

        for key, combo in self._combo_settings():
            saved = store.value(key, None)
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
            saved = store.value(key, None)
            if saved is not None and combo.findText(str(saved)) >= 0:
                combo.setCurrentText(str(saved))

        for key, check in self._check_settings():
            saved = store.value(key, None)
            if saved is not None:
                check.setChecked(str(saved).lower() in ("true", "1"))

        self.social_size.setValue(int(store.value("social_size_mb", 45)))

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
        store.setValue("output_dir", self.out_edit.currentText())
        store.setValue("preset", self._preset_key())
        for key, combo in self._combo_settings():
            store.setValue(key, combo.currentData())
        for key, combo in self._text_combo_settings():
            store.setValue(key, combo.currentText())
        for key, check in self._check_settings():
            store.setValue(key, check.isChecked())
        store.setValue("social_size_mb", self.social_size.value())
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
        if not self._ready or self.table.rowCount() == 0:
            return
        available = self.table.columnWidth(0)
        # Leave room for the filename itself alongside the picture.
        width = max(96, min(THUMB_WIDTH, available - 120))

        viewport = self.table.viewport().height()
        if viewport > 0:
            by_height = max(48, viewport // MIN_VISIBLE_CLIPS - 6)
            width = max(96, min(width, round(by_height * 16 / 9)))

        height = round(width * 9 / 16)
        if self.table.iconSize().width() == width:
            return
        self.table.setIconSize(QSize(width, height))
        for row in range(self.table.rowCount()):
            self.table.setRowHeight(row, height + 6)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._save()
        # First, because it is the one holding a decoder open on the card.
        self.player.shutdown()
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(4000)
        # Retired scans are included: one can still be inside a probe, and a
        # QThread destroyed while running takes the process with it.
        if self.update_check and self.update_check.isRunning():
            self.update_check.wait(2000)
        for thread in [self.scan_worker, self.copy_worker, *self._retired_scans]:
            if thread and thread.isRunning():
                thread.stop()
                thread.wait(2000)
        for loader in [self._strip_loader, *self._retired_strips]:
            if loader and loader.isRunning():
                loader.wait(2000)
        self.thumbs.shutdown()
        super().closeEvent(event)

    # -- hardware -------------------------------------------------------------

    def _hardware_found(self, found) -> None:
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
        self.hw_encoder, self.hw_label = found
        self.gpu_check.setEnabled(True)
        self.gpu_check.setText(f"Use hardware encoding ({self.hw_label})")
        self.gpu_help.setText(
            f"Hands the encoding to the video chip on your {self.hw_label} "
            "hardware instead of the processor. Roughly three times faster, and "
            "slightly larger for the same quality. Detected by running a test "
            "encode, so it is known to work on this machine."
        )

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
        self._sync_thumbnail_size()
        self._refresh_export_markers()
        self._update_counts()
        if count == 0:
            self.clip_count_label.setText("No readable video files found here")

        message = scan.timestamps_are_unreliable([c.modified for c in self.clips])
        if message:
            self.warning_label.setText(message)
            self.warning_label.show()

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
        clips = self._relevant_clips()
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
    def _repopulate(combo: QComboBox, options: list[tuple[str, int]]) -> None:
        """Replace a combo's contents, keeping the current choice if it survives."""
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
        sample = max(clips, key=lambda c: c.width * c.height * (c.fps or 60), default=None)
        previous = self.edit_codec_combo.currentData()
        self.edit_codec_combo.blockSignals(True)
        self.edit_codec_combo.clear()
        for key, (label, _, _, _) in EDIT_CODECS.items():
            if sample is not None and sample.width:
                gb_per_hour = edit_bitrate_mbps(key, sample) * 450 / 1000
                self.edit_codec_combo.addItem(f"{label}  ~{gb_per_hour:.0f} GB/hour", key)
            else:
                self.edit_codec_combo.addItem(label, key)
        index = self.edit_codec_combo.findData(previous)
        self.edit_codec_combo.setCurrentIndex(index if index >= 0 else 0)
        self.edit_codec_combo.blockSignals(False)

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

        # Held rather than dropped. A FilmstripLoader cannot be told to stop
        # mid-decode, and letting the last reference to a running QThread go is
        # how Qt takes the process down with it.
        if self._strip_loader and self._strip_loader.isRunning():
            self._strip_loader.wait(50)
            self._retired_strips.append(self._strip_loader)
        self._retired_strips = [t for t in self._retired_strips
                                if t.isRunning()]
        self._strip_loader = FilmstripLoader(self.tools, clip, self)
        self._strip_loader.ready.connect(self._strip_ready)
        self._strip_loader.start()

    def _strip_ready(self, clip_path: str, strip) -> None:
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
        for key, button in self.preset_buttons.items():
            if button.isChecked():
                return key
        return "master"

    def _on_preset_changed(self) -> None:
        if not self._ready:
            return
        key = self._preset_key()
        self.preset_help.setText(PRESETS[key].blurb)
        self.options_stack.setCurrentIndex(PRESET_ORDER.index(key))
        self.colour_combo.setEnabled(key != "remux")
        self.audio_check.setEnabled(key != "remux")
        self.trim_note.setVisible(key == "remux")
        self._on_colour_changed()
        self._on_quality_changed()
        self._refresh_export_markers()
        self._update_estimate()

    def _on_colour_changed(self) -> None:
        if not self._ready:
            return
        if self._preset_key() == "remux":
            # The help text stays legible even though the control above it is
            # not available, because it is explaining why.
            self.colour_help.setText(
                "Remux copies the video stream as it is, so there is no colour "
                "processing to choose."
            )
            return
        key = self.colour_combo.currentData()
        for mode_key, _, description in COLOUR_MODES:
            if mode_key == key:
                self.colour_help.setText(description)
                break

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
        self._update_estimate()

    def _on_date_toggled(self, checked: bool) -> None:
        self.export_date.setEnabled(checked)
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
        out_dir = Path(self.out_edit.currentText().strip() or ".")
        key = self._preset_key()
        subfolders = self.subfolder_check.isChecked()
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
        if not self.date_check.isChecked():
            return None
        return self.export_date.date().toPython()

    def _on_social_mode(self) -> None:
        by_size = self.social_mode.currentData() == "size"
        self.social_size.setEnabled(by_size)
        self.social_quality.setEnabled(not by_size)
        self._update_estimate()

    def current_settings(self) -> ExportSettings:
        return ExportSettings(
            colour=self.colour_combo.currentData(),
            edit_codec=self.edit_codec_combo.currentData(),
            master_crf=self.master_quality.currentData(),
            master_speed=self.master_speed.currentText(),
            social_mode=self.social_mode.currentData(),
            social_size_mb=self.social_size.value(),
            social_crf=self.social_quality.currentData(),
            social_height=self.social_height.currentData(),
            social_fps=self.social_fps.currentData(),
            upload_height=self.upload_height.currentData() or 1080,
            upload_crf=self.upload_quality.currentData(),
            upload_speed=self.upload_speed.currentText(),
            use_gpu=self.gpu_check.isChecked() and self.gpu_check.isEnabled(),
            hw_encoder=self.hw_encoder,
            keep_audio=self.audio_check.isChecked(),
        )

    def _update_estimate(self) -> None:
        if not self._ready:
            return
        clips = self.selected_clips()
        if not clips:
            self.estimate_label.setText("Tick some clips to see an estimated output size.")
            return
        settings = self.current_settings()
        key = self._preset_key()
        total = sum(estimate_output_size(c, key, settings) for c in clips)

        if self.join_check.isChecked() and len(clips) > 1:
            if key == "social" and settings.social_mode == "size":
                total = settings.social_size_mb * 1024 * 1024
            summary = f"1 joined file, about {human_size(total)}"
        else:
            summary = f"{len(clips)} files, about {human_size(total)} in total"

        runtime = human_duration(sum(c.duration for c in clips))
        self.estimate_label.setText(f"{summary}  ·  {runtime} of footage")

    # -- queue ----------------------------------------------------------------

    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose where exports go")
        if folder:
            self.out_edit.insertItem(0, folder)
            self.out_edit.setCurrentIndex(0)

    def _add_to_queue(self) -> None:
        clips = self.selected_clips()
        if not clips:
            QMessageBox.warning(self, "Nothing ticked", "Tick at least one clip first.")
            return
        out_dir = Path(self.out_edit.currentText().strip())
        if not str(out_dir).strip():
            QMessageBox.warning(self, "No output folder", "Choose where the exports should go.")
            return

        key = self._preset_key()
        settings = self.current_settings()
        subfolders = self.subfolder_check.isChecked()
        stamp = self.flight_date()

        # Two jobs writing the same filename would just overwrite each other.
        already = {
            output_key(j.out_path) for j in self.jobs
            if j.status in (JobStatus.PENDING, JobStatus.RUNNING)
        }
        before = len(self.jobs)

        if self.join_check.isChecked() and len(clips) > 1:
            # Joined in DVR counter order: the file timestamps cannot be trusted.
            ordered = sorted(clips, key=lambda c: (c.sequence, natural_key(c.path.name)))

            # Refused rather than exported wrongly. A join built from mismatched
            # clips does not fail; it produces a file that is silent after the
            # first clip, or the wrong length, and looks like it worked.
            problems = join_problems(ordered)
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
        skipped = (1 if self.join_check.isChecked() and len(clips) > 1 else len(clips)) - added
        self._rebuild_queue()
        note = f"{added} queued"
        if skipped > 0:
            note += f", {skipped} already in the queue"
        self.statusBar().showMessage(note, 5000)

    def _rebuild_queue(self) -> None:
        # The single funnel for anything that changes the queue, so this is
        # where the strip learns what to say and when to open itself.
        self.queue_toggle.setText(self._queue_summary())
        if self.jobs:
            self._open_queue()

        self.queue_table.setRowCount(len(self.jobs))
        for row, job in enumerate(self.jobs):
            # Showing what will be written, not what is being read, so the
            # effect of the flight-date setting is visible before you start.
            name_item = QTableWidgetItem(job.out_path.name)
            name_item.setToolTip(f"{job.name}\n  ->  {job.out_path}")
            self.queue_table.setItem(row, 0, name_item)
            self.queue_table.setItem(row, 1, QTableWidgetItem(job.preset_label))
            bar = self.queue_table.cellWidget(row, 2)
            if not isinstance(bar, QProgressBar):
                bar = QProgressBar()
                bar.setRange(0, 1000)
                bar.setTextVisible(True)
                self.queue_table.setCellWidget(row, 2, bar)
            bar.setValue(int(job.progress * 1000))
            bar.setFormat(f"{job.progress * 100:.0f}%")
            status = job.status.value
            if job.message and job.status in (JobStatus.DONE, JobStatus.FAILED):
                status = f"{job.status.value} — {job.message}"
            self.queue_table.setItem(row, 3, QTableWidgetItem(status))

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
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.worker.start()

    def _update_overall(self, done_seconds: float) -> None:
        fraction = max(0.0, min(1.0, done_seconds / self._queue_total))
        self.overall_bar.setValue(int(fraction * 1000))
        self.overall_bar.setFormat(f"overall {fraction * 100:.0f}%")

        elapsed = time.monotonic() - self._queue_started
        text = f"elapsed {human_duration(elapsed)}"
        # Wait until there is enough done for the rate to mean anything.
        if fraction > 0.02 and elapsed > 3:
            remaining = elapsed / fraction - elapsed
            text += f"  ·  about {human_duration(remaining)} left"
        self.overall_label.setText(text)

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
        available = scan.free_space(existing_ancestor(pending[0].out_path.parent))
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
        item = self.queue_table.item(row, 3)
        if item:
            item.setText(JobStatus.RUNNING.value)

    def _job_progress(self, index: int, fraction: float, speed: str) -> None:
        job, row = self._reported_job(index)
        if job is None:
            return
        job.progress = fraction
        bar = self.queue_table.cellWidget(row, 2) if row is not None else None
        if isinstance(bar, QProgressBar):
            bar.setValue(int(fraction * 1000))
            bar.setFormat(f"{fraction * 100:.0f}%  {speed}".strip())
        self._update_overall(self._queue_done + fraction * job.total_duration)

    def _job_finished(self, index: int, ok: bool, message: str) -> None:
        job, row = self._reported_job(index)
        if job is None:
            return
        if row is not None:
            item = self.queue_table.item(row, 3)
            if item:
                item.setText(f"{job.status.value} — {message}" if message
                             else job.status.value)
            bar = self.queue_table.cellWidget(row, 2)
            if isinstance(bar, QProgressBar) and ok:
                bar.setValue(1000)
                bar.setFormat("100%")
        self._queue_done += job.total_duration
        self._update_overall(self._queue_done)

    def _queue_finished(self, completed: int, failed: int) -> None:
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        # Newly written files mean more clips can be marked as done.
        self._refresh_export_markers()
        note = f"Finished: {completed} exported"
        if failed:
            note += f", {failed} failed"
        self.statusBar().showMessage(note, 10000)

        total = time.monotonic() - self._queue_started
        self.overall_bar.setValue(1000)
        self.overall_bar.setFormat("done")
        self.overall_label.setText(f"finished in {human_duration(total)}")

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
        dialog = CopyDialog(len(clips), needed, self.export_date.date(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # One flight date for the session: setting it here fills it in for
        # exports too, so both routes off the card end up labelled the same.
        self.export_date.setDate(QDate(dialog.flight_date))
        self.date_check.setChecked(True)

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
