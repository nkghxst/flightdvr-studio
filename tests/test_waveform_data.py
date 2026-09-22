# FlightDVR Studio - browse, trim and convert HDZero goggle DVR footage.
# Copyright (C) 2026 Isadu Nkemi
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.

"""Independent behavioural checks for bounded waveform accumulation."""

from __future__ import annotations

import struct
import sys
from dataclasses import fields, is_dataclass

import pytest

from flightdvr.audio_plan import AudioAsset
from flightdvr.waveform_data import (
    WaveformAccumulator,
    WaveformAssetKey,
    WaveformDataError,
    WaveformEnvelope,
    WaveformInspection,
    WaveformRequest,
    WaveformStatus,
)


DIGEST = "0123456789abcdef" * 4


def _asset(channels: int, samples: int) -> AudioAsset:
    return AudioAsset(
        track="music.wav",
        sha256=DIGEST,
        stream_index=0,
        sample_rate=48_000,
        channels=channels,
        decoded_samples=samples,
    )


def _pcm(frames: list[tuple[float, ...]]) -> bytes:
    if not frames:
        return b""
    return b"".join(struct.pack("<" + "f" * len(frame), *frame) for frame in frames)


def _envelope(
    frames: list[tuple[float, ...]],
    *,
    max_bins: int = 4096,
    leaf_frames: int = 1024,
    chunks: list[int] | None = None,
) -> WaveformEnvelope:
    accumulator = WaveformAccumulator(
        len(frames[0]), max_bins=max_bins, leaf_frames=leaf_frames
    )
    payload = _pcm(frames)
    if chunks is None:
        accumulator.consume(payload)
    else:
        cursor = 0
        for size in chunks:
            accumulator.consume(payload[cursor : cursor + size])
            cursor += size
        accumulator.consume(payload[cursor:])
    return accumulator.finish(WaveformAssetKey.from_asset(_asset(len(frames[0]), len(frames))))


def test_signed_per_channel_impulses_and_quiet_gaps_are_preserved():
    frames = [
        (-0.5, 0.0),
        (0.25, 0.0),
        (0.0, 0.75),
        (0.0, -0.25),
        (0.0, 0.0),
    ]
    result = _envelope(frames, leaf_frames=2)

    assert [(item.start_frame, item.end_frame) for item in result.bins] == [
        (0, 2),
        (2, 4),
        (4, 5),
    ]
    assert result.bins[0].minimum == (-0.5, 0.0)
    assert result.bins[0].maximum == (0.25, 0.0)
    assert result.bins[1].minimum == (0.0, -0.25)
    assert result.bins[1].maximum == (0.0, 0.75)
    assert result.bins[2].minimum == (0.0, 0.0)
    assert result.bins[2].maximum == (0.0, 0.0)


def test_split_bytes_and_decoder_chunking_do_not_change_extrema():
    frames = [(float(index - 4), float(4 - index)) for index in range(11)]
    whole = _envelope(frames, leaf_frames=3)
    split = _envelope(
        frames,
        leaf_frames=3,
        chunks=[1, 2, 5, 7, 11, 3, 19],
    )

    assert split == whole


def test_final_partial_bin_is_explicit_but_partial_pcm_frame_is_invalid():
    frames = [(float(index), -float(index)) for index in range(10)]
    result = _envelope(frames, leaf_frames=4)
    assert (result.bins[-1].start_frame, result.bins[-1].end_frame) == (8, 10)

    accumulator = WaveformAccumulator(2, leaf_frames=4)
    accumulator.consume(_pcm(frames) + b"\x00")
    with pytest.raises(WaveformDataError, match="partial frame"):
        accumulator.finish(WaveformAssetKey.from_asset(_asset(2, 10)))


