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

"""Tests for the source-space model behind the Vertical preset."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from flightdvr.media import ClipInfo, Tools, frame_rate_mode
from flightdvr.presets import (
    ExportSettings, build_commands, estimate_output_size, vertical_crop,
    vertical_output_size, vertical_problems,
)


TOOLS = Tools(Path("ffmpeg"), Path("ffprobe"))


@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def clip(width: int = 1280, height: int = 720) -> ClipInfo:
    return ClipInfo(
        path=Path("hdz_022.ts"),
        size=599_189_652,
        modified=datetime(2025, 10, 8, 18, 53),
        duration=10.0,
        width=width,
        height=height,
        fps=60.0,
        video_codec="hevc",
        audio_codec="aac",
        pix_fmt="yuvj420p",
        color_range="pc",
        color_space="bt470bg",
        color_primaries="bt470bg",
        color_transfer="smpte170m",
    )


def test_vertical_crop_uses_an_even_square_sar_source_rect():
    """The preview and ffmpeg must share a true 9:16 source-space crop.

    A full-height 1280x720 crop would be 405 pixels wide, which is odd and
    produces a non-square sample aspect ratio after scaling. The largest exact
    even crop is 396x704, centred eight pixels from the top and bottom.
    """
    model = vertical_crop(clip(), position=50)

    assert (model.x, model.y, model.width, model.height) == (442, 8, 396, 704)
    assert (model.output_width, model.output_height) == (720, 1280)
    assert model.width * 16 == model.height * 9
    assert all(value % 2 == 0 for value in (
        model.x, model.y, model.width, model.height,
        model.output_width, model.output_height,
    ))


def test_vertical_position_uses_one_deterministic_even_source_coordinate_model():
    """Left, centre and right must agree between preview and the command."""
    source = clip(width=1920, height=1080)

    left = vertical_crop(source, position=0)
    centre = vertical_crop(source, position=50)
    right = vertical_crop(source, position=100)

    assert (left.x, centre.x, right.x) == (0, 662, 1326)
    assert (left.y, centre.y, right.y) == (12, 12, 12)
    assert (left.width, left.height) == (594, 1056)
    assert (left.output_width, left.output_height) == (1080, 1920)


def test_vertical_output_size_keeps_a_portrait_source_at_its_native_delivery_size():
    assert vertical_output_size(clip(width=720, height=1280)) == (720, 1280)


def test_a_source_too_narrow_for_the_even_exact_crop_names_both_dimensions():
    problems = vertical_problems([clip(width=200, height=1000)])

    assert len(problems) == 1
    assert "200×1000" in problems[0]
    assert "558×992" in problems[0]


def test_a_source_without_dimensions_is_refused_before_crop_geometry():
    problems = vertical_problems([clip(width=0, height=0)])

    assert len(problems) == 1
    assert "dimensions" in problems[0]
    assert "hdz_022.ts" in problems[0]


def test_vertical_command_crops_then_scales_with_square_sar_and_keeps_audio():
    command = build_commands(
        TOOLS, clip(), "vertical", ExportSettings(),
        Path("out.mp4"), Path("work"),
    )[0]
    flat = " ".join(command)

    assert "crop=396:704:442:8" in flat
    assert "scale=720:1280:flags=lanczos" in flat
    assert "setsar=1" in flat
    assert "-an" not in command
    cfr = frame_rate_mode(TOOLS, "cfr")
    assert cfr[0] in command
    assert "cfr" in command


def test_vertical_quality_is_independent_from_master_quality_and_speed():
    settings = ExportSettings(
        master_crf=14,
        master_speed="slower",
        vertical_crf=26,
        vertical_speed="fast",
    )
    command = build_commands(
        TOOLS, clip(), "vertical", settings,
        Path("out.mp4"), Path("work"),
    )[0]

    assert command[command.index("-crf") + 1] == "26"
    assert command[command.index("-preset") + 1] == "fast"
    assert estimate_output_size(clip(), "vertical", settings) < estimate_output_size(
        clip(), "vertical", ExportSettings(vertical_crf=14)
    )


def test_a_joined_vertical_command_crops_each_source_before_shared_delivery_scale():
    first = clip(width=1280, height=720)
    second = clip(width=1920, height=1080)
    command = build_commands(
        TOOLS, first, "vertical", ExportSettings(),
        Path("out.mp4"), Path("work"), clips=[first, second],
        total_duration=20.0,
    )[0]
    flat = " ".join(command)

    assert "crop=396:704:442:8" in flat
    assert "crop=594:1056:662:12" in flat
    assert flat.count("scale=1080:1920:flags=lanczos") == 2
    assert "setsar=1" in flat
    assert "-filter_complex" in command


def test_vertical_estimate_uses_the_delivery_pixel_rate_not_the_landscape_source():
    source = clip(width=1280, height=960)
    vertical = estimate_output_size(source, "vertical", ExportSettings())
    master = estimate_output_size(source, "master", ExportSettings())

    assert vertical < master


def test_the_overlay_maps_the_same_source_crop_after_a_resize(qt_app):
    """Preview geometry may change; the source-space crop must not."""
    from PySide6.QtCore import QRect
    from flightdvr.player import FrameView

    model = vertical_crop(clip(), position=25)
    view = FrameView()
    view.set_vertical_crop(model)

    small = view._crop_display_rect(QRect(0, 0, 640, 360), model)
    large = view._crop_display_rect(QRect(10, 20, 1000, 562), model)

    assert view.vertical_crop == model
    assert small.left() / 640 == pytest.approx((large.left() - 10) / 1000)
    assert small.top() / 360 == pytest.approx((large.top() - 20) / 562)
    assert small.width() / 640 == pytest.approx(large.width() / 1000)
    assert small.height() / 360 == pytest.approx(large.height() / 562)


def test_dragging_the_overlay_emits_the_same_position_the_slider_uses(qt_app):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QImage, QMouseEvent
    from flightdvr.player import FrameView

    model = vertical_crop(clip(), position=50)
    view = FrameView()
    view.resize(640, 360)
    view.set_image(QImage(1280, 720, QImage.Format.Format_RGB32))
    view.set_vertical_crop(model)
    positions = []
    clicks = []
    view.vertical_position_changed.connect(positions.append)
    view.vertical_position_changed.connect(
        lambda value: view.set_vertical_crop(vertical_crop(clip(), value))
    )
    view.clicked.connect(lambda: clicks.append(True))

    press = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(320, 180),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    move = QMouseEvent(
        QEvent.Type.MouseMove, QPointF(400, 180),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    move_again = QMouseEvent(
        QEvent.Type.MouseMove, QPointF(500, 180),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(500, 180),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.mousePressEvent(press)
    view.mouseMoveEvent(move)
    view.mouseMoveEvent(move_again)
    view.mouseReleaseEvent(release)

    assert positions and positions[-1] > positions[0] > 50
    assert clicks == []
    assert view._vertical_drag_offset is None


def test_vertical_position_is_a_persisted_panel_choice_and_keeps_audio(qt_app):
    from flightdvr.export_panel import ExportPanel
    from flightdvr.presets import PRESET_ORDER

    panel = ExportPanel()
    panel.preset_buttons["vertical"].setChecked(True)
    panel.vertical_position.setValue(75)
    panel.vertical_quality.setCurrentIndex(3)
    panel.vertical_speed.setCurrentText("fast")

    assert panel.preset_key() == "vertical"
    assert panel.options_stack.currentIndex() == PRESET_ORDER.index("vertical")
    assert panel.audio_check.isEnabled()
    saved = panel.capture()
    assert saved["vertical_position"] == 75
    assert saved["vertical_quality"] == 24
    assert saved["vertical_speed"] == "fast"

    panel.vertical_position.setValue(0)
    panel.apply(saved)
    assert panel.vertical_position.value() == 75
    assert panel.settings("").vertical_position == 75
    assert panel.settings("").vertical_crf == 24
    assert panel.settings("").vertical_speed == "fast"


def test_the_worker_refuses_a_narrow_source_before_creating_any_file(tmp_path):
    from flightdvr.jobs import ExportWorker, Job

    job = Job(
        clips=[clip(width=200, height=1000)],
        preset_key="vertical",
        settings=ExportSettings(),
        out_path=tmp_path / "vertical.mp4",
    )
    ok, message = ExportWorker(TOOLS, [job], tmp_path)._run_job(0, job)

    assert not ok
    assert "200×1000" in message and "558×992" in message
    assert not (tmp_path / "vertical.mp4").exists()
    assert not list(tmp_path.glob("*.flightdvr-part"))


def test_the_window_refuses_a_narrow_source_before_rendering_a_target(qt_app, monkeypatch):
    """The UI boundary must reject the named case before naming or queueing."""
    from types import SimpleNamespace
    from flightdvr.ui import MainWindow

    warnings = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda *args: warnings.append(args[1:]),
    )
    panel = SimpleNamespace(
        output_text=lambda: "/out",
        subfolders_enabled=lambda: True,
        flight_date=lambda: None,
        join_enabled=lambda: False,
    )
    fake = SimpleNamespace(
        selected_clips=lambda: [clip(width=200, height=1000)],
        export_panel=panel,
        _preset_key=lambda: "vertical",
        current_settings=lambda: ExportSettings(),
        flight_date=lambda: None,
        jobs=[],
    )
    MainWindow._add_to_queue(fake)

    assert len(warnings) == 1
    assert "200×1000" in warnings[0][1]
    assert fake.jobs == []
