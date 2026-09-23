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

"""Music edits in samples, on the clock each value lives on.

Every expected value here is worked out by hand from the rates and written as
a literal, never computed by the code under test.
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from flightdvr.audio_plan import (
    AudioAsset, AudioMode, MusicChoice, SampleSpan, ShortTrackPolicy,
)
from flightdvr.music_edit import (
    EditKind, classify, frame_time, music_view, native_at, pixel_to_sample,
    rational_fps, sample_to_pixel, step, valid_passage,
)

NTSC = Fraction(30000, 1001)


def asset(rate=44_100, seconds=25):
    return AudioAsset(Path("song.mp3"), "a" * 64, 0, rate, 2, rate * seconds)


def replace_choice(passage, **kw):
    track = asset(passage.rate, 25)
    return MusicChoice(mode=AudioMode.REPLACE, asset=track, passage=passage, **kw)


# -- which kind of edit ---------------------------------------------------------

@pytest.mark.parametrize("field, value", [
    ("music_level", Fraction(1, 2)),
    ("dvr_level", Fraction(1, 10)),
    ("fade_in_samples", 12_345),
    ("fade_out_samples", 0),
])
def test_level_and_fade_edits_are_parameters(field, value):
    old = replace_choice(SampleSpan(0, 441_000, 44_100))
    assert classify(old, replace(old, **{field: value})) is EditKind.PARAMETER


@pytest.mark.parametrize("change", [
    {"passage": SampleSpan(1, 441_000, 44_100)},
    {"passage": SampleSpan(0, 440_999, 44_100)},
    {"short_track": ShortTrackPolicy.PLAY_ONCE},
    {"mode": AudioMode.MIX},
])
def test_what_moves_the_music_is_structural(change):
    old = replace_choice(SampleSpan(0, 441_000, 44_100))
    assert classify(old, replace(old, **change)) is EditKind.STRUCTURAL


def test_a_structural_edit_with_a_level_change_is_structural():
    old = replace_choice(SampleSpan(0, 441_000, 44_100))
    new = replace(old, passage=SampleSpan(44_100, 441_000, 44_100),
                  music_level=Fraction(1, 2))
    assert classify(old, new) is EditKind.STRUCTURAL


def test_no_change_is_no_edit():
    old = replace_choice(SampleSpan(0, 441_000, 44_100))
    assert classify(old, replace(old)) is EditKind.NONE


# -- keyboard steps: each lane's own clock, rational frame rates --------------

def test_frame_rate_floats_become_the_ratios_they_stand_for():
    assert rational_fps(29.97002997) == NTSC
    assert rational_fps(59.94005994) == Fraction(60000, 1001)
    assert rational_fps(60.0) == 60
    assert rational_fps(0) == 60                     # unknown: the default


def test_ntsc_frames_are_cumulative_not_a_rounded_step_repeated():
    # 48 000 * 1001 / 30 000 = 1601.6 samples a frame.
    assert [frame_time(k, 48_000, NTSC) for k in range(1, 6)] == [
        1602, 3203, 4805, 6406, 8008]
    # A repeated rounded step would say 1602 * 5 = 8010: two samples out.
    assert frame_time(5, 48_000, NTSC) != 5 * frame_time(1, 48_000, NTSC)


def test_the_same_frame_is_a_different_sample_count_on_each_clock():
    # One NTSC frame on the track's 44.1 kHz clock is 1471.47 samples.
    assert frame_time(1, 44_100, NTSC) == 1471
    assert frame_time(10, 44_100, NTSC) == 14_715
    assert frame_time(10, 48_000, NTSC) == 16_016


def test_steps_walk_the_grid_both_ways():
    # Fades are on 48 kHz at 60 fps: 800 samples a frame.
    assert step(0, 1, 48_000, Fraction(60)) == 800
    assert step(800, 1, 48_000, Fraction(60)) == 1600
    assert step(1600, -1, 48_000, Fraction(60)) == 800
    assert step(0, -1, 48_000, Fraction(60)) == 0    # not below zero


def test_an_off_grid_value_steps_to_the_next_boundary_not_by_a_fixed_amount():
    # 440 999 on a 44.1 kHz / NTSC grid sits between frames 299 and 300.
    assert step(440_999, 1, 44_100, NTSC) == frame_time(300, 44_100, NTSC)
    assert frame_time(300, 44_100, NTSC) == 441_441
    assert step(440_999, -1, 44_100, NTSC) == frame_time(299, 44_100, NTSC)
    assert frame_time(299, 44_100, NTSC) == 439_970


def test_whole_second_steps_are_whole_seconds_of_that_clock():
    assert step(100, 1, 44_100, NTSC, seconds=True) == 44_100
    assert step(44_100, 1, 44_100, NTSC, seconds=True) == 88_200
    assert step(44_101, -1, 44_100, NTSC, seconds=True) == 44_100


# -- pointer mapping ----------------------------------------------------------

def test_a_pointer_maps_exactly_onto_a_nonzero_origin_span():
    span = SampleSpan(88_200, 1_102_500, 44_100)       # 2 s .. 25 s
    assert pixel_to_sample(0, 460, span) == 88_200
    assert pixel_to_sample(460, 460, span) == 1_102_500
    assert pixel_to_sample(230, 460, span) == 595_350  # the exact middle
    # 1 014 300 / 460 = 2205 exactly: one pixel of this lane
    assert pixel_to_sample(1, 460, span) == 88_200 + 2_205


def test_a_pointer_past_either_end_pins_to_it():
    span = SampleSpan(0, 480_000, 48_000)
    assert pixel_to_sample(-40, 100, span) == 0
    assert pixel_to_sample(900, 100, span) == 480_000


def test_drawing_and_reading_agree():
    span = SampleSpan(88_200, 1_102_500, 44_100)
    assert sample_to_pixel(595_350, 460, span) == 230.0


# -- the equivalence the three input methods share ---------------------------

def test_pointer_keys_and_typing_reach_the_same_fade():
    """4.000 s of fade on the output clock, reached three ways."""
    lane = SampleSpan(0, 480_000, 48_000)             # a 10 s output
    by_pointer = pixel_to_sample(400, 1000, lane)     # 4/10 of the lane
    by_keys = 0
    for _ in range(240):                              # 240 frames at 60 fps
        by_keys = step(by_keys, 1, 48_000, Fraction(60))
    by_typing = round(4.000 * 48_000)
    assert by_pointer == by_keys == by_typing == 192_000


def test_an_exact_sample_no_box_can_show_is_still_a_valid_passage_end():
    assert valid_passage(0, 440_999, 1_102_500) == (0, 440_999)


# -- valid passages -----------------------------------------------------------

def test_a_passage_stays_inside_the_track_and_never_empties():
    assert valid_passage(-5, 2_000_000, 1_102_500) == (0, 1_102_500)
    assert valid_passage(500, 500, 1_102_500) == (500, 501)      # end onto start
    assert valid_passage(1_102_500, 1_102_500, 1_102_500) == (1_102_499, 1_102_500)


# -- what is drawn: requested against effective -------------------------------

def test_overlapping_fades_draw_the_fitted_pair_and_keep_the_request():
    # A 10 s output, fades asked 8 s and 6 s: they are fitted in proportion.
    # 480 000 * 384 000 // 672 000 = 274 285; the rest, 205 715.
    choice = replace_choice(SampleSpan(0, 441_000, 44_100),
                            fade_in_samples=384_000, fade_out_samples=288_000)
    view = music_view(choice, 480_000)
    assert (view.fade_in_effective, view.fade_out_effective) == (274_285, 205_715)
    assert (view.fade_in_requested, view.fade_out_requested) == (384_000, 288_000)


def test_a_three_second_loop_repeats_with_a_partial_last_one():
    # 3 s at 44.1 kHz is 144 000 samples at 48 kHz. Over 10 s: starts at 0, 3,
    # 6 and 9 s, and the last runs 1 s.
    choice = replace_choice(SampleSpan(0, 132_300, 44_100))
    view = music_view(choice, 480_000)
    assert view.music_samples == 144_000
    assert list(view.repeat_starts()) == [0, 144_000, 288_000, 432_000]
    assert view.repeats == 4
    assert view.partial_final == 48_000
    assert view.silence_from is None


def test_play_once_leaves_planned_silence_after_the_passage():
    choice = replace_choice(SampleSpan(0, 132_300, 44_100),
                            short_track=ShortTrackPolicy.PLAY_ONCE)
    view = music_view(choice, 480_000)
    assert view.audible_samples == 144_000
    assert view.silence_from == 144_000


def test_the_track_sample_under_an_output_sample_crosses_the_clock_once():
    # Passage from 2 s of a 44.1 kHz track, 3 s long, looped.
    choice = replace_choice(SampleSpan(88_200, 220_500, 44_100))
    view = music_view(choice, 480_000)
    assert native_at(choice, 0, view) == 88_200
    assert native_at(choice, 48_000, view) == 88_200 + 44_100   # 1 s in
    assert native_at(choice, 144_000, view) == 88_200           # loops
    assert native_at(choice, 143_999, view) == 220_499          # last, not the end


def test_no_music_draws_nothing():
    assert music_view(MusicChoice(mode=AudioMode.ORIGINAL), 480_000) is None
    unread = MusicChoice(track=Path("song.mp3"), mode=AudioMode.REPLACE)
    assert music_view(unread, 480_000) is None


def test_the_last_output_sample_never_reads_past_the_passage_end():
    """A 6-sample passage at 44.1 kHz lasts 7 samples at 48 kHz, and the last
    one rounds to track sample 6 — the passage's exclusive end."""
    choice = replace_choice(SampleSpan(100, 106, 44_100))
    view = music_view(choice, 7)
    assert view.music_samples == 7
    assert native_at(choice, 6, view) == 105
