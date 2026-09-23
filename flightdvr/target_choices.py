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

"""Each planned output's choices, written down and read back exactly.

No Qt and no files here: the session stores what this returns, and the window
decides what to do with what it reads. Two rules shape it.

Music is exact. A passage is three integers on the track's own sample clock,
a level is a numerator and a denominator, and a fade is integer output
samples. Nothing goes through seconds or a float, so reopening gives back the
very choice that was saved rather than one a rounding step away from it.

A saved track is a *claim*, not an asset. The file may have moved or been
rewritten since, so what is read back is a :class:`SavedMusic` naming the
bytes that were chosen. It becomes an executable choice again only when a fresh
read of the file matches it (:meth:`SavedMusic.mismatch`); until then the
window holds the requests and refuses to play or queue them.

A malformed entry is skipped and reported. It never takes its valid
neighbours or the rest of the session down with it.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from fractions import Fraction
from pathlib import Path

from .assembly import Item
from .audio_plan import (
    AudioAsset, AudioMode, MusicChoice, SampleSpan, ShortTrackPolicy)
from .output_plan import OutputTarget, PlannedOutput
from .presets import PRESETS, ExportSettings

# Worked out afresh on every machine, never carried in a file: the encoder the
# saving computer had may not exist on the one reopening it.
MACHINE_FIELDS = frozenset({"hw_encoder"})


class Malformed(ValueError):
    """One stored entry could not be read as a choice."""


# -- small typed readers --------------------------------------------------------


def _integer(raw, what: str, *, minimum: int = 0) -> int:
    # bool is an int to Python and never an int to this format.
    if type(raw) is not int:
        raise Malformed(f"{what} must be a whole number")
    if raw < minimum:
        raise Malformed(f"{what} must be at least {minimum}")
    return raw


def _text(raw, what: str, *, empty: bool = True) -> str:
    if not isinstance(raw, str):
        raise Malformed(f"{what} must be text")
    if not empty and not raw.strip():
        raise Malformed(f"{what} cannot be empty")
    return raw


def _flag(raw, what: str) -> bool:
    if type(raw) is not bool:
        raise Malformed(f"{what} must be true or false")
    return raw


def _mapping(raw, what: str) -> dict:
    if not isinstance(raw, dict):
        raise Malformed(f"{what} must be an object")
    return raw


def _ratio(raw, what: str) -> Fraction:
    if (not isinstance(raw, list) or len(raw) != 2
            or any(type(part) is not int for part in raw)):
        raise Malformed(f"{what} must be [numerator, denominator]")
    numerator, denominator = raw
    if denominator <= 0:
        raise Malformed(f"{what} needs a positive denominator")
    value = Fraction(numerator, denominator)
    if not 0 <= value <= 1:
        raise Malformed(f"{what} must be between zero and one")
    return value


def _ratio_out(value: Fraction) -> list[int]:
    value = Fraction(value)
    return [value.numerator, value.denominator]


def _enum(kind, raw, what: str):
    try:
        return kind(_text(raw, what))
    except ValueError:
        raise Malformed(f"{what} is not a value this version knows") from None


# -- targets ------------------------------------------------------------------


def encode_target(target: OutputTarget) -> dict:
    """Ordered item identities, repeats kept: an Assembly *is* its order."""
    return {"assembly": target.is_assembly,
            "items": [item.as_dict() for item in target.items]}


def decode_target(raw) -> OutputTarget:
    raw = _mapping(raw, "target")
    assembly = _flag(raw.get("assembly", False), "target assembly")
    stored = raw.get("items")
    if not isinstance(stored, list) or not stored:
        raise Malformed("target items must be a non-empty list")
    items = []
    for entry in stored:
        entry = _mapping(entry, "target item")
        items.append(Item(_text(entry.get("clip"), "target clip", empty=False),
                          _text(entry.get("range", ""), "target range")))
    try:
        return OutputTarget(tuple(items), is_assembly=assembly)
    except ValueError as problem:
        raise Malformed(str(problem)) from None


# -- settings -----------------------------------------------------------------


def encode_settings(settings: ExportSettings) -> dict:
    return {one.name: getattr(settings, one.name)
            for one in fields(ExportSettings)
            if one.name not in MACHINE_FIELDS}


def decode_settings(raw, *, hw_encoder: str = "") -> ExportSettings:
    """Each known field checked against its own type; missing fields take the
    defaults, unknown ones are ignored, and the machine's encoder is this
    machine's."""
    raw = _mapping(raw, "settings")
    values = {}
    defaults = ExportSettings()
    for one in fields(ExportSettings):
        if one.name in MACHINE_FIELDS or one.name not in raw:
            continue
        value, kind = raw[one.name], type(getattr(defaults, one.name))
        if kind is bool:
            values[one.name] = _flag(value, one.name)
        elif kind is int:
            values[one.name] = _integer(value, one.name)
        else:
            values[one.name] = _text(value, one.name)
    return ExportSettings(**values, hw_encoder=hw_encoder)


# -- music --------------------------------------------------------------------


