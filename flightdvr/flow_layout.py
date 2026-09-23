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

"""Which view the window is in, and which stage of Flow it is showing.

Policy only. Nothing here imports Qt, holds a widget or knows that a window
exists: it answers what the order is, which stages can honestly be offered
yet, and where Back and Next go. `ui.py` owns every widget, the stacking and
the reparenting, and asks these questions rather than deciding them itself —
so the order can be tested without building a window, and the window has one
place to change when a stage becomes real.

The stage order is not invented here. It is the order of the approved Flow
user revision, `flow_user.py:36`:

    STAGES = ["Browse", "Trim", "Assemble", "Music", "Output", "Queue"]
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    """How the same session is being presented."""

    CLASSIC = "classic"
    FLOW = "flow"


class Stage(str, Enum):
    """One page of Flow, in the approved order."""

    BROWSE = "browse"
    TRIM = "trim"
    ASSEMBLE = "assemble"
    MUSIC = "music"
    OUTPUT = "output"
    QUEUE = "queue"


# The approved order, and the only place it is written down.
STAGE_ORDER: tuple[Stage, ...] = (
    Stage.BROWSE, Stage.TRIM, Stage.ASSEMBLE, Stage.MUSIC, Stage.OUTPUT,
    Stage.QUEUE,
)

TITLES: dict[Stage, str] = {
    Stage.BROWSE: "Browse",
    Stage.TRIM: "Trim",
    Stage.ASSEMBLE: "Assemble",
    Stage.MUSIC: "Music",
    Stage.OUTPUT: "Output",
    Stage.QUEUE: "Queue",
}


def mode_from_stored(value) -> Mode:
    """The mode a stored setting asks for, or Classic.

    Classic is the default and the fallback, per issue #84: an unreadable,
    unknown or missing value is not an error to report at somebody, it is a
    window that opens the way it always did.
    """
    try:
        return Mode(str(value).strip().lower())
    except (ValueError, AttributeError, TypeError):
        return Mode.CLASSIC


def offered_stages(built) -> tuple[Stage, ...]:
    """The stages that can honestly be shown, in order.

    A stage with nothing behind it yet is left out rather than offered empty.
    An empty page that a stage bar advertises is worse than a stage bar with a
    gap in it: the first says the feature is there and broken, the second says
    it is not there yet — and this slice is a foundation, not six finished
    pages.
    """
    chosen = {Stage(stage) for stage in built}
    return tuple(stage for stage in STAGE_ORDER if stage in chosen)


def first_stage(offered) -> Stage | None:
    ordered = tuple(offered)
    return ordered[0] if ordered else None


def neighbours(current: Stage, offered) -> tuple[Stage | None, Stage | None]:
    """What Back and Next reach from here, skipping what is not built.

    `None` on either side means the button is at an end and has nothing to do,
    which the window shows as disabled rather than as a button that silently
    does nothing.
    """
    ordered = tuple(offered)
    if current not in ordered:
        return None, None
    index = ordered.index(current)
    back = ordered[index - 1] if index > 0 else None
    forward = ordered[index + 1] if index + 1 < len(ordered) else None
    return back, forward


def stage_from_stored(value, offered) -> Stage | None:
    """The stage a stored setting asks for, if it is still one we offer."""
    ordered = tuple(offered)
    try:
        stage = Stage(str(value).strip().lower())
    except (ValueError, AttributeError, TypeError):
        return first_stage(ordered)
    return stage if stage in ordered else first_stage(ordered)


def title(stage: Stage) -> str:
    return TITLES[Stage(stage)]


# -- what each page is made of -------------------------------------------------


class Region(str, Enum):
    """The parts a page can be built from.

    Named rather than implied so the geometry can be checked without a window,
    and so "Queue has no picture" is a fact a test can read rather than
    something you confirm by looking at a screenshot.
    """

    LIST = "list"          # the tall recordings table
    VIEWPORT = "viewport"  # the one shared picture and its transport
    PANEL = "panel"        # this page's own panel


REGIONS: dict[Stage, tuple[Region, ...]] = {
    # The recordings list is tall and *beside* a narrower picture column, which
    # is the whole point of the approved Browse: a list you can read without
    # the picture sitting on top of it.
    Stage.BROWSE: (Region.LIST, Region.VIEWPORT, Region.PANEL),
    Stage.TRIM: (Region.VIEWPORT, Region.PANEL),
    Stage.ASSEMBLE: (Region.VIEWPORT, Region.PANEL),
    Stage.MUSIC: (Region.VIEWPORT, Region.PANEL),
    Stage.OUTPUT: (Region.VIEWPORT, Region.PANEL),
    # Queue has no viewport, and that is settled rather than pending. The only
    # picture available for a committed job would be its *source*, which this
    # release refuses to dress as output, and a partial file is never played.
    # So the body goes to the jobs and to what they were submitted with.
    Stage.QUEUE: (Region.PANEL,),
}


def regions_for(stage: Stage) -> tuple[Region, ...]:
    return REGIONS[Stage(stage)]


def shows_viewport(stage: Stage) -> bool:
    """Whether this page borrows the one picture at all.

    Borrows, never owns: a page that hid a viewport it still held would keep a
    decoder alive for a page that shows nothing, which is how Queue would get
    its space dishonestly.
    """
    return Region.VIEWPORT in regions_for(stage)


def shows_list(stage: Stage) -> bool:
    return Region.LIST in regions_for(stage)


# -- what is selected, and everything that follows from it ---------------------
#
# One value answers "what is being looked at", and the pages read it. It is
# deliberately here rather than in the shell: W5's picture recipe has to consume
# the same value, and a recipe that needed a window to find out what it was
# rendering would be a second authority by construction.
#
# `Stage` does not appear below on purpose. Navigation changes geometry and
# labels; it does not choose material. A page that derived its clock from its
# own name would flip from source to output seconds merely because somebody
# pressed Next, which is the same wrong-target defect as editing the output you
# were not looking at, reached by a different route.


class Domain(str, Enum):
    """What kind of thing is selected."""

    SOURCE = "source"        # a recording, inspected on its own time
    WORKING = "working"      # an editable planned output
    SUBMITTED = "submitted"  # a committed job, read-only for ever
    NOTHING = "nothing"      # nothing resolves; carries the reason why


class Clock(str, Enum):
    """Which time the transport and strips are asserting."""

    SOURCE = "source"
    OUTPUT = "output"


@dataclass(frozen=True)
class OccurrenceSource:
    """One occurrence and the recording it reads, held by value.

    Primitive strings rather than a live `ClipInfo`: this travels into a
    revision and into W5, and an alias to a mutable object would let the
    material change underneath a context that claims to be immutable.
    """

    ordinal: int
    fingerprint: str
    sid: str
    source_path: str

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("an occurrence ordinal is a non-negative integer")
        for name in ("fingerprint", "sid", "source_path"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"occurrence {name} must be a string")


def occurrences_of(sequence) -> tuple[OccurrenceSource, ...]:
    """Snapshot a compiled plan's occurrences as primitives, in order."""
    if sequence is None:
        return ()
    return tuple(
        OccurrenceSource(
            one.id.ordinal, one.fingerprint, one.sid, one.source_path)
        for one in sequence.occurrences
    )


