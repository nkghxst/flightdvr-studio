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

"""The goggles' star button: its sidecar, the clip list, and the filmstrip.

HDZero goggles write "<clip>.star.txt" beside a recording, one "MM:SS star"
line per press. SAMPLE is a real one, byte for byte, from a Box Pro card.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import QApplication

from flightdvr import media, scan
from flightdvr.browser_panel import review_state_text, review_state_tooltip
from flightdvr.media import ClipInfo, read_stars, star_sidecar
from flightdvr.session import KEEP, UNREVIEWED

SAMPLE = b"0:33 star\n1:45 star\n"


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def recording(folder: Path, name="20260705-115834.ts", stars=SAMPLE) -> Path:
    clip = folder / name
    clip.write_bytes(b"x" * 4096)
    if stars is not None:
        star_sidecar(clip).write_bytes(stars)
    return clip


# -- the sidecar ---------------------------------------------------------------

def test_the_sidecar_is_named_after_the_whole_file_name(tmp_path):
    assert (star_sidecar(tmp_path / "20260705-115834.ts").name
            == "20260705-115834.ts.star.txt")


def test_a_real_sidecar_reads_as_seconds(tmp_path):
    assert read_stars(recording(tmp_path)) == [33.0, 105.0]


def test_no_sidecar_is_no_stars(tmp_path):
    assert read_stars(recording(tmp_path, stars=None)) == []


def test_stars_are_sorted_once_each_whatever_the_file_looks_like(tmp_path):
    clip = recording(tmp_path, stars=(
        b"\xef\xbb\xbf2:05 star\r\n"      # a BOM and Windows line endings
        b"0:07 star\r\n"
        b"\r\n"
        b"not a time\r\n"
        b"2:05 star\r\n"                  # pressed twice in one second
        b"75:30 star\r\n"))               # minutes are not capped at 59
    assert read_stars(clip) == [7.0, 125.0, 4530.0]


def test_probe_carries_the_stars_through_the_deeper_retry(tmp_path, monkeypatch):
    """The first pass fails on this stream and the retry's ClipInfo is the one
    returned, so the stars have to be on that one too."""
    clip = recording(tmp_path)
    passes = []

    def fake_probe(tools, path, info, extra, **_kwargs):
        passes.append(extra)
        if extra:
            info.width, info.height, info.duration = 1280, 720, 120.0
            info.video_codec = "h264"
            info.video_duration = 120.0
        else:
            info.error = "could not read the stream"

    monkeypatch.setattr(media, "_probe_once", fake_probe)
    found = media.probe(None, clip)
    assert len(passes) == 2, "the fixture must take the retry path"
    assert not found.error
    assert found.stars == [33.0, 105.0]


# -- copying off the card ------------------------------------------------------

def test_ingest_brings_the_stars_along_under_the_new_name(tmp_path):
    card = tmp_path / "card"
    card.mkdir()
    source = recording(card)
    library = tmp_path / "library"

    written, problems = scan.copy_clips(
        [source], library, by_date=True, date_prefix=True,
        flight_date=datetime(2026, 7, 5).date())

    assert not problems
    assert written[0].name == "2026-07-05_20260705-115834.ts"
    assert star_sidecar(written[0]).read_bytes() == SAMPLE
    assert read_stars(written[0]) == [33.0, 105.0]


def test_ingest_fills_in_stars_for_a_clip_already_in_the_library(tmp_path):
    """A clip copied by an older version arrived without its sidecar."""
    card = tmp_path / "card"
    card.mkdir()
    source = recording(card)
    library = tmp_path / "library"
    library.mkdir()
    (library / source.name).write_bytes(source.read_bytes())

    written, problems = scan.copy_clips([source], library, by_date=False,
                                        date_prefix=False)
    assert not problems
    assert read_stars(written[0]) == [33.0, 105.0]


def test_ingest_without_stars_writes_no_sidecar(tmp_path):
    card = tmp_path / "card"
    card.mkdir()
    source = recording(card, stars=None)
    written, problems = scan.copy_clips([source], tmp_path / "library",
                                        by_date=False, date_prefix=False)
    assert written and not problems
    assert not star_sidecar(written[0]).exists()


# -- the clip list -------------------------------------------------------------

def test_the_state_column_counts_stars_on_the_letter_line():
    assert review_state_text(KEEP, 0, None, star_count=2) == "K  ★ 2"
    assert (review_state_text(UNREVIEWED, 1, 3, star_count=1)
            == "U  ★ 1\n1 range\n3 flights")
    # Unstarred clips read exactly as they always did.
    assert review_state_text(KEEP, 1, 1) == "K\n1 range\n1 flight"
    assert "2 starred moments" in review_state_tooltip(KEEP, 0,
                                                      star_count=2)
    assert "starred" not in review_state_tooltip(KEEP, 0)


def test_a_starred_clip_shows_its_star_in_the_list(app, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    (tmp_path / "home").mkdir()
    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow

    window = MainWindow(find_tools())
    try:
        for name, stars in (("hdz_001.ts", [33.0, 105.0]), ("hdz_002.ts", [])):
            window._add_clip(window._scan_generation, ClipInfo(
                path=tmp_path / name, size=4096, modified=datetime.now(),
                duration=120.0, stars=stars))
        states = {window.table.item(row, 0).text():
                  window.table.item(row, 5).text()
                  for row in range(window.table.rowCount())}
        assert states == {"hdz_001.ts": "U  ★ 2", "hdz_002.ts": "U"}
    finally:
        window.close()


# -- the filmstrip -------------------------------------------------------------

def starred_bar(stars=(30.0, 150.0)):
    from flightdvr.trim import TrimBar

    bar = TrimBar()
    bar.resize(400, 60)
    bar.set_clip(200.0, 0.0, 200.0)
    bar.set_stars(stars)
    return bar


def press(bar, x):
    bar.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(x, 30),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))


def test_the_filmstrip_paints_a_mark_at_each_star(app):
    from flightdvr.trim import STAR_FILL

    def goldish(color: QColor) -> bool:
        return (color.red() > 150 and color.green() > 100
                and color.blue() < 90)

    marked = starred_bar().grab().toImage()
    plain = starred_bar(stars=()).grab().toImage()
    assert goldish(STAR_FILL), "the oracle must recognise the mark's colour"
    for seconds in (30.0, 150.0):
        x = starred_bar()._x_for(seconds)
        assert goldish(marked.pixelColor(x, 40)), seconds
        assert not goldish(plain.pixelColor(x, 40)), seconds
    # Nothing between the stars.
    assert not goldish(marked.pixelColor(starred_bar()._x_for(90.0), 40))


def test_a_star_past_the_end_is_not_drawn_or_clickable(app):
    bar = starred_bar(stars=(250.0,))
    assert bar._star_near(bar.width() - 1) is None


def test_clicking_near_a_star_lands_on_it_exactly(app):
    bar = starred_bar()
    moved = []
    bar.playhead_moved.connect(moved.append)
    press(bar, bar._x_for(30.0) + 4)
    assert moved == [30.0]
    assert bar.playhead == 30.0


def test_clicking_away_from_a_star_is_an_ordinary_seek(app):
    bar = starred_bar()
    moved = []
    bar.playhead_moved.connect(moved.append)
    x = bar._x_for(90.0)
    press(bar, x)
    assert moved == [bar._time_for(x)]


def test_the_trim_handles_still_win_over_a_star_beside_them(app):
    bar = starred_bar(stars=(1.0,))
    press(bar, bar._x_for(0.0))
    assert bar._dragging == "in"


def test_hovering_a_star_says_when_it_was_pressed(app):
    bar = starred_bar()
    bar.mouseMoveEvent(QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(bar._x_for(150.0), 30),
        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier))
    assert bar.toolTip() == "Starred in the goggles at 2:30"
    bar.mouseMoveEvent(QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(bar._x_for(90.0), 30),
        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier))
    assert bar.toolTip() == ""
