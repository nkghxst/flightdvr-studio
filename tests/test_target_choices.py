"""Each output's choices, saved and read back exactly (W4)."""

from __future__ import annotations

import json
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from flightdvr.assembly import Item
from flightdvr.audio_plan import (
    AudioAsset, AudioMode, MusicChoice, SampleSpan, ShortTrackPolicy)
from flightdvr.output_plan import OutputTarget, PlannedOutput
from flightdvr.presets import ExportSettings
from flightdvr.target_choices import (
    Malformed, SavedMusic, decode_music, decode_output, decode_outputs,
    decode_settings, decode_target, encode_music, encode_output,
    encode_outputs, encode_settings, encode_target)

SHA_A = "a" * 64
SHA_B = "b" * 64
A = OutputTarget.clip_or_range("source-fp", "range-a")
B = OutputTarget.clip_or_range("source-fp", "range-b")


def asset(sha=SHA_A, rate=44_100, samples=441_000, track="D:/music/a.wav",
          stream=0):
    return AudioAsset(Path(track), sha, stream, rate, 2, samples)


def choice_a():
    return MusicChoice(mode=AudioMode.MIX, asset=asset(),
                       passage=SampleSpan(44_101, 176_403, 44_100),
                       short_track=ShortTrackPolicy.LOOP,
                       music_level=Fraction(2, 3), dvr_level=Fraction(1, 7),
                       fade_in_samples=12_345, fade_out_samples=67_891)


def choice_b():
    return MusicChoice(mode=AudioMode.REPLACE,
                       asset=asset(SHA_B, 48_000, 480_000, "D:/music/b.flac"),
                       passage=SampleSpan(96_001, 240_007, 48_000),
                       short_track=ShortTrackPolicy.PLAY_ONCE,
                       music_level=Fraction(3, 5), dvr_level=Fraction(1, 4),
                       fade_in_samples=23_456, fade_out_samples=34_567)


def through_json(value):
    return json.loads(json.dumps(value))


# -- exact values ---------------------------------------------------------------


def test_a_and_b_come_back_exactly_as_integers_and_fractions():
    for chosen in (choice_a(), choice_b()):
        stored = through_json(encode_music(chosen))
        shown, saved = decode_music(stored)
        assert saved is not None
        assert saved.passage == chosen.passage
        assert (saved.music_level, saved.dvr_level) == (
            chosen.music_level, chosen.dvr_level)
        assert type(saved.music_level) is Fraction
        assert (saved.fade_in_samples, saved.fade_out_samples) == (
            chosen.fade_in_samples, chosen.fade_out_samples)
        # Confirmed against the same bytes, it is the very choice saved.
        assert saved.resolved(chosen.asset) == chosen
        # Meanwhile the window holds only the requests, which cannot play.
        assert shown.asset is None and shown.passage is None
        assert shown.track == chosen.asset.track and shown.mode == chosen.mode


def test_the_stored_form_is_literal_integers_never_seconds():
    stored = through_json(encode_music(choice_a()))
    assert stored == {
        "mode": "mix", "track": str(Path("D:/music/a.wav")),
        "reference": {"sha256": SHA_A, "stream": 0, "rate": 44_100,
                      "channels": 2, "samples": 441_000},
        "passage": [44_101, 176_403, 44_100],
        "short_track": "loop", "music_level": [2, 3], "dvr_level": [1, 7],
        "fade_in_samples": 12_345, "fade_out_samples": 67_891}


def test_negative_control_a_seconds_route_would_not_round_trip():
    """What the integers protect against, shown rather than assumed."""
    passage = choice_a().passage
    seconds = round(passage.end / passage.rate, 3)
    assert round(seconds * passage.rate) != passage.end       # 176 402
    assert float(Fraction(2, 3)) != Fraction(2, 3)


def test_settings_keep_their_values_and_never_carry_the_encoder():
    settings = ExportSettings(master_crf=21, colour="rec709", use_gpu=True,
                              hw_encoder="h264_nvenc", keep_audio=False,
                              social_size_mb=7)
    stored = through_json(encode_settings(settings))
    assert "hw_encoder" not in stored
    back = decode_settings(stored, hw_encoder="h264_qsv")
    assert back == replace(settings, hw_encoder="h264_qsv")


def test_unknown_settings_are_ignored_and_missing_ones_take_defaults():
    back = decode_settings({"master_crf": 24, "from_the_future": "x"})
    assert back == ExportSettings(master_crf=24)


@pytest.mark.parametrize("stored", [
    {"master_crf": True},             # bool is not a number here
    {"master_crf": 18.5},
    {"master_crf": -1},
    {"use_gpu": 1},
    {"colour": 3},
])
def test_a_mistyped_setting_is_refused(stored):
    with pytest.raises(Malformed):
        decode_settings(stored)


def test_assembly_order_and_repeats_are_the_identity():
    a, b = Item("fp-1", "r1"), Item("fp-2", "")
    repeated = OutputTarget.assembly([a, b, a])
    assert decode_target(through_json(encode_target(repeated))) == repeated
    assert decode_target(through_json(
        encode_target(OutputTarget.assembly([a, b])))) != repeated
    whole = OutputTarget.clip_or_range("fp-1")
    ranged = OutputTarget.clip_or_range("fp-1", "r1")
    assert decode_target(encode_target(whole)) == whole != ranged
    assert decode_target(encode_target(ranged)) == ranged


