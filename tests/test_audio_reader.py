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

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import pytest

import flightdvr.audio_reader as reader_module
from flightdvr.audio_plan import AudioAsset
from flightdvr.audio_reader import (
    AudioAssetError,
    AudioReaderError,
    FfmpegPcmReader,
    MAX_READ_BYTES,
    MusicAssetProbe,
    inspect_music_asset,
)
from flightdvr.media import Tools


DIGEST = "a" * 64
TOOLS = Tools(Path("ffmpeg"), Path("ffprobe"))


def asset(path=Path("music.wav"), *, rate=44_100, samples=44_100):
    return AudioAsset(path, DIGEST, 0, rate, 1, samples)


class FakePipe:
    def __init__(self, data=b"", pieces=()):
        self.data = bytearray(data)
        self.pieces = list(pieces)
        self.closed = False

    def read(self, size=-1):
        if self.pieces:
            size = min(size, self.pieces.pop(0))
        if not self.data:
            return b""
        if size < 0:
            size = len(self.data)
        found = bytes(self.data[:size])
        del self.data[:size]
        return found

    def close(self):
        self.closed = True

    def __iter__(self):
        return iter(())


class FakeProcess:
    def __init__(self, data=b"", pieces=(), code=0):
        self.stdout = FakePipe(data, pieces)
        self.stderr = FakePipe()
        self.code = code
        self.returncode = None
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = self.code
        return self.code

    def terminate(self):
        self.terminated += 1
        self.returncode = self.code

    def kill(self):
        self.killed += 1
        self.returncode = self.code


def floats(*values):
    return b"".join(struct.pack("<f", value) for value in values)


def install_processes(monkeypatch, processes):
    commands = []

    def popen(command, **kwargs):
        commands.append((command, kwargs))
        return processes.pop(0)

    monkeypatch.setattr(reader_module.subprocess, "Popen", popen)
    return commands


def test_music_reader_converts_native_extent_once_and_uses_contiguous_clock(tmp_path):
    track = tmp_path / "-music.wav"
    track.write_bytes(b"fixture")
    reader = FfmpegPcmReader.for_music(
        TOOLS, asset(track, rate=44_100, samples=44_101))
    command = reader._command(4_800)

    assert reader.frames == 48_001
    assert str(track.resolve()) in command
    assert command[command.index("-map") + 1] == "0:a:0"
    filters = command[command.index("-af") + 1]
    assert "aresample=48000:async=0" in filters
    assert "atrim=start_sample=4800" in filters
    assert "apad" not in filters


def test_source_reader_uses_explicit_timeline_extent_and_clean_padding_filter(tmp_path):
    track = tmp_path / "source.mp4"
    reader = FfmpegPcmReader.for_source(
        TOOLS, track, stream_index=1, timeline_frames=9_600)
    command = reader._command(480)
    filters = command[command.index("-af") + 1]

    assert reader.frames == 9_600
    assert command[command.index("-map") + 1] == "0:a:1"
    assert "aresample=48000:async=1:first_pts=0" in filters
    assert "apad=whole_len=9600" in filters
    assert "atrim=end_sample=9600" in filters
    assert "atrim=start_sample=480" in filters


def test_partial_pipe_reads_assemble_one_exact_bounded_float_block(
        monkeypatch, tmp_path):
    track = tmp_path / "music.wav"
    process = FakeProcess(floats(0.25, -0.5, 0.75, -1.0), pieces=(3, 2, 7, 4))
    commands = install_processes(monkeypatch, [process])
    reader = FfmpegPcmReader.for_music(
        TOOLS, asset(track, rate=48_000, samples=2))
    try:
        assert tuple(reader.read(0, 2, lambda: False)) == pytest.approx(
            (0.25, -0.5, 0.75, -1.0))
        assert len(commands) == 1
        assert MAX_READ_BYTES == 3_840
    finally:
        reader.close()


def test_noncontiguous_read_discards_the_old_child_before_starting_another(
        monkeypatch, tmp_path):
    first = FakeProcess(floats(0.1, 0.1))
    second = FakeProcess(floats(0.9, 0.9))
    commands = install_processes(monkeypatch, [first, second])
    reader = FfmpegPcmReader.for_music(
        TOOLS, asset(tmp_path / "music.wav", rate=48_000, samples=20))
    try:
        assert reader.read(0, 1, lambda: False)[0] == pytest.approx(0.1)
        assert reader.read(10, 1, lambda: False)[0] == pytest.approx(0.9)
        assert len(commands) == 2
        assert first.terminated == 1
        assert "atrim=start_sample=10" in commands[1][0][
            commands[1][0].index("-af") + 1]
    finally:
        reader.close()


