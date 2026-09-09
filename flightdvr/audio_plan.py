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

"""Immutable audio choices and sample-exact output decisions.

There is deliberately no FFmpeg or Qt here. Export and a future live preview
must consume the same resolved value rather than each deciding where a loop or
fade lands. All intervals are integer, half-open sample spans; displayed
decimal seconds are an input/output concern, not stored timing state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from pathlib import Path


OUTPUT_RATE = 48_000
OUTPUT_CHANNELS = 2


class AudioMode(str, Enum):
    ORIGINAL = "original"
    NO_SOUND = "no_sound"
    REPLACE = "replace"
    MIX = "mix"


class ShortTrackPolicy(str, Enum):
    PLAY_ONCE = "play_once"
    LOOP = "loop"


def _fraction(value) -> Fraction:
    if isinstance(value, float):
        value = str(value)
    return Fraction(value)


def round_samples(value: Fraction) -> int:
    """Round a positive rational to nearest, with exact halves rounded up."""
    value = Fraction(value)
    if value < 0:
        raise ValueError("sample positions cannot be negative")
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


@dataclass(frozen=True)
class SampleSpan:
    """One non-empty half-open interval on a named sample clock."""

    start: int
    end: int
    rate: int

    def __post_init__(self) -> None:
        if type(self.start) is not int or type(self.end) is not int:
            raise ValueError("sample coordinates must be integers")
        if type(self.rate) is not int or self.rate <= 0:
            raise ValueError("sample rate must be a positive integer")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("a sample span must be non-empty and half-open")

    @property
    def samples(self) -> int:
        return self.end - self.start

    def samples_at(self, rate: int) -> int:
        if type(rate) is not int or rate <= 0:
            raise ValueError("sample rate must be a positive integer")
        return round_samples(Fraction(self.samples * rate, self.rate))


@dataclass(frozen=True)
class AudioAsset:
    """A validated local audio stream and the content identity submitted."""

    track: Path
    sha256: str
    stream_index: int
    sample_rate: int
    channels: int
    decoded_samples: int

    def __post_init__(self) -> None:
        track = Path(self.track)
        digest = str(self.sha256).lower()
        if not str(track).strip() or str(track) == ".":
            raise ValueError("an audio track path cannot be empty")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("audio identity must be a full SHA-256")
        if type(self.stream_index) is not int or self.stream_index < 0:
            raise ValueError("audio stream index must be non-negative")
        if type(self.sample_rate) is not int or self.sample_rate <= 0:
            raise ValueError("audio sample rate must be positive")
        if type(self.channels) is not int or self.channels <= 0:
            raise ValueError("audio channel count must be positive")
        if type(self.decoded_samples) is not int or self.decoded_samples <= 0:
            raise ValueError("audio must contain decoded samples")
        object.__setattr__(self, "track", track)
        object.__setattr__(self, "sha256", digest)


@dataclass(frozen=True)
class MusicChoice:
    """One output's submitted export-audio choices.

    ``track`` stays first for the S1 ``MusicChoice(Path(...))`` contract. A
    track-only value is an unresolved Replace choice: probing supplies its
    asset and passage before it can become an executable plan. No track and no
    mode is the compatibility sentinel that keeps the old ``keep_audio`` path.
    """

    track: Path | None = None
    mode: AudioMode | None = None
    asset: AudioAsset | None = None
    passage: SampleSpan | None = None
    short_track: ShortTrackPolicy = ShortTrackPolicy.LOOP
    music_level: Fraction = Fraction(1)
    dvr_level: Fraction = Fraction(1, 4)
    fade_in_samples: int = OUTPUT_RATE
    fade_out_samples: int = 2 * OUTPUT_RATE

    def __post_init__(self) -> None:
        track = None if self.track is None else Path(self.track)
        if track is not None and (not str(track).strip() or str(track) == "."):
            raise ValueError("a music track path cannot be empty")
        mode = None if self.mode is None else AudioMode(self.mode)
        policy = ShortTrackPolicy(self.short_track)
        music = _fraction(self.music_level)
        dvr = _fraction(self.dvr_level)
        if not 0 <= music <= 1 or not 0 <= dvr <= 1:
            raise ValueError("audio levels must be between zero and one")
        if (type(self.fade_in_samples) is not int
                or type(self.fade_out_samples) is not int
                or self.fade_in_samples < 0 or self.fade_out_samples < 0):
            raise ValueError("fade lengths must be non-negative integer samples")
        if self.asset is not None:
            if track is None:
                track = self.asset.track
            elif track != self.asset.track:
                raise ValueError("music track and validated asset disagree")
        if mode is None and track is not None:
            mode = AudioMode.REPLACE
        if mode in (AudioMode.REPLACE, AudioMode.MIX) and track is None:
            raise ValueError("Replace and Mix need a music track")
        if mode in (AudioMode.ORIGINAL, AudioMode.NO_SOUND):
            if track is not None or self.asset is not None or self.passage is not None:
                raise ValueError(f"{mode.value} cannot carry a music track")
        if self.passage is not None and self.asset is None:
            raise ValueError("a selected passage needs a validated audio asset")
        if self.asset is not None and self.passage is not None:
            if self.passage.rate != self.asset.sample_rate:
                raise ValueError("music passage uses the wrong sample clock")
            if self.passage.end > self.asset.decoded_samples:
                raise ValueError("music passage extends beyond the decoded track")
        object.__setattr__(self, "track", track)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "short_track", policy)
        object.__setattr__(self, "music_level", music)
        object.__setattr__(self, "dvr_level", dvr)

    @property
    def configured(self) -> bool:
        return self.mode is not None


@dataclass(frozen=True)
class OutputAudioPlan:
    mode: AudioMode
    output: SampleSpan
    source_has_audio: bool
    asset: AudioAsset | None = None
    passage: SampleSpan | None = None
    short_track: ShortTrackPolicy | None = None
    music_samples: int = 0
    audible_samples: int = 0
    fade_in_samples: int = 0
    fade_out_samples: int = 0
    music_gain: Fraction = Fraction(0)
    dvr_gain: Fraction = Fraction(0)

    @property
    def identity(self) -> str:
        value = {
            "mode": self.mode.value,
            "output": [self.output.start, self.output.end, self.output.rate],
            "source_has_audio": self.source_has_audio,
            "asset": None if self.asset is None else {
                "path": str(self.asset.track), "sha256": self.asset.sha256,
                "stream": self.asset.stream_index, "rate": self.asset.sample_rate,
                "channels": self.asset.channels,
                "samples": self.asset.decoded_samples,
            },
            "passage": None if self.passage is None else [
                self.passage.start, self.passage.end, self.passage.rate],
            "policy": None if self.short_track is None else self.short_track.value,
            "music_samples": self.music_samples,
            "audible_samples": self.audible_samples,
            "fades": [self.fade_in_samples, self.fade_out_samples],
            "gains": [str(self.music_gain), str(self.dvr_gain)],
        }
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def music_position(self, output_sample: int) -> int | None:
        """Canonical passage-relative music sample, or planned silence."""
        if not 0 <= output_sample < self.output.samples:
            raise ValueError("output sample is outside the plan")
        if output_sample >= self.audible_samples or self.music_samples <= 0:
            return None
        if self.short_track is ShortTrackPolicy.LOOP:
            return output_sample % self.music_samples
        return output_sample

    def envelope(self, output_sample: int) -> Fraction:
        if self.music_position(output_sample) is None:
            return Fraction(0)
        gain = Fraction(1)
        if self.fade_in_samples:
            gain = min(gain, Fraction(output_sample, self.fade_in_samples))
        if self.fade_out_samples:
            gain = min(gain, Fraction(
                self.audible_samples - output_sample, self.fade_out_samples))
        return gain


def _effective_fades(audible: int, fade_in: int, fade_out: int) -> tuple[int, int]:
    fade_in = min(audible, fade_in)
    fade_out = min(audible, fade_out)
    total = fade_in + fade_out
    if total <= audible or total == 0:
        return fade_in, fade_out
    effective_in = audible * fade_in // total
    return effective_in, audible - effective_in


def resolve_audio_plan(
    choice: MusicChoice,
    output_samples: int,
    *,
    source_has_audio: bool,
    preset_key: str,
    joined: bool = False,
    bundle: bool = False,
) -> OutputAudioPlan:
    """Resolve choices for the bounded S2 one-range Master export."""
    if not choice.configured:
        raise ValueError("an unconfigured audio choice uses the legacy export path")
    if type(output_samples) is not int or output_samples <= 0:
        raise ValueError("finished output must contain a positive integer sample count")
    if preset_key != "master" or joined or bundle:
        context = "delivery bundle" if bundle else "Assembly" if joined else preset_key
        raise ValueError(f"music/audio choices are not supported for {context} in S2")
    output = SampleSpan(0, output_samples, OUTPUT_RATE)
    mode = AudioMode(choice.mode)
    if mode is AudioMode.ORIGINAL:
        return OutputAudioPlan(mode, output, source_has_audio,
                               dvr_gain=Fraction(int(source_has_audio)))
    if mode is AudioMode.NO_SOUND:
        return OutputAudioPlan(mode, output, source_has_audio)
    if choice.asset is None or choice.passage is None:
        raise ValueError("music must be validated and have a selected passage")
    music_samples = choice.passage.samples_at(OUTPUT_RATE)
    if music_samples <= 0:
        raise ValueError("selected music passage is empty on the output clock")
    audible = (output_samples if choice.short_track is ShortTrackPolicy.LOOP
               else min(output_samples, music_samples))
    fade_in, fade_out = _effective_fades(
        audible, choice.fade_in_samples, choice.fade_out_samples)
    if mode is AudioMode.MIX and source_has_audio:
        divisor = max(Fraction(1), choice.music_level + choice.dvr_level)
        music_gain = choice.music_level / divisor
        dvr_gain = choice.dvr_level / divisor
    else:
        music_gain = choice.music_level
        dvr_gain = Fraction(0)
    return OutputAudioPlan(
        mode, output, source_has_audio, choice.asset, choice.passage,
        choice.short_track, music_samples, audible, fade_in, fade_out,
        music_gain, dvr_gain,
    )
