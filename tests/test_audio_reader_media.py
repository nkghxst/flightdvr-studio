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

"""Generated-media measurements for the local FFmpeg PCM readers."""

from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
import wave
from array import array
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import pytest

from flightdvr.audio_plan import OUTPUT_RATE, round_samples
from flightdvr.audio_reader import (
    AudioAssetError,
    AudioReaderError,
    FfmpegPcmReader,
    HASH_CHUNK_BYTES,
    MusicAssetProbe,
    inspect_music_asset,
)
from flightdvr.media import Tools, find_tools


pytestmark = pytest.mark.integration

NATIVE_RATE = 44_100
NATIVE_FRAMES = 529_217
PASSAGE_START_NATIVE = 13_237
PASSAGE_END_NATIVE = 31_003
TIMELINE_FRAMES = 38_400
SOURCE_EVENT_DELAY = 6_576
SOURCE_EVENT_FRAMES = 8_640


def run(command: list[str], *, text: bool = True):
    return subprocess.run(command, check=True, capture_output=True, text=text)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def floats(data: bytes) -> tuple[float, ...]:
    assert len(data) % 4 == 0
    return tuple(value[0] for value in struct.iter_unpack("<f", data))


def independent_music_reference(tools: Tools, path: Path) -> tuple[float, ...]:
    """Decode from zero without using the reader's command or mapping helpers."""
    completed = run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(path), "-map", "0:a:0", "-vn", "-sn", "-dn",
        "-af", "aresample=48000:async=0,"
        "aformat=sample_fmts=flt:channel_layouts=stereo",
        "-f", "f32le", "pipe:1",
    ], text=False)
    return floats(completed.stdout)


def read_all(reader: FfmpegPcmReader) -> tuple[float, ...]:
    blocks: list[float] = []
    start = 0
    while start < reader.frames:
        size = min(480, reader.frames - start)
        blocks.extend(reader.read(start, size, lambda: False))
        start += size
    return tuple(blocks)


def stereo_peak(values: tuple[float, ...], start: int, end: int) -> float:
    return max(abs(value) for value in values[start * 2:end * 2])


def active_frames(values: tuple[float, ...], threshold: float = 1e-4) -> list[int]:
    return [frame for frame in range(len(values) // 2)
            if max(abs(values[frame * 2]), abs(values[frame * 2 + 1]))
            > threshold]


def measured_frequency(values: tuple[float, ...], start: int,
                       frames: int = 4_800) -> float:
    channel = values[start * 2:(start + frames) * 2:2]
    crossings = sum(before <= 0 < after
                    for before, after in zip(channel, channel[1:]))
    return crossings * OUTPUT_RATE / (frames - 1)


@dataclass(frozen=True)
class Media:
    music: Path
    source: Path
    silent: Path
    partial_pcm: Path
    music_digest: str
    review_ids: dict[str, str]


@pytest.fixture(scope="module")
def tools() -> Tools:
    return find_tools()


def write_music(path: Path) -> None:
    """Write exact native samples with distinct events around a passage."""
    samples = array("h")
    amplitude = 18_000
    sections = (
        (0, PASSAGE_START_NATIVE, 293),
        (PASSAGE_START_NATIVE, 22_111, 719),
        (22_111, PASSAGE_END_NATIVE, 1_231),
        (PASSAGE_END_NATIVE, NATIVE_FRAMES, 2_003),
    )
    for start, end, frequency in sections:
        for offset in range(end - start):
            samples.append(round(amplitude * math.sin(
                2 * math.pi * frequency * offset / NATIVE_RATE)))
    assert len(samples) == NATIVE_FRAMES
    with wave.open(str(path), "wb") as target:
        target.setparams((1, 2, NATIVE_RATE, NATIVE_FRAMES,
                          "NONE", "not compressed"))
        target.writeframes(samples.tobytes())


def write_source_event(path: Path) -> None:
    samples = array("h")
    for offset in range(SOURCE_EVENT_FRAMES):
        samples.append(round(15_000 * math.sin(
            2 * math.pi * 997 * offset / OUTPUT_RATE)))
    with wave.open(str(path), "wb") as target:
        target.setparams((1, 2, OUTPUT_RATE, SOURCE_EVENT_FRAMES,
                          "NONE", "not compressed"))
        target.writeframes(samples.tobytes())


def probe_streams(tools: Tools, path: Path) -> list[dict]:
    completed = run([
        str(tools.ffprobe), "-v", "error", "-show_entries",
        "stream=index,codec_type,codec_name,sample_rate,channels,start_time,"
        "duration", "-of", "json", str(path),
    ])
    return json.loads(completed.stdout)["streams"]


@pytest.fixture(scope="module")
def media(tmp_path_factory, tools: Tools) -> Media:
    root = tmp_path_factory.mktemp("audio-reader-media")
    music = root / "music-44100-events.wav"
    event = root / "source-event.wav"
    source = root / "source-delayed-event.mkv"
    silent = root / "source-no-audio.mkv"
    partial_pcm = root / "partial-pcm.wav"
    write_music(music)
    write_source_event(event)
    with wave.open(str(partial_pcm), "wb") as target:
        target.setparams((1, 2, OUTPUT_RATE, 0, "NONE", "not compressed"))
        target.writeframes(struct.pack("<h", 12_000) * 2_400)
    partial_pcm.write_bytes(partial_pcm.read_bytes()[:-1])
    video = "color=c=black:size=64x48:rate=25:duration=0.8"
    run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", video, "-itsoffset",
        f"{SOURCE_EVENT_DELAY / OUTPUT_RATE:.9f}", "-i", str(event),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "ffv1", "-c:a",
        "pcm_s16le", str(source),
    ])
    run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", video, "-c:v", "ffv1", "-an", str(silent),
    ])

    with wave.open(str(music), "rb") as opened:
        assert (opened.getframerate(), opened.getnchannels(),
                opened.getnframes()) == (NATIVE_RATE, 1, NATIVE_FRAMES)
    source_streams = probe_streams(tools, source)
    audio = next(stream for stream in source_streams
                 if stream["codec_type"] == "audio")
    assert (audio["codec_name"], int(audio["sample_rate"]),
            int(audio["channels"])) == ("pcm_s16le", OUTPUT_RATE, 1)
    assert float(audio["start_time"]) == pytest.approx(
        SOURCE_EVENT_DELAY / OUTPUT_RATE, abs=0.001)
    assert not [stream for stream in probe_streams(tools, silent)
                if stream["codec_type"] == "audio"]

    paths = {
        "music": music,
        "source": source,
        "silent": silent,
        "partial_pcm": partial_pcm,
    }
    hashes = {name: digest(path) for name, path in paths.items()}
    made = Media(
        music, source, silent, partial_pcm, hashes["music"],
        {name: value[:16] for name, value in hashes.items()},
    )
    print(f"audio reader fixture review IDs: {made.review_ids}")
    return made


