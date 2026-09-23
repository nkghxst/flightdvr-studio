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

"""What a music edit means, in samples, with no widgets anywhere near it.

Every way of editing the music — dragging a handle, pressing a key, typing a
number — ends here as an integer sample on the clock that value lives on. The
passage is on the track's own clock (`passage.rate`, the asset's native rate);
the fades and everything drawn along the finished output are on the output
clock (`OUTPUT_RATE`). Mixing the two up is the defect this module exists to
make impossible to write without noticing: 44.1 kHz and 48 kHz differ by nine
percent, which is a fade in the wrong place, not a rounding error.

Nothing here decides what the export does. `audio_plan` owns that; the view
below only describes the resolver's answer so it can be drawn.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

from .audio_plan import (
    OUTPUT_RATE, AudioMode, MusicChoice, SampleSpan, ShortTrackPolicy,
    _effective_fades, round_samples,
)

# When a recording's frame rate is not known, keyboard steps use this. HDZero
# records at 60; a wrong guess moves a handle by a slightly different small
# amount, never onto a different clock.
DEFAULT_FPS = Fraction(60)


class EditKind(str, Enum):
    """What an edit asks of monitoring.

    Parameters change how loud things are, and when fades ramp, without moving
    anything: the readers, the position and the loop phase all stay. Anything
    else moves where the music comes from or how long it runs, so the stream
    has to be prepared again.
    """

    NONE = "none"
    PARAMETER = "parameter"
    STRUCTURAL = "structural"


_STRUCTURAL = ("mode", "track", "asset", "passage", "short_track")
_PARAMETERS = ("music_level", "dvr_level", "fade_in_samples", "fade_out_samples")


def classify(old: MusicChoice, new: MusicChoice) -> EditKind:
    """Which kind of edit turns `old` into `new`."""
    if any(getattr(old, name) != getattr(new, name) for name in _STRUCTURAL):
        return EditKind.STRUCTURAL
    if any(getattr(old, name) != getattr(new, name) for name in _PARAMETERS):
        return EditKind.PARAMETER
    return EditKind.NONE


def rational_fps(fps) -> Fraction:
    """A frame rate as the exact ratio it stands for.

    Recordings report 59.94 or 29.97 as floats. Those are 60000/1001 and
    30000/1001; a float step accumulates the difference, and after a minute of
    presses a handle is a frame off.
    """
    try:
        value = Fraction(fps).limit_denominator(1001)
    except (TypeError, ValueError, ZeroDivisionError):
        return DEFAULT_FPS
    return value if value > 0 else DEFAULT_FPS


def frame_time(frames: int, rate: int, fps: Fraction) -> int:
    """Where frame `frames` starts, in samples on a `rate` clock.

    Cumulative: frame k is `round(k * rate / fps)`, not k steps of a rounded
    step. At 30000/1001 fps and 48 kHz one frame is 1601.6 samples, and adding
    1602 each time drifts a sample every two and a half frames.
    """
    if frames < 0:
        return -frame_time(-frames, rate, fps)
    return round_samples(Fraction(frames * rate) / fps)


def step(value: int, direction: int, rate: int, fps: Fraction, *,
         seconds: bool = False) -> int:
    """The next frame (or whole second) boundary after or before `value`.

    On the grid anchored at zero of that clock, so repeated presses land on
    the same samples whichever value they started from, and a value that is
    already exact but off the grid (loaded from a file, say) moves to the
    nearest boundary in the pressed direction rather than by a fixed amount.
    """
    if direction not in (-1, 1):
        raise ValueError("a step goes one way or the other")
    unit = Fraction(rate) if seconds else Fraction(rate) / fps
    index = Fraction(value) / unit
    if direction > 0:
        target = int(index) + 1
    else:
        whole = int(index)
        target = whole - 1 if index == whole else whole
    if seconds:
        return max(0, target * rate)
    return max(0, frame_time(target, rate, fps))


def pixel_to_sample(x, width: int, span: SampleSpan) -> int:
    """A pointer position on a lane, as a sample on that lane's clock.

    Exact rational arithmetic, halves rounding up as the resolver rounds, and
    held inside the lane: dragging past either end pins to it.
    """
    if width <= 0:
        return span.start
    x = min(max(Fraction(x), Fraction(0)), Fraction(width))
    return span.start + round_samples(x * span.samples / width)


def sample_to_pixel(sample: int, width: int, span: SampleSpan) -> float:
    """Where a sample on a lane's clock is drawn."""
    if span.samples <= 0:
        return 0.0
    return float(Fraction(sample - span.start) * width / span.samples)


