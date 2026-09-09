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

"""The music controls, against the merged contract they edit.

These are component tests: they build the widget, drive it the way a person or
a caller would, and read the value object back. They are offscreen, so they say
what the component does, not that it is readable on a real display — that
remains pending, as it does for the browser work.
"""

from __future__ import annotations

import builtins
from fractions import Fraction
from pathlib import Path

import pytest

from flightdvr.audio_plan import (
    OUTPUT_RATE, AudioAsset, AudioMode, MusicChoice, SampleSpan,
    ShortTrackPolicy, resolve_audio_plan,
)
from flightdvr.music_panel import MusicPanel, samples_of, seconds_of


TRACK = Path("D:/music/nocturne.mp3")


def an_asset(seconds: float = 30.0, rate: int = 44_100) -> AudioAsset:
    """A validated track, as the probe that this panel may not run would
    return one. The digest is a literal: nothing here hashes a file."""
    return AudioAsset(
        track=TRACK,
        sha256="a" * 64,
        stream_index=0,
        sample_rate=rate,
        channels=2,
        decoded_samples=int(seconds * rate),
    )


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qt_app):
    made = MusicPanel()
    yield made
    made.deleteLater()


def edits(panel) -> list:
    """Record every `changed` emission, so a load can be proved silent."""
    seen = []
    panel.changed.connect(lambda: seen.append(True))
    return seen


# -- 1. a valid choice survives the round trip ---------------------------------


@pytest.mark.parametrize("mode", [AudioMode.ORIGINAL, AudioMode.NO_SOUND])
def test_a_source_only_mode_round_trips(panel, mode):
    panel.load(MusicChoice(mode=mode), target="hdz_047.ts · Launch")
    assert panel.capture() == MusicChoice(mode=mode)


@pytest.mark.parametrize("mode", [AudioMode.REPLACE, AudioMode.MIX])
@pytest.mark.parametrize("policy", list(ShortTrackPolicy))
def test_a_music_choice_round_trips_including_levels_and_fades(panel, mode,
                                                               policy):
    asset = an_asset()
    original = MusicChoice(
        track=TRACK, mode=mode, asset=asset,
        passage=SampleSpan(44_100, 22 * 44_100, 44_100),
        short_track=policy,
        music_level=Fraction(4, 5), dvr_level=Fraction(1, 10),
        fade_in_samples=samples_of(0.5), fade_out_samples=samples_of(3.0),
    )
    panel.load(original, target="hdz_047.ts · Launch")

    assert panel.capture() == original


def test_a_track_only_replace_stays_unresolved(panel):
    """The contract models a track with no asset as an unresolved Replace.

    Inventing an asset here would mean probing, which is not this component's
    job and not on this thread.
    """
    original = MusicChoice(track=TRACK)
    panel.load(original, target="hdz_047.ts · Launch")

    captured = panel.capture()
    assert captured.mode is AudioMode.REPLACE
    assert captured.asset is None
    assert captured.passage is None
    assert captured == original


# -- 2. a programmatic load is not an edit -------------------------------------


def test_loading_a_choice_emits_no_edit(panel):
    """The trap that makes a panel overwrite the value it was just given.

    A caller that saves on `changed` would otherwise persist a value nobody
    chose, every time it showed one.
    """
    seen = edits(panel)
    panel.load(
        MusicChoice(track=TRACK, mode=AudioMode.MIX, asset=an_asset(),
                    music_level=Fraction(1, 2), dvr_level=Fraction(3, 10)),
        target="hdz_047.ts · Launch")
    assert seen == []


def test_injecting_an_asset_emits_no_edit(panel):
    seen = edits(panel)
    panel.load(MusicChoice(track=TRACK), target="hdz_047.ts · Launch")
    panel.set_asset(an_asset())
    assert seen == []


def test_a_real_edit_does_emit(panel):
    panel.load(MusicChoice(track=TRACK, mode=AudioMode.REPLACE,
                           asset=an_asset()), target="hdz_047.ts · Launch")
    seen = edits(panel)
    panel.music_level.setValue(55)
    assert seen, "a person changing the music level is an edit"


# -- 3. one target's choice cannot reach another -------------------------------


def test_editing_after_a_second_load_leaves_the_first_choice_alone(panel):
    first = MusicChoice(track=TRACK, mode=AudioMode.REPLACE, asset=an_asset(),
                        music_level=Fraction(1))
    second = MusicChoice(track=TRACK, mode=AudioMode.MIX, asset=an_asset(),
                         music_level=Fraction(1, 4))

    panel.load(first, target="A")
    panel.load(second, target="B")
    panel.music_level.setValue(7)
    captured = panel.capture()

    # The value objects are frozen, so this also says the panel keeps no
    # shared mutable state of its own between loads.
    assert first.music_level == Fraction(1)
    assert second.music_level == Fraction(1, 4)
    assert captured.music_level == Fraction(7, 100)


def test_capture_does_not_hand_back_the_object_it_was_given(panel):
    original = MusicChoice(track=TRACK, mode=AudioMode.REPLACE,
                           asset=an_asset())
    panel.load(original, target="A")
    assert panel.capture() is not original


