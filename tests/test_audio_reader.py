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
import threading
import tracemalloc
from pathlib import Path

import pytest

import flightdvr.audio_reader as reader_module
from flightdvr.audio_plan import AudioAsset
from flightdvr.audio_reader import (
    AudioAssetError,
    AudioOperationCancelled,
    AudioReaderError,
    ERROR_MESSAGE_CHARS,
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


class BlockingPipe(FakePipe):
    def __init__(self, entered, released):
        super().__init__()
        self.entered = entered
        self.released = released

    def read(self, size=-1):
        self.entered.set()
        assert self.released.wait(2), "the reader was not unblocked"
        return b""


class BlockingProcess(FakeProcess):
    def __init__(self, entered, released):
        super().__init__()
        self.stdout = BlockingPipe(entered, released)
        self.released = released

    def terminate(self):
        super().terminate()
        self.released.set()


class FakeProbeProcess:
    def __init__(self, stdout, stderr="", code=0):
        stdout = stdout.encode() if isinstance(stdout, str) else stdout
        stderr = stderr.encode() if isinstance(stderr, str) else stderr
        self.stdout = FakePipe(stdout)
        self.stderr = FakePipe(stderr)
        self.returncode = code

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class TimedOutProbeProcess(FakeProcess):
    def __init__(self, cancelled):
        super().__init__()
        self.cancelled = cancelled

    def wait(self, timeout=None):
        if not self.cancelled.is_set():
            self.cancelled.set()
            raise reader_module.subprocess.TimeoutExpired("ffprobe", timeout)
        return super().wait(timeout)


class LongDiagnosticPipe:
    def __init__(self, size):
        self.remaining = size
        self.closed = False

    def read(self, size=-1):
        if not self.remaining:
            return b""
        found = self.remaining if size < 0 else min(size, self.remaining)
        self.remaining -= found
        return b"x" * found

    def close(self):
        self.closed = True

    def __iter__(self):
        if self.remaining:
            yield self.read(self.remaining) + b"\n"


class StreamingProbeProcess(FakeProcess):
    def __init__(self, stdout, diagnostic_bytes):
        super().__init__(code=0)
        self.stdout = FakePipe(stdout)
        self.stderr = LongDiagnosticPipe(diagnostic_bytes)

    def communicate(self, timeout=None):
        raise AssertionError("probe capture must not use unbounded communicate()")


class WaitingProcess(FakeProcess):
    def __init__(self, data):
        super().__init__(data)
        self.wait_calls = 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.returncode is None:
            raise reader_module.subprocess.TimeoutExpired("ffmpeg", timeout)
        return self.returncode


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
    assert "-xerror" in command
    assert command[command.index("-ar") + 1] == "48000"
    assert command[command.index("-ac") + 1] == "2"


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
    assert "-xerror" in command


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


def test_contiguous_pulls_reuse_one_child(monkeypatch, tmp_path):
    process = FakeProcess(floats(0.1, 0.1, 0.2, 0.2))
    commands = install_processes(monkeypatch, [process])
    reader = FfmpegPcmReader.for_music(
        TOOLS, asset(tmp_path / "music.wav", rate=48_000, samples=2))
    try:
        assert tuple(reader.read(0, 1, lambda: False)) == pytest.approx(
            (0.1, 0.1))
        assert tuple(reader.read(1, 1, lambda: False)) == pytest.approx(
            (0.2, 0.2))
        assert len(commands) == 1
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


def test_partial_source_pcm_frame_and_nonzero_exit_are_errors(
        monkeypatch, tmp_path):
    failing = FakeProcess(b"", code=7)
    failing.stderr = FakePipe()
    install_processes(monkeypatch, [
        FakeProcess(b"\x00"),
        failing,
    ])
    partial = FfmpegPcmReader.for_source(
        TOOLS, tmp_path / "partial.mp4", stream_index=0, timeline_frames=1)
    try:
        with pytest.raises(AudioReaderError, match="truncated stereo frame"):
            partial.read(0, 1, lambda: False)
    finally:
        partial.close()

    broken = FfmpegPcmReader.for_source(
        TOOLS, tmp_path / "broken.mp4", stream_index=0, timeline_frames=1)
    try:
        with pytest.raises(AudioReaderError, match=r"code 7"):
            broken.read(0, 1, lambda: False)
    finally:
        broken.close()


def test_complete_final_block_cannot_hide_a_known_child_failure(
        monkeypatch, tmp_path):
    process = FakeProcess(floats(*([0.25, 0.25] * 480)), code=7)
    process.stderr = FakePipe(b"decoder failed after PCM\n")
    process.returncode = 7
    install_processes(monkeypatch, [process])
    reader = FfmpegPcmReader.for_music(
        TOOLS, asset(tmp_path / "music.wav", rate=48_000, samples=480))
    try:
        with pytest.raises(AudioReaderError, match="decoder failed after PCM"):
            reader.read(0, 480, lambda: False)
    finally:
        reader.close()


def test_final_extent_wait_keeps_polling_cancellation(monkeypatch, tmp_path):
    process = WaitingProcess(floats(0.25, 0.25))
    install_processes(monkeypatch, [process])
    reader = FfmpegPcmReader.for_music(
        TOOLS, asset(tmp_path / "music.wav", rate=48_000, samples=1))
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        return checks >= 4

    try:
        with pytest.raises(AudioOperationCancelled):
            reader.read(0, 1, cancelled)
        assert process.wait_calls == 1
    finally:
        reader.close()


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
        TOOLS, asset(tmp_path / "music.wav", rate=48_000, samples=2))
    reader.read(0, 1, lambda: False)
    reader.request_stop()
    reader.request_stop()
    reader.close()
    reader.close()
    assert process.terminated == 1