# -- a saved track is a claim until confirmed -------------------------------------


@pytest.mark.parametrize("fresh, why", [
    (asset(SHA_B), "changed"),
    (asset(stream=1), "stream"),
    (asset(rate=48_000), "sample rate"),
    (asset(samples=176_402), "length"),
    (asset(samples=400_000), "length"),      # the passage would still fit
    (replace(asset(), channels=1), "channel"),
])
def test_a_different_read_is_named_and_never_adopted(fresh, why):
    _shown, saved = decode_music(encode_music(choice_a()))
    assert why in saved.mismatch(fresh)
    with pytest.raises(ValueError):
        saved.resolved(fresh)


def test_the_same_track_confirms_exactly():
    _shown, saved = decode_music(encode_music(choice_a()))
    assert saved.mismatch(asset()) == ""
    assert saved.resolved(asset()) == choice_a()


def test_an_edit_while_unconfirmed_keeps_the_track_and_passage():
    shown, saved = decode_music(encode_music(choice_a()))
    edited = saved.with_requests(replace(shown, music_level=Fraction(1, 9),
                                         fade_in_samples=7))
    assert edited.passage == saved.passage and edited.sha256 == SHA_A
    assert edited.music_level == Fraction(1, 9)
    assert edited.resolved(asset()).passage == choice_a().passage
    stored = encode_music(shown, edited)
    assert stored["passage"] == [44_101, 176_403, 44_100]
    assert stored["music_level"] == [1, 9] and stored["fade_in_samples"] == 7


def test_an_unread_track_stays_unread_and_invents_no_reference():
    unread = MusicChoice(track=Path("D:/music/c.mp3"), mode=AudioMode.REPLACE)
    stored = encode_music(unread)
    assert "reference" not in stored and "passage" not in stored
    assert decode_music(stored) == (unread, None)


def test_no_choice_and_original_are_distinct():
    assert encode_music(MusicChoice()) is None
    assert decode_music(None) == (MusicChoice(), None)
    original = MusicChoice(mode=AudioMode.ORIGINAL)
    assert decode_music(encode_music(original)) == (original, None)


@pytest.mark.parametrize("damage", [
    {"music_level": [1, 0]},
    {"music_level": [3, 2]},
    {"music_level": [True, 2]},
    {"music_level": 0.5},
    {"fade_in_samples": 1.0},
    {"fade_in_samples": -3},
    {"passage": [1, 1, 44_100]},
    {"passage": [0, 10, 0]},
    {"passage": [0, 441_001, 44_100]},        # beyond the saved length
    {"passage": [0, 10, 48_000]},              # the wrong clock
    {"mode": "shout"},
    {"short_track": "bounce"},
    {"reference": {"sha256": "abc", "stream": 0, "rate": 44_100,
                   "channels": 2, "samples": 441_000}},
    {"reference": {"sha256": SHA_A, "stream": False, "rate": 44_100,
                   "channels": 2, "samples": 441_000}},
    {"mode": "original"},                      # Original cannot hold a track
])
def test_damaged_music_is_refused(damage):
    stored = dict(encode_music(choice_a()), **damage)
    with pytest.raises(Malformed):
        decode_music(stored)


# -- whole outputs and the selection ----------------------------------------------


def planned(target, music=MusicChoice(), preset="master", **settings):
    return PlannedOutput(target, preset, ExportSettings(**settings), music)


def test_a_malformed_entry_is_skipped_without_moving_the_selection():
    stored = through_json(encode_outputs(
        [(planned(A, choice_a()), None), (planned(B, choice_b(), "social"),
                                          None)], B))
    stored["outputs"][0]["preset"] = "no-such-preset"
    read = decode_outputs(stored["outputs"], stored["selected_output"])
    assert [one.target for one in read.outputs] == [B]
    assert read.selected == B
    assert len(read.problems) == 1 and "output 1" in read.problems[0]


def test_a_selection_naming_a_lost_entry_selects_nothing():
    stored = through_json(encode_outputs(
        [(planned(A), None), (planned(B), None)], A))
    stored["outputs"][0]["settings"] = {"master_crf": "high"}
    read = decode_outputs(stored["outputs"], stored["selected_output"])
    assert [one.target for one in read.outputs] == [B]
    assert read.selected is None                # never the neighbour


def test_no_selection_stays_distinct_from_the_first():
    stored = encode_outputs([(planned(A), None)], None)
    assert "selected_output" not in stored
    assert decode_outputs(stored["outputs"], None).selected is None


def test_a_duplicate_target_is_reported_not_merged():
    stored = through_json(encode_outputs(
        [(planned(A, preset="master"), None),
         (planned(A, preset="social"), None)], None))
    read = decode_outputs(stored["outputs"])
    assert [one.planned.preset_key for one in read.outputs] == ["master"]
    assert "repeats" in read.problems[0]


