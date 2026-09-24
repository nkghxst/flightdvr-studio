"""Pure W5 picture-intent checks; decoded pixels are tested separately."""

from datetime import datetime
from fractions import Fraction
from pathlib import Path

import pytest

from flightdvr.assembly import Item
from flightdvr.media import ClipInfo, Select
from flightdvr.output_plan import (
    OutputTarget, PlannedOutput, ResolvedPieceProvenance, WorkingOutput,
)
from flightdvr.presets import ExportSettings
from flightdvr.preview_recipe import (
    PictureLocation, PictureTerminal, make_preview_recipe,
)
from flightdvr.sequence_plan import Resolution, compile_sequence


def clip(name, *, fps=60.0, size=(1280, 720), span=(2, 4), sid="r"):
    found = ClipInfo(
        path=Path(name), size=1024, modified=datetime(2026, 9, 24),
        duration=10, width=size[0], height=size[1], fps=fps,
        video_codec="hevc",
    )
    found.selects = [Select(*span, sid=sid)]
    return found


def recipe(clips, preset="master", settings=None, revision="w5"):
    items = tuple(Item(found.fingerprint, found.real_selects[0].sid)
                  for found in clips)
    target = OutputTarget.assembly(items) if len(items) > 1 else OutputTarget(items)
    working = WorkingOutput(
        target, tuple(clips), joined=len(clips) > 1,
        piece_provenance=tuple(
            ResolvedPieceProvenance(i, id(found), item.fingerprint, item.sid)
            for i, (item, found) in enumerate(zip(items, clips))),
    )
    sequence = compile_sequence(
        working, resolution=Resolution.success(), revision=revision)
    planned = PlannedOutput(target, preset, settings or ExportSettings())
    mapping = {one.id: found for one, found in zip(sequence.occurrences, clips)}
    return make_preview_recipe(planned, working, sequence, mapping)


def test_repeated_occurrences_and_half_open_seams_are_distinct():
    a, b = clip("A.ts"), clip("B.ts", span=(5, 8))
    result = recipe([a, b, a])
    assert result.duration == 7
    expected = [(0, 0, 2), (2, 1, 5), (5, 2, 2)]
    for at, ordinal, source in expected:
        found = result.map_output_time(at)
        assert isinstance(found, PictureLocation)
        assert (found.occurrence.ordinal, found.source) == (ordinal, source)
    assert isinstance(result.map_output_time(7), PictureTerminal)
    with pytest.raises(ValueError):
        result.map_output_time(Fraction(7) + Fraction(1, 10_000))


def test_slow_is_a_derived_clock_not_a_rewritten_sequence():
    for source_rate, output_rate in ((60, 30), (90, 45)):
        result = recipe([clip(f"slow-{source_rate}.ts", fps=source_rate)],
                        "slowmo")
        assert result.sequence.total_duration == 2
        assert result.duration == 4 and result.cadence == output_rate
        assert result.map_output_time(0).source == 2
        assert result.map_output_time(Fraction(2) - Fraction(1, 10_000)).source < 3
        assert result.map_output_time(2).source == 3
        assert isinstance(result.map_output_time(4), PictureTerminal)


def test_vertical_uses_source_crop_and_export_canvas():
    result = recipe([clip("portrait.ts")], "vertical")
    crop = result.occurrences[0].crop
    assert (crop.width, crop.height, crop.x, crop.y) == (396, 704, 442, 8)
    assert result.canvas == (720, 1280)
    with pytest.raises(ValueError, match="too narrow"):
        recipe([clip("narrow.ts", size=(300, 720))], "vertical")


def test_material_key_excludes_audio_quality_and_naming_but_carries_picture():
    source = clip("same.ts")
    base = recipe([source], "social", ExportSettings(social_height=540))
    quality = recipe([source], "social", ExportSettings(
        social_height=540, social_crf=26, social_size_mb=24))
    picture = recipe([source], "social", ExportSettings(social_height=360))
    assert quality.material_key == base.material_key
    assert picture.material_key != base.material_key
    assert recipe([source], "social", revision="new").material_key != base.material_key
