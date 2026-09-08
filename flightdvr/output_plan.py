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
from pathlib import Path
from typing import Iterable

from .assembly import Item
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
        if any(not isinstance(item, Item) or not item.fingerprint
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
class MusicChoice:
    """The optional music source selected for one output.

    Timing, gain, decoding and persistence belong to later audio integration.
    Keeping this first value small avoids making prototype controls into a
    production contract while still establishing per-output ownership.
    """

    track: Path | None = None

    def __post_init__(self) -> None:
        if self.track is None:
            return
        track = Path(self.track)
        if not str(track).strip() or str(track) == ".":
            raise ValueError("a music track path cannot be empty")
        object.__setattr__(self, "track", track)


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
