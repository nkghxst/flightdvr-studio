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

    # MainWindow's ordinary session/recent paths are real application state.
    # Keep this acceptance case from writing sessions/recent.json in the
    # developer's home while retaining the production session behavior.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

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
    made.export_panel.master_speed.setCurrentText("veryfast")
    assert made.export_panel.master_speed.currentText() == "veryfast"

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

    assert window._music_target is not None
    assert window._music_probe is not None, {
        "track_status": window.preview_view.track_status.text(),
        "music_target": window._music_target,
    }

    def music_ready() -> bool:
        if window._music_trouble:
            raise AssertionError(window._music_trouble)
        return window.music_panel.capture().asset is not None

    _wait_for(
        qt_app,
        music_ready,
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

    assert AudioMode(window.music_panel.mode_combo.currentData()) is AudioMode.REPLACE
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

    def export_finished() -> bool:
        # The completion signal settles Job.status before the worker thread's
        # final event-loop turn returns. Accept DONE first; the separate wait
        # below observes the worker's settled state without racing that signal.
        if job.status is JobStatus.DONE:
            return True
        if job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
            raise AssertionError(
                f"the real Master export ended as {job.status}: {job.message}")
        if window.worker is not None and not window.worker.isRunning():
            raise AssertionError(
                f"the export worker stopped before completion: {job.status}, "
                f"{job.message}")
        return False

    _wait_for(qt_app, export_finished,
              "the real Master export", timeout=180.0)

    def worker_settled() -> bool:
        worker = window.worker
        return worker is not None and not worker.isRunning()

    _wait_for(qt_app, worker_settled,
              "the export worker to settle", timeout=10.0)
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


# -- stage A: ordinary configured sound on every preset that carries it -------
#
# Worker-published files, measured here. The expectations are fixture
# declarations; nothing below asks the resolver or the command builder.
#
# The recording is 20 s of 1280x720 at 30 fps. Its sound is 300 Hz with 600 Hz
# only in [13, 14); its picture is red either side of a green centre band
# (x 442..838, the default Vertical crop), turning yellow in that same second
# while a blue box shows inside the band.
# The song is 1000 Hz with 1500 Hz only in [5, 6). The range is [12, 18) and
# the passage [4, 10), so both events land at output [1, 2): source 13 s and
# song 5 s, never source 1 s or song 13 s.

STAGE_A_RANGE = (12.0, 18.0)
STAGE_A_SAMPLES = 288_000                 # 6 s at 48 kHz
STAGE_A_FRAMES = 180                      # 6 s at 30 fps
STAGE_A_EVENT_FRAMES = range(30, 60)      # output [1, 2)
STAGE_A_SIZES = {
    "master": (1280, 720), "edit": (1280, 720),
    "upload": (1920, 1080), "vertical": (720, 1280),
}
STAGE_A_WINDOW = 4_800                    # 0.1 s: whole cycles of every tone
STAGE_A_TONES = (300, 600, 1000, 1500)
# What each mode must carry at output 0.5 s, 1.5 s and 2.5 s.
STAGE_A_EXPECTED = {
    "original": {0.5: {300}, 1.5: {600}, 2.5: {300}},
    "replace": {0.5: {1000}, 1.5: {1500}, 2.5: {1000}},
    "mix": {0.5: {300, 1000}, 1.5: {600, 1500}, 2.5: {300, 1000}},
}


def _stage_a_recording(tools, path: Path) -> None:
    yellow = "enable='gte(t,13)*lt(t,14)'"
    picture = (
        "color=red:s=1280x720:r=30:d=20,"
        f"drawbox=x=0:y=0:w=442:h=720:color=yellow:t=fill:{yellow},"
        f"drawbox=x=838:y=0:w=442:h=720:color=yellow:t=fill:{yellow},"
        "drawbox=x=442:y=0:w=396:h=720:color=green:t=fill,"
        f"drawbox=x=600:y=300:w=80:h=80:color=blue:t=fill:{yellow}")
    wave_ = "0.4*sin(2*PI*if(gte(t,13)*lt(t,14),600,300)*t)"
    _run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-y", "-f", "lavfi", "-i", picture,
        "-f", "lavfi", "-i", f"aevalsrc='{wave_}|{wave_}':s=48000:d=20",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-f", "mpegts", str(path),
    ])


def _stage_a_song(path: Path) -> None:
    samples = array("h")
    for offset in range(12 * 44_100):
        frequency = 1500 if 5 * 44_100 <= offset < 6 * 44_100 else 1000
        samples.append(round(
            14_000 * math.sin(2 * math.pi * frequency * offset / 44_100)))
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(44_100)
        target.writeframes(samples.tobytes())


