"""Native layout matrix for the compact-layout work (isolated, read-only media).

    python layout_matrix.py OUT_ROOT LABEL WIDTHxHEIGHT [WIDTHxHEIGHT ...]

Runs the current checkout natively (Windows platform plugin) with the same
storage isolation as tests/conftest.py, scans a generated 14-clip card, and for
each size x list mode x Music on/off records the window, each outer-layout
item's height and minimum, the fully visible list rows, and the Music band, and
saves a window grab. Nothing is played; no real settings or media are touched.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1])
LABEL = sys.argv[2]
SIZES = [tuple(int(v) for v in s.split("x")) for s in sys.argv[3:]]
HOME = ROOT / f"home-{LABEL}"
SHOTS = ROOT / "matrix" / LABEL
CARD = ROOT / "fixtures" / "card14"
TRACK = ROOT / "fixtures" / "music" / "fixture-track.wav"
FFMPEG_BIN = r"D:\Dev\tools\ffmpeg\ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-7.1\bin"

for name in ("HOME", "USERPROFILE"):
    os.environ[name] = str(HOME)
for name in ("APPDATA", "LOCALAPPDATA"):
    os.environ[name] = str(HOME / name)
    (HOME / name).mkdir(parents=True, exist_ok=True)
os.environ.pop("QT_QPA_PLATFORM", None)
os.environ["PATH"] = FFMPEG_BIN + os.pathsep + os.environ["PATH"]
SHOTS.mkdir(parents=True, exist_ok=True)
assert Path.home() == HOME

sys.path.insert(0, os.getcwd())
from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

import flightdvr.ui as ui  # noqa: E402
import flightdvr.updates as updates  # noqa: E402

settings_file = HOME / "flightdvr-settings.ini"
ui.QSettings = lambda *_a, **_k: QSettings(str(settings_file), QSettings.Format.IniFormat)
updates.should_check = lambda *a, **k: False
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(TRACK), ""))

from flightdvr.classic_layout import BrowserMode  # noqa: E402
from flightdvr.flow_layout import Mode  # noqa: E402
from flightdvr.media import find_tools  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])


def pump(seconds: float, until=None) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until else True


window = ui.MainWindow(find_tools())
window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
screen = app.primaryScreen().availableGeometry()
window.move(screen.x(), screen.y())
window.show()
pump(2)
window.source_combo.insertItem(0, str(CARD), str(CARD))
window.source_combo.setCurrentIndex(0)
window._scan()
pump(90, lambda: len(window.clips) >= 14 and not (
    window.scan_worker and window.scan_worker.isRunning()))
window.set_view_mode(Mode.CLASSIC)
window.table.setCurrentCell(0, 0)
window._load_selected_clip()
pump(4)
view = window.preview_view
track_chosen = False


def rows_visible() -> int:
    if hasattr(window, "_fully_visible_rows"):
        try:
            return window._fully_visible_rows()
        except Exception:
            pass
    return -1


def record(tag: str) -> dict:
    layout = window._outer_layout
    items = []
    for i in range(layout.count()):
        w = layout.itemAt(i).widget()
        if w is None or not w.isVisible():
            continue
        name = w.objectName() or type(w).__name__
        items.append(dict(item=name, top=w.geometry().top(), h=w.height(),
                          min=w.minimumSizeHint().height()))
    central = window.centralWidget()
    band = view.music_band
    band_bottom = band.mapTo(window, band.rect().bottomLeft()).y() if band.isVisible() else None
    return dict(tag=tag, window=[window.width(), window.height()],
                window_min=window.minimumSizeHint().height(),
                screen_avail=[screen.width(), screen.height()],
                central_h=central.height(), list_rows=rows_visible(),
                browser_h=window.browser_panel.height(),
                preview_h=view.preview_box.height() if hasattr(view, "preview_box") else None,
                band_h=band.height() if band.isVisible() else 0,
                band_body_h=view.music_body.height() if view.music_body.isVisible() else 0,
                band_bottom=band_bottom, items=items,
                preview_floor=view.preview_box.content_floor(),
                preview_useful=view.preview_box.useful_height(view.preview_box.width()),
                sidebar_hint=view.sidebar.sizeHint().height(),
                list_viewport=window.browser_panel.table.viewport().height(),
                list_folded=window.browser_panel.folded)


results = []
for width, height in SIZES:
    for mode_name in ("collapsed", "normal", "expanded"):
        for music in (False, True):
            window.set_browser_mode(getattr(BrowserMode, mode_name.upper()))
            if view.music_band.isChecked() != music:
                view.music_band.setChecked(music)
            if music and not track_chosen:
                pump(1)
                window._choose_music_track()
                pump(12)
                track_chosen = True
            window.resize(width, height)
            pump(2.5)
            tag = f"{width}x{height}-{mode_name}-music{'on' if music else 'off'}"
            data = record(tag)
            results.append(data)
            window.grab().save(str(SHOTS / f"{tag}.png"))
            print(f"{tag}: win={data['window']} min={data['window_min']} rows={data['list_rows']} "
                  f"browser={data['browser_h']} band={data['band_h']}/{data['band_body_h']} "
                  f"band_bottom={data['band_bottom']} preview={data['preview_h']} "
                  f"floor={data['preview_floor']} useful={data['preview_useful']} "
                  f"viewport={data['list_viewport']} folded={data['list_folded']}")

view.music_band.setChecked(False)
window.close()
pump(2)
(SHOTS / "matrix.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
