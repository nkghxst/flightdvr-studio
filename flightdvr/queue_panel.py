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
    QAbstractItemView, QAbstractScrollArea, QHBoxLayout, QHeaderView, QLabel,
    QProgressBar, QPushButton, QSizePolicy, QSpacerItem, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from .jobs import Job, JobStatus
from .widgets import GAP, TIGHT, dim

# Classic's queue is a strip under the work, so its table is kept short. A page
# that is only the queue lifts that, and the table grows with its rows.
STRIP_TABLE_HEIGHT = 150
UNBOUNDED = 16777215


class QueuePanel(QWidget):
    """Render jobs and emit queue actions without owning export execution."""

    start_requested = Signal()
    cancel_requested = Signal()
    remove_requested = Signal()
    clear_requested = Signal()
    reveal_requested = Signal()
    about_requested = Signal()
    item_activated = Signal(object)
    # The job whose row is selected, or None. Selecting is looking: nothing
    # here starts, stops, removes or plays anything.
    job_selected = Signal(object)

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
        # A floor, not a size: it stretches wherever there is room. At 220 the
        # header alone held Flow's Queue page past the 1060px compact width
        # once a job's count lengthened the toggle beside it.
        self.overall_bar.setMinimumWidth(140)
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
        self.table.setMaximumHeight(STRIP_TABLE_HEIGHT)
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

        # What a selected job was submitted with, read from the job itself.
        # Shown only where the queue is the page; the strip has no room for it.
        self.details = QWidget()
        details = QVBoxLayout(self.details)
        details.setContentsMargins(0, TIGHT, 0, 0)
        details.setSpacing(TIGHT)
        self.details_title = QLabel("")
        details.addWidget(self.details_title)
        self.details_note = dim(QLabel(
            "Read-only. Editing the planned output it came from does not "
            "reach it."))
        self.details_note.setWordWrap(True)
        details.addWidget(self.details_note)
        self.details_body = QLabel("")
        self.details_body.setWordWrap(True)
        self.details_body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        details.addWidget(self.details_body)
        self.details.hide()
        layout.addWidget(self.details)

        # Takes the slack only when the queue is the page, so the jobs sit at
        # the top rather than being spread down it. Inert in the strip.
        self._tail = QSpacerItem(0, 0, QSizePolicy.Policy.Minimum,
                                 QSizePolicy.Policy.Minimum)
        layout.addItem(self._tail)

        self._jobs: list[Job] = []
        self._fills_page = False
        self._strip_was_open: bool | None = None
        self._shown_job: Job | None = None
        self.table.itemSelectionChanged.connect(self._on_selection)

    # -- the queue as a page (Flow) ---------------------------------------------

    def set_fills_page(self, fills: bool) -> None:
        """Arrange for a page that is only the queue, or back to the strip.

        On the page the table is as tall as its rows, the actions sit directly
        under it and the submitted details under those; the slack goes below.
        The body stays open, because collapsing it would empty the page.
        """
        fills = bool(fills)
        if fills and not self._fills_page:
            # Classic's own choice, open or closed, comes back with the strip.
            self._strip_was_open = self.toggle.isChecked()
        self._fills_page = fills
        if fills:
            self.table.setMaximumHeight(UNBOUNDED)
            self.table.setSizeAdjustPolicy(
                QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
            self.table.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Maximum)
            self._tail.changeSize(0, 0, QSizePolicy.Policy.Minimum,
                                  QSizePolicy.Policy.Expanding)
            self.open_queue()
        else:
            self.table.setMaximumHeight(STRIP_TABLE_HEIGHT)
            self.table.setSizeAdjustPolicy(
                QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
            self.table.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Expanding)
            self._tail.changeSize(0, 0, QSizePolicy.Policy.Minimum,
                                  QSizePolicy.Policy.Minimum)
            if self._strip_was_open is not None:
                self.toggle.setChecked(self._strip_was_open)
                self._strip_was_open = None
        self.toggle.setEnabled(not fills)
        self.details.setVisible(fills and self._shown_job is not None)
        self.body.layout().invalidate()

    @property
    def fills_page(self) -> bool:
        return self._fills_page

    def selected_job(self) -> Job | None:
        rows = self.table.selectionModel().selectedRows()
        if len(rows) != 1:
            return None
        row = rows[0].row()
        return self._jobs[row] if 0 <= row < len(self._jobs) else None

    def _on_selection(self) -> None:
        self.job_selected.emit(self.selected_job())

    def show_details(self, title: str, lines: list[str]) -> None:
        """Say what the selected job was submitted with. The window words it."""
        self._shown_job = self.selected_job()
        self.details_title.setText(f"<b>{title}</b>")
        self.details_body.setText("\n".join(lines))
        self.details.setVisible(self._fills_page)

    def clear_details(self) -> None:
        self._shown_job = None
        self.details_title.setText("")
        self.details_body.setText("")
        self.details.hide()

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
        # The selection is a job, not a row number. Rows are rewritten in place,
        # so after a removal the same row can hold a different job — and its
        # details would then describe something nobody selected.
        was = self.selected_job()
        self._jobs = list(jobs)
        self.toggle.setText(self.summary(jobs))
        if jobs:
            self.open_queue()
            if self._fills_page:
                # What Classic would have done had it been showing: jobs open
                # the strip, so it comes back open.
                self._strip_was_open = True

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

        blocked = self.table.blockSignals(True)
        try:
            self.table.clearSelection()
            if was is not None:
                for row, job in enumerate(self._jobs):
                    if job is was:
                        self.table.selectRow(row)
                        break
        finally:
            self.table.blockSignals(blocked)
        # Announced every time, so what the details say follows the job's
        # status rather than the moment it was selected.
        self.job_selected.emit(self.selected_job())

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