@pytest.fixture(scope="module")
def stage_a_media(tools, tmp_path_factory):
    from flightdvr.audio_plan import AudioAsset
    from flightdvr.media import probe

    root = tmp_path_factory.mktemp("stage-a-ordinary")
    recording = root / "recording.ts"
    _stage_a_recording(tools, recording)
    clip = probe(tools, recording)
    assert not clip.error, clip.error
    assert (clip.width, clip.height, round(clip.fps), clip.has_audio) == (
        1280, 720, 30, True)
    song = root / "song-44100.wav"
    _stage_a_song(song)
    asset = AudioAsset(song.resolve(), _file_sha256(song), 0, 44_100, 1,
                       12 * 44_100)
    return clip, asset


def _stage_a_choice(asset, mode: str):
    from fractions import Fraction

    from flightdvr.audio_plan import AudioMode, MusicChoice, SampleSpan

    kind = AudioMode(mode) if mode != "legacy" else None
    if kind is None:
        return MusicChoice()
    if kind in (AudioMode.ORIGINAL, AudioMode.NO_SOUND):
        return MusicChoice(mode=kind)
    return MusicChoice(
        asset=asset, passage=SampleSpan(4 * 44_100, 10 * 44_100, 44_100),
        mode=kind,
        music_level=Fraction(1) if kind is AudioMode.REPLACE else Fraction(3, 4),
        dvr_level=Fraction(1, 4), fade_in_samples=4_800, fade_out_samples=4_800)


def _stage_a_settings(**changes):
    from flightdvr.presets import PASSTHROUGH, ExportSettings

    return ExportSettings(
        master_speed="ultrafast", upload_speed="ultrafast",
        vertical_speed="ultrafast", edit_codec="prores_lt",
        colour=PASSTHROUGH, **changes)


def _stage_a_export(tools, clip, preset: str, choice, root: Path, name: str,
                    settings=None, span=STAGE_A_RANGE) -> Path:
    from copy import copy

    from flightdvr.jobs import ExportWorker, Job

    ranged = copy(clip)
    ranged.trim_in, ranged.trim_out = span
    suffix = ".mov" if preset == "edit" else ".mp4"
    out = root / f"{preset}-{name}{suffix}"
    job = Job([ranged], preset, settings or _stage_a_settings(), out,
              audio=choice)
    worker = ExportWorker(tools, [job], root / "work")
    ok, message = worker._run_job(0, job)
    assert ok, f"{preset}/{name}: {message}"
    assert not list(root.glob("*.flightdvr-part*"))
    return out


def _stage_a_facts(tools, path: Path) -> dict:
    found = json.loads(_run([
        str(tools.ffprobe), "-v", "error", "-count_frames",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,sample_rate,channels,"
        "nb_read_frames:format_tags=major_brand",
        "-of", "json", str(path),
    ]).stdout)
    streams = found.get("streams", [])
    return {
        "brand": found["format"].get("tags", {}).get("major_brand", ""),
        "video": [(s["codec_name"], int(s["width"]), int(s["height"]),
                   int(s["nb_read_frames"]))
                  for s in streams if s["codec_type"] == "video"],
        "audio": [(s["codec_name"], int(s["sample_rate"]), int(s["channels"]))
                  for s in streams if s["codec_type"] == "audio"],
    }


def _stage_a_picture(tools, path: Path) -> list[str]:
    """Every decoded frame's own hash: the picture, independent of audio."""
    lines = _run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(path), "-map", "0:v:0", "-f", "framemd5", "-",
    ]).stdout.splitlines()
    return [line.rsplit(",", 1)[-1].strip() for line in lines
            if line and not line.startswith("#")]


def _stage_a_columns(tools, path: Path, width: int, height: int,
                     columns: tuple[int, ...]) -> list[list[str]]:
    """Per frame, the colour of each named column of a small scaled copy."""
    raw = _run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(path), "-map", "0:v:0",
        "-vf", f"scale={width}:{height}:flags=area", "-pix_fmt", "rgb24",
        "-f", "rawvideo", "-",
    ], text=False).stdout
    size = width * height * 3
    assert len(raw) % size == 0
    frames = []
    for start in range(0, len(raw), size):
        frame = raw[start:start + size]
        row = height // 2
        labels = []
        for column in columns:
            at = (row * width + column) * 3
            red, green, blue = frame[at], frame[at + 1], frame[at + 2]
            if blue > 150 and red < 100 and green < 100:
                labels.append("blue")
            elif red > 150 and green > 150 and blue < 100:
                labels.append("yellow")
            elif red > 150 and green < 100:
                labels.append("red")
            elif green > 80 and red < 60:
                labels.append("green")
            else:
                labels.append("other")
        frames.append(labels)
    return frames