def test_multichannel_assets_keep_every_signed_channel():
    frames = [
        (-1.0, 0.5, 2.0),
        (1.0, -0.5, -2.0),
        (0.25, 3.0, -4.0),
    ]
    result = _envelope(frames, leaf_frames=2)

    assert result.asset.channels == 3
    assert result.bins[0].minimum == (-1.0, -0.5, -2.0)
    assert result.bins[0].maximum == (1.0, 0.5, 2.0)
    assert result.bins[1].minimum == (0.25, 3.0, -4.0)


def test_balanced_coarsening_has_literal_levels_and_chunk_invariance():
    frames = [(float(index), -float(index)) for index in range(33)]
    whole = _envelope(frames, max_bins=2, leaf_frames=1)
    split = _envelope(
        frames,
        max_bins=2,
        leaf_frames=1,
        chunks=[1, 3, 2, 9, 4, 5, 7, 11, 13],
    )

    assert split == whole
    assert [(item.start_frame, item.end_frame) for item in whole.bins] == [
        (0, 32),
        (32, 33),
    ]
    assert whole.bins[0].minimum == (0.0, -31.0)
    assert whole.bins[0].maximum == (31.0, 0.0)
    assert whole.bins[1].minimum == (32.0, -32.0)


def test_nonfinite_pcm_is_unavailable_data_not_fabricated_silence():
    accumulator = WaveformAccumulator(2)
    accumulator.consume(struct.pack("<ff", float("nan"), 0.0))

    with pytest.raises(WaveformDataError, match="non-finite"):
        accumulator.finish(WaveformAssetKey.from_asset(_asset(2, 1)))


def test_accumulator_retains_bounded_structure_and_no_whole_pcm():
    frames = [(float(index % 5), -float(index % 7)) for index in range(101)]
    accumulator = WaveformAccumulator(2, max_bins=4, leaf_frames=2)
    payload = _pcm(frames)
    accumulator.consume(payload)

    assert accumulator.retained_pcm_bytes < 8
    assert accumulator.active_bin_count <= 6
    result = accumulator.finish(WaveformAssetKey.from_asset(_asset(2, len(frames))))
    assert len(result.bins) <= 4

    def deep_size(value, seen=None):
        seen = set() if seen is None else seen
        identity = id(value)
        if identity in seen:
            return 0
        seen.add(identity)
        total = sys.getsizeof(value)
        if is_dataclass(value):
            for field in fields(value):
                total += deep_size(getattr(value, field.name), seen)
        elif isinstance(value, (tuple, list)):
            total += sum(deep_size(item, seen) for item in value)
        return total

    assert deep_size(result) > len(payload)


def test_presentation_coarsening_reuses_the_immutable_envelope():
    frames = [(float(index), -float(index)) for index in range(16)]
    original = _envelope(frames, leaf_frames=2)
    coarser = original.coarsen(2)

    assert original.coverage_end == coarser.coverage_end == 16
    assert original.asset == coarser.asset
    assert len(original.bins) == 8
    assert len(coarser.bins) == 2
    assert original.bins[0].minimum == (0.0, -1.0)


def test_combined_result_requires_the_envelope_key_of_its_new_asset():
    asset = _asset(2, 2)
    request = WaveformRequest(generation=4, request_key="selection-4")
    envelope = _envelope([(0.0, 0.0), (1.0, -1.0)], leaf_frames=1)
    result = WaveformInspection.ready(request, asset, envelope)
    assert result.status is WaveformStatus.READY
    assert result.envelope is envelope

    other_asset = AudioAsset(
        track=asset.track,
        sha256="fedcba9876543210" * 4,
        stream_index=asset.stream_index,
        sample_rate=asset.sample_rate,
        channels=asset.channels,
        decoded_samples=asset.decoded_samples,
    )
    with pytest.raises(WaveformDataError, match="does not match"):
        WaveformInspection.ready(request, other_asset, envelope)

    unavailable = WaveformInspection.unavailable(request, asset, "non-finite PCM")
    assert unavailable.status is WaveformStatus.WAVEFORM_UNAVAILABLE
    assert unavailable.envelope is None
