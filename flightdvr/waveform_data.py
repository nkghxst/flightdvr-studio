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

"""Bounded, immutable waveform data for one inspected audio asset.

The accumulator consumes decoded native-rate float PCM once and retains only
per-bin extrema.  It deliberately has no presentation or Qt dependencies:
selection, painting and playback can consume the immutable envelope without
re-decoding the source.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .audio_plan import AudioAsset


MAX_BINS = 4096
LEAF_FRAMES = 1024
FLOAT_BYTES = 4


class WaveformDataError(ValueError):
    """The decoded PCM cannot produce a trustworthy waveform envelope."""


class WaveformStatus(str, Enum):
    READY = "ready"
    WAVEFORM_UNAVAILABLE = "waveform_unavailable"


@dataclass(frozen=True, slots=True)
class WaveformRequest:
    """The request identity carried through the optional combined route."""

    generation: int
    request_key: str
    max_bins: int = MAX_BINS
    leaf_frames: int = LEAF_FRAMES

    def __post_init__(self) -> None:
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("waveform generation must be a non-negative integer")
        if not str(self.request_key).strip():
            raise ValueError("waveform request key cannot be empty")
        if type(self.max_bins) is not int or self.max_bins <= 0:
            raise ValueError("waveform bin limit must be positive")
        if type(self.leaf_frames) is not int or self.leaf_frames <= 0:
            raise ValueError("waveform leaf width must be positive")


@dataclass(frozen=True, slots=True)
class WaveformAssetKey:
    """Content and decoder metadata that identifies an envelope's asset."""

    sha256: str
    stream_index: int
    sample_rate: int
    channels: int
    decoded_samples: int

    @classmethod
    def from_asset(cls, asset: AudioAsset) -> "WaveformAssetKey":
        if not isinstance(asset, AudioAsset):
            raise TypeError("waveform asset key needs an AudioAsset")
        return cls(
            sha256=asset.sha256,
            stream_index=asset.stream_index,
            sample_rate=asset.sample_rate,
            channels=asset.channels,
            decoded_samples=asset.decoded_samples,
        )

    def __post_init__(self) -> None:
        digest = str(self.sha256).lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("waveform identity must be a full SHA-256")
        if type(self.stream_index) is not int or self.stream_index < 0:
            raise ValueError("waveform stream index must be non-negative")
        if type(self.sample_rate) is not int or self.sample_rate <= 0:
            raise ValueError("waveform sample rate must be positive")
        if type(self.channels) is not int or self.channels <= 0:
            raise ValueError("waveform channel count must be positive")
        if type(self.decoded_samples) is not int or self.decoded_samples <= 0:
            raise ValueError("waveform sample count must be positive")
        object.__setattr__(self, "sha256", digest)


@dataclass(frozen=True, slots=True)
class WaveformBin:
    """Extrema for one half-open native-frame interval."""

    start_frame: int
    end_frame: int
    minimum: tuple[float, ...]
    maximum: tuple[float, ...]

    def __post_init__(self) -> None:
        if type(self.start_frame) is not int or type(self.end_frame) is not int:
            raise WaveformDataError("waveform coordinates must be integers")
        if self.start_frame < 0 or self.end_frame <= self.start_frame:
            raise WaveformDataError("waveform bins must be non-empty and half-open")
        minimum = tuple(float(value) for value in self.minimum)
        maximum = tuple(float(value) for value in self.maximum)
        if not minimum or len(minimum) != len(maximum):
            raise WaveformDataError("waveform extrema need one pair per channel")
        for low, high in zip(minimum, maximum):
            if not math.isfinite(low) or not math.isfinite(high) or low > high:
                raise WaveformDataError("waveform extrema must be finite and ordered")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    @classmethod
    def merge(cls, left: "WaveformBin", right: "WaveformBin") -> "WaveformBin":
        if left.end_frame != right.start_frame:
            raise WaveformDataError("waveform bins cannot have a gap or overlap")
        if len(left.minimum) != len(right.minimum):
            raise WaveformDataError("waveform bins have different channel counts")
        return cls(
            left.start_frame,
            right.end_frame,
            tuple(min(a, b) for a, b in zip(left.minimum, right.minimum)),
            tuple(max(a, b) for a, b in zip(left.maximum, right.maximum)),
        )


