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
    resolve_monitor_audio_plan,
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
    SequencePcmReader,
    SequenceSourceSegment,
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


# -- S3a: a sequence source is one reader on the finished-output clock --------

def sequence_occurrence(index, source_start, duration, output_start, path):
    from flightdvr.assembly import Item
    from flightdvr.sequence_plan import (
        OccurrenceId, SequenceOccurrence, TimeSpan,
    )

    source = TimeSpan(Fraction(source_start),
                      Fraction(source_start + duration))
    output = TimeSpan(Fraction(output_start),
                      Fraction(output_start + duration))
    return SequenceOccurrence(
        OccurrenceId("s3a-a-b-a", index),
        Item(path, f"range-{path}"),
        f"{path}.ts",
        source,
        output,
        output,
        SampleSpan(output_start * OUTPUT_RATE,
                   (output_start + duration) * OUTPUT_RATE,
                   OUTPUT_RATE),
    )


def aba_occurrences():
    return (
        sequence_occurrence(0, 10, 3, 0, "A"),
        sequence_occurrence(1, 2, 2, 3, "B"),
        sequence_occurrence(2, 10, 3, 5, "A"),
    )


def source_signal(base, frame, channel):
    """Independent absolute-frame oracle; wrong origins and channels differ."""
    return base + frame / (100 * OUTPUT_RATE) + channel / 1_000


def emitted_at(stream, output_sample):
    stream.reprime(output_sample)
    return first_block(stream)


