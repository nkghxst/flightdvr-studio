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


AUDIO_FRAME_SAMPLES = 1_024
AUDIO_SAMPLE_TOLERANCE = AUDIO_FRAME_SAMPLES


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


def planned_audio_chains(
    plan: OutputAudioPlan,
    *,
    music_input_index: int,
    source_label: str | None = None,
    source_seek_samples: int = 0,
    output_label: str = "planned_audio",
) -> tuple[list[str], str]:
    """Graph chains for one finished-time Replace or Mix decision.

    Input indices and the optional finished source label are explicit because
    a joined output has several source inputs before its one music input.  The
    caller owns video mapping and may compose these chains into an existing
    filter graph.
    """
    if plan.mode not in (AudioMode.REPLACE, AudioMode.MIX):
        raise ValueError("a planned music graph needs Replace or Mix")
    if type(music_input_index) is not int or music_input_index < 0:
        raise ValueError("music input index must be non-negative")
    if not output_label or any(mark in output_label for mark in "[];"):
        raise ValueError("audio output label is malformed")
    assert plan.asset is not None and plan.passage is not None
    stream = plan.asset.stream_index
    start, end = plan.passage.start, plan.passage.end
    total = plan.output.samples
    music = (
        f"[{music_input_index}:a:{stream}]"
        f"atrim=start_sample={start}:end_sample={end},"
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
        if not source_label or not source_label.startswith("["):
            raise ValueError(
                "Mix with source audio needs a finished source label")
        source_total = total + source_seek_samples
        dvr = (
            f"{source_label}aresample={plan.output.rate}:async=1:first_pts=0,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"atrim=start_sample={source_seek_samples}:end_sample={source_total},"
            f"asetpts=PTS-STARTPTS,apad=whole_len={total},"
            f"atrim=end_sample={total},volume={_decimal(plan.dvr_gain)},"
            f"asetpts=PTS+{source_seek_samples}/{plan.output.rate}/TB[dvr]"
        )
        chains.append(dvr)
        chains.append(
            f"[music][dvr]amix=inputs=2:duration=longest:normalize=0,"
            f"atrim=end_sample={total}[{output_label}]")
    else:
        chains.append(f"[music]anull[{output_label}]")
    return chains, f"[{output_label}]"


def audio_filter_args(plan: OutputAudioPlan, *, source_seek_samples: int = 0
                      ) -> list[str]:
    """Explicit single-source graph/map; output is always exactly T."""
    if plan.mode not in (AudioMode.REPLACE, AudioMode.MIX):
        return []
    chains, output = planned_audio_chains(
        plan, music_input_index=1, source_label="[0:a:0]",
        source_seek_samples=source_seek_samples,
    )
    return ["-filter_complex", ";".join(chains),
            "-map", "0:v:0", "-map", output]


def validate_expected_audio(tools: Tools, path: Path,
                            plan: OutputAudioPlan, *, cancelled=lambda: False,
                            expected_codec: str = "aac",
                            ) -> tuple[bool, str]:
    """The existing video validator's audio counterpart before publication.

    Refuses rather than guesses: a probe that fails or says something
    unreadable is never taken as "no audio", which would pass a No sound plan
    on a file nobody checked. Exactly one audio stream when sound is
    expected and none otherwise, in the codec this preset promises.

    The extent check is per codec. AAC keeps its existing whole-frame rule;
    PCM has no such frames, so it is counted exactly by decoding it.
    """
    pcm = expected_codec == "pcm_s16le"
    # Audio streams only: counting frames over every stream decoded the whole
    # picture as well, which on an Edit mezzanine is the expensive part.
    command = [str(tools.ffprobe), "-v", "error", "-select_streams", "a"]
    if not pcm:
        command.append("-count_frames")
    command += ["-show_streams", "-of", "json", str(path)]
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=NO_WINDOW,
    )
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=0.05)
            break
        except subprocess.TimeoutExpired:
            if cancelled():
                proc.terminate()
                try:
                    proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                return False, "Cancelled"
    if proc.returncode != 0:
        detail = (stderr or "").strip().splitlines()[-1:] or ["no detail"]
        return False, f"could not read the finished audio: {detail[0][:200]}"
    try:
        parsed = json.loads(stdout)
        streams = parsed["streams"]
        if not isinstance(streams, list):
            raise TypeError("streams is not a list")
    except (json.JSONDecodeError, KeyError, TypeError):
        return False, "could not read the finished audio: unreadable probe"
    audio = [s for s in streams
             if isinstance(s, dict) and s.get("codec_type") == "audio"]
    if len(audio) != len(streams):
        return False, "could not read the finished audio: unreadable probe"
    expected = (plan.mode in (AudioMode.REPLACE, AudioMode.MIX)
                or plan.mode is AudioMode.ORIGINAL and plan.source_has_audio)
    if expected and not audio:
        return False, "ffmpeg produced no audio for the submitted audio plan"
    if not expected and audio:
        return False, "ffmpeg produced audio for a No sound plan"
    if not audio:
        return True, ""
    if len(audio) != 1:
        return False, f"ffmpeg produced {len(audio)} audio streams, not one"
    stream = audio[0]
    try:
        codec = str(stream["codec_name"])
        rate = int(stream["sample_rate"])
        channels = int(stream["channels"])
    except (KeyError, TypeError, ValueError):
        return False, "could not read the finished audio: missing stream details"
    if codec != expected_codec:
        return False, (f"ffmpeg produced {codec} audio where this preset "
                       f"promises {expected_codec}")
    if rate != plan.output.rate or channels != 2:
        return False, f"ffmpeg produced unexpected audio format: {rate} Hz, {channels} channels"
    if pcm:
        counted, message = count_pcm_samples(tools, path, cancelled=cancelled)
        if counted is None:
            return False, message
        if counted != plan.output.samples:
            return False, (
                "ffmpeg produced the wrong audio extent: "
                f"{counted} decoded samples for a "
                f"{plan.output.samples}-sample plan"
            )
        return True, ""
    try:
        frames = int(stream.get("nb_read_frames") or 0)
    except (TypeError, ValueError):
        frames = 0
    decoded_samples = frames * AUDIO_FRAME_SAMPLES
    if (frames <= 0
            or abs(decoded_samples - plan.output.samples)
            > AUDIO_SAMPLE_TOLERANCE):
        return False, (
            "ffmpeg produced the wrong audio extent: "
            f"{decoded_samples} decoded samples for a "
            f"{plan.output.samples}-sample plan"
        )
    return True, ""


