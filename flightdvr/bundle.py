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

"""Several presets out of one range or one assembly, planned before anything moves.

One reviewed range usually needs more than one result: a master to keep, a
compact copy to send, a vertical one to post. Doing that today means pressing
Add to queue, changing a radio button, and pressing it again, which is ceremony
and forgets which settings produced which file.

Nothing here mutates a queue or touches Qt. It renders what *would* be queued —
every target name, its size and its runtime — so the confirmation can show the
whole result before the first job exists, and so a member the app already knows
would fail is explained rather than queued. `ui.py` does the mutating, once, out
of what this returned.

"One action" is queueing, not a transaction. The jobs stay ordinary independent
jobs on the one worker: if the third fails, the two finished files are still
good and are not deleted to simulate a rollback.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from .format import (
    BadTemplate, UnknownTemplateField, check_stem, check_template,
    expand_template, export_fields, output_key,
)
from .media import ClipInfo
from .presets import (
    PRESETS, PRESET_ORDER, ExportSettings, describe_join_problems,
    estimate_output_size, join_problems, output_runtime, slow_problems,
    templated_output_path, vertical_problems,
)


@dataclass(frozen=True)
class Piece:
    """One unit of material, and where it sits among the ranges of its clip.

    `index` and `total` are what `export_fields` needs to decide whether the
    name carries a range number at all, so they are carried from where the
    grouping is known rather than rediscovered here. Getting them from the
    flattened list would number every range of a card in one sequence, and the
    single-preset path numbers them per recording.
    """

    clip: ClipInfo
    index: int = 0
    total: int = 1


@dataclass(frozen=True)
class PlannedJob:
    """One job this member would add: the material, its name and its target."""

    clips: list[ClipInfo]
    stem: str
    target: Path


@dataclass
class Member:
    """One preset in the bundle, planned or refused.

    A member that cannot be produced carries the reason instead of the jobs.
    Both are useful: the confirmation shows a refused member greyed out with
    its explanation, rather than hiding it, because "why can I not have a
    vertical of this" is a question the panel should answer where it is asked.
    """

    key: str
    jobs: list[PlannedJob] = field(default_factory=list)
    problem: str = ""
    size: int = 0
    runtime: float = 0.0

    @property
    def label(self) -> str:
        return PRESETS[self.key].label

    @property
    def usable(self) -> bool:
        return not self.problem and bool(self.jobs)


def _recordings(pieces: list[Piece]) -> list[ClipInfo]:
    """One clip per recording, for the checks that report by filename.

    Two ranges of one recording share its dimensions and its frame rate, so
    asking about both names the same file twice in the refusal — and unlike
    the single-preset path, where the refusal is a message box somebody
    dismisses, this text sits in the checklist for as long as the dialog is
    open. Seen in the native shots: a two-range clip produced "hdz_047.ts is
    320x720 and needs at least 396x704 source pixels" twice in one line.
    """
    unique: dict[str, ClipInfo] = {}
    for piece in pieces:
        unique.setdefault(str(piece.clip.path), piece.clip)
    return list(unique.values())


def plan_member(key: str, pieces: list[Piece], *, joined: bool,
                out_dir: Path, template: str, subfolders: bool,
                stamp, session_name: str,
                settings: ExportSettings) -> Member:
    """What one preset would add, or why it cannot.

    The order of the checks is the order `_add_to_queue` uses, and for the same
    reason: a pilot choosing Vertical should hear about a narrow source now,
    not after the queue has reached the front of it.
    """
    member = Member(key)
    if not pieces:
        member.problem = "there is nothing to export"
        return member

    clips = [p.clip for p in pieces]

    if key == "vertical":
        problems = vertical_problems(_recordings(pieces))
        if problems:
            member.problem = "; ".join(problems)
            return member

    if joined:
        problems = join_problems(clips, re_encoding=key != "remux",
                                 slowing=key == "slowmo")
        if problems:
            member.problem = describe_join_problems(clips, problems)
            return member
    elif key == "slowmo":
        problems = slow_problems(_recordings(pieces))
        if problems:
            member.problem = "; ".join(problems)
            return member

    suffix = PRESETS[key].suffix
    try:
        check_template(template)
        if joined:
            fields = export_fields(clips[0], 0, 1, suffix,
                                   flight_date=stamp, session_name=session_name)
            # The same marker the single-preset join uses, in the same place —
            # inside the clip field rather than appended to the rendered name —
            # so a bundle names a join exactly as pressing Add to queue would.
            fields["clip"] = f"{fields['clip']}_joined"
            stem = expand_template(template, fields)
            check_stem(stem)
            member.jobs = [PlannedJob(
                list(clips), stem,
                templated_output_path(out_dir, stem, key, subfolders))]
        else:
            planned: list[PlannedJob] = []
            for piece in pieces:
                stem = expand_template(template, export_fields(
                    piece.clip, piece.index, piece.total, suffix,
                    flight_date=stamp, session_name=session_name,
                ))
                check_stem(stem)
                planned.append(PlannedJob(
                    [piece.clip], stem,
                    templated_output_path(out_dir, stem, key, subfolders)))
            member.jobs = planned
    except (UnknownTemplateField, BadTemplate) as exc:
        member.problem = str(exc)
        return member

    # Two ranges of this one preset landing on one name is this member's own
    # problem, not the bundle's: only one file would ever exist, and queueing
    # either of them would hide that. Refused here so the member is offered
    # with the reason rather than silently producing fewer files than it says.
    seen: set[str] = set()
    for job in member.jobs:
        identity = output_key(job.target)
        if identity in seen:
            member.problem = (
                f"two of these ranges would both be written to "
                f"{Path(identity).name}"
            )
            member.jobs = []
            return member
        seen.add(identity)

    member.size = _member_size(member, key, settings, joined)
    footage = sum(c.trimmed_duration or c.duration for c in clips)
    member.runtime = output_runtime(key, footage)
    return member


def _member_size(member: Member, key: str, settings: ExportSettings,
                 joined: bool) -> int:
    """What this member is expected to write, in bytes.

    A size-targeted Social join is the number that was asked for rather than
    the sum of its parts, which is what the single-preset estimate already
    says about the same job.
    """
    if joined and key == "social" and settings.social_mode == "size":
        return settings.social_size_mb * 1024 * 1024
    return sum(estimate_output_size(clip, key, settings)
               for job in member.jobs for clip in job.clips)


def plan_bundle(keys, pieces: list[Piece], **context) -> list[Member]:
    """Every offered preset, planned, in the order the panel shows them.

    Every preset is planned rather than only the ticked ones, because the
    checklist has to say which of them are unavailable before anybody ticks
    anything.
    """
    wanted = [key for key in PRESET_ORDER if key in set(keys)]
    return [plan_member(key, pieces, **context) for key in wanted]


def collisions(members: list[Member], already: set[str]) -> list[str]:
    """Names two chosen members would share, or that the queue already holds.

    Across members this is a real possibility rather than a defensive check: a
    template with no `{preset}` field and subfolders turned off gives Master,
    Social, Upload, Vertical and Slow motion the same name and the same `.mp4`
    extension, so five jobs would write one file and the last one would win.

    Unlike the single-preset path, an output already queued is refused rather
    than skipped. There the skip is ordinary — re-ticking clips queued a moment
    ago — but a bundle is one deliberate action whose whole promise is that the
    listed files are the files that get made.
    """
    problems: list[str] = []
    owner: dict[str, str] = {}
    for member in members:
        for job in member.jobs:
            identity = output_key(job.target)
            name = Path(identity).name
            if identity in already:
                problems.append(f"{name} is already in the queue")
            elif identity in owner:
                problems.append(
                    f"{name} would be written by both {owner[identity]} "
                    f"and {member.label}")
            else:
                owner[identity] = member.label
    return problems


def frozen_settings(settings: ExportSettings) -> ExportSettings:
    """A copy, so a member keeps the settings it was queued with.

    The panel's own object goes on being edited after the dialog closes, and
    an export is a promise about the settings that were on screen when it was
    confirmed.
    """
    return replace(settings)
