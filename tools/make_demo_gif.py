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

"""Record the short demo loop used in the README and on forums.

Frames come from the window drawing itself, so there is no cursor and no
desktop behind it. ffmpeg then builds a GIF with a generated palette, which is
what keeps the file small enough to post.

    python tools/make_demo_gif.py <folder of clips> [output dir]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flightdvr.media import NO_WINDOW, find_tools  # noqa: E402
from flightdvr.ui import MainWindow  # noqa: E402

# Shared with the stills on purpose. These two had their own copies of the
# window size once, drifted apart, and the recording ended up showing fewer
# export options than the screenshots did.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_screenshots import OUTPUT_SHOWN, WINDOW  # noqa: E402

FPS = 8
INTERVAL_MS = 1000 // FPS

# Wide enough that the settings text stays readable once GitHub scales the
# README image down.
GIF_WIDTH = 1000


class Recorder:
    def __init__(self, source: Path, out_dir: Path):
        self.source = source
        self.out = out_dir
        self.frames_dir = out_dir / "_frames"
        for d in (self.out, self.frames_dir):
            d.mkdir(parents=True, exist_ok=True)
        for old in self.frames_dir.glob("*.png"):
            old.unlink()

        self.app = QApplication.instance() or QApplication([])
        self.w = MainWindow(find_tools())
        self.w.resize(*WINDOW)
        self.w.show()
        self.app.processEvents()
        actual = (self.w.width(), self.w.height())
        if actual != WINDOW:
            print(f"note: window settled at {actual}, not {WINDOW}")
        self.frame = 0
        self.script: list = []

    # -- recording ------------------------------------------------------------

    def capture(self) -> None:
        self.w.grab().save(str(self.frames_dir / f"f_{self.frame:04d}.png"))
        self.frame += 1

    def tick(self) -> None:
        """One frame: run whatever this beat calls for, then photograph it."""
        if self.script:
            action = self.script.pop(0)
            if action is not None:
                action()
        self.capture()
        if self.script:
            QTimer.singleShot(INTERVAL_MS, self.tick)
        else:
            self.finish()

    def hold(self, frames: int) -> None:
        self.script += [None] * frames

    def do(self, fn) -> None:
        self.script.append(fn)

    # -- the performance ------------------------------------------------------

    def build_script(self) -> None:
        w = self.w

        self.hold(4)
        # Tick three clips, one beat each, so the ticks are visible.
        for row in range(3):
            self.do(lambda r=row: w.table.item(r, 0).setCheckState(Qt.CheckState.Checked))
            self.hold(1)
        self.hold(3)

        # Select a clip and let the permanent preview filmstrip land.
        self.do(lambda: (w.table.selectRow(1), w._on_clip_selected()))
        self.hold(int(FPS * 5.5))

        # Walk the in point in, as though dragging the handle.
        for seconds in range(0, 46, 5):
            self.do(lambda s=seconds: self.set_in(float(s)))
        self.hold(3)
        # Then pull the out point back.
        clip_end = 190.0
        for seconds in range(int(clip_end), 150, -8):
            self.do(lambda s=seconds: self.set_out(float(s)))
        self.hold(6)

        # Queue it and let the encode run.
        self.do(
            lambda: w.export_panel.preset_buttons["social"].setChecked(True)
        )
        self.hold(2)
        self.do(w._add_to_queue)
        self.hold(3)
        self.do(w._start)
        self.hold(int(FPS * 6))

    def set_in(self, seconds: float) -> None:
        w = self.w
        if not w._trim_clip:
            return
        w.trim_bar.in_point = seconds
        w.trim_bar.playhead = seconds
        w.trim_bar.update()
        w._on_trim_changed(w.trim_bar.in_point, w.trim_bar.out_point)
        w._on_playhead(seconds)

    def set_out(self, seconds: float) -> None:
        w = self.w
        if not w._trim_clip:
            return
        w.trim_bar.out_point = min(seconds, w._trim_clip.duration)
        w.trim_bar.playhead = w.trim_bar.out_point
        w.trim_bar.update()
        w._on_trim_changed(w.trim_bar.in_point, w.trim_bar.out_point)
        w._on_playhead(w.trim_bar.out_point)

    # -- start / finish -------------------------------------------------------

    def start(self) -> None:
        w = self.w
        w.export_panel.out_edit.setCurrentText(OUTPUT_SHOWN)
        w.source_combo.insertItem(0, str(self.source), str(self.source))
        w.source_combo.setCurrentIndex(0)
        w.recursive_check.setChecked(False)
        print("scanning…")
        w._scan()
        w.scan_worker.done.connect(lambda _n: QTimer.singleShot(7000, self.roll))

    def roll(self) -> None:
        print(f"recording at {FPS} fps…")
        self.build_script()
        self.tick()

    def finish(self) -> None:
        w = self.w
        if w.worker and w.worker.isRunning():
            w.worker.cancel()
            w.worker.wait(6000)
        print(f"   {self.frame} frames")
        self.encode()
        self.app.quit()

    def encode(self) -> None:
        tools = find_tools()
        pattern = str(self.frames_dir / "f_%04d.png")
        palette = self.frames_dir / "palette.png"
        gif = self.out / "demo.gif"
        mp4 = self.out / "demo.mp4"

        scale = f"scale={GIF_WIDTH}:-2:flags=lanczos"
        # A palette built from the whole clip keeps the UI colours honest and
        # the file far smaller than a naive conversion.
        subprocess.run([str(tools.ffmpeg), "-v", "error", "-y", "-framerate", str(FPS),
                        "-i", pattern, "-vf", f"{scale},palettegen=stats_mode=diff",
                        str(palette)], creationflags=NO_WINDOW, check=True)
        subprocess.run([str(tools.ffmpeg), "-v", "error", "-y", "-framerate", str(FPS),
                        "-i", pattern, "-i", str(palette), "-lavfi",
                        f"{scale}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5",
                        "-loop", "0", str(gif)], creationflags=NO_WINDOW, check=True)
        # An MP4 as well: forums and Reddit prefer video to a heavy GIF.
        subprocess.run([str(tools.ffmpeg), "-v", "error", "-y", "-framerate", str(FPS),
                        "-i", pattern, "-vf", f"{scale},format=yuv420p",
                        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
                        "-movflags", "+faststart", str(mp4)],
                       creationflags=NO_WINDOW, check=True)

        for f in self.frames_dir.glob("*.png"):
            f.unlink()
        self.frames_dir.rmdir()
        print(f"   {gif.name}  {gif.stat().st_size // 1024} KB")
        print(f"   {mp4.name}  {mp4.stat().st_size // 1024} KB")


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: make_demo_gif.py <folder of clips> [output dir]")
    source = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("docs")
    if not source.exists():
        raise SystemExit(f"no such folder: {source}")

    rec = Recorder(source, out)
    QTimer.singleShot(600, rec.start)
    QTimer.singleShot(240000, rec.app.quit)
    return rec.app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