# -- 4. the request is kept, not the clamped value -----------------------------


def test_requested_fades_are_not_re_derived_by_the_panel(panel):
    """`resolve_audio_plan` shortens a fade pair that will not fit.

    That is the export's business. Showing or storing the clamped number here
    would rewrite what was asked for, and the next capture would persist the
    rewrite as though it had been chosen.
    """
    asked_in, asked_out = samples_of(5.0), samples_of(5.0)
    original = MusicChoice(
        track=TRACK, mode=AudioMode.REPLACE, asset=an_asset(),
        fade_in_samples=asked_in, fade_out_samples=asked_out)
    panel.load(original, target="hdz_047.ts · Launch")

    captured = panel.capture()
    assert captured.fade_in_samples == asked_in
    assert captured.fade_out_samples == asked_out

    # And the export really would shorten them, so the two numbers are
    # genuinely different rather than trivially equal.
    plan = resolve_audio_plan(captured, 6 * OUTPUT_RATE,
                              source_has_audio=True, preset_key="master")
    assert plan.fade_in_samples + plan.fade_out_samples <= 6 * OUTPUT_RATE
    assert plan.fade_in_samples < asked_in


def test_exact_sample_values_survive_the_round_trip(panel):
    """Samples are the unit; a two-decimal box must not become the storage.

    Found in review. Every value the other round-trip tests used happened to
    land on a whole centisecond, so the quantisation never showed. One sample
    at 44.1 kHz is 0.0000227 s, which a two-decimal box displays as 0.00 and
    reads back as zero; 440 999 samples displays as 10.00 and reads back as
    441 000. Both ends of the passage moved, and the fades with them.
    """
    asset = an_asset(rate=44_100)
    original = MusicChoice(
        track=TRACK, mode=AudioMode.REPLACE, asset=asset,
        passage=SampleSpan(1, 440_999, 44_100),
        fade_in_samples=1, fade_out_samples=47_999)

    panel.load(original, target="hdz_047.ts · Launch")
    captured = panel.capture()

    assert captured.passage == original.passage
    assert captured.fade_in_samples == 1
    assert captured.fade_out_samples == 47_999
    assert captured == original


def test_editing_one_end_leaves_the_other_end_exact(panel):
    """Touching one control must not quantise the one beside it."""
    asset = an_asset(rate=44_100)
    panel.load(MusicChoice(track=TRACK, mode=AudioMode.REPLACE, asset=asset,
                           passage=SampleSpan(1, 440_999, 44_100),
                           fade_in_samples=1, fade_out_samples=47_999),
               target="A")

    panel.fade_in.setValue(2.0)          # a real edit to one box only
    captured = panel.capture()

    assert captured.fade_in_samples == samples_of(2.0)
    assert captured.fade_out_samples == 47_999, "the untouched box was rewritten"
    assert captured.passage == SampleSpan(1, 440_999, 44_100)


def test_editing_one_passage_end_leaves_the_other_exact(panel):
    """The same trap as the fades, in the control beside them.

    A passage is one span, so the handler is tempted to re-read both boxes.
    The end nobody touched cannot be expressed in two decimals, so re-reading
    it rounds it — the reviewed defect, moved one control along.
    """
    asset = an_asset(rate=44_100)
    panel.load(MusicChoice(track=TRACK, mode=AudioMode.REPLACE, asset=asset,
                           passage=SampleSpan(1, 440_999, 44_100)),
               target="A")

    panel.passage_start.setValue(2.0)
    passage = panel.capture().passage

    assert passage.start == samples_of(2.0, 44_100)
    assert passage.end == 440_999, "the untouched end was rewritten"


def test_levels_stay_exact_fractions(panel):
    """A float would drift a value the export multiplies by."""
    panel.load(MusicChoice(track=TRACK, mode=AudioMode.MIX, asset=an_asset()),
               target="A")
    panel.music_level.setValue(33)
    panel.dvr_level.setValue(7)

    captured = panel.capture()
    assert captured.music_level == Fraction(33, 100)
    assert captured.dvr_level == Fraction(7, 100)
    assert isinstance(captured.music_level, Fraction)


# -- 5. a context that cannot carry music says so ------------------------------


@pytest.mark.parametrize("context,fragment", [
    (dict(joined=True), "Assembly"),
    (dict(bundle=True), "delivery bundle"),
    (dict(preset_key="social"), "social"),
])
def test_an_unsupported_context_is_shown_and_locked(panel, context, fragment):
    """The same refusals the resolver makes, said before the commit rather
    than at it."""
    panel.load(MusicChoice(track=TRACK, mode=AudioMode.REPLACE,
                           asset=an_asset()), target="Assembly", **context)

    assert not panel.unsupported_label.isHidden()
    assert fragment in panel.unsupported_label.text()
    assert not panel.mode_combo.isEnabled()
    assert not panel.music_level.isEnabled()


def test_a_supported_context_is_editable_and_says_nothing(panel):
    panel.load(MusicChoice(track=TRACK, mode=AudioMode.REPLACE,
                           asset=an_asset()), target="hdz_047.ts · Launch")
    assert panel.unsupported_label.isHidden()
    assert panel.mode_combo.isEnabled()