def test_probe_measures_native_frames_and_full_file_identity(
        tools: Tools, media: Media, tmp_path: Path):
    assert media.music.stat().st_size > HASH_CHUNK_BYTES
    with media.music.open("rb") as opened:
        first_chunk_digest = hashlib.sha256(
            opened.read(HASH_CHUNK_BYTES)).hexdigest()
    assert first_chunk_digest != media.music_digest
    asset = inspect_music_asset(tools, media.music)
    assert asset.stream_index == 0
    assert (asset.sample_rate, asset.channels, asset.decoded_samples) == (
        NATIVE_RATE, 1, NATIVE_FRAMES)
    assert asset.sha256 == media.music_digest

    truncated = tmp_path / "truncated.wav"
    truncated.write_bytes(media.music.read_bytes()[:30])
    with pytest.raises(AudioAssetError):
        inspect_music_asset(tools, truncated)


def test_music_reader_matches_from_zero_reference_and_exact_seek_suffix(
        tools: Tools, media: Media):
    asset = inspect_music_asset(tools, media.music)
    reference = independent_music_reference(tools, media.music)
    reader = FfmpegPcmReader.for_music(tools, asset)
    passage_start = round_samples(Fraction(
        PASSAGE_START_NATIVE * OUTPUT_RATE, NATIVE_RATE))
    passage_end = round_samples(Fraction(
        PASSAGE_END_NATIVE * OUTPUT_RATE, NATIVE_RATE))
    try:
        measured = read_all(reader)
        assert reader.frames == round_samples(Fraction(
            NATIVE_FRAMES * OUTPUT_RATE, NATIVE_RATE))
        assert reader.frames % 480 == 19
        assert len(measured) == reader.frames * 2 == len(reference)
        assert measured == reference
        assert all(measured[offset] == measured[offset + 1]
                   for offset in range(0, len(measured), 2))

        # The requested origin is deliberately non-centisecond and normalized.
        # Starting at the native coordinate instead would still sound plausible,
        # so prove that mutant against a distinct pre-passage tone.
        suffix_frames = 480
        suffix = tuple(reader.read(
            passage_start, suffix_frames, lambda: False))
        assert suffix == reference[
            passage_start * 2:(passage_start + suffix_frames) * 2]
        native_origin_mutant = reference[
            PASSAGE_START_NATIVE * 2:
            (PASSAGE_START_NATIVE + suffix_frames) * 2]
        assert suffix != native_origin_mutant

        assert stereo_peak(reference, passage_start + 200,
                           passage_start + 680) > 0.35
        assert stereo_peak(reference, passage_end + 200,
                           passage_end + 680) > 0.35
        section_starts = [
            0,
            passage_start,
            round_samples(Fraction(22_111 * OUTPUT_RATE, NATIVE_RATE)),
            passage_end,
        ]
        frequencies = [
            measured_frequency(reference, start + 1_000)
            for start in section_starts
        ]
        assert frequencies == pytest.approx((293, 719, 1_231, 2_003), abs=3)
        print(
            "music measurement: "
            f"native={NATIVE_FRAMES} normalized={reader.frames} "
            f"passage=[{passage_start},{passage_end}) "
            f"seek_suffix={suffix_frames} exact_floats={len(suffix)} "
            f"tones={[round(value, 1) for value in frequencies]}"
        )
    finally:
        reader.close()


