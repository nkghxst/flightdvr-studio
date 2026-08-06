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

"""The threads that keep the window responsive, and the copy dialog.

Each carries a generation or a cancel flag for the same reason: they finish
in whatever order they finish, and a result from a run nobody is waiting on
any more must not be mistaken for the current one.
"""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from . import scan
from .format import human_size
from .media import (
    ClipInfo, Tools, detect_hardware_encoder, probe, stop_process,
)
from .widgets import dim

# Probing is I/O bound on a card reader, so a few at once helps a lot; beyond
# about four the reader becomes the limit and it gets slower again.
PROBE_WORKERS = 4

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
    """Works out which hardware encoder, if any, this machine can really use.

    Cancellable, because the answer costs a real test encode per candidate. A
    window closed while this is still going used to be destroyed with the
    thread still running, which takes the process down with it — an abort on
    macOS, where a VideoToolbox probe is slow enough to still be there.
    """

    result = Signal(object)

    def __init__(self, tools: Tools, parent=None):
        super().__init__(parent)
        self.tools = tools
        self._cancel = False
        self._process: subprocess.Popen | None = None

    def stop(self) -> None:
        """Ask the probe to give up, and unblock it so it notices.

        Order matters, as it does in the decoder: the flag first, then the
        process, or the run can start another encode after the process is gone.
        """
        self._cancel = True
        stop_process(self._process)

    def _register(self, proc) -> None:
        self._process = proc
        # stop() can land between the encode starting and this assignment, in
        # which case it found nothing to stop and this is the only thing that
        # will stop it.
        if self._cancel and proc is not None:
            stop_process(proc)

    def run(self) -> None:
        try:
            found = detect_hardware_encoder(
                self.tools, should_stop=lambda: self._cancel,
                register=self._register,
            )
        except Exception:  # pragma: no cover - never block startup on this
            found = None
        # A cancelled probe has nothing to say, and the window it would say it
        # to is on its way out.
        if not self._cancel:
            self.result.emit(found)


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