@dataclass(frozen=True)
class SavedMusic:
    """A track and passage chosen earlier, not yet confirmed to be the same
    bytes now.

    Everything asked for is held exactly. ``requested`` is what the window
    may show and edit meanwhile: the same mode, levels, fades and policy, on
    the same track, with no asset and so no passage — which every route that
    plays or queues already refuses.
    """

    track: Path
    sha256: str
    stream_index: int
    sample_rate: int
    channels: int
    decoded_samples: int
    passage: SampleSpan | None
    mode: AudioMode
    short_track: ShortTrackPolicy
    music_level: Fraction
    dvr_level: Fraction
    fade_in_samples: int
    fade_out_samples: int

    def __post_init__(self) -> None:
        if self.mode not in (AudioMode.REPLACE, AudioMode.MIX):
            raise Malformed("a saved track belongs to Replace or Mix")
        # The same rules the asset it names will be held to.
        try:
            reference = AudioAsset(
                self.track, self.sha256, self.stream_index, self.sample_rate,
                self.channels, self.decoded_samples)
            if self.passage is not None:
                MusicChoice(mode=self.mode, asset=reference,
                            passage=self.passage)
            self.requested
        except ValueError as problem:
            raise Malformed(str(problem)) from None
        object.__setattr__(self, "track", reference.track)
        object.__setattr__(self, "sha256", reference.sha256)

    @classmethod
    def of(cls, choice: MusicChoice) -> "SavedMusic":
        asset = choice.asset
        if asset is None:
            raise ValueError("only a read track can be saved as a reference")
        return cls(
            asset.track, asset.sha256, asset.stream_index, asset.sample_rate,
            asset.channels, asset.decoded_samples, choice.passage,
            choice.mode, choice.short_track, choice.music_level,
            choice.dvr_level, choice.fade_in_samples, choice.fade_out_samples)

    @property
    def requested(self) -> MusicChoice:
        return MusicChoice(
            track=self.track, mode=self.mode, short_track=self.short_track,
            music_level=self.music_level, dvr_level=self.dvr_level,
            fade_in_samples=self.fade_in_samples,
            fade_out_samples=self.fade_out_samples)

    def with_requests(self, edited: MusicChoice) -> "SavedMusic":
        """An edit made while unconfirmed, kept without touching the track or
        the passage it cannot see."""
        return replace(
            self, mode=edited.mode, short_track=edited.short_track,
            music_level=edited.music_level, dvr_level=edited.dvr_level,
            fade_in_samples=edited.fade_in_samples,
            fade_out_samples=edited.fade_out_samples)

    def mismatch(self, asset: AudioAsset) -> str:
        """Why a fresh read is not the saved track, or "" if it is."""
        if asset.sha256 != self.sha256:
            return "it has changed since it was chosen"
        if asset.stream_index != self.stream_index:
            return "its audio is in a different stream now"
        if asset.sample_rate != self.sample_rate:
            return "its sample rate has changed"
        if (self.passage is not None
                and self.passage.end > asset.decoded_samples):
            return "it is shorter than the passage chosen from it"
        return ""

    def resolved(self, asset: AudioAsset) -> MusicChoice:
        """The exact saved choice on a freshly confirmed asset."""
        if self.mismatch(asset):
            raise ValueError("the read track is not the saved one")
        return replace(self.requested, asset=asset, passage=self.passage)


def _levels_out(choice) -> dict:
    return {"short_track": ShortTrackPolicy(choice.short_track).value,
            "music_level": _ratio_out(choice.music_level),
            "dvr_level": _ratio_out(choice.dvr_level),
            "fade_in_samples": choice.fade_in_samples,
            "fade_out_samples": choice.fade_out_samples}


def encode_music(choice: MusicChoice, pending: SavedMusic | None = None):
    """The stored form of one output's music, or None for no choice at all.

    ``pending`` wins over ``choice``: an unconfirmed saved track is written
    back exactly as it was read, not as the track-only stand-in the window
    shows meanwhile.
    """
    if pending is not None:
        stored = {"mode": pending.mode.value, "track": str(pending.track),
                  "reference": {"sha256": pending.sha256,
                                "stream": pending.stream_index,
                                "rate": pending.sample_rate,
                                "channels": pending.channels,
                                "samples": pending.decoded_samples}}
        if pending.passage is not None:
            stored["passage"] = [pending.passage.start, pending.passage.end,
                                 pending.passage.rate]
        stored.update(_levels_out(pending))
        return stored
    if not choice.configured:
        return None
    if choice.asset is not None:
        return encode_music(choice, SavedMusic.of(choice))
    stored = {"mode": choice.mode.value}
    if choice.track is not None:
        stored["track"] = str(choice.track)
    stored.update(_levels_out(choice))
    return stored