def test_source_reader_preserves_delayed_timeline_and_clean_tail_padding(
        tools: Tools, media: Media):
    reader = FfmpegPcmReader.for_source(
        tools, media.source, stream_index=0,
        timeline_frames=TIMELINE_FRAMES)
    try:
        measured = read_all(reader)
        assert len(measured) == TIMELINE_FRAMES * 2
        active = active_frames(measured)
        assert active[0] == pytest.approx(SOURCE_EVENT_DELAY, abs=2)
        assert active[-1] == pytest.approx(
            SOURCE_EVENT_DELAY + SOURCE_EVENT_FRAMES - 1, abs=2)
        assert stereo_peak(measured, 0, SOURCE_EVENT_DELAY - 48) < 1e-6
        assert stereo_peak(measured, SOURCE_EVENT_DELAY + 96,
                           SOURCE_EVENT_DELAY + 576) > 0.30
        event_end = SOURCE_EVENT_DELAY + SOURCE_EVENT_FRAMES
        assert stereo_peak(measured, event_end + 48, TIMELINE_FRAMES) < 1e-6
        print(
            "source measurement: "
            f"timeline={TIMELINE_FRAMES} leading_silence={SOURCE_EVENT_DELAY} "
            f"active=[{active[0]},{active[-1]}] event={SOURCE_EVENT_FRAMES} "
            "trailing_silence="
            f"{TIMELINE_FRAMES - event_end}"
        )
    finally:
        reader.close()


def test_absent_declared_audio_fails_instead_of_becoming_silence(
        tools: Tools, media: Media):
    reader = FfmpegPcmReader.for_source(
        tools, media.silent, stream_index=0, timeline_frames=480)
    try:
        with pytest.raises(AudioReaderError):
            reader.read(0, 480, lambda: False)
    finally:
        reader.close()


def test_recoverable_decoder_error_is_not_accepted_as_asset_or_clean_silence(
        tools: Tools, media: Media):
    control = subprocess.run([
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(media.partial_pcm), "-map", "0:a:0", "-f", "f32le",
        "pipe:1",
    ], capture_output=True)
    assert control.returncode == 0
    assert len(control.stdout) // 4 == 2_399
    assert b"Invalid PCM packet" in control.stderr

    with pytest.raises(AudioAssetError, match="Invalid data found"):
        inspect_music_asset(tools, media.partial_pcm)

    reader = FfmpegPcmReader.for_source(
        tools, media.partial_pcm, stream_index=0, timeline_frames=2_400)
    try:
        with pytest.raises(AudioReaderError, match="Invalid data found"):
            read_all(reader)
    finally:
        reader.close()


def test_probe_stop_before_worker_entry_has_no_child_or_late_success(
        tools: Tools, media: Media):
    probe = MusicAssetProbe(tools, media.music, 41)
    ready: list[tuple] = []
    failed: list[tuple] = []
    probe.ready.connect(lambda *args: ready.append(args))
    probe.failed.connect(lambda *args: failed.append(args))
    probe.stop()
    probe.start()
    assert probe.wait(5_000)
    assert not probe.isRunning()
    assert probe._process is None
    assert ready == []
    assert failed == []


def test_reader_stop_settles_a_real_ffmpeg_child(
        tools: Tools, media: Media):
    asset = inspect_music_asset(tools, media.music)
    reader = FfmpegPcmReader.for_music(tools, asset)
    reader.read(0, 480, lambda: False)
    process = reader._process
    assert process is not None
    reader.request_stop()
    reader.close()
    assert process.poll() is not None
    assert reader._process is None
    assert reader._stderr_thread is None
