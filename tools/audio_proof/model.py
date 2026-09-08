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

"""Inert, immutable sample timeline for the isolated proof; no app imports."""
from bisect import bisect_right
from dataclasses import dataclass, field
from fractions import Fraction


@dataclass(frozen=True)
class Span:
    occurrence: str
    start: int
    end: int
    speed: Fraction = Fraction(1)
    source_audio: bool = True

    def __post_init__(self):
        if type(self.start) is not int or type(self.end) is not int:
            raise ValueError("sample coordinates must be integers")
        object.__setattr__(self, "speed", Fraction(self.speed))
        if not self.occurrence or self.start < 0 or self.end <= self.start:
            raise ValueError("invalid source span")
        if self.speed not in (Fraction(1), Fraction(1, 2)):
            raise ValueError("proof supports only 1x and 0.5x")
        if self.speed != 1 and self.source_audio:
            raise ValueError("slow-source audio needs a separate transform proof")

    @property
    def duration(self):
        return (self.end - self.start) * self.speed.denominator // self.speed.numerator


@dataclass(frozen=True)
class Timeline:
    spans: tuple[Span, ...]
    edges: tuple[int, ...] = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "spans", tuple(self.spans))
        if not self.spans:
            raise ValueError("empty timeline")
        edges, total = [], 0
        for span in self.spans:
            total += span.duration
            edges.append(total)
        object.__setattr__(self, "edges", tuple(edges))

    @property
    def duration(self):
        return self.edges[-1]

    def locate(self, output_sample):
        if not 0 <= output_sample < self.duration:
            raise ValueError("outside output timeline")
        index = bisect_right(self.edges, output_sample)
        span = self.spans[index]
        offset = self.edges[index - 1] if index else 0
        return span, span.start + (output_sample - offset) * span.speed


@dataclass(frozen=True)
class AudioPlan:
    timeline: Timeline
    music_samples: int
    loop: bool = True
    fade_in: int = 0
    fade_out: int = 0

    def __post_init__(self):
        if any(type(v) is not int for v in
               (self.music_samples, self.fade_in, self.fade_out)):
            raise ValueError("music and fade lengths must be integer samples")
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
