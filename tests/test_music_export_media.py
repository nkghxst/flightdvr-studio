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

"""One generated-media music export through the actual Classic UI.

The music wiring tests deliberately stop at the queued ``Job``. That leaves a
plausible failure untested: the panel can carry a Replace choice into the
queue while the finished file is silent, has the wrong tone, or has the wrong
extent. This case uses the real file chooser route, the real asynchronous
music probe, the real queue button and the real export worker.

The expected signal is generated independently as a PCM WAV. The assertion
decodes the finished file with ffmpeg and measures its samples itself; it does
not call the production audio validator. Its small local oracle is also shown
to reject both silence and a wrong-frequency fixture, so a missing or wrong
music stream cannot make the test pass merely by being present.

This is intentionally one case: one generated video, one known track, one
trimmed Master/Replace job. Hardware probing, scan workers, thumbnails,
preview decoding, settings persistence and physical playback are outside the
floor and are isolated below.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
import wave
from array import array
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QThread, Signal, Qt


pytestmark = pytest.mark.integration

OUTPUT_RATE = 48_000
SOURCE_SECONDS = 2.4
MUSIC_SECONDS = 3.0
RANGE_IN = 0.4
RANGE_OUT = 1.8
EXPECTED_SECONDS = RANGE_OUT - RANGE_IN
EXPECTED_SAMPLES = round(EXPECTED_SECONDS * OUTPUT_RATE)
TONE_HZ = 997
WRONG_TONE_HZ = 440
TONE_AMPLITUDE = 16_000
SAMPLE_TOLERANCE = 2_048  # two AAC frames, including codec priming/padding
SPECTRAL_WINDOW = 8_192
MINIMUM_TONE_MAGNITUDE = 0.02
TONE_TO_WRONG_RATIO = 3.0


class _NoHardwareProbe(QObject):
    """Keep hardware encoder discovery outside this media acceptance case."""

    result = Signal(object)

    def __init__(self, tools, parent=None):
        super().__init__(parent)

    def start(self, *_args) -> None:
        pass

    def isRunning(self) -> bool:  # noqa: N802 (Qt naming)
        return False

    def stop(self) -> None:
        pass

    def wait(self, *_args) -> bool:
        return True


class _NoScan(QObject):
    """The clip is injected after it has been probed by this test."""

    counted = Signal(int, int)
    found = Signal(int, object)
    done = Signal(int, int)

    def __init__(self, tools, folder, recursive, generation, parent=None):
        super().__init__(parent)

    def start(self) -> None:
        pass

    def isRunning(self) -> bool:  # noqa: N802 (Qt naming)
        return False

    def stop(self) -> None:
        pass

    def wait(self, *_args) -> bool:
        return True


class _NoFilmstrip(QThread):
    """Prevent a preview decode from competing with the export worker."""

    ready = Signal(object)
    activity_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, *args, **kwargs):
        parent = args[3] if len(args) > 3 else kwargs.get("parent")
        super().__init__(parent)

    def start(self, *_args) -> None:
        # The real loader's finished signal releases the loader. Preserve that
        # lifecycle even though this case does not inspect a filmstrip.
        self.finished.emit()

    def isRunning(self) -> bool:  # noqa: N802 (Qt naming)
        return False

    def stop(self) -> None:
        pass

    def wait(self, *_args) -> bool:
        return True


@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture
def ui_window(tools, tmp_path, qt_app, monkeypatch):
    """A real MainWindow with only unrelated background work replaced."""
    import flightdvr.ui as ui

    monkeypatch.setattr(ui, "HardwareProbe", _NoHardwareProbe)
    monkeypatch.setattr(ui, "ScanWorker", _NoScan)
    monkeypatch.setattr(ui, "FilmstripLoader", _NoFilmstrip)
    monkeypatch.setattr(
        "flightdvr.updates.should_check", lambda *args, **kwargs: False)

    from flightdvr.ui import MainWindow

    made = MainWindow(tools)
    # Do not let the unrelated idle sweep start after _scan_done below.
    monkeypatch.setattr(made.thumbs, "request", lambda *_args: None)
    monkeypatch.setattr(made.thumbs, "resume", lambda: None)
    monkeypatch.setattr(made, "_start_flight_analysis", lambda: None)
    monkeypatch.setattr(made.player, "load", lambda *_args, **_kwargs: None)
    made._ready = True

    card = tmp_path / "card"
    card.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    made.source_combo.insertItem(0, str(card), str(card))
    made.source_combo.setCurrentIndex(0)
    made.export_panel.out_edit.addItem(str(output))
    made.export_panel.out_edit.setCurrentText(str(output))
    made.export_panel.subfolder_check.setChecked(False)
    made.export_panel.date_check.setChecked(False)
    made.export_panel.master_speed.setCurrentText("ultrafast")
    assert made.export_panel.master_speed.currentText() == "ultrafast"

    try:
        yield made, card, output
    finally:
        made.close()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            qt_app.processEvents()
            running = [thread for thread in made.findChildren(QThread)
                       if thread.isRunning()]
            if not running:
                break
            time.sleep(0.01)
        left = [thread for thread in made.findChildren(QThread)
                if thread.isRunning()]
        assert not left, [type(thread).__name__ for thread in left]


def _run(command: list[str], *, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, capture_output=True, text=text)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _make_source(tools, path: Path):
    """Create and independently probe one short video-only card clip."""
    _run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-y", "-f", "lavfi", "-i",
        f"testsrc2=size=320x180:rate=30:duration={SOURCE_SECONDS:g}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an", "-f", "mpegts", str(path),
    ])
    assert path.exists() and path.stat().st_size > 0

    from flightdvr.media import probe

    clip = probe(tools, path)
    assert not clip.error, clip.error
    assert (clip.width, clip.height) == (320, 180)
    assert clip.fps == pytest.approx(30.0, abs=0.01)
    assert clip.duration == pytest.approx(SOURCE_SECONDS, abs=0.05)
    assert not clip.has_audio, "the generated source must be video-only"
    return clip


def _make_tone(path: Path, *, frequency: int = TONE_HZ) -> None:
    samples = array("h")
    count = round(MUSIC_SECONDS * OUTPUT_RATE)
    for offset in range(count):
        samples.append(round(
            TONE_AMPLITUDE * math.sin(
                2 * math.pi * frequency * offset / OUTPUT_RATE)))

    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(OUTPUT_RATE)
        output.writeframes(samples.tobytes())

    with wave.open(str(path), "rb") as check:
        assert (check.getnchannels(), check.getsampwidth(),
                check.getframerate(), check.getnframes()) == (
                    1, 2, OUTPUT_RATE, count)


def _wait_for(app, condition, description: str, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {description}")


def _stream_facts(tools, path: Path) -> list[dict]:
    result = _run([
        str(tools.ffprobe), "-v", "error", "-show_entries",
        "stream=codec_type,codec_name,sample_rate,channels,duration",
        "-of", "json", str(path),
    ])
    return json.loads(result.stdout).get("streams", [])


def _decode_mono(tools, path: Path) -> array:
    result = _run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar",
        str(OUTPUT_RATE), "-f", "f32le", "-",
    ], text=False)
    assert len(result.stdout) % 4 == 0
    decoded = array("f")
    decoded.frombytes(result.stdout)
    return decoded


def _tone_magnitude(samples: array, frequency: int) -> float:
    """Measure one frequency away from codec edges and transitions."""
    if len(samples) < SPECTRAL_WINDOW:
        return 0.0
    start = max(0, len(samples) // 2 - SPECTRAL_WINDOW // 2)
    window = samples[start:start + SPECTRAL_WINDOW]
    mean = sum(window) / len(window)
    cosine = sine = 0.0
    for index, value in enumerate(window):
        angle = 2 * math.pi * frequency * index / OUTPUT_RATE
        centered = value - mean
        cosine += centered * math.cos(angle)
        sine += centered * math.sin(angle)
    return 2 * math.hypot(cosine, sine) / len(window)


def _matches_expected_tone(samples: array) -> tuple[bool, dict[str, float]]:
    metrics = {
        "decoded_samples": float(len(samples)),
        "duration_seconds": len(samples) / OUTPUT_RATE,
        "expected_magnitude": _tone_magnitude(samples, TONE_HZ),
        "wrong_magnitude": _tone_magnitude(samples, WRONG_TONE_HZ),
    }
    okay = (
        abs(len(samples) - EXPECTED_SAMPLES) <= SAMPLE_TOLERANCE
        and metrics["expected_magnitude"] >= MINIMUM_TONE_MAGNITUDE
        and metrics["expected_magnitude"] >= (
            TONE_TO_WRONG_RATIO * metrics["wrong_magnitude"])
    )
    return okay, metrics


def _tone_fixture(frequency: int) -> array:
    """A deliberately wrong/silent candidate for the oracle self-check."""
    return array("f", (
        TONE_AMPLITUDE / 32_768 * math.sin(
            2 * math.pi * frequency * offset / OUTPUT_RATE)
        for offset in range(EXPECTED_SAMPLES)
    ))


def test_ui_replace_music_survives_real_export_and_decodes_as_the_known_tone(
        tools, ui_window, qt_app, tmp_path, monkeypatch):
    """A queued Replace choice must become the requested samples on disk.

    The test is intentionally named after the silent/wrong-output failure it
    catches. It exercises one actual UI path and measures the file that the
    worker publishes, not the status returned by the production validator.
    """
    window, card, output = ui_window
    source = card / "generated_acceptance.ts"
    music = tmp_path / "known_997hz.wav"
    clip = _make_source(tools, source)
    _make_tone(music)

    # Let the real browser path own this already-probed generated clip. The
    # scan/thread/thumbnail work is deliberately isolated by the fixture, but
    # _add_clip and _scan_done are the same UI delivery boundary as a card scan.
    window._add_clip(window._scan_generation, clip)
    window._scan_done(window._scan_generation, 1)
    window.table.setCurrentCell(0, 0)
    window._load_selected_clip()

    # Use the actual range controls so the queue gets one explicit Master
    # range, rather than bypassing the trim UI by assigning ClipInfo fields.
    window.trim_bar.set_playhead(RANGE_IN)
    window._set_in()
    window.trim_bar.set_playhead(RANGE_OUT)
    window._set_out()
    assert clip.trim_in == pytest.approx(RANGE_IN, abs=0.01)
    assert clip.trim_out == pytest.approx(RANGE_OUT, abs=0.01)
    assert len(clip.real_selects) == 1
    window._sync_music_panel()

    # The file dialog is the only interaction replaced: the click, probe,
    # panel update and generation/target handling remain production code.
    monkeypatch.setattr(
        "flightdvr.ui.QFileDialog.getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(music), "")),
    )
    window.music_band.setChecked(True)
    qt_app.processEvents()
    window.preview_view.track_button.click()
    _wait_for(
        qt_app,
        lambda: window.music_panel.capture().asset is not None,
        "the real music probe",
    )

    choice = window.music_panel.capture()
    assert choice.mode.value == "replace"
    assert choice.track == music.resolve()
    assert choice.asset is not None
    assert choice.asset.sample_rate == OUTPUT_RATE
    assert choice.asset.channels == 1
    assert choice.asset.decoded_samples == round(MUSIC_SECONDS * OUTPUT_RATE)

    from flightdvr.audio_plan import AudioMode
    from flightdvr.jobs import JobStatus
    from PySide6.QtWidgets import QPushButton

    assert window.music_panel.mode_combo.currentData() is AudioMode.REPLACE
    window.music_panel.mode_combo.setCurrentIndex(
        window.music_panel.mode_combo.findData(AudioMode.REPLACE))
    qt_app.processEvents()
    window.table.item(0, 0).setCheckState(Qt.CheckState.Checked)

    add_buttons = [button for button in
                   window.export_panel.findChildren(QPushButton)
                   if button.text() == "Add to queue"]
    assert len(add_buttons) == 1
    add_buttons[0].click()
    qt_app.processEvents()

    assert len(window.jobs) == 1
    job = window.jobs[0]
    assert job.preset_key == "master"
    assert job.status is JobStatus.PENDING
    assert job.clips[0].trimmed_duration == pytest.approx(EXPECTED_SECONDS,
                                                           abs=0.02)
    assert job.audio.mode is AudioMode.REPLACE
    assert job.audio.asset is not None
    assert job.audio.asset.track == music.resolve()
    assert window.queue_panel.table.rowCount() == 1
    assert window.queue_panel.table.item(0, 0).text() == job.out_path.name

    queue_snapshot = {
        "preset": job.preset_key,
        "mode": job.audio.mode.value,
        "clip": source.name,
        "music": music.name,
        "trim_in": clip.trim_in,
        "trim_out": clip.trim_out,
        "output": job.out_path.name,
    }
    print(
        "MUSIC_EXPORT_QUEUE_SNAPSHOT "
        + json.dumps(queue_snapshot, sort_keys=True)
    )

    # This is the actual UI queue action, not a direct ExportWorker call.
    window.queue_panel.start_button.click()
    _wait_for(qt_app, lambda: job.status is JobStatus.DONE,
              "the real Master export", timeout=180.0)
    assert not window.worker.isRunning()
    assert job.out_path.exists() and job.out_path.stat().st_size > 0

    streams = _stream_facts(tools, job.out_path)
    videos = [stream for stream in streams
              if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams
              if stream.get("codec_type") == "audio"]
    assert len(videos) == 1
    assert len(audios) == 1, streams
    assert int(audios[0]["sample_rate"]) == OUTPUT_RATE
    assert int(audios[0]["channels"]) == 2

    decoded = _decode_mono(tools, job.out_path)
    matches, metrics = _matches_expected_tone(decoded)
    assert matches, metrics
    print(
        "MUSIC_EXPORT_MEDIA_MEASUREMENT "
        + json.dumps({
            **metrics,
            "source_sha256_16": _file_sha256(source)[:16],
            "music_sha256_16": _file_sha256(music)[:16],
            "output_sha256_16": _file_sha256(job.out_path)[:16],
            "ffmpeg": str(tools.ffmpeg),
            "ffprobe": str(tools.ffprobe),
        }, sort_keys=True)
    )

    # Explicitly demonstrate the independent oracle's negative net: a dropped
    # stream (silence) and a plausible but wrong track do not satisfy it.
    silent_ok, _ = _matches_expected_tone(
        array("f", [0.0]) * EXPECTED_SAMPLES)
    wrong_ok, _ = _matches_expected_tone(_tone_fixture(WRONG_TONE_HZ))
    assert not silent_ok
    assert not wrong_ok