def material_revision(
    domain: Domain,
    occurrences: tuple[OccurrenceSource, ...] = (),
    *,
    submitted: str = "",
    source_path: str = "",
) -> str:
    """A stable identity for the *material*, derived rather than remembered.

    Order, range and source identity go in; settings and music deliberately do
    not. Changing a preset or a track is an edit to the same material and must
    not fence a decoder mid-play; reordering an Assembly or retrimming a range
    is different material and must.

    Derived, because a revision somebody has to remember to bump is a revision
    that eventually is not bumped — and the symptom is a stale frame or a stale
    PCM block painted against new material, which looks like a glitch rather
    than like the bookkeeping error it is.
    """
    parts = [Domain(domain).value, submitted, source_path]
    parts.extend(
        f"{one.ordinal}:{one.fingerprint}:{one.sid}:{one.source_path}"
        for one in occurrences
    )
    digest = hashlib.sha1("\u0000".join(parts).encode("utf-8"))
    return digest.hexdigest()[:20]


@dataclass(frozen=True)
class SelectedContext:
    """The one thing every Flow page reads to know what it is showing.

    Sol's field list from `SOL_PREVIEW_CONTRACT_FEEDBACK_20260922.md`, adopted
    whole so W1 and W5 cannot grow parallel answers to the same question.
    """

    domain: Domain
    revision: str
    target: object | None = None          # exact OutputTarget when it is an output
    sequence: object | None = None        # resolved SequencePlan, when there is one
    occurrences: tuple[OccurrenceSource, ...] = ()
    preset_key: str = ""
    settings: object | None = None        # defensive snapshot, never a live alias
    music: object | None = None           # defensive snapshot
    submitted: str = ""                   # job identity, when committed
    source_path: str = ""                 # the recording, when the domain is source
    refusal: str = ""                     # why nothing can be shown, in words

    def __post_init__(self) -> None:
        domain = Domain(self.domain)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "occurrences", tuple(self.occurrences))
        if not isinstance(self.revision, str) or not self.revision:
            raise ValueError("a selected context needs a revision")
        if domain is Domain.NOTHING:
            if not self.refusal:
                raise ValueError(
                    "an unresolved context must say why, in words somebody "
                    "can read")
            return
        if domain in (Domain.WORKING, Domain.SUBMITTED) and self.target is None:
            raise ValueError(f"a {domain.value} context needs its exact target")
        if domain is Domain.SUBMITTED and not self.submitted:
            raise ValueError("a submitted context needs its job identity")
        if domain is Domain.SOURCE and not self.source_path:
            raise ValueError("a source context needs the recording it reads")
        if self.sequence is not None and len(self.occurrences) != len(
                self.sequence.occurrences):
            raise ValueError(
                "occurrence snapshot does not match the compiled sequence")

    # -- what the pages ask it -------------------------------------------------

    @property
    def resolved(self) -> bool:
        return self.domain is not Domain.NOTHING and not self.refusal

    @property
    def editable(self) -> bool:
        """Only a working output, and only while it resolves.

        A submitted job is read-only for ever: its settings are the ones it was
        made with, and the queue's own note says editing the plan it came from
        does not reach it.
        """
        return self.domain is Domain.WORKING and self.resolved

    @property
    def clock(self) -> Clock:
        """Source time, or the finished output's time.

        An ordinary single-range output is still an output: its first sample is
        output zero while the recording is somewhere else entirely, and reading
        one as the other is the defect this property exists to make impossible
        to reach by accident.
        """
        if self.domain is Domain.SOURCE:
            return Clock.SOURCE
        if self.domain is Domain.NOTHING:
            return Clock.SOURCE
        return Clock.OUTPUT

    @property
    def bound_to_sequence(self) -> bool:
        return self.sequence is not None

    def same_material(self, other: "SelectedContext | None") -> bool:
        """Whether a callback from `other` still belongs to this context."""
        return other is not None and other.revision == self.revision


def nothing_selected(reason: str) -> SelectedContext:
    """No material, and the reason a person can read."""
    return SelectedContext(
        domain=Domain.NOTHING,
        revision=material_revision(Domain.NOTHING),
        refusal=reason,
    )