PCM_CHUNK_BYTES = 1 << 16
PCM_STDERR_TAIL = 4096


def count_pcm_samples(tools: Tools, path: Path, *, cancelled=lambda: False
                      ) -> tuple[int | None, str]:
    """Per-channel sample frames in the first audio stream, counted exactly.

    Decoded to 16-bit stereo-interleaved bytes on a pipe and counted in
    bounded chunks, never held: a mezzanine's sound is not read into memory.
    No rate or channel conversion is forced, so what is counted is what the
    file holds (the stream was already checked to be 48 kHz stereo s16le).

    Cancellation is observed while a read is waiting: the pipe is drained by
    a reader thread and this function polls the cancel flag between its
    reports. On cancel, failure or an exception the child is terminated,
    then killed if it lingers, and both reader threads are joined. A decode
    that fails or leaves a partial sample frame is never a count.
    """
    import queue
    import threading

    command = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error",
               "-i", str(path), "-map", "0:a:0", "-f", "s16le", "-"]
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=NO_WINDOW,
    )
    reports: queue.Queue = queue.Queue(maxsize=8)
    stop = threading.Event()
    tail = bytearray()

    def read_stdout() -> None:
        try:
            while not stop.is_set():
                data = proc.stdout.read(PCM_CHUNK_BYTES)
                while not stop.is_set():
                    try:
                        reports.put(len(data), timeout=0.05)
                        break
                    except queue.Full:
                        continue
                if not data:
                    return
        except (OSError, ValueError):
            reports.put(-1)

    def read_stderr() -> None:
        try:
            for line in iter(proc.stderr.readline, b""):
                tail.extend(line)
                del tail[:-PCM_STDERR_TAIL]
        except (OSError, ValueError):
            pass

    readers = [threading.Thread(target=read_stdout, daemon=True),
               threading.Thread(target=read_stderr, daemon=True)]
    for reader in readers:
        reader.start()

    def settle(kill: bool) -> None:
        stop.set()
        if kill and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for reader in readers:
            reader.join(timeout=2)
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()
            except OSError:
                pass

    total = 0
    try:
        while True:
            if cancelled():
                settle(kill=True)
                return None, "Cancelled"
            try:
                size = reports.get(timeout=0.05)
            except queue.Empty:
                continue
            if size < 0:
                settle(kill=True)
                return None, "could not read the finished audio"
            if size == 0:
                break
            total += size
        while proc.poll() is None:
            if cancelled():
                settle(kill=True)
                return None, "Cancelled"
            try:
                proc.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        settle(kill=True)
        raise
    settle(kill=False)
    if proc.returncode != 0:
        detail = bytes(tail).decode("utf-8", "replace").strip().splitlines()
        return None, ("could not decode the finished audio: "
                      + (detail[-1][:200] if detail else f"exit {proc.returncode}"))
    if total % 4:
        return None, "the finished audio ends part-way through a sample"
    return total // 4, ""