def valid_passage(start: int, end: int, total: int) -> tuple[int, int]:
    """A passage the contract accepts: inside the track and not empty.

    Moving one end never drags the other: an end pushed onto the start stops
    one sample after it, and a start pushed onto the end stops one before.
    """
    if total <= 0:
        raise ValueError("a passage needs a track with samples in it")
    start = min(max(0, int(start)), total - 1)
    end = min(max(start + 1, int(end)), total)
    return start, end


@dataclass(frozen=True)
class MusicView:
    """What the finished output does with the music, for drawing.

    Requested fades are the choice's; effective fades are what the resolver
    fits into the audible part. Both are kept, because showing only the
    effective pair would make the request look as if it had been changed.
    """

    output_samples: int
    audible_samples: int
    music_samples: int              # the passage's length on the output clock
    fade_in_requested: int
    fade_out_requested: int
    fade_in_effective: int
    fade_out_effective: int
    loops: bool

    @property
    def repeats(self) -> int:
        """How many times the passage starts, the partial last one included."""
        if self.music_samples <= 0:
            return 0
        return -(-self.audible_samples // self.music_samples)

    @property
    def partial_final(self) -> int:
        """The length of an incomplete last repeat, or zero if it completes."""
        if self.music_samples <= 0:
            return 0
        return self.audible_samples % self.music_samples

    @property
    def silence_from(self) -> int | None:
        """Where planned silence begins (play once, shorter than the output)."""
        if self.audible_samples < self.output_samples:
            return self.audible_samples
        return None

    def repeat_starts(self) -> range:
        return range(0, self.audible_samples, max(1, self.music_samples))


def music_view(choice: MusicChoice, output_samples: int) -> MusicView | None:
    """The drawing of `choice` over an output `output_samples` long.

    None when there is no music to place: Original or No sound, or a track
    that has not been read yet. Uses the resolver's own fade fitting, so what
    is drawn is what the export and the monitor will do.
    """
    if (choice.mode not in (AudioMode.REPLACE, AudioMode.MIX)
            or choice.asset is None or choice.passage is None
            or output_samples <= 0):
        return None
    music = choice.passage.samples_at(OUTPUT_RATE)
    if music <= 0:
        return None
    loops = choice.short_track is ShortTrackPolicy.LOOP
    audible = output_samples if loops else min(output_samples, music)
    fade_in, fade_out = _effective_fades(
        audible, choice.fade_in_samples, choice.fade_out_samples)
    return MusicView(output_samples, audible, music, choice.fade_in_samples,
                     choice.fade_out_samples, fade_in, fade_out, loops)


def native_at(choice: MusicChoice, output_sample: int,
              view: MusicView) -> int | None:
    """The track sample heard at an output sample, or None for silence.

    Output clock in, the track's native clock out: the passage-relative
    position is converted once, the way the stream converts its origin.
    """
    if view is None or not 0 <= output_sample < view.output_samples:
        return None
    if output_sample >= view.audible_samples:
        return None
    relative = (output_sample % view.music_samples if view.loops
                else output_sample)
    passage = choice.passage
    native = passage.start + round_samples(
        Fraction(relative * passage.rate, OUTPUT_RATE))
    # The end is exclusive; the last output sample of a repeat can round onto
    # it, and that sample belongs to the passage's last one.
    return min(native, passage.end - 1)
