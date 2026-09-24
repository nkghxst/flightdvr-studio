"""Immutable picture intent for one selected working output.

The nominal SequencePlan remains the authority for occurrence/source time.
This value adds only the preset's picture and display-time decisions; it owns
no decoder, widget, cache, or audio choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping

from .media import ClipInfo
from .output_plan import OutputTarget, PlannedOutput, WorkingOutput
from .presets import (
    PRESETS, SLOW_FACTOR, VerticalCrop, join_target_format, slow_output_rate,
    vertical_crop, vertical_output_size,
)
from .sequence_plan import (
    OccurrenceId, SequenceLocation, SequencePlan, SequencePlanError,
    SequenceTerminal, TimeSpan,
)


@dataclass(frozen=True)
class PictureOccurrence:
    id: OccurrenceId
    fingerprint: str
    path: str
    source: TimeSpan
    width: int
    height: int
    fps: Fraction
    is_full_range: bool
    crop: VerticalCrop | None


@dataclass(frozen=True)
class PictureLocation:
    occurrence: OccurrenceId
    source: Fraction
    nominal_output: Fraction
    display_output: Fraction


@dataclass(frozen=True)
class PictureTerminal:
    revision: str
    display_output: Fraction


@dataclass(frozen=True)
class PreviewRecipe:
    target: OutputTarget
    sequence: SequencePlan
    occurrences: tuple[PictureOccurrence, ...]
    preset_key: str
    colour: str
    canvas: tuple[int, int]
    cadence: Fraction
    time_factor: int
    material_key: tuple
    warning: str = ""

    @property
    def duration(self) -> Fraction:
        return self.sequence.total_duration * self.time_factor

    def map_output_time(self, seconds) -> PictureLocation | PictureTerminal:
        display = Fraction(str(seconds))
        if display < 0 or display > self.duration:
            raise SequencePlanError("picture position is outside the output")
        nominal = display / self.time_factor
        found = self.sequence.locate_output(nominal)
        if isinstance(found, SequenceTerminal):
            return PictureTerminal(self.sequence.revision, display)
        assert isinstance(found, SequenceLocation)
        return PictureLocation(found.occurrence, found.source, nominal, display)

    def occurrence(self, identity: OccurrenceId) -> PictureOccurrence:
        self.sequence.get_occurrence(identity)
        return self.occurrences[identity.ordinal]


def _even_width(width: int, height: int, target_height: int) -> int:
    """FFmpeg scale=-2:H's nearest even aspect-preserving width."""
    return max(2, 2 * round(width * target_height / height / 2))


def make_preview_recipe(
    planned: PlannedOutput,
    working: WorkingOutput,
    sequence: SequencePlan,
    clips: Mapping[OccurrenceId, ClipInfo],
) -> PreviewRecipe:
    """Resolve a defensive, occurrence-aware picture snapshot or refuse it."""
    if planned.target != working.target:
        raise ValueError("selected target and resolved material disagree")
    if planned.preset_key not in PRESETS:
        raise ValueError("unknown picture preset")
    if len(sequence.occurrences) != len(working.pieces):
        raise ValueError("sequence and resolved material differ in length")
    resolved = []
    pictures = []
    for one in sequence.occurrences:
        clip = clips.get(one.id)
        if (clip is None or clip.fingerprint != one.fingerprint
                or str(clip.path) != one.source_path):
            raise ValueError(f"picture occurrence {one.id.ordinal} changed source")
        if clip.width <= 0 or clip.height <= 0 or clip.fps <= 0:
            raise ValueError(f"picture occurrence {one.id.ordinal} lacks geometry/rate")
        crop = (vertical_crop(clip, planned.settings.vertical_position)
                if planned.preset_key == "vertical" else None)
        resolved.append(clip)
        pictures.append(PictureOccurrence(
            one.id, one.fingerprint, one.source_path, one.source,
            clip.width, clip.height, Fraction(str(clip.fps)),
            clip.is_full_range, crop))

    key = planned.preset_key
    if key == "vertical":
        if len(resolved) == 1:
            canvas = vertical_output_size(resolved[0])
        else:
            # This is the same max-dimension input as join_filtergraph.
            from .presets import vertical_join_canvas
            canvas = vertical_join_canvas(resolved)
    elif len(resolved) > 1:
        canvas = join_target_format(resolved)[:2]
    else:
        clip = resolved[0]
        canvas = (clip.width - clip.width % 2,
                  clip.height - clip.height % 2)

    if key in ("social", "upload"):
        selected_height = (planned.settings.social_height if key == "social"
                           else planned.settings.upload_height)
        should_scale = (selected_height > 0 and
                        (key == "upload" or selected_height < resolved[0].height))
        if should_scale and selected_height != canvas[1]:
            canvas = (_even_width(*canvas, selected_height), selected_height)

    if key == "slowmo":
        cadence = Fraction(str(slow_output_rate(resolved)))
        factor = SLOW_FACTOR
    elif key == "social" and planned.settings.social_fps:
        source_rate = (Fraction(str(join_target_format(resolved)[2]))
                       if len(resolved) > 1 else Fraction(str(resolved[0].fps)))
        cadence = min(source_rate, Fraction(planned.settings.social_fps))
        factor = 1
    else:
        cadence = (Fraction(str(join_target_format(resolved)[2]))
                   if len(resolved) > 1 else Fraction(str(resolved[0].fps)))
        factor = 1

    picture_settings = (
        planned.settings.colour,
        planned.settings.vertical_position if key == "vertical" else None,
        planned.settings.social_height if key == "social" else None,
        planned.settings.social_fps if key == "social" else None,
        planned.settings.upload_height if key == "upload" else None,
    )
    material_key = (
        planned.target, sequence.revision,
        tuple((p.id, p.fingerprint, p.path, p.source, p.width, p.height,
               p.fps, p.is_full_range, p.crop) for p in pictures),
        key, picture_settings, canvas, cadence, factor,
    )
    warning = ("Remux cut is approximate until the completed file is checked"
               if key == "remux" else "")
    return PreviewRecipe(planned.target, sequence, tuple(pictures), key,
                         planned.settings.colour, canvas, cadence, factor,
                         material_key, warning)
