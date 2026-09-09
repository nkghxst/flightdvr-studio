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

"""The S3 live-audio core against requirement-derived fake readers.

These tests settle sample mapping, bounded memory, generation fencing, monitor
separation and the cooperative cancellation protocol.  They do not represent
a decoder, device callback, native UI, audible result or latency measurement.
"""

from __future__ import annotations

import math
import threading
import time
from fractions import Fraction
from pathlib import Path

import pytest

from flightdvr.audio_plan import (
    AudioAsset,
    AudioMode,
    MusicChoice,
    OUTPUT_RATE,
    SampleSpan,
    ShortTrackPolicy,
    resolve_audio_plan,
)
from flightdvr.audio_stream import (
    BLOCK_FRAMES,
    MAX_QUEUED_PCM_BYTES,
    QUEUE_CAPACITY,
    AudioStream,
    Buffering,
    LiveAudioMapping,
    MonitorState,
    Paused,
    PcmBlock,
    PcmBuffer,
    StreamFailed,
    StreamState,
)


DIGEST = "a" * 64


class FakeReader:
    """Deterministic normalised reader; values come from absolute positions."""

    def __init__(self, frames=20_000, value=lambda frame, channel: 0.5):
        self.frames = frames
        self.value = value
        self.reads: list[tuple[int, int]] = []
        self.stop_requests = 0
        self.closed = False
        self.close_requests = 0

    def read(self, start, frames, cancelled):
        if cancelled():
            raise RuntimeError("cancelled before read")
        if start < 0 or start + frames > self.frames:
            raise ValueError("fake read is out of range")
        self.reads.append((start, frames))
        return [
            self.value(frame, channel)
            for frame in range(start, start + frames)
            for channel in range(2)
        ]

    def request_stop(self):
        self.stop_requests += 1

    def close(self):
        self.close_requests += 1
        self.closed = True


class BlockingReader(FakeReader):
    """A cooperative blocked read whose stop request supplies the unblock."""

    def __init__(self, frames=20_000):
        super().__init__(frames)
        self.read_started = threading.Event()
        self.release = threading.Event()

    def read(self, start, frames, cancelled):
        self.read_started.set()
        while not self.release.wait(0.01):
            if cancelled():
                raise RuntimeError("cooperatively cancelled")
        if cancelled():
            raise RuntimeError("cooperatively cancelled")
        return super().read(start, frames, cancelled)

    def request_stop(self):
        super().request_stop()
        self.release.set()


def wait_for(predicate, message="condition was not reached"):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError(message)


def source_plan(mode, frames, *, source_has_audio=True):
    return resolve_audio_plan(
        MusicChoice(mode=mode),
        frames,
        source_has_audio=source_has_audio,
        preset_key="master",
    ) if mode in (AudioMode.ORIGINAL, AudioMode.NO_SOUND) else None


def music_plan(mode, frames, *, policy=ShortTrackPolicy.LOOP,
               music_level=1, dvr_level=Fraction(1, 4),
               passage=SampleSpan(4_410, 8_820, 44_100), source=True):
    asset = AudioAsset(Path("music.wav"), DIGEST, 0, 44_100, 2, 44_100)
    choice = MusicChoice(
        asset.track,
        mode,
        asset,
        passage,
        policy,
        music_level,
        dvr_level,
        0,
        0,
    )
    return resolve_audio_plan(
        choice, frames, source_has_audio=source, preset_key="master")


def mapping(plan, source_start=100):
    return LiveAudioMapping(
        plan,
        SampleSpan(source_start, source_start + plan.output.samples, OUTPUT_RATE),
    )


def first_block(stream):
    wait_for(lambda: stream.queued_blocks > 0, "producer did not queue a block")
    stream.resume()
    return stream.pull()


def stop(stream):
    stream.request_stop()
    assert stream.wait_stopped(2), "cooperative fake reader did not stop"


def test_mapping_converts_the_native_passage_origin_once_for_normalised_readers():
    plan = music_plan(AudioMode.REPLACE, BLOCK_FRAMES)
    made = mapping(plan)
    assert plan.passage.start == 4_410
    assert made.music_origin == 4_800

    with pytest.raises(ValueError, match="48 kHz"):
        LiveAudioMapping(plan, SampleSpan(100, 541, 44_100))
    with pytest.raises(ValueError, match="equal duration"):
        LiveAudioMapping(plan, SampleSpan(100, 101, OUTPUT_RATE))