def test_aba_emitted_pcm_switches_exact_origins_and_occurrences_at_both_seams():
    a = FakeReader(
        frames=20 * OUTPUT_RATE,
        value=lambda frame, channel: source_signal(0.1, frame, channel))
    b = FakeReader(
        frames=10 * OUTPUT_RATE,
        value=lambda frame, channel: source_signal(0.4, frame, channel))
    occurrences = aba_occurrences()
    segments = tuple(
        SequenceSourceSegment.from_occurrence(
            occurrence, a if occurrence.source_path == "A.ts" else b)
        for occurrence in occurrences)
    sequence = SequencePcmReader(segments)
    plan = resolve_monitor_audio_plan(
        MusicChoice(mode=AudioMode.ORIGINAL), 8 * OUTPUT_RATE,
        source_has_audio=True, preset_key="master")
    stream = AudioStream(
        LiveAudioMapping(
            plan, SampleSpan(0, 8 * OUTPUT_RATE, OUTPUT_RATE)),
        source_reader=sequence,
        monitor=MonitorState(level=1, muted=False),
    )
    try:
        stream.start()

        first = emitted_at(stream, 0)
        assert first.output_start == 0
        assert first.planned[0] == pytest.approx(source_signal(
            0.1, 10 * OUTPUT_RATE, 0))
        assert first.planned[1] == pytest.approx(source_signal(
            0.1, 10 * OUTPUT_RATE, 1))

        seam_three = emitted_at(stream, 3 * OUTPUT_RATE)
        assert seam_three.planned[0] == pytest.approx(source_signal(
            0.4, 2 * OUTPUT_RATE, 0))
        assert seam_three.planned[0] != 0.0
        assert seam_three.planned[0] != pytest.approx(source_signal(
            0.1, 13 * OUTPUT_RATE, 0)), "A continued through the B seam"

        seam_five = emitted_at(stream, 5 * OUTPUT_RATE)
        assert seam_five.planned[0] == pytest.approx(source_signal(
            0.1, 10 * OUTPUT_RATE, 0))
        assert seam_five.planned[0] != pytest.approx(source_signal(
            0.4, 4 * OUTPUT_RATE, 0)), "B continued through the second A seam"

        crossing = emitted_at(stream, 3 * OUTPUT_RATE - BLOCK_FRAMES // 2)
        assert crossing.frames == BLOCK_FRAMES
        assert crossing.planned[(BLOCK_FRAMES // 2 - 1) * 2] == pytest.approx(
            source_signal(0.1, 13 * OUTPUT_RATE - 1, 0))
        assert crossing.planned[(BLOCK_FRAMES // 2) * 2] == pytest.approx(
            source_signal(0.4, 2 * OUTPUT_RATE, 0))

        before_reads = (len(a.reads), len(b.reads))
        with pytest.raises(ValueError, match="outside"):
            stream.reprime(8 * OUTPUT_RATE)
        assert (len(a.reads), len(b.reads)) == before_reads

        assert [segment.occurrence for segment in segments] == [
            occurrence.id for occurrence in occurrences]
        assert occurrences[0].id != occurrences[2].id
        assert sequence.frames == 8 * OUTPUT_RATE
    finally:
        stop(stream)
    assert a.stop_requests == a.close_requests == 1
    assert b.stop_requests == b.close_requests == 1


def test_finished_time_music_crosses_seams_and_loops_at_six_seconds_in_emitted_pcm():
    passage = SampleSpan(4 * OUTPUT_RATE, 10 * OUTPUT_RATE, OUTPUT_RATE)
    asset = AudioAsset(
        Path("music.wav"), DIGEST, 0, OUTPUT_RATE, 2, 12 * OUTPUT_RATE)
    choice = MusicChoice(
        asset.track, AudioMode.REPLACE, asset, passage,
        ShortTrackPolicy.LOOP, 1, Fraction(0), 0, 0)
    plan = resolve_monitor_audio_plan(
        choice, 8 * OUTPUT_RATE,
        source_has_audio=True, preset_key="master")
    music = FakeReader(
        frames=12 * OUTPUT_RATE,
        value=lambda frame, channel: source_signal(0.2, frame, channel))
    stream = AudioStream(
        LiveAudioMapping(
            plan, SampleSpan(0, 8 * OUTPUT_RATE, OUTPUT_RATE)),
        music_reader=music,
        monitor=MonitorState(level=1, muted=False),
    )
    try:
        stream.start()
        expected = {
            0: 4,
            3 * OUTPUT_RATE: 7,
            5 * OUTPUT_RATE: 9,
            6 * OUTPUT_RATE: 4,
        }
        for output_sample, music_second in expected.items():
            block = emitted_at(stream, output_sample)
            assert block.planned[0] == pytest.approx(source_signal(
                0.2, music_second * OUTPUT_RATE, 0))
            assert block.planned[1] == pytest.approx(source_signal(
                0.2, music_second * OUTPUT_RATE, 1))
            assert block.planned[0] != 0.0
        assert emitted_at(stream, 3 * OUTPUT_RATE).planned[0] != pytest.approx(
            source_signal(0.2, 4 * OUTPUT_RATE, 0)), (
                "music restarted at the first source seam")
        assert emitted_at(stream, 5 * OUTPUT_RATE).planned[0] != pytest.approx(
            source_signal(0.2, 4 * OUTPUT_RATE, 0)), (
                "music restarted at the second source seam")
    finally:
        stop(stream)


def test_dvr_twelve_to_eighteen_and_music_four_to_ten_keep_independent_origins():
    occurrence = sequence_occurrence(0, 12, 6, 0, "DVR")
    source = FakeReader(
        frames=20 * OUTPUT_RATE,
        value=lambda frame, channel: source_signal(0.1, frame, channel))
    sequence = SequencePcmReader((
        SequenceSourceSegment.from_occurrence(occurrence, source),))
    passage = SampleSpan(4 * OUTPUT_RATE, 10 * OUTPUT_RATE, OUTPUT_RATE)
    asset = AudioAsset(
        Path("music.wav"), DIGEST, 0, OUTPUT_RATE, 2, 12 * OUTPUT_RATE)
    choice = MusicChoice(
        asset.track, AudioMode.MIX, asset, passage,
        ShortTrackPolicy.LOOP, Fraction(1, 2), Fraction(1, 2), 0, 0)
    plan = resolve_monitor_audio_plan(
        choice, 6 * OUTPUT_RATE,
        source_has_audio=True, preset_key="master")
    music = FakeReader(
        frames=12 * OUTPUT_RATE,
        value=lambda frame, channel: source_signal(0.4, frame, channel))
    stream = AudioStream(
        LiveAudioMapping(
            plan, SampleSpan(0, 6 * OUTPUT_RATE, OUTPUT_RATE)),
        source_reader=sequence, music_reader=music,
        monitor=MonitorState(level=1, muted=False),
    )
    try:
        stream.start()
        for output_second, dvr_second, music_second in (
                (0, 12, 4), (1, 13, 5), (5, 17, 9)):
            block = emitted_at(stream, output_second * OUTPUT_RATE)
            expected_left = (
                source_signal(0.1, dvr_second * OUTPUT_RATE, 0)
                + source_signal(0.4, music_second * OUTPUT_RATE, 0)
            ) / 2
            expected_right = (
                source_signal(0.1, dvr_second * OUTPUT_RATE, 1)
                + source_signal(0.4, music_second * OUTPUT_RATE, 1)
            ) / 2
            assert block.planned[0] == pytest.approx(expected_left)
            assert block.planned[1] == pytest.approx(expected_right)
            assert block.planned[0] != 0.0
    finally:
        stop(stream)


def test_sequence_segments_preserve_compiler_cumulative_sample_boundaries():
    from flightdvr.assembly import Item
    from flightdvr.sequence_plan import (
        OccurrenceId, SequenceOccurrence, SequencePlan, TimeSpan,
    )

    duration = Fraction(7, 5 * OUTPUT_RATE)  # 1.4 samples per occurrence
    first_output = TimeSpan(Fraction(0), duration)
    second_output = TimeSpan(duration, 2 * duration)
    first = SequenceOccurrence(
        OccurrenceId("cumulative", 0), Item("A", "first"), "A.ts",
        TimeSpan(Fraction(0), duration), first_output, first_output,
        SampleSpan(0, 1, OUTPUT_RATE))
    second = SequenceOccurrence(
        OccurrenceId("cumulative", 1), Item("B", "second"), "B.ts",
        TimeSpan(Fraction(1), Fraction(1) + duration),
        second_output, second_output, SampleSpan(1, 3, OUTPUT_RATE))
    compiled = SequencePlan("cumulative", (first, second), (0, 1, 3))
    a = FakeReader(frames=10)
    b = FakeReader(frames=OUTPUT_RATE + 10)

    reader = SequencePcmReader(tuple(
        SequenceSourceSegment.from_occurrence(occurrence, leaf)
        for occurrence, leaf in zip(compiled.occurrences, (a, b))))

    assert [(segment.output.start, segment.output.end)
            for segment in reader.segments] == [(0, 1), (1, 3)]
    assert reader.frames == compiled.total_samples == 3
    assert len(reader.read(0, 3, lambda: False)) == 6
    assert a.reads == [(0, 1)]
    assert b.reads == [(OUTPUT_RATE, 2)], (
        "the second occurrence was independently rounded back to one sample")


def test_sequence_reader_allows_only_explicit_known_silence():
    from flightdvr.sequence_plan import OccurrenceId

    source = FakeReader(
        frames=20, value=lambda frame, channel: 0.25 + channel / 10)
    reader = SequencePcmReader((
        SequenceSourceSegment(
            OccurrenceId("silence", 0), SampleSpan(0, 2, OUTPUT_RATE),
            3, source),
        SequenceSourceSegment(
            OccurrenceId("silence", 1), SampleSpan(2, 4, OUTPUT_RATE),
            0, None),
        SequenceSourceSegment(
            OccurrenceId("silence", 2), SampleSpan(4, 6, OUTPUT_RATE),
            8, source),
    ))

    values = tuple(reader.read(0, 6, lambda: False))

    assert values[:4] == pytest.approx((0.25, 0.35, 0.25, 0.35))
    assert values[4:8] == (0.0, 0.0, 0.0, 0.0)
    assert values[8:] == pytest.approx((0.25, 0.35, 0.25, 0.35))


def test_sequence_reader_refuses_malformed_maps_short_sources_and_bad_blocks():
    from flightdvr.sequence_plan import OccurrenceId

    def segment(index, start, end, *, source_start=0, reader=None):
        return SequenceSourceSegment(
            OccurrenceId("bad-map", index),
            SampleSpan(start, end, OUTPUT_RATE), source_start,
            reader or FakeReader(frames=100))

    with pytest.raises(ValueError, match="start at zero"):
        SequencePcmReader((segment(0, 1, 2),))
    with pytest.raises(ValueError, match="gap or overlap"):
        SequencePcmReader((segment(0, 0, 2), segment(1, 3, 4)))
    with pytest.raises(ValueError, match="order"):
        SequencePcmReader((segment(0, 0, 2), segment(0, 2, 4)))
    with pytest.raises(ValueError, match="extends beyond"):
        SequencePcmReader((
            segment(0, 0, 4, source_start=8, reader=FakeReader(frames=10)),))

    class ShortReader(FakeReader):
        def read(self, start, frames, cancelled):
            return [0.5] * (frames * 2 - 1)

    short = SequencePcmReader((
        segment(0, 0, 4, reader=ShortReader(frames=10)),))
    with pytest.raises(ValueError, match="truncated"):
        short.read(0, 4, lambda: False)

    class UnreadableReader(FakeReader):
        def read(self, start, frames, cancelled):
            raise OSError("source became unreadable")

    unreadable = SequencePcmReader((
        segment(0, 0, 4, reader=UnreadableReader(frames=10)),))
    with pytest.raises(OSError, match="unreadable"):
        unreadable.read(0, 4, lambda: False)


def test_sequence_reader_polls_cancellation_across_seams_without_zero_filling():
    from flightdvr.sequence_plan import OccurrenceId

    cancelled = threading.Event()

    class CancellingReader(FakeReader):
        def read(self, start, frames, is_cancelled):
            values = super().read(start, frames, is_cancelled)
            cancelled.set()
            return values

    first = CancellingReader(frames=10)
    second = FakeReader(frames=10)
    reader = SequencePcmReader((
        SequenceSourceSegment(
            OccurrenceId("cancel", 0), SampleSpan(0, 2, OUTPUT_RATE),
            0, first),
        SequenceSourceSegment(
            OccurrenceId("cancel", 1), SampleSpan(2, 4, OUTPUT_RATE),
            0, second),
    ))

    with pytest.raises(RuntimeError, match="cancel"):
        reader.read(0, 4, cancelled.is_set)
    assert second.reads == [], "the reader crossed a seam after cancellation"


def test_sequence_reader_stops_and_closes_each_distinct_leaf_once():
    occurrences = aba_occurrences()
    a = FakeReader(frames=20 * OUTPUT_RATE)
    b = FakeReader(frames=10 * OUTPUT_RATE)
    reader = SequencePcmReader(tuple(
        SequenceSourceSegment.from_occurrence(
            occurrence, a if occurrence.source_path == "A.ts" else b)
        for occurrence in occurrences))

    reader.request_stop()
    reader.request_stop()
    reader.close()
    reader.close()

    assert a.stop_requests == a.close_requests == 1
    assert b.stop_requests == b.close_requests == 1


def test_sequence_reader_stop_unblocks_a_leaf_and_wait_remains_separate():
    occurrence = sequence_occurrence(0, 0, 1, 0, "blocked")
    leaf = BlockingReader(frames=OUTPUT_RATE)
    source = SequencePcmReader((
        SequenceSourceSegment.from_occurrence(occurrence, leaf),))
    plan = resolve_monitor_audio_plan(
        MusicChoice(mode=AudioMode.ORIGINAL), OUTPUT_RATE,
        source_has_audio=True, preset_key="master")
    stream = AudioStream(
        LiveAudioMapping(plan, SampleSpan(0, OUTPUT_RATE, OUTPUT_RATE)),
        source_reader=source)
    stream.start()
    assert leaf.read_started.wait(1)

    stream.request_stop()

    assert leaf.stop_requests == 1
    assert stream.state in (StreamState.STOPPING, StreamState.STOPPED)
    assert stream.wait_stopped(2)
    assert leaf.close_requests == 1


# -- W3: gains and fades change in place --------------------------------------

from flightdvr.audio_stream import parameters_only  # noqa: E402


def w3_plan(level=1, *, frames=12 * BLOCK_FRAMES,
            passage=SampleSpan(4_410, 5_410, 44_100), policy=ShortTrackPolicy.LOOP):
    return music_plan(AudioMode.REPLACE, frames, passage=passage,
                      music_level=level, policy=policy, source=False)


def drain(stream, count):
    stream.resume()
    blocks = []
    while len(blocks) < count:
        try:
            block = stream.pull()
        except Buffering:
            time.sleep(0.002)
            continue
        if block is None:
            break
        blocks.append(block)
    return blocks


def test_a_parameter_update_keeps_readers_position_and_loop_phase():
    """Level 1 -> 1/2 while four blocks are queued and a fifth may be in
    flight. Every block is wholly one gain or the other; the old ones are a
    prefix no longer than the queue plus one; the track positions run on as
    if nothing had happened; and the reader was asked for exactly what an
    untouched stream asks for — no seek, no second reader."""
    passage = SampleSpan(4_410, 5_410, 44_100)       # loops every 1088 frames
    origin = 4_800
    loop = passage.samples_at(OUTPUT_RATE)
    assert loop == 1_088

    def run(update):
        reader = FakeReader(value=lambda frame, channel: float(frame))
        stream = AudioStream(mapping(w3_plan(1, passage=passage)),
                             music_reader=reader, monitor=MonitorState(level=1))
        try:
            stream.start()
            wait_for(lambda: stream.queued_blocks == QUEUE_CAPACITY)
            if update:
                assert stream.update_parameters(
                    w3_plan(Fraction(1, 2), passage=passage))
                assert stream.generation == 0
            blocks = drain(stream, 12)
        finally:
            stop(stream)
        return reader.reads, blocks

    control_reads, _ = run(update=False)
    reads, blocks = run(update=True)

    assert reads == control_reads, "the update made the reader seek"
    gains = []
    for number, block in enumerate(blocks):
        left = tuple(block.planned)[::2]
        expected = [origin + (block.output_start + i) % loop
                    for i in range(block.frames)]
        ratios = {round(value / position, 6)
                  for value, position in zip(left, expected)}
        assert len(ratios) == 1, f"block {number} mixes two gains: {ratios}"
        gains.append(ratios.pop())
    old = gains.index(0.5)
    assert set(gains[:old]) == {1.0} and set(gains[old:]) == {0.5}
    assert old <= QUEUE_CAPACITY + 1, (
        f"{old} blocks carried the old gain; the queue holds {QUEUE_CAPACITY}")


def test_the_block_being_rendered_during_an_update_is_wholly_old():
    """The in-flight barrier. The worker has taken the mapping and is inside
    the music read when the update lands: that block is rendered entirely
    with the old gain, and the next entirely with the new one."""
    reader = BlockingReader(frames=20_000)
    reader.value = lambda frame, channel: float(frame)
    stream = AudioStream(mapping(w3_plan(1, frames=2 * BLOCK_FRAMES)),
                         music_reader=reader, monitor=MonitorState(level=1))
    try:
        stream.start()
        assert reader.read_started.wait(2), "the worker never started a read"
        assert stream.update_parameters(w3_plan(Fraction(1, 2),
                                                frames=2 * BLOCK_FRAMES))
        reader.release.set()
        first, second = drain(stream, 2)
    finally:
        stop(stream)
    first_left = tuple(first.planned)[::2]
    second_left = tuple(second.planned)[::2]
    assert first_left[0] == 4_800.0 and first_left[-1] == 4_800.0 + 479
    assert second_left[0] == (4_800 + 480) * 0.5
    assert second_left[-1] == (4_800 + 959) * 0.5


@pytest.mark.parametrize("change", [
    {"passage": SampleSpan(4_411, 5_410, 44_100)},
    {"passage": SampleSpan(4_410, 5_409, 44_100)},
    {"policy": ShortTrackPolicy.PLAY_ONCE},
    {"frames": 13 * BLOCK_FRAMES},
])
def test_anything_but_a_gain_or_fade_is_refused_and_changes_nothing(change):
    reader = FakeReader(value=lambda frame, channel: float(frame))
    before = w3_plan(1)
    stream = AudioStream(mapping(before), music_reader=reader,
                         monitor=MonitorState(level=1))
    kwargs = {"level": Fraction(1, 2), **change}
    level = kwargs.pop("level")
    assert not stream.update_parameters(w3_plan(level, **kwargs))
    assert stream.audio_plan == before
    stop(stream)


def test_a_different_source_interval_is_different_material():
    plan = w3_plan(1)
    assert not parameters_only(mapping(plan, 100), mapping(plan, 101))
    assert parameters_only(mapping(plan, 100),
                           mapping(w3_plan(Fraction(1, 2)), 100))


def test_a_stopped_stream_takes_no_update():
    stream = AudioStream(mapping(w3_plan(1)), music_reader=FakeReader(),
                         monitor=MonitorState(level=1))
    stop(stream)
    assert not stream.update_parameters(w3_plan(Fraction(1, 2)))
