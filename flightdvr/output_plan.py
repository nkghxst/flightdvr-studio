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

"""Editable output choices shared by alternate presentations of one session.

This deliberately stops before persistence, widgets or queue control. Classic
and Flow need one place to edit the same planned outputs, but a planned output
is not a queued job: :class:`flightdvr.jobs.Job` takes its own settings snapshot
at the commit-to-render boundary.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Iterable

from .assembly import Item
from .audio_plan import MusicChoice
from .presets import PRESETS, ExportSettings


@dataclass(frozen=True)
class OutputTarget:
    """Stable source references for one planned output.

    A clip or range is one existing Assembly ``Item``. An Assembly is its
    ordered items. Holding references rather than ``ClipInfo`` copies avoids a
    second clip/range database and preserves the identity rules already used by
    the session and Assembly model.
    """

    items: tuple[Item, ...]
    is_assembly: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        if not self.items:
            raise ValueError("an output target needs at least one source reference")
        if not self.is_assembly and len(self.items) != 1:
            raise ValueError("a clip/range target has exactly one source reference")
        if any(not isinstance(item, Item)
               or not isinstance(item.fingerprint, str)
               or not item.fingerprint.strip()
               or not isinstance(item.sid, str)
               for item in self.items):
            raise ValueError("output targets require existing clip fingerprints")

    @classmethod
    def clip_or_range(cls, fingerprint: str, sid: str = "") -> "OutputTarget":
        """Refer to a whole clip, or to its stable range id when supplied."""
        return cls((Item(fingerprint, sid),))

    @classmethod
    def assembly(cls, items: Iterable[Item]) -> "OutputTarget":
        """Refer to an Assembly by its existing ordered item references."""
        return cls(tuple(items), is_assembly=True)


@dataclass(frozen=True)
class PlannedOutput:
    """A defensive value returned by :class:`OutputPlan`."""

    target: OutputTarget
    preset_key: str
    settings: ExportSettings
    music: MusicChoice = MusicChoice()

    def __post_init__(self) -> None:
        if self.preset_key not in PRESETS:
            raise ValueError(f"unknown export preset: {self.preset_key}")
        if not isinstance(self.settings, ExportSettings):
            raise TypeError("settings must be ExportSettings")
        if not isinstance(self.music, MusicChoice):
            raise TypeError("music must be MusicChoice")
        # ExportSettings is mutable today. The plan owns this copy and never
        # exposes it directly, so two views or targets cannot acquire an alias.
        object.__setattr__(self, "settings", deepcopy(self.settings))


class OutputPlan:
    """Ordered planned outputs with one stable selected editing target."""

    def __init__(self) -> None:
        self._outputs: dict[OutputTarget, PlannedOutput] = {}
        self._selected: OutputTarget | None = None

    @property
    def targets(self) -> tuple[OutputTarget, ...]:
        """Targets in the order they first entered the plan."""
        return tuple(self._outputs)

    @property
    def selected_target(self) -> OutputTarget | None:
        return self._selected

    @property
    def selected(self) -> PlannedOutput | None:
        if self._selected is None:
            return None
        return self.get(self._selected)

    def set_choices(
        self,
        target: OutputTarget,
        preset_key: str,
        settings: ExportSettings,
        music: MusicChoice = MusicChoice(),
    ) -> PlannedOutput:
        """Add or replace one target's choices without selecting another."""
        if not isinstance(target, OutputTarget):
            raise TypeError("target must be OutputTarget")
        planned = PlannedOutput(target, preset_key, settings, music)
        self._outputs[target] = planned
        if self._selected is None:
            self._selected = target
        return self.get(target)

    def select(self, target: OutputTarget) -> PlannedOutput:
        """Choose which existing output a presentation is editing."""
        if target not in self._outputs:
            raise KeyError("output target is not in this plan")
        self._selected = target
        return self.get(target)

    def get(self, target: OutputTarget) -> PlannedOutput:
        """Return a defensive copy; edits return through ``set_choices``."""
        try:
            planned = self._outputs[target]
        except KeyError:
            raise KeyError("output target is not in this plan") from None
        return deepcopy(planned)

@dataclass(frozen=True)
class WorkingOutput:
    """One output this session would build, and the identity it is keyed by.

    A descriptor rather than a bare `OutputTarget`, because a target alone
    cannot say which queued piece it came from or where it sits in an ordered
    set — and a sidebar that lists targets while the queue builds pieces is two
    answers to one question waiting to disagree.

    `pieces` is what the queue would hand to one `Job`: one entry for an
    ordinary export, the whole ordered run for an Assembly.
    """

    target: OutputTarget
    pieces: tuple
    label: str = ""
    joined: bool = False

    @property
    def piece(self):
        """The single piece, for the ordinary case."""
        return self.pieces[0] if self.pieces else None


def ordinary_pieces(clips) -> list:
    """Ticked clips expanded the way an ordinary export expands them.

    A function rather than a method, so the queue and the sidebar call the
    same one without either owning it. A clip with three selects becomes three
    ordinary clips here, and everything downstream carries on believing a
    recording has one in point and one out point.
    """
    return [piece for clip in clips for piece in clip.for_export()]


def target_for_piece(piece) -> OutputTarget | None:
    """The identity of one already-resolved export piece.

    Fingerprint plus the stable range id, which is what the session and the
    Assembly already key on. The piece is whatever the caller resolved — this
    invents no fingerprint and looks nothing up.
    """
    clip = getattr(piece, "clip", piece)
    fingerprint = getattr(clip, "fingerprint", "")
    if not fingerprint:
        return None
    ranges = getattr(clip, "real_selects", None) or []
    sid = ""
    if ranges:
        current = min(getattr(clip, "current", 0), len(ranges) - 1)
        sid = ranges[max(0, current)].sid
    return OutputTarget.clip_or_range(fingerprint, sid)


def working_outputs(pieces, *, joined: bool = False) -> list[WorkingOutput]:
    """The outputs a queue action would build, from pieces already resolved.

    Deliberately takes pieces rather than clips. The two routes resolve
    differently and only their own callers know how: an ordinary export is
    ticked clips expanded by `for_export`, and an Assembly is the ordered rows
    it actually names — which can include material nobody ticked and the same
    range more than once. Re-deriving either here would be a second authority
    on what gets exported, and the first thing a second authority does is
    disagree.

    Joined gives one output for the whole run, because that is one job.
    """
    resolved = [piece for piece in pieces if piece is not None]
    if not resolved:
        return []
    if joined:
        items = []
        for piece in resolved:
            target = target_for_piece(piece)
            if target is not None:
                items.extend(target.items)
        if not items:
            return []
        return [WorkingOutput(OutputTarget.assembly(items), tuple(resolved),
                              label=f"{len(resolved)} ranges joined",
                              joined=True)]
    outputs = []
    for piece in resolved:
        target = target_for_piece(piece)
        if target is None:
            continue
        outputs.append(WorkingOutput(target, (piece,), label=piece_label(piece)))
    return outputs


def piece_label(piece) -> str:
    """What to call one output: its recording, and its range when it has one."""
    clip = getattr(piece, "clip", piece)
    name = getattr(getattr(clip, "path", None), "name", "") or "this recording"
    ranges = getattr(clip, "real_selects", None) or []
    if not ranges:
        return name
    current = min(getattr(clip, "current", 0), len(ranges) - 1)
    chosen = ranges[max(0, current)]
    return f"{name} · {chosen.name}" if chosen.name else name