@pytest.mark.parametrize("mode,expected", [
    (AudioMode.ORIGINAL, 0.8),
    (AudioMode.NO_SOUND, 0.0),
    (AudioMode.REPLACE, 0.5),
    (AudioMode.MIX, 0.56),
])
def test_four_export_modes_drive_the_same_live_sample_decision(mode, expected):
    plan = (source_plan(mode, BLOCK_FRAMES) if mode in (
        AudioMode.ORIGINAL, AudioMode.NO_SOUND) else music_plan(mode, BLOCK_FRAMES))
    source = FakeReader(value=lambda frame, channel: 0.8)
    music = FakeReader(value=lambda frame, channel: 0.5)
    stream = AudioStream(
        mapping(plan),
        source_reader=source if mode in (AudioMode.ORIGINAL, AudioMode.MIX) else None,
        music_reader=music if mode in (AudioMode.REPLACE, AudioMode.MIX) else None,
    )
    try:
        stream.start()
        block = first_block(stream)
        assert block.frames == BLOCK_FRAMES
        assert block.planned[0] == pytest.approx(expected)
    finally:
        stop(stream)


def test_music_loops_from_the_selected_in_point_not_file_zero():
    plan = music_plan(
        AudioMode.REPLACE,
        5,
        passage=SampleSpan(4_410, 4_412, 44_100),
    )
    reader = FakeReader(value=lambda frame, channel: frame)
    stream = AudioStream(mapping(plan), music_reader=reader,
                         monitor=MonitorState(level=1))
    try:
        stream.start()
        block = first_block(stream)
        assert tuple(block.planned)[::2] == pytest.approx(
            [4_800, 4_801, 4_800, 4_801, 4_800])
        assert reader.reads == [(4_800, 2), (4_800, 2), (4_800, 1)]
    finally:
        stop(stream)


def test_monitor_changes_rebuild_the_next_pull_even_when_the_block_was_queued():
    plan = source_plan(AudioMode.ORIGINAL, 2 * BLOCK_FRAMES)
    stream = AudioStream(mapping(plan), source_reader=FakeReader(),
                         monitor=MonitorState(level=0.25, muted=False))
    try:
        stream.start()
        wait_for(lambda: stream.queued_blocks == 2)
        stream.set_monitor(level=1)
        stream.resume()
        first = stream.pull()
        assert first.monitor_revision == 1
        assert tuple(first.monitored) == tuple(first.planned)

        stream.set_monitor(muted=True)
        second = stream.pull()
        assert second.monitor_revision == 2
        assert set(second.monitored) == {0.0}
        assert set(second.planned) == {0.5}
    finally:
        stop(stream)


def test_pcm_buffers_are_defensive_read_only_float32_storage():
    planned = [0.25, -0.5]
    monitored = [0.1, -0.2]
    block = PcmBlock(0, 0, 0, 1, planned, monitored)
    planned[0] = 1.0
    monitored[0] = 1.0

    assert isinstance(block.planned, PcmBuffer)
    assert tuple(block.planned) == pytest.approx((0.25, -0.5))
    assert tuple(block.monitored) == pytest.approx((0.1, -0.2))
    assert block.planned.view.readonly
    with pytest.raises(TypeError):
        block.planned.view[0] = 0.0


def test_four_full_blocks_bound_both_retained_pcm_buffers_to_30720_bytes():
    plan = source_plan(AudioMode.ORIGINAL, 10 * BLOCK_FRAMES)
    stream = AudioStream(mapping(plan), source_reader=FakeReader())
    try:
        stream.start()
        wait_for(lambda: stream.queued_blocks == QUEUE_CAPACITY)
        assert stream.queued_blocks == 4
        assert stream.queued_pcm_bytes == MAX_QUEUED_PCM_BYTES == 30_720
        # One worker-rendering block plus reader/decoder caches and Python
        # object overhead are deliberately outside this exact queue payload.
    finally:
        stop(stream)


def test_pause_eof_and_restart_are_observable_without_conflating_buffering():
    plan = source_plan(AudioMode.ORIGINAL, BLOCK_FRAMES)
    stream = AudioStream(mapping(plan), source_reader=FakeReader())
    try:
        assert stream.state is StreamState.READY
        stream.start()
        wait_for(lambda: stream.queued_blocks == 1)
        assert stream.state is StreamState.PAUSED
        with pytest.raises(Paused):
            stream.pull()

        stream.resume()
        assert stream.pull() is not None
        wait_for(lambda: stream.state is StreamState.EOF)
        assert stream.pull() is None
        assert stream.at_eof

        generation = stream.restart()
        assert generation == 1
        wait_for(lambda: stream.queued_blocks == 1)
        assert stream.state is StreamState.PAUSED
        with pytest.raises(Paused):
            stream.pull()
        stream.resume()
        restarted = stream.pull()
        assert restarted.output_start == 0
        assert restarted.generation == 1
    finally:
        stop(stream)


def test_reprime_discards_an_inflight_old_generation_and_fences_sink_submission():
    plan = source_plan(AudioMode.ORIGINAL, 3 * BLOCK_FRAMES)
    reader = BlockingReader()
    stream = AudioStream(mapping(plan), source_reader=reader)
    try:
        stream.start()
        assert reader.read_started.wait(1)
        stale = PcmBlock(0, 0, 0, 1, (0.5, 0.5), (0.125, 0.125))
        assert stream.reprime(BLOCK_FRAMES) == 1
        assert not stream.is_current(stale)
        reader.release.set()
        block = first_block(stream)
        assert block.generation == 1
        assert block.output_start == BLOCK_FRAMES
        assert stream.is_current(block)
    finally:
        stop(stream)