def test_request_stop_unblocks_a_held_read_and_worker_close_settles_it(
        monkeypatch, tmp_path):
    entered = threading.Event()
    released = threading.Event()
    process = BlockingProcess(entered, released)
    install_processes(monkeypatch, [process])
    reader = FfmpegPcmReader.for_music(
        TOOLS, asset(tmp_path / "music.wav", rate=48_000, samples=1))
    outcomes = []

    def held_read():
        try:
            reader.read(0, 1, lambda: False)
        except Exception as exc:
            outcomes.append(exc)

    worker = threading.Thread(target=held_read)
    worker.start()
    assert entered.wait(1)
    reader.request_stop()
    worker.join(2)
    assert not worker.is_alive()
    assert len(outcomes) == 1
    assert isinstance(outcomes[0], AudioOperationCancelled)
    reader.close()
    assert process.terminated == 1
    assert reader._stderr_thread is None


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


def test_each_asset_stage_checks_cancellation_and_settles_its_child(
        monkeypatch, tmp_path):
    track = tmp_path / "music.wav"
    track.write_bytes(b"x" * (reader_module.HASH_CHUNK_BYTES + 1))

    probe_cancelled = threading.Event()
    probe_process = TimedOutProbeProcess(probe_cancelled)
    install_processes(monkeypatch, [probe_process])
    registered = []
    with pytest.raises(AudioOperationCancelled):
        reader_module._probe_first_audio(
            TOOLS, track, probe_cancelled.is_set, registered.append)
    assert registered == [probe_process, None]
    assert probe_process.terminated == 1

    count_process = FakeProcess(floats(0.0))
    install_processes(monkeypatch, [count_process])
    count_checks = iter((False, True))
    registered = []
    with pytest.raises(AudioOperationCancelled):
        reader_module._count_native_samples(
            TOOLS, track, 48_000, 1, lambda: next(count_checks),
            registered.append)
    assert registered == [count_process, None]
    assert count_process.terminated == 1

    hash_checks = iter((False, True))
    with pytest.raises(AudioOperationCancelled):
        reader_module._file_sha256(track, lambda: next(hash_checks))


