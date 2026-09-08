# FlightDVR Studio - browse, trim and convert HDZero goggle DVR footage.
# Copyright (C) 2026 Isadu Nkemi
# SPDX-License-Identifier: GPL-3.0-or-later
# This program is free software under GNU GPL v3 or later, without warranty.
# See LICENSE and <https://www.gnu.org/licenses/>.
"""Inert, immutable sample timeline for the isolated proof; no app imports."""
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class Span:
    occurrence: str
    start: int
    end: int
    speed: Fraction = Fraction(1)
    source_audio: bool = True

    def __post_init__(self):
        if not self.occurrence or self.start < 0 or self.end <= self.start:
            raise ValueError("invalid source span")
        if self.speed not in (Fraction(1), Fraction(1, 2)):
            raise ValueError("proof supports only 1x and 0.5x")
        if self.speed != 1 and self.source_audio:
            raise ValueError("slow-source audio needs a separate transform proof")

    @property
    def duration(self):
        return int((self.end - self.start) / self.speed)


@dataclass(frozen=True)
class Timeline:
    spans: tuple[Span, ...]

    def __post_init__(self):
        object.__setattr__(self, "spans", tuple(self.spans))
        if not self.spans:
            raise ValueError("empty timeline")

    @property
    def duration(self):
        return sum(s.duration for s in self.spans)

    def locate(self, output_sample):
        if not 0 <= output_sample < self.duration:
            raise ValueError("outside output timeline")
        offset = 0
        for span in self.spans:
            if output_sample < offset + span.duration:
                source = span.start + (output_sample - offset) * span.speed
                return span, source
            offset += span.duration
        raise AssertionError("unreachable")


@dataclass(frozen=True)
class AudioPlan:
    timeline: Timeline
    music_samples: int
    loop: bool = True
    fade_in: int = 0
    fade_out: int = 0

    def __post_init__(self):
        if self.music_samples <= 0 or min(self.fade_in, self.fade_out) < 0:
            raise ValueError("invalid music/fade length")
        if self.fade_in + self.fade_out > self.audible_samples:
            raise ValueError("effective fades must be clamped before playback")

    @property
    def audible_samples(self):
        return (self.timeline.duration if self.loop else
                min(self.timeline.duration, self.music_samples))

    def music_at(self, output_sample):
        if not 0 <= output_sample < self.audible_samples:
            return None, 0.0
        gain = 1.0
        if self.fade_in:
            gain = min(gain, output_sample / self.fade_in)
        if self.fade_out:
            gain = min(gain, (self.audible_samples - output_sample) / self.fade_out)
        return output_sample % self.music_samples, gain