def test_stop_request_unblocks_a_cooperative_reader_without_joining_itself():
    plan = source_plan(AudioMode.ORIGINAL, BLOCK_FRAMES)
    reader = BlockingReader()
    stream = AudioStream(mapping(plan), source_reader=reader)
    stream.start()
    assert reader.read_started.wait(1)

    # This call only signals. The explicit wait below is the blocking boundary.
    stream.request_stop()
    assert reader.stop_requests == 1
    assert stream.state in (StreamState.STOPPING, StreamState.STOPPED)
    assert stream.wait_stopped(2)
    assert reader.closed
    assert stream.state is StreamState.STOPPED


def test_default_and_restart_require_explicit_resume_and_unmute():
    plan = source_plan(AudioMode.ORIGINAL, 2 * BLOCK_FRAMES)
    reader = FakeReader(value=lambda frame, channel: 0.5)
    stream = AudioStream(mapping(plan), source_reader=reader)
    try:
        stream.start()
        wait_for(lambda: stream.queued_blocks == 2)
        stream.resume()
        assert set(stream.pull().monitored) == {0.0}

        stream.set_monitor(level=1, muted=False)
        generation = stream.restart()
        assert generation == 1
        assert stream.state is StreamState.PAUSED
        with pytest.raises(Paused):
            stream.pull()
        wait_for(lambda: stream.queued_blocks == 2)
        stream.resume()
        assert set(stream.pull().monitored) == {0.0}
        stream.set_monitor(muted=False)
        assert set(stream.pull().monitored) == {0.5}
    finally:
        stop(stream)


def test_stop_before_start_closes_owned_readers_and_clears_the_queue():
    plan = source_plan(AudioMode.ORIGINAL, BLOCK_FRAMES)
    reader = FakeReader()
    stream = AudioStream(mapping(plan), source_reader=reader)
    stream.request_stop()
    assert stream.wait_stopped(0)
    assert stream.state is StreamState.STOPPED
    assert reader.stop_requests == 1
    assert reader.close_requests == 1
    assert stream.queued_blocks == 0
    assert stream.queued_pcm_bytes == 0


def test_settled_shutdown_releases_every_queued_buffer_and_closes_once():
    plan = source_plan(AudioMode.ORIGINAL, 10 * BLOCK_FRAMES)
    reader = FakeReader()
    stream = AudioStream(mapping(plan), source_reader=reader)
    stream.start()
    wait_for(lambda: stream.queued_blocks == QUEUE_CAPACITY)
    assert stream.queued_pcm_bytes == MAX_QUEUED_PCM_BYTES
    stop(stream)
    assert stream.queued_blocks == 0
    assert stream.queued_pcm_bytes == 0
    assert reader.close_requests == 1


@pytest.mark.parametrize("bad", [[0.0], [math.nan, 0.0]])
def test_truncated_or_nonfinite_reader_output_fails_loudly(bad):
    class BadReader(FakeReader):
        def read(self, start, frames, cancelled):
            return bad

    plan = source_plan(AudioMode.ORIGINAL, BLOCK_FRAMES)
    stream = AudioStream(mapping(plan), source_reader=BadReader())
    try:
        stream.start()
        stream.resume()
        wait_for(lambda: stream.state is StreamState.FAILED)
        with pytest.raises(StreamFailed):
            stream.pull()
        assert stream.wait_stopped(2)
        with pytest.raises(RuntimeError, match="failed"):
            stream.reprime(0)
        with pytest.raises(RuntimeError, match="failed"):
            stream.restart()
        assert stream.state is StreamState.FAILED
    finally:
        stop(stream)


def test_required_and_bounded_readers_are_validated_before_the_worker_starts():
    original = source_plan(AudioMode.ORIGINAL, BLOCK_FRAMES)
    with pytest.raises(ValueError, match="source reader"):
        AudioStream(mapping(original))
    with pytest.raises(ValueError, match="extends beyond"):
        AudioStream(mapping(original, source_start=19_800),
                    source_reader=FakeReader(frames=20_000))

    replace = music_plan(AudioMode.REPLACE, BLOCK_FRAMES)
    with pytest.raises(ValueError, match="music reader"):
        AudioStream(mapping(replace))


def test_one_reader_used_for_both_inputs_gets_one_stop_and_one_close():
    plan = music_plan(AudioMode.MIX, BLOCK_FRAMES)
    shared = FakeReader()
    stream = AudioStream(mapping(plan), source_reader=shared, music_reader=shared)
    stream.start()
    wait_for(lambda: stream.queued_blocks == 1)
    stop(stream)
    assert shared.stop_requests == 1
    assert shared.close_requests == 1
    stream.request_stop()
    assert stream.wait_stopped(0)
    assert shared.stop_requests == 1
    assert shared.close_requests == 1