def test_probe_work_runs_off_caller_and_stop_emits_no_late_outcome(
        monkeypatch, tmp_path):
    track = tmp_path / "music.wav"
    entered = threading.Event()
    released = threading.Event()
    process = FakeProcess()
    caller_ident = threading.get_ident()
    worker_idents = []

    def held_inspection(*args, cancelled, register_process):
        worker_idents.append(threading.get_ident())
        register_process(process)
        entered.set()
        try:
            assert released.wait(2)
            if cancelled():
                raise AudioOperationCancelled("Cancelled")
            return asset(track)
        finally:
            register_process(None)

    monkeypatch.setattr(reader_module, "inspect_music_asset", held_inspection)
    probe = MusicAssetProbe(TOOLS, track, 7)
    ready = []
    failed = []
    probe.ready.connect(lambda *args: ready.append(args))
    probe.failed.connect(lambda *args: failed.append(args))
    assert worker_idents == []
    probe.start()
    assert entered.wait(1)
    probe.stop()
    released.set()
    assert probe.wait(2_000)
    assert len(worker_idents) == 1
    assert worker_idents[0] != caller_ident
    assert process.terminated == 1
    assert ready == []
    assert failed == []


def test_successful_probe_emits_generation_and_existing_asset_once(
        monkeypatch, tmp_path):
    made = asset(tmp_path / "music.wav")
    monkeypatch.setattr(reader_module, "inspect_music_asset",
                        lambda *args, **kwargs: made)
    probe = MusicAssetProbe(TOOLS, made.track, 11)
    ready = []
    failed = []
    probe.ready.connect(lambda *args: ready.append(args))
    probe.failed.connect(lambda *args: failed.append(args))
    probe.run()
    assert ready == [(11, made)]
    assert failed == []


@pytest.mark.parametrize("stdout", [
    "not json",
    '{"streams": []}',
    '{"streams": [{"sample_rate": "0", "channels": 0}]}',
])
def test_malformed_or_unusable_first_audio_stream_is_rejected(
        monkeypatch, tmp_path, stdout):
    install_processes(monkeypatch, [FakeProbeProcess(stdout)])
    with pytest.raises(AudioAssetError, match="first audio stream"):
        reader_module._probe_first_audio(
            TOOLS, tmp_path / "bad.bin", lambda: False, lambda proc: None)


def test_probe_and_diagnostic_drains_are_bounded_before_allocation(
        monkeypatch, tmp_path):
    diagnostic_bytes = 2 * 1024 * 1024 + 1
    pipe = LongDiagnosticPipe(diagnostic_bytes)
    log = reader_module.deque(maxlen=reader_module.STDERR_LINES)
    tracemalloc.start()
    reader_module._drain_stderr(pipe, log)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert sum(map(len, log)) <= reader_module.STDERR_LINE_CHARS
    assert peak < 256 * 1024
    print(
        "diagnostic drain measurement: "
        f"input={diagnostic_bytes} retained={sum(map(len, log))} peak={peak}"
    )

    stdout = (b'{"streams":[{"codec_name":"pcm_s16le",'
              b'"sample_rate":"48000","channels":1}]}')
    process = StreamingProbeProcess(stdout, diagnostic_bytes)
    install_processes(monkeypatch, [process])
    assert reader_module._probe_first_audio(
        TOOLS, tmp_path / "music.wav", lambda: False,
        lambda proc: None) == (48_000, 1)

    oversized = StreamingProbeProcess(
        stdout + b" " * reader_module.PROBE_STDOUT_BYTES,
        diagnostic_bytes=0,
    )
    install_processes(monkeypatch, [oversized])
    with pytest.raises(AudioAssetError, match="too much stream metadata"):
        reader_module._probe_first_audio(
            TOOLS, tmp_path / "music.wav", lambda: False,
            lambda proc: None)


def test_probe_emits_one_bounded_person_readable_failure(monkeypatch, tmp_path):
    reason = "x" * (ERROR_MESSAGE_CHARS + 50)

    def fail(*args, **kwargs):
        raise AudioAssetError(reason)

    monkeypatch.setattr(reader_module, "inspect_music_asset", fail)
    probe = MusicAssetProbe(TOOLS, tmp_path / "bad.wav", 9)
    ready = []
    failed = []
    probe.ready.connect(lambda *args: ready.append(args))
    probe.failed.connect(lambda *args: failed.append(args))
    probe.run()
    assert ready == []
    assert failed == [(9, "x" * ERROR_MESSAGE_CHARS)]