def decode_music(raw) -> tuple[MusicChoice, SavedMusic | None]:
    """What the window holds now, and the saved track it must confirm.

    A choice that names a read track comes back as its track-only stand-in
    plus the :class:`SavedMusic` to confirm. One that was never read (a track
    without a reference) comes back as the same unread choice, which the
    queue refuses exactly as it did before saving.
    """
    if raw is None:
        return MusicChoice(), None
    raw = _mapping(raw, "music")
    mode = _enum(AudioMode, raw.get("mode"), "music mode")
    requests = dict(
        short_track=_enum(ShortTrackPolicy,
                          raw.get("short_track", ShortTrackPolicy.LOOP.value),
                          "short-track policy"),
        music_level=(_ratio(raw["music_level"], "music level")
                     if "music_level" in raw else Fraction(1)),
        dvr_level=(_ratio(raw["dvr_level"], "recording level")
                   if "dvr_level" in raw else Fraction(1, 4)),
        fade_in_samples=_integer(raw.get("fade_in_samples", 48_000),
                                 "fade in"),
        fade_out_samples=_integer(raw.get("fade_out_samples", 96_000),
                                  "fade out"),
    )
    track = raw.get("track")
    if track is not None:
        track = Path(_text(track, "music track", empty=False))
    reference = raw.get("reference")
    if reference is None:
        if "passage" in raw:
            raise Malformed("a passage needs the track it was chosen from")
        try:
            return MusicChoice(track=track, mode=mode, **requests), None
        except ValueError as problem:
            raise Malformed(str(problem)) from None
    if track is None:
        raise Malformed("a track reference needs its path")
    reference = _mapping(reference, "track reference")
    passage = raw.get("passage")
    if passage is not None:
        if (not isinstance(passage, list) or len(passage) != 3
                or any(type(part) is not int for part in passage)):
            raise Malformed("a passage is [start, end, rate] in samples")
        try:
            passage = SampleSpan(*passage)
        except ValueError as problem:
            raise Malformed(str(problem)) from None
    saved = SavedMusic(
        track=track,
        sha256=_text(reference.get("sha256"), "track digest", empty=False),
        stream_index=_integer(reference.get("stream"), "track stream"),
        sample_rate=_integer(reference.get("rate"), "track rate", minimum=1),
        channels=_integer(reference.get("channels"), "track channels",
                          minimum=1),
        decoded_samples=_integer(reference.get("samples"), "track length",
                                 minimum=1),
        passage=passage, mode=mode, **requests)
    return saved.requested, saved


# -- whole outputs ----------------------------------------------------------------


@dataclass(frozen=True)
class SavedOutput:
    planned: PlannedOutput
    pending: SavedMusic | None = None

    @property
    def target(self) -> OutputTarget:
        return self.planned.target


def encode_output(planned: PlannedOutput,
                  pending: SavedMusic | None = None) -> dict:
    stored = {"target": encode_target(planned.target),
              "preset": planned.preset_key,
              "settings": encode_settings(planned.settings)}
    music = encode_music(planned.music, pending)
    if music is not None:
        stored["music"] = music
    return stored


def decode_output(raw, *, hw_encoder: str = "") -> SavedOutput:
    raw = _mapping(raw, "output")
    target = decode_target(raw.get("target"))
    preset = _text(raw.get("preset"), "preset", empty=False)
    if preset not in PRESETS:
        raise Malformed(f"{preset!r} is not a preset this version knows")
    settings = decode_settings(raw.get("settings", {}), hw_encoder=hw_encoder)
    music, pending = decode_music(raw.get("music"))
    return SavedOutput(PlannedOutput(target, preset, settings, music), pending)


def encode_outputs(entries, selected: OutputTarget | None) -> dict:
    """``entries`` are (PlannedOutput, SavedMusic | None) in plan order.

    The selection is stored as the target itself. A position would pick a
    neighbour the moment an entry before it failed to read.
    """
    stored = {"outputs": [encode_output(planned, pending)
                          for planned, pending in entries]}
    if selected is not None:
        stored["selected_output"] = encode_target(selected)
    return stored


@dataclass(frozen=True)
class DecodedOutputs:
    outputs: tuple[SavedOutput, ...]
    selected: OutputTarget | None
    problems: tuple[str, ...]
    # Entries this version could not read, exactly as stored, so saving again
    # does not quietly delete what a newer version (or a repair) could.
    unread: tuple = ()


def decode_outputs(outputs, selected=None, *,
                   hw_encoder: str = "") -> DecodedOutputs:
    """Every entry that reads, in order; each that does not is named in
    ``problems`` and skipped. A selection that names no surviving entry
    selects nothing."""
    read, problems, seen, unread = [], [], set(), []
    if not isinstance(outputs, list):
        return DecodedOutputs((), None, ("outputs must be a list",)
                              if outputs is not None else ())
    for index, raw in enumerate(outputs):
        try:
            one = decode_output(raw, hw_encoder=hw_encoder)
        except Malformed as problem:
            problems.append(f"output {index + 1}: {problem}")
            unread.append(raw)
            continue
        if one.target in seen:
            problems.append(f"output {index + 1}: repeats an earlier output")
            continue
        seen.add(one.target)
        read.append(one)
    chosen = None
    if selected is not None:
        try:
            chosen = decode_target(selected)
        except Malformed as problem:
            problems.append(f"selected output: {problem}")
        if chosen not in seen:
            chosen = None
    return DecodedOutputs(tuple(read), chosen, tuple(problems), tuple(unread))