def test_a_whole_output_round_trips_with_its_pending_track():
    _shown, saved = decode_music(encode_music(choice_a()))
    one = planned(A, saved.requested, "upload", upload_crf=24)
    back = decode_output(through_json(encode_output(one, saved)),
                         hw_encoder="x")
    assert back.pending == saved
    assert back.planned.music == saved.requested
    assert back.planned.settings == ExportSettings(upload_crf=24,
                                                   hw_encoder="x")


@pytest.mark.parametrize("damage", [
    "not a dict",
    {"target": {"items": []}, "preset": "master"},
    {"target": {"items": [{"clip": ""}]}, "preset": "master"},
    {"target": {"assembly": "yes", "items": [{"clip": "x"}]},
     "preset": "master"},
    {"target": {"items": [{"clip": "x"}, {"clip": "y"}]},
     "preset": "master"},                       # two items but not an Assembly
    {"target": {"items": [{"clip": "x"}]}},    # no preset
])
def test_damaged_outputs_are_refused(damage):
    with pytest.raises(Malformed):
        decode_output(damage)


def test_outputs_that_are_not_a_list_give_nothing():
    assert decode_outputs(None).outputs == ()
    assert decode_outputs({"x": 1}).problems


# -- settings are the panel's own choices (Sol's review of 8277dce) ---------------


@pytest.mark.parametrize("stored", [
    {"master_crf": 999},
    {"social_size_mb": 0},
    {"social_size_mb": 2001},
    {"colour": "invalid"},
    {"social_mode": "unknown"},
    {"vertical_position": 999},
    {"vertical_position": -1},
    {"edit_codec": "h265"},
    {"master_speed": "ludicrous"},
    {"social_crf": 18},                       # a Master level, not Social's
    {"social_height": 1234},
    {"upload_height": 2160},
    {"social_fps": 24},
])
def test_a_value_the_panel_cannot_hold_is_refused(stored):
    with pytest.raises(Malformed):
        decode_settings(stored)


def test_the_review_s_probe_is_refused_as_one_entry_and_spares_its_neighbours():
    bad = {"master_crf": 999, "social_size_mb": 0, "colour": "invalid",
           "social_mode": "unknown", "vertical_position": 999}
    stored = through_json(encode_outputs(
        [(planned(A), None), (planned(B), None)], B))
    stored["outputs"][0]["settings"] = bad
    read = decode_outputs(stored["outputs"], stored["selected_output"])
    assert [one.target for one in read.outputs] == [B]
    assert read.selected == B and read.unread == (stored["outputs"][0],)


def test_every_default_and_every_offered_value_is_accepted():
    from flightdvr.presets import (
        COLOUR_MODES, EDIT_CODECS, QUALITY_LEVELS, SOCIAL_QUALITY_LEVELS,
        SPEEDS)
    from flightdvr.target_choices import FPS_STEPS, RESOLUTION_STEPS
    assert decode_settings(encode_settings(ExportSettings())) == ExportSettings()
    offered = (
        [{"colour": key} for key, _l, _h in COLOUR_MODES]
        + [{"edit_codec": key} for key in EDIT_CODECS]
        + [{"master_speed": speed} for speed in SPEEDS]
        + [{"upload_crf": crf} for crf, _n, _h in QUALITY_LEVELS]
        + [{"social_crf": crf} for crf, _n, _h in SOCIAL_QUALITY_LEVELS]
        + [{"social_height": h} for h in (0, *RESOLUTION_STEPS)]
        + [{"social_fps": f} for f in (0, *FPS_STEPS)]
        + [{"social_size_mb": 4}, {"social_size_mb": 2000},
           {"vertical_position": 0}, {"vertical_position": 100},
           {"social_mode": "size"}, {"social_mode": "quality"}])
    for stored in offered:
        decode_settings(stored)


def test_every_stored_setting_has_a_rule():
    """A field added to ExportSettings later must say what it may hold."""
    from dataclasses import fields
    from flightdvr import target_choices
    for one in fields(ExportSettings):
        if one.name in target_choices.MACHINE_FIELDS:
            continue
        if type(getattr(ExportSettings(), one.name)) is bool:
            continue
        assert (one.name in target_choices._CHOICES
                or one.name in target_choices._RANGES), one.name


def test_the_rules_match_the_output_panel(qtbot=None):
    """The codec's copies of the panel's lists and bounds are the panel's."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from flightdvr import export_panel, target_choices
    assert tuple(export_panel.RESOLUTION_STEPS) == target_choices.RESOLUTION_STEPS
    assert tuple(export_panel.FPS_STEPS) == target_choices.FPS_STEPS
    panel = export_panel.ExportPanel()
    assert (panel.social_size.minimum(), panel.social_size.maximum()) == (
        target_choices._RANGES["social_size_mb"])
    assert (panel.vertical_position.minimum(),
            panel.vertical_position.maximum()) == (
        target_choices._RANGES["vertical_position"])
    modes = {panel.social_mode.itemData(i)
             for i in range(panel.social_mode.count())}
    assert modes == target_choices._CHOICES["social_mode"]
    panel.deleteLater()