@dataclass(frozen=True, slots=True)
class WaveformEnvelope:
    """A complete or bounded overview with no retained PCM samples."""

    asset: WaveformAssetKey
    coverage_start: int
    coverage_end: int
    bins: tuple[WaveformBin, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.asset, WaveformAssetKey):
            raise TypeError("waveform envelope needs an asset key")
        if type(self.coverage_start) is not int or type(self.coverage_end) is not int:
            raise WaveformDataError("waveform coverage coordinates must be integers")
        if self.coverage_start < 0 or self.coverage_end <= self.coverage_start:
            raise WaveformDataError("waveform coverage must be non-empty and half-open")
        if self.coverage_end > self.asset.decoded_samples:
            raise WaveformDataError("waveform coverage exceeds the asset")
        bins = tuple(self.bins)
        if not bins or len(bins) > MAX_BINS:
            raise WaveformDataError("waveform envelope has an invalid bin count")
        cursor = self.coverage_start
        for item in bins:
            if not isinstance(item, WaveformBin):
                raise TypeError("waveform envelope bins must be WaveformBin values")
            if len(item.minimum) != self.asset.channels:
                raise WaveformDataError("waveform bin channel count disagrees with asset")
            if item.start_frame != cursor or item.end_frame > self.coverage_end:
                raise WaveformDataError("waveform bins must exactly cover their envelope")
            cursor = item.end_frame
        if cursor != self.coverage_end:
            raise WaveformDataError("waveform bins leave a gap in their envelope")
        object.__setattr__(self, "bins", bins)

    def coarsen(self, max_bins: int) -> "WaveformEnvelope":
        """Return a coarser view without reacquiring or re-decoding PCM."""

        if type(max_bins) is not int or max_bins <= 0:
            raise ValueError("waveform bin limit must be positive")
        if len(self.bins) <= max_bins:
            return self
        bins = list(self.bins)
        while len(bins) > max_bins:
            reduced: list[WaveformBin] = []
            index = 0
            while index + 1 < len(bins):
                reduced.append(WaveformBin.merge(bins[index], bins[index + 1]))
                index += 2
            if index < len(bins):
                reduced.append(bins[index])
            bins = reduced
        return WaveformEnvelope(
            asset=self.asset,
            coverage_start=self.coverage_start,
            coverage_end=self.coverage_end,
            bins=tuple(bins),
        )


@dataclass(frozen=True, slots=True)
class WaveformInspection:
    """One atomic opt-in result: request, asset and waveform status together."""

    request: WaveformRequest
    asset: AudioAsset
    status: WaveformStatus
    envelope: WaveformEnvelope | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request, WaveformRequest):
            raise TypeError("waveform inspection needs a request")
        if not isinstance(self.asset, AudioAsset):
            raise TypeError("waveform inspection needs a valid audio asset")
        status = WaveformStatus(self.status)
        object.__setattr__(self, "status", status)
        key = WaveformAssetKey.from_asset(self.asset)
        if status is WaveformStatus.READY:
            if self.envelope is None or self.envelope.asset != key:
                raise WaveformDataError("waveform result does not match its asset")
            if self.reason is not None:
                raise WaveformDataError("a ready waveform cannot carry a failure reason")
        else:
            if self.envelope is not None:
                raise WaveformDataError("an unavailable waveform cannot carry an envelope")
            if not str(self.reason or "").strip():
                raise WaveformDataError("an unavailable waveform needs a reason")

    @classmethod
    def ready(
        cls,
        request: WaveformRequest,
        asset: AudioAsset,
        envelope: WaveformEnvelope,
    ) -> "WaveformInspection":
        return cls(request, asset, WaveformStatus.READY, envelope)

    @classmethod
    def unavailable(
        cls,
        request: WaveformRequest,
        asset: AudioAsset,
        reason: str,
    ) -> "WaveformInspection":
        return cls(request, asset, WaveformStatus.WAVEFORM_UNAVAILABLE, None, reason)


@dataclass(slots=True)
class _MutableBin:
    start_frame: int
    end_frame: int
    minimum: list[float]
    maximum: list[float]
    level: int

    def freeze(self) -> WaveformBin:
        return WaveformBin(
            self.start_frame,
            self.end_frame,
            tuple(self.minimum),
            tuple(self.maximum),
        )

    @classmethod
    def merge(cls, left: "_MutableBin", right: "_MutableBin") -> "_MutableBin":
        if left.end_frame != right.start_frame or left.level != right.level:
            raise WaveformDataError("waveform coarsening needs adjacent equal-level bins")
        return cls(
            left.start_frame,
            right.end_frame,
            [min(a, b) for a, b in zip(left.minimum, right.minimum)],
            [max(a, b) for a, b in zip(left.maximum, right.maximum)],
            left.level + 1,
        )


