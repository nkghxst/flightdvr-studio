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

"""The collapsible export queue and its local rendering behaviour."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QProgressBar,
    QPushButton, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout,
    QWidget,
)

from .jobs import Job, JobStatus
from .widgets import GAP, TIGHT


class QueuePanel(QWidget):
    """Render jobs and emit queue actions without owning export execution."""

    start_requested = Signal()
    cancel_requested = Signal()
    remove_requested = Signal()
    clear_requested = Signal()
    reveal_requested = Signal()
    about_requested = Signal()
    item_activated = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(TIGHT)

        header = QHBoxLayout()
        self.toggle = QToolButton()
        self.toggle.setText("Queue — empty")
        self.toggle.setCheckable(True)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.toggle.setAutoRaise(True)
        self.toggle.toggled.connect(self._on_toggled)
        header.addWidget(self.toggle)

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

        # These stay on the header because About carries the GPL and LGPL
        # notices; a licence hidden inside an empty queue is not reachable.
        open_out = QPushButton("Open output folder")
        open_out.clicked.connect(lambda *_: self.reveal_requested.emit())
        header.addWidget(open_out)

        about = QPushButton("About")
        about.setToolTip("Version, licence and attribution")
        about.clicked.connect(lambda *_: self.about_requested.emit())
        header.addWidget(about)
        outer.addLayout(header)

        self.body = QWidget()
        self.body.hide()
        outer.addWidget(self.body)
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Clip", "Preset", "Progress", "Status"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.itemDoubleClicked.connect(
            lambda item, *_: self.item_activated.emit(item)
        )
        self.table.setMaximumHeight(150)
        head = self.table.horizontalHeader()
        # Filenames are short; progress is the thing worth watching, so it gets
        # the width rather than the name column.
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        self.start_button = QPushButton("Start export")
        self.start_button.clicked.connect(lambda *_: self.start_requested.emit())
        row.addWidget(self.start_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(
            lambda *_: self.cancel_requested.emit()
        )
        row.addWidget(self.cancel_button)

        remove = QPushButton("Remove selected")
        remove.setToolTip("Drop the selected rows. Delete key does the same.")
        remove.clicked.connect(lambda *_: self.remove_requested.emit())
        row.addWidget(remove)

        clear = QPushButton("Clear queue")
        clear.setToolTip("Empty the queue. Anything currently encoding carries on.")
        clear.clicked.connect(lambda *_: self.clear_requested.emit())
        row.addWidget(clear)
        row.addStretch(1)
        layout.addLayout(row)

    def _on_toggled(self, open_: bool) -> None:
        self.body.setVisible(open_)
        self.toggle.setArrowType(
            Qt.ArrowType.DownArrow if open_ else Qt.ArrowType.RightArrow
        )

    def open_queue(self) -> None:
        if not self.toggle.isChecked():
            self.toggle.setChecked(True)

    @staticmethod
    def summary(jobs: list[Job]) -> str:
        """What the strip says when it is closed, and while it is open."""
        if not jobs:
            return "Queue — empty"
        counts: dict[JobStatus, int] = {}
        for job in jobs:
            counts[job.status] = counts.get(job.status, 0) + 1
        order = [
            JobStatus.RUNNING, JobStatus.PENDING, JobStatus.DONE,
            JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.SKIPPED,
        ]
        parts = [
            f"{counts[status]} {status.value.lower()}"
            for status in order if counts.get(status)
        ]
        return "Queue — " + ", ".join(parts)

    def rebuild(self, jobs: list[Job]) -> None:
        self.toggle.setText(self.summary(jobs))
        if jobs:
            self.open_queue()

        self.table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            # Showing what will be written, not what is being read, makes a
            # changed flight date visible before the export starts.
            name_item = QTableWidgetItem(job.out_path.name)
            name_item.setToolTip(f"{job.name}\n  ->  {job.out_path}")
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(job.preset_label))
            bar = self.table.cellWidget(row, 2)
            if not isinstance(bar, QProgressBar):
                bar = QProgressBar()
                bar.setRange(0, 1000)
                bar.setTextVisible(True)
                self.table.setCellWidget(row, 2, bar)
            bar.setValue(int(job.progress * 1000))
            bar.setFormat(f"{job.progress * 100:.0f}%")
            status = job.status.value
            if job.message and job.status in (JobStatus.DONE, JobStatus.FAILED):
                status = f"{job.status.value} — {job.message}"
            self.table.setItem(row, 3, QTableWidgetItem(status))

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)

    def set_overall(self, fraction: float, text: str) -> None:
        self.overall_bar.setValue(int(fraction * 1000))
        self.overall_bar.setFormat(f"overall {fraction * 100:.0f}%")
        self.overall_label.setText(text)

    def finish_overall(self, text: str) -> None:
        self.overall_bar.setValue(1000)
        self.overall_bar.setFormat("done")
        self.overall_label.setText(text)

    def mark_started(self, row: int) -> None:
        item = self.table.item(row, 3)
        if item:
            item.setText(JobStatus.RUNNING.value)

    def mark_progress(self, row: int, fraction: float, speed: str) -> None:
        bar = self.table.cellWidget(row, 2)
        if isinstance(bar, QProgressBar):
            bar.setValue(int(fraction * 1000))
            bar.setFormat(f"{fraction * 100:.0f}%  {speed}".strip())

    def mark_finished(self, row: int, ok: bool, status: JobStatus,
                      message: str) -> None:
        item = self.table.item(row, 3)
        if item:
            item.setText(f"{status.value} — {message}" if message else status.value)
        bar = self.table.cellWidget(row, 2)
        if isinstance(bar, QProgressBar) and ok:
            bar.setValue(1000)
            bar.setFormat("100%")
