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

from fractions import Fraction
from pathlib import Path

import pytest

from flightdvr.audio_plan import (
    AudioAsset, AudioMode, MusicChoice, OUTPUT_RATE, SampleSpan,
    ShortTrackPolicy, resolve_audio_plan, round_samples,
)


DIGEST = "a" * 64


def asset(samples=44_100 * 8, rate=44_100):
    return AudioAsset(Path("music.wav"), DIGEST, 0, rate, 2, samples)


def music(*, start=44_100, end=3 * 44_100, policy=ShortTrackPolicy.LOOP,
          fade_in=0, fade_out=0, mode=AudioMode.REPLACE,
          music_level=1, dvr_level=Fraction(1, 4)):
    found = asset()
    return MusicChoice(
        found.track, mode, found, SampleSpan(start, end, found.sample_rate),
        policy, music_level, dvr_level, fade_in, fade_out,
    )


def resolve(choice, seconds=5, source=True):
    return resolve_audio_plan(choice, seconds * OUTPUT_RATE,
                              source_has_audio=source, preset_key="master")


def test_s1_track_only_choice_stays_a_replace_choice_but_is_not_executable():
    choice = MusicChoice(Path("launch.wav"))
    assert choice.track == Path("launch.wav")
    assert choice.mode is AudioMode.REPLACE
    with pytest.raises(ValueError, match="validated"):
        resolve(choice)


def test_original_no_sound_replace_and_mix_have_distinct_actual_semantics():
    original = resolve(MusicChoice(mode=AudioMode.ORIGINAL))
    silent = resolve(MusicChoice(mode=AudioMode.NO_SOUND))
    replaced = resolve(music())
    mixed = resolve(music(mode=AudioMode.MIX), source=True)
    assert (original.music_gain, original.dvr_gain) == (0, 1)
    assert (silent.music_gain, silent.dvr_gain) == (0, 0)
    assert (replaced.music_gain, replaced.dvr_gain) == (1, 0)
    assert (mixed.music_gain, mixed.dvr_gain) == (Fraction(4, 5), Fraction(1, 5))


def test_mix_without_dvr_retains_the_requested_music_gain():
    plan = resolve(music(mode=AudioMode.MIX, music_level=Fraction(1, 2)),
                   source=False)
    assert (plan.music_gain, plan.dvr_gain) == (Fraction(1, 2), 0)


def test_half_open_clocks_are_integer_and_44100_converts_half_up_to_48000():
    with pytest.raises(ValueError, match="integers"):
        SampleSpan(0.0, 1, 48_000)
    with pytest.raises(ValueError, match="non-empty"):
        SampleSpan(3, 3, 48_000)
    assert round_samples(Fraction(1, 2)) == 1
    assert SampleSpan(1, 44_101, 44_100).samples_at(48_000) == 48_000


def test_loop_restarts_at_the_selected_in_point_not_file_zero():
    plan = resolve(music(start=44_100, end=3 * 44_100), seconds=5)
    assert plan.music_samples == 96_000
    # Passage-relative zero means native track sample 44,100, not file zero.
    assert plan.passage.start == 44_100
    assert plan.music_position(0) == 0
    assert plan.music_position(96_000) == 0


def test_play_once_ends_at_the_passage_end_and_does_not_shorten_video():
    plan = resolve(music(policy=ShortTrackPolicy.PLAY_ONCE), seconds=5)
    assert plan.output.samples == 240_000
    assert plan.audible_samples == 96_000
    assert plan.music_position(95_999) == 95_999
    assert plan.music_position(96_000) is None


def test_long_music_is_cut_at_the_finished_output_end():
    plan = resolve(music(end=7 * 44_100), seconds=2)
    assert plan.audible_samples == plan.output.samples == 96_000


def test_overlapping_fades_share_the_audible_interval_in_requested_ratio():
    # Audible length 100; requested 90:60 becomes exactly 60:40.
    found = AudioAsset(Path("music.wav"), DIGEST, 0, 100, 2, 100)
    choice = MusicChoice(found.track, AudioMode.REPLACE, found,
                         SampleSpan(0, 100, 100), ShortTrackPolicy.PLAY_ONCE,
                         1, Fraction(1, 4), 90, 60)
    plan = resolve_audio_plan(choice, 100, source_has_audio=True,
                              preset_key="master")
    assert (plan.fade_in_samples, plan.fade_out_samples) == (60, 40)
    assert plan.envelope(0) == 0
    assert plan.envelope(99) == Fraction(1, 40)


def test_plan_identity_changes_with_timing_or_content_but_is_stable_for_a_copy():
    first = resolve(music(), seconds=5)
    same = resolve(music(), seconds=5)
    later = resolve(music(start=2 * 44_100), seconds=5)
    assert first.identity == same.identity
    assert first.identity != later.identity


@pytest.mark.parametrize("preset,joined,bundle", [
    ("social", False, False), ("slowmo", False, False),
    ("master", True, False), ("master", False, True),
])
def test_unsupported_music_contexts_fail_instead_of_dropping_the_track(
        preset, joined, bundle):
    with pytest.raises(ValueError, match="not supported"):
        resolve_audio_plan(music(), OUTPUT_RATE, source_has_audio=True,
                           preset_key=preset, joined=joined, bundle=bundle)


def test_unconfigured_choice_is_reserved_for_the_unchanged_legacy_path():
    assert not MusicChoice().configured
    with pytest.raises(ValueError, match="legacy export path"):
        resolve(MusicChoice())