class WaveformAccumulator:
    """Consume packed native f32le PCM into bounded balanced extrema bins."""

    def __init__(
        self,
        channels: int,
        *,
        max_bins: int = MAX_BINS,
        leaf_frames: int = LEAF_FRAMES,
    ) -> None:
        if type(channels) is not int or channels <= 0:
            raise ValueError("waveform channel count must be positive")
        if type(max_bins) is not int or max_bins <= 0:
            raise ValueError("waveform bin limit must be positive")
        if type(leaf_frames) is not int or leaf_frames <= 0:
            raise ValueError("waveform leaf width must be positive")
        self.channels = channels
        self.max_bins = max_bins
        self.leaf_frames = leaf_frames
        self._frame_bytes = channels * FLOAT_BYTES
        self._format = "<" + ("f" * channels)
        self._partial = bytearray()
        self._history: list[_MutableBin] = []
        self._transition: _MutableBin | None = None
        self._level = 0
        self._current_start = 0
        self._current_count = 0
        self._current_min: list[float] = []
        self._current_max: list[float] = []
        self._total_frames = 0
        self._error: str | None = None
        self._finished = False

    @property
    def retained_pcm_bytes(self) -> int:
        """Bytes retained from an incomplete frame; complete PCM is discarded."""

        return len(self._partial)

    @property
    def active_bin_count(self) -> int:
        """Object count retained while decoding, including the active bin."""

        return len(self._history) + (1 if self._transition is not None else 0) + (
            1 if self._current_count else 0
        )

    @property
    def decoded_frames(self) -> int:
        return self._total_frames

    def consume(self, data: bytes | bytearray | memoryview) -> None:
        if self._finished:
            raise RuntimeError("waveform accumulator is already finished")
        if self._error is not None:
            return
        if not data:
            return
        self._partial.extend(data)
        complete_bytes = len(self._partial) - (len(self._partial) % self._frame_bytes)
        if complete_bytes == 0:
            return
        payload = bytes(self._partial[:complete_bytes])
        del self._partial[:complete_bytes]
        for values in struct.iter_unpack(self._format, payload):
            if not all(math.isfinite(value) for value in values):
                self._error = "decoded PCM contains a non-finite sample"
                return
            self._consume_frame(values)

    def _consume_frame(self, values: Iterable[float]) -> None:
        values = tuple(values)
        if self._current_count == 0:
            self._current_start = self._total_frames
            self._current_min = list(values)
            self._current_max = list(values)
        else:
            for index, value in enumerate(values):
                self._current_min[index] = min(self._current_min[index], value)
                self._current_max[index] = max(self._current_max[index], value)
        self._current_count += 1
        self._total_frames += 1
        if self._current_count >= self.leaf_frames << self._level:
            self._append_completed(self._take_current())

    def _take_current(self) -> _MutableBin:
        result = _MutableBin(
            self._current_start,
            self._total_frames,
            self._current_min,
            self._current_max,
            self._level,
        )
        self._current_count = 0
        self._current_min = []
        self._current_max = []
        return result

    def _append_completed(self, item: _MutableBin) -> None:
        if self._transition is not None:
            item = _MutableBin.merge(self._transition, item)
            self._transition = None
            self._level = item.level
        elif self._history and item.level != self._history[-1].level:
            raise WaveformDataError("waveform bins changed level without a balanced merge")
        self._history.append(item)
        if len(self._history) > self.max_bins:
            self._coarsen_history()

    def _coarsen_history(self) -> None:
        source = self._history
        if not source:
            return
        old_level = source[0].level
        if any(item.level != old_level for item in source):
            raise WaveformDataError("waveform history contains mixed coarsening levels")
        reduced: list[_MutableBin] = []
        index = 0
        while index + 1 < len(source):
            reduced.append(_MutableBin.merge(source[index], source[index + 1]))
            index += 2
        self._transition = source[index] if index < len(source) else None
        self._history = reduced
        if self._transition is None:
            self._level = old_level + 1
        else:
            # The unpaired old-level tail is matched with the next completed
            # old-level bin.  Future accumulation therefore uses the old width
            # for exactly that one transition bin, then advances one level.
            self._level = old_level

    def _flush(self) -> None:
        if self._current_count:
            current = self._take_current()
            if self._transition is not None:
                self._history.append(_MutableBin.merge(self._transition, current))
                self._transition = None
                self._level = current.level + 1
            else:
                self._history.append(current)
        if self._transition is not None:
            self._history.append(self._transition)
            self._transition = None
        while len(self._history) > self.max_bins:
            self._coarsen_history()
            if self._transition is not None:
                self._history.append(self._transition)
                self._transition = None

    def finish(self, asset: WaveformAssetKey) -> WaveformEnvelope:
        if self._finished:
            raise RuntimeError("waveform accumulator is already finished")
        self._finished = True
        if self._partial:
            raise WaveformDataError("decoded PCM ends with a partial frame")
        if self._error is not None:
            raise WaveformDataError(self._error)
        if not isinstance(asset, WaveformAssetKey):
            raise TypeError("waveform accumulator needs an asset key")
        if self._total_frames != asset.decoded_samples:
            raise WaveformDataError("waveform extent disagrees with decoded audio")
        self._flush()
        if not self._history:
            raise WaveformDataError("decoded PCM contains no samples")
        envelope = WaveformEnvelope(
            asset=asset,
            coverage_start=0,
            coverage_end=self._total_frames,
            bins=tuple(item.freeze() for item in self._history),
        )
        self._history.clear()
        self._transition = None
        return envelope