def _stage_a_magnitude(samples: array, seconds: float, frequency: int) -> float:
    start = round(seconds * OUTPUT_RATE) - STAGE_A_WINDOW // 2
    window = samples[start:start + STAGE_A_WINDOW]
    if len(window) != STAGE_A_WINDOW:
        return 0.0
    mean = sum(window) / STAGE_A_WINDOW
    cosine = sine = 0.0
    for index, value in enumerate(window):
        angle = 2 * math.pi * frequency * index / OUTPUT_RATE
        cosine += (value - mean) * math.cos(angle)
        sine += (value - mean) * math.sin(angle)
    return 2 * math.hypot(cosine, sine) / STAGE_A_WINDOW


def _stage_a_events_hold(samples: array, mode: str) -> bool:
    """Wanted tones present; every other test tone at most a quarter of the
    weakest wanted one, at each literal point."""
    for point, wanted in STAGE_A_EXPECTED[mode].items():
        present = [_stage_a_magnitude(samples, point, tone) for tone in wanted]
        if min(present) < 0.015:
            return False
        for tone in STAGE_A_TONES:
            if tone not in wanted and (
                    _stage_a_magnitude(samples, point, tone)
                    > min(present) / 4):
                return False
    return True


@pytest.mark.parametrize("preset", ["master", "edit", "upload", "vertical"])
def test_stage_a_ordinary_range_carries_each_mode_on_its_own_time(
        tools, stage_a_media, tmp_path, preset):
    clip, asset = stage_a_media
    outputs = {
        mode: _stage_a_export(tools, clip, preset,
                              _stage_a_choice(asset, mode), tmp_path, mode)
        for mode in ("legacy", "original", "no_sound", "replace", "mix")
    }
    outputs["legacy_silent"] = _stage_a_export(
        tools, clip, preset, _stage_a_choice(asset, "legacy"), tmp_path,
        "legacy-silent", settings=_stage_a_settings(keep_audio=False))
    width, height = STAGE_A_SIZES[preset]
    codec = "pcm_s16le" if preset == "edit" else "aac"
    for mode, path in outputs.items():
        facts = _stage_a_facts(tools, path)
        assert (facts["brand"].strip() == "qt") == (preset == "edit"), facts
        assert [v[1:] for v in facts["video"]] == [
            (width, height, STAGE_A_FRAMES)], (mode, facts)
        if mode in ("no_sound", "legacy_silent"):
            assert facts["audio"] == [], facts
        elif mode != "legacy":
            assert facts["audio"] == [(codec, OUTPUT_RATE, 2)], (mode, facts)

    # The picture is the base route's picture, frame for frame: adding a
    # music input and an explicit audio map changed nothing about it. The
    # base itself has two pictures here. Keeping the recording's sound (and
    # so Original) repeats the first frame, because this recording's sound
    # starts 21 ms before its picture; without it the range is exact. Each
    # mode is held to the base route that treats that sound the same way.
    legacy = _stage_a_picture(tools, outputs["legacy"])
    legacy_silent = _stage_a_picture(tools, outputs["legacy_silent"])
    assert len(legacy) == len(legacy_silent) == STAGE_A_FRAMES
    assert legacy != legacy_silent
    for mode in ("original", "mix"):
        assert _stage_a_picture(tools, outputs[mode]) == legacy, mode
    for mode in ("no_sound", "replace"):
        assert _stage_a_picture(tools, outputs[mode]) == legacy_silent, mode

    # ...and the exact route's picture is the range's own: the event second
    # at output [1, 2), and for Vertical the band fills both edges.
    event = {}
    for name in ("replace", "legacy"):
        if preset == "vertical":
            seen = _stage_a_columns(tools, outputs[name], 36, 64, (0, 18, 35))
            assert all(labels[0] == labels[2] == "green" for labels in seen)
            marked = [n for n, labels in enumerate(seen) if labels[1] == "blue"]
        else:
            seen = _stage_a_columns(tools, outputs[name], 64, 36, (4,))
            assert all(label in ("red", "yellow") for (label,) in seen)
            marked = [n for n, (label,) in enumerate(seen) if label == "yellow"]
        event[name] = (marked[0], marked[-1], len(marked))
    assert event["replace"] == (30, 59, 30)
    assert event["legacy"] == (31, 60, 30)      # measured base behaviour

    decoded = {mode: _decode_mono(tools, outputs[mode])
               for mode in ("original", "replace", "mix")}
    for mode, samples in decoded.items():
        if preset == "edit":
            assert len(samples) == STAGE_A_SAMPLES, (mode, len(samples))
        else:
            assert abs(len(samples) - STAGE_A_SAMPLES) <= SAMPLE_TOLERANCE
        assert _stage_a_events_hold(samples, mode), mode
    # Mix levels against the single-signal files, as literal fractions.
    assert (_stage_a_magnitude(decoded["mix"], 1.5, 600)
            / _stage_a_magnitude(decoded["original"], 1.5, 600)
            ) == pytest.approx(0.25, abs=0.08)
    assert (_stage_a_magnitude(decoded["mix"], 1.5, 1500)
            / _stage_a_magnitude(decoded["replace"], 1.5, 1500)
            ) == pytest.approx(0.75, abs=0.10)
    # The oracle is not satisfied by silence or by the other mode's sound.
    silent = array("f", [0.0]) * STAGE_A_SAMPLES
    assert not _stage_a_events_hold(silent, "replace")
    assert not _stage_a_events_hold(decoded["original"], "replace")
    assert not _stage_a_events_hold(decoded["replace"], "mix")
    # A song one second early or late does not read as the song's time.
    second = OUTPUT_RATE
    replace = decoded["replace"]
    early = replace[second:] + array("f", [0.0]) * second
    late = array("f", [0.0]) * second + replace[:-second]
    assert not _stage_a_events_hold(early, "replace")
    assert not _stage_a_events_hold(late, "replace")
    print("STAGE_A_ORDINARY_MEASUREMENT " + json.dumps({
        "preset": preset,
        "decoded_samples": {m: len(s) for m, s in decoded.items()},
        "frames": len(legacy),
        "event_frames": event,
        "output_sha256_16": {m: _file_sha256(p)[:16]
                             for m, p in outputs.items()},
        "ffmpeg": str(tools.ffmpeg),
    }, sort_keys=True))