def test_the_panel_refuses_the_same_contexts_the_resolver_does(panel):
    """The wording is the panel's; the rule is the resolver's. If S2 ever
    accepted an Assembly, this would fail rather than the panel quietly
    continuing to refuse it."""
    choice = MusicChoice(track=TRACK, mode=AudioMode.REPLACE, asset=an_asset())
    for context in (dict(joined=True), dict(bundle=True),
                    dict(preset_key="social")):
        with pytest.raises(ValueError):
            resolve_audio_plan(choice, OUTPUT_RATE, source_has_audio=True,
                               preset_key=context.get("preset_key", "master"),
                               joined=context.get("joined", False),
                               bundle=context.get("bundle", False))


def test_the_recording_level_is_editable_exactly_in_mix(panel):
    """The level of the recording under the music only means anything in Mix.

    This was wrong and the tests did not catch it: `currentData()` hands back
    a plain string for a `str` Enum, so an `is` comparison quietly disabled the
    control in the one mode that needs it. Found by looking at the render.
    """
    asset = an_asset()
    panel.load(MusicChoice(track=TRACK, mode=AudioMode.MIX, asset=asset),
               target="A")
    assert panel.dvr_level.isEnabled(), "Mix must expose the recording level"

    for mode in (AudioMode.REPLACE, AudioMode.ORIGINAL, AudioMode.NO_SOUND):
        choice = (MusicChoice(track=TRACK, mode=mode, asset=asset)
                  if mode is AudioMode.REPLACE else MusicChoice(mode=mode))
        panel.load(choice, target="A")
        assert not panel.dvr_level.isEnabled(), mode


def test_the_mode_is_read_back_as_the_enum_not_a_bare_string(panel):
    """Qt returns the plain string for a `str` Enum through a QVariant, which
    is what made an identity comparison fail. One seam normalises it."""
    panel.load(MusicChoice(track=TRACK, mode=AudioMode.MIX, asset=an_asset()),
               target="A")
    assert panel._mode() is AudioMode.MIX
    assert panel.capture().mode is AudioMode.MIX


# -- 6. monitoring is not an export choice -------------------------------------


def test_no_monitor_state_reaches_the_captured_choice(panel):
    """Asserted on the value object rather than on a widget: the point is that
    a listening level cannot be exported, whatever the UI grows later."""
    panel.load(MusicChoice(track=TRACK, mode=AudioMode.MIX, asset=an_asset()),
               target="A")
    captured = panel.capture()

    fields = set(vars(captured))
    for monitoring in ("mute", "muted", "volume", "monitor", "monitor_level",
                       "listening", "listening_level"):
        assert monitoring not in fields
    assert not hasattr(panel, "mute_button")
    assert not hasattr(panel, "volume_slider")


# -- what the component must not do at all -------------------------------------


def test_the_panel_opens_no_files_and_hashes_nothing(panel, monkeypatch):
    """Probing belongs off this thread and outside this lease.

    Asserted rather than assumed: an asset is injected, and every path here is
    exercised with the door to the filesystem nailed shut.
    """
    import hashlib

    def refuse_open(*args, **kwargs):
        raise AssertionError("the music panel must not open files")

    def refuse_hash(*args, **kwargs):
        raise AssertionError("the music panel must not hash files")

    monkeypatch.setattr(builtins, "open", refuse_open)
    monkeypatch.setattr(Path, "open", refuse_open)
    monkeypatch.setattr(Path, "read_bytes", refuse_open)
    monkeypatch.setattr(hashlib, "sha256", refuse_hash)

    panel.load(MusicChoice(track=TRACK), target="A")
    panel.set_asset(an_asset())
    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData(AudioMode.MIX))
    panel.music_level.setValue(40)
    panel.fade_in.setValue(1.5)
    panel.capture()


def test_seconds_and_samples_agree_at_the_output_rate():
    """Seconds are display; samples are what the contract stores."""
    assert samples_of(1.0) == OUTPUT_RATE
    assert samples_of(0.5) == OUTPUT_RATE // 2
    assert seconds_of(OUTPUT_RATE) == 1.0
    assert samples_of(seconds_of(3 * OUTPUT_RATE)) == 3 * OUTPUT_RATE


def test_the_passage_uses_the_track_clock_not_the_output_clock(panel):
    """A 44.1 kHz track's passage is expressed at 44.1 kHz, per the contract;
    using the output rate would silently move the music."""
    asset = an_asset(seconds=30.0, rate=44_100)
    panel.load(MusicChoice(track=TRACK, mode=AudioMode.REPLACE, asset=asset),
               target="A")
    panel.passage_start.setValue(2.0)
    panel.passage_end.setValue(12.0)

    passage = panel.capture().passage
    assert passage.rate == 44_100
    assert passage.start == samples_of(2.0, 44_100)
    assert passage.end == samples_of(12.0, 44_100)


def test_without_an_asset_there_is_no_passage_to_offer(panel):
    panel.load(MusicChoice(track=TRACK), target="A")
    assert not panel.passage_box.isEnabled()
    assert panel.capture().passage is None