def test_music_short_read_is_an_error_but_clean_source_tail_is_timeline_silence(
        monkeypatch, tmp_path):
    install_processes(monkeypatch, [
        FakeProcess(floats(0.5, 0.5)),
        FakeProcess(floats(0.5, 0.5)),
    ])
    music = FfmpegPcmReader.for_music(
        TOOLS, asset(tmp_path / "music.wav", rate=48_000, samples=2))
    with pytest.raises(AudioReaderError, match="validated extent"):
        music.read(0, 2, lambda: False)
    music.close()

    source = FfmpegPcmReader.for_source(
        TOOLS, tmp_path / "source.mp4", stream_index=0, timeline_frames=2)
    try:
        assert tuple(source.read(0, 2, lambda: False)) == pytest.approx(
            (0.5, 0.5, 0.0, 0.0))
    finally:
        source.close()


def test_nonfinite_pcm_and_reads_outside_the_bound_fail(monkeypatch, tmp_path):
    install_processes(monkeypatch, [
        FakeProcess(floats(float("nan"), 0.0)),
    ])
    reader = FfmpegPcmReader.for_music(
        TOOLS, asset(tmp_path / "music.wav", rate=48_000, samples=1))
    try:
        with pytest.raises(AudioReaderError, match="non-finite"):
            reader.read(0, 1, lambda: False)
        with pytest.raises(ValueError, match="beyond"):
            reader.read(1, 1, lambda: False)
        with pytest.raises(ValueError, match="1 to 480"):
            reader.read(0, 481, lambda: False)
    finally:
        reader.close()


def test_request_stop_and_close_are_idempotent(monkeypatch, tmp_path):
    process = FakeProcess(floats(0.0, 0.0))
    install_processes(monkeypatch, [process])
    reader = FfmpegPcmReader.for_music(
        TOOLS, asset(tmp_path / "music.wav", rate=48_000, samples=1))
    reader.read(0, 1, lambda: False)
    reader.request_stop()
    reader.request_stop()
    reader.close()
    reader.close()
    assert process.terminated == 1


def test_inspection_returns_existing_asset_with_full_hash_and_native_count(
        monkeypatch, tmp_path):
    track = tmp_path / "music.wav"
    track.write_bytes(b"whole file including tail")
    monkeypatch.setattr(reader_module, "_probe_first_audio",
                        lambda *args: (44_100, 1))
    monkeypatch.setattr(reader_module, "_count_native_samples",
                        lambda *args: 123_457)

    found = inspect_music_asset(TOOLS, track)

    assert isinstance(found, AudioAsset)
    assert found.track == track.resolve()
    assert found.stream_index == 0
    assert (found.sample_rate, found.channels, found.decoded_samples) == (
        44_100, 1, 123_457)
    assert found.sha256 == hashlib.sha256(track.read_bytes()).hexdigest()


def test_inspection_rejects_a_file_changed_during_the_operation(
        monkeypatch, tmp_path):
    track = tmp_path / "music.wav"
    track.write_bytes(b"before")
    monkeypatch.setattr(reader_module, "_probe_first_audio",
                        lambda *args: (48_000, 2))
    monkeypatch.setattr(reader_module, "_count_native_samples",
                        lambda *args: 48_000)

    def change_while_hashing(path, cancelled):
        path.write_bytes(b"different length")
        return "b" * 64

    monkeypatch.setattr(reader_module, "_file_sha256", change_while_hashing)
    with pytest.raises(AudioAssetError, match="changed"):
        inspect_music_asset(TOOLS, track)


def test_cancelled_probe_emits_no_late_ready_or_failure(monkeypatch, tmp_path):
    track = tmp_path / "music.wav"
    made = asset(track)
    monkeypatch.setattr(reader_module, "inspect_music_asset",
                        lambda *args, **kwargs: made)
    probe = MusicAssetProbe(TOOLS, track, 7)
    ready = []
    failed = []
    probe.ready.connect(lambda *args: ready.append(args))
    probe.failed.connect(lambda *args: failed.append(args))
    probe.stop()
    probe.run()
    assert ready == []
    assert failed == []