@pytest.mark.parametrize("wrong", ["removed", "second_early", "second_late"])
def test_stage_a_music_on_the_wrong_origin_is_caught(
        tools, stage_a_media, tmp_path, monkeypatch, wrong):
    """The music must start at the range, not the recording's start.

    Removed compensation starts the song 12 s late, past the passage: no
    music at all. One second either way leaves a second of it missing. Each
    is refused before publication; the event oracle's own sensitivity to a
    one-second shift is shown on the real Replace output above.
    """
    from copy import copy

    import flightdvr.audio_export as audio_export
    from flightdvr.jobs import ExportWorker, Job

    clip, asset = stage_a_media
    actual = audio_export.audio_filter_args

    def misplaced(plan, *, source_seek_samples=0, **kwargs):
        seek = {"removed": 0,
                "second_early": source_seek_samples - OUTPUT_RATE,
                "second_late": source_seek_samples + OUTPUT_RATE}[wrong]
        return actual(plan, source_seek_samples=seek, **kwargs)

    monkeypatch.setattr(audio_export, "audio_filter_args", misplaced)
    out = tmp_path / "misplaced.mov"
    ranged = copy(clip)
    ranged.trim_in, ranged.trim_out = STAGE_A_RANGE
    job = Job([ranged], "edit", _stage_a_settings(), out,
              audio=_stage_a_choice(asset, "replace"))
    ok, message = ExportWorker(tools, [job], tmp_path / "work")._run_job(0, job)
    assert not ok
    assert ("no audio" if wrong == "removed" else "wrong audio extent") in message
    assert not out.exists()
    assert not list(tmp_path.glob("*.flightdvr-part*"))


@pytest.mark.parametrize("preset", ["master", "edit"])
def test_stage_a_silent_recording_modes(tools, tmp_path, stage_a_media,
                                        preset):
    """Replace and Mix need no sound of the recording's own, even with
    Keep sound off; Original on a silent recording has no audio stream."""
    from flightdvr.media import probe

    _clip, asset = stage_a_media
    source = tmp_path / "silent.ts"
    _run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-y", "-f", "lavfi", "-i", "color=red:s=1280x720:r=30:d=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an", "-f", "mpegts", str(source),
    ])
    silent = probe(tools, source)
    assert not silent.error and not silent.has_audio
    span = (1.0, 3.0)
    codec = "pcm_s16le" if preset == "edit" else "aac"
    original = _stage_a_export(
        tools, silent, preset, _stage_a_choice(asset, "original"), tmp_path,
        "original", span=span)
    assert _stage_a_facts(tools, original)["audio"] == []
    for mode in ("replace", "mix"):
        out = _stage_a_export(
            tools, silent, preset, _stage_a_choice(asset, mode), tmp_path,
            mode, settings=_stage_a_settings(keep_audio=False), span=span)
        assert _stage_a_facts(tools, out)["audio"] == [
            (codec, OUTPUT_RATE, 2)], mode
        samples = _decode_mono(tools, out)
        if preset == "edit":
            assert len(samples) == 2 * OUTPUT_RATE
        # Output 1.5 s is song 5.5 s; there is no recording sound to mix.
        music = _stage_a_magnitude(samples, 1.5, 1500)
        level = 1.0 if mode == "replace" else 0.75
        assert music == pytest.approx(level * 14_000 / 32_768, rel=0.15)
        assert _stage_a_magnitude(samples, 1.5, 1000) < music / 4
        assert _stage_a_magnitude(samples, 0.5, 1000) >= 0.015
