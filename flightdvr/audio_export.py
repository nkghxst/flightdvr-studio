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

"""Compile one resolved audio plan into the existing FFmpeg export command."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .audio_plan import AudioMode, OutputAudioPlan
from .media import NO_WINDOW, Tools


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_music_asset(plan: OutputAudioPlan) -> None:
    """Refuse a missing or replaced external track before starting export."""
    if plan.asset is None:
        return
    try:
        actual = file_sha256(plan.asset.track)
    except OSError as exc:
        raise ValueError(f"music file cannot be read: {exc}") from exc
    if actual != plan.asset.sha256:
        raise ValueError("music file has changed since it was selected")


def music_input_args(plan: OutputAudioPlan) -> list[str]:
    if plan.mode not in (AudioMode.REPLACE, AudioMode.MIX):
        return []
    assert plan.asset is not None
    return ["-i", str(plan.asset.track)]


def _decimal(value) -> str:
    return f"{float(value):.12g}"


def audio_filter_args(plan: OutputAudioPlan, *, source_seek_samples: int = 0
                      ) -> list[str]:
    """Explicit graph/map for Replace or Mix; output is always exactly T."""
    if plan.mode not in (AudioMode.REPLACE, AudioMode.MIX):
        return []
    assert plan.asset is not None and plan.passage is not None
    stream = plan.asset.stream_index
    start, end = plan.passage.start, plan.passage.end
    total = plan.output.samples
    music = (
        f"[1:a:{stream}]atrim=start_sample={start}:end_sample={end},"
        f"asetpts=PTS-STARTPTS,aresample={plan.output.rate},"
        "aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"atrim=end_sample={plan.music_samples}"
    )
    if plan.short_track.value == "loop":
        music += f",aloop=loop=-1:size={plan.music_samples}:start=0"
    else:
        music += f",apad=whole_len={total}"
    music += f",atrim=end_sample={total}"
    if plan.fade_in_samples:
        music += (f",afade=t=in:ss=0:ns={plan.fade_in_samples}:curve=tri")
    if plan.fade_out_samples:
        music += (f",afade=t=out:ss={plan.audible_samples - plan.fade_out_samples}:"
                  f"ns={plan.fade_out_samples}:curve=tri")
    music += f",volume={_decimal(plan.music_gain)}"
    if source_seek_samples:
        # The ordinary trim's accurate output-side seek applies to every mapped
        # stream. Give music that timestamp origin so the seek lands on passage
        # sample zero instead of advancing its loop/fade by the source in point.
        music += f",asetpts=PTS+{source_seek_samples}/{plan.output.rate}/TB"
    music += "[music]"
    chains = [music]
    if plan.mode is AudioMode.MIX and plan.source_has_audio:
        source_total = total + source_seek_samples
        dvr = (
            f"[0:a:0]aresample={plan.output.rate}:async=1:first_pts=0,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"atrim=end_sample={source_total},apad=whole_len={source_total},"
            f"atrim=end_sample={source_total},volume={_decimal(plan.dvr_gain)}[dvr]"
        )
        chains.append(dvr)
        chains.append(
            f"[music][dvr]amix=inputs=2:duration=longest:normalize=0,"
            f"atrim=end_sample={total}[planned_audio]")
    else:
        chains.append("[music]anull[planned_audio]")
    return ["-filter_complex", ";".join(chains),
            "-map", "0:v:0", "-map", "[planned_audio]"]


def validate_expected_audio(tools: Tools, path: Path,
                            plan: OutputAudioPlan) -> tuple[bool, str]:
    """The existing video validator's audio counterpart before publication."""
    result = subprocess.run(
        [str(tools.ffprobe), "-v", "error", "-show_streams", "-of", "json",
         str(path)], capture_output=True, text=True, timeout=60,
        creationflags=NO_WINDOW,
    )
    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError:
        streams = []
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    expected = (plan.mode in (AudioMode.REPLACE, AudioMode.MIX)
                or plan.mode is AudioMode.ORIGINAL and plan.source_has_audio)
    if expected and not audio:
        return False, "ffmpeg produced no audio for the submitted audio plan"
    if not expected and audio:
        return False, "ffmpeg produced audio for a No sound plan"
    if audio:
        rate = int(audio[0].get("sample_rate") or 0)
        channels = int(audio[0].get("channels") or 0)
        if rate != plan.output.rate or channels != 2:
            return False, f"ffmpeg produced unexpected audio format: {rate} Hz, {channels} channels"
    return True, ""
