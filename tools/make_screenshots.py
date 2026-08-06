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

"""Capture the screenshots used in the README.

The window renders itself to a pixmap rather than the desktop being
photographed, so there is no cursor, no wallpaper and no other windows in the
frame, and the result is identical every run.

    python tools/make_screenshots.py <folder of clips> [output dir]

Run it on the normal desktop, not with QT_QPA_PLATFORM=offscreen: the offscreen
platform has no fonts and every label comes out as empty boxes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flightdvr.media import find_tools  # noqa: E402
from flightdvr.ui import MainWindow  # noqa: E402

# Deliberately not a path under C:\Users\<name>, so no username is on show.
OUTPUT_SHOWN = r"D:\FPV\Exports"

# Every capture uses this, including the demo recording, which imports it from
# here. Two copies of the number drifted apart once and produced a GIF that was
# 60px shorter than the stills, cutting off the last of the export options.
#
# Comfortably above the layout's minimum height, so Qt does not silently clamp
# it and hand back a window of a different size than was asked for.
WINDOW = (1240, 1160)


class Session:
    def __init__(self, source: Path, out_dir: Path):
        self.source = source
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        self.app = QApplication.instance() or QApplication([])
        self.w = MainWindow(find_tools())
        self.w.resize(*WINDOW)
        self.w.show()
        self.app.processEvents()
        actual = (self.w.width(), self.w.height())
        if actual != WINDOW:
            print(f"note: window settled at {actual}, not {WINDOW}")

    def shot(self, name: str) -> None:
        self.app.processEvents()
        path = self.out / name
        self.w.grab().save(str(path))
        print(f"   {name}  ({path.stat().st_size // 1024} KB)")

    def later(self, ms: int, fn) -> None:
        QTimer.singleShot(ms, fn)

    # -- the sequence ---------------------------------------------------------

    def start(self) -> None:
        w = self.w
        w.export_panel.out_edit.setCurrentText(OUTPUT_SHOWN)
        w.source_combo.insertItem(0, str(self.source), str(self.source))
        w.source_combo.setCurrentIndex(0)
        w.recursive_check.setChecked(False)
        print("scanning…")
        w._scan()
        w.scan_worker.done.connect(lambda _n: self.later(9000, self.browse_shot))

    def browse_shot(self) -> None:
        """Thumbnails in, a few clips ticked, a preset chosen."""
        w = self.w
        for row in range(min(3, w.table.rowCount())):
            w.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        w.export_panel.preset_buttons["master"].setChecked(True)
        w.table.selectRow(0)
        w.table.scrollToTop()
        print("capturing:")
        self.shot("01-browse.png")
        self.later(400, self.open_trim)

    def open_trim(self) -> None:
        w = self.w
        # Queue first, so the trim shot shows a window with work in it rather
        # than a large empty panel.
        w.export_panel.preset_buttons["social"].setChecked(True)
        w._add_to_queue()
        w.table.selectRow(1)
        w._on_clip_selected()
        # The filmstrip needs a moment; it decodes every keyframe once.
        self.later(9000, self.trim_shot)

    def trim_shot(self) -> None:
        w = self.w
        clip = w._trim_clip
        if clip and clip.duration > 90:
            w.trim_bar.in_point = 42.0
            w.trim_bar.out_point = min(clip.duration - 12.0, 165.0)
            w.trim_bar.playhead = 96.0
            w.trim_bar.update()
            w._on_trim_changed(w.trim_bar.in_point, w.trim_bar.out_point)
            w._on_playhead(96.0)
        self.shot("02-trim.png")
        self.later(400, self.queue_up)

    def queue_up(self) -> None:
        w = self.w
        w._start()
        print("   encoding for the queue shot…")
        self.later(22000, self.queue_shot)

    def queue_shot(self) -> None:
        self.shot("03-queue.png")
        w = self.w
        if w.worker and w.worker.isRunning():
            w.worker.cancel()
            w.worker.wait(6000)
        self.later(800, self.done)

    def done(self) -> None:
        print("\ndone")
        self.app.quit()


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: make_screenshots.py <folder of clips> [output dir]")
    source = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("docs")
    if not source.exists():
        raise SystemExit(f"no such folder: {source}")

    session = Session(source, out)
    session.later(600, session.start)
    QTimer.singleShot(180000, session.app.quit)
    return session.app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
