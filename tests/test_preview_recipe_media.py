"""Generated decoded-picture checks against W5's actual FFmpeg command.

The fixture encodes independent source ordinals in grayscale blocks.  The
oracle reads those blocks back from decoded pixels, not from FFmpeg timestamps
or the recipe's expected frame index.  The 30-first control proves that an
unchanged output count can still conceal discarded source pictures.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from flightdvr.media import ClipInfo, ToolsMissing, find_tools
from flightdvr.output_plan import PlannedOutput, ordinary_pieces, working_outputs
from flightdvr.player import PreviewSize, build_recipe_command
from flightdvr.presets import (
    ExportSettings, LEVELS, PASSTHROUGH, REC709, build_commands,
)
from flightdvr.preview_recipe import make_preview_recipe
from flightdvr.sequence_plan import Resolution, compile_sequence


SOURCE = (320, 180)
VIEW = PreviewSize(160, 90)
BITS = 16
BLOCK = 10
STEP = 18
MARK_X = 12
MARK_Y = 24


def _tools():
    try:
        return find_tools()
    except ToolsMissing as problem:
        pytest.skip(f"generated media needs FFmpeg: {problem}")


def _marked_frame(ordinal: int) -> bytes:
    width, height = SOURCE
    assert MARK_X + (BITS - 1) * STEP + BLOCK < width
    assert MARK_Y + BLOCK < height
    frame = bytearray(bytes((30, 60, 90)) * (width * height))
    for bit in range(BITS):
        shade = 245 if ordinal & (1 << bit) else 8
        for y in range(MARK_Y, MARK_Y + BLOCK):
            at = (y * width + MARK_X + bit * STEP) * 3
            frame[at:at + BLOCK * 3] = bytes((shade,)) * BLOCK * 3
    return bytes(frame)


def _ordinal(frame: bytes, width: int, reduction: int) -> int:
    result = 0
    y = (MARK_Y + BLOCK // 2) // reduction
    for bit in range(BITS):
        x = (MARK_X + bit * STEP + BLOCK // 2) // reduction
        if frame[(y * width + x) * 3] > 127:
            result |= 1 << bit
    return result


def _frames(command: list[str], width: int, height: int) -> list[bytes]:
    completed = subprocess.run(command, capture_output=True, timeout=40)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    frame_bytes = width * height * 3
    assert len(completed.stdout) % frame_bytes == 0, "partial decoded frame"
    return [completed.stdout[at:at + frame_bytes]
            for at in range(0, len(completed.stdout), frame_bytes)]


@pytest.mark.parametrize("rate,expected_cadence", [(60, 30), (90, 45)])
def test_actual_slow_recipe_preserves_every_source_ordinal(
        tmp_path: Path, rate: int, expected_cadence: int):
    tools = _tools()
    source = tmp_path / f"ordinals-{rate}.nut"
    nframes = rate * 3  # long enough to exercise the fast-seek lead-in too
    raw = b"".join(_marked_frame(index) for index in range(nframes))
    made = subprocess.run([
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "320x180",
        "-r", str(rate), "-i", "pipe:0", "-an", "-c:v", "ffv1",
        "-level", "3", "-g", "1", str(source),
    ], input=raw, capture_output=True, timeout=40)
    assert made.returncode == 0, made.stderr.decode("utf-8", "replace")
    probed = subprocess.run([
        str(tools.ffprobe), "-v", "error", "-select_streams", "v:0",
        "-count_frames", "-show_entries",
        "stream=codec_name,width,height,r_frame_rate,nb_read_frames",
        "-of", "json", str(source),
    ], capture_output=True, text=True, timeout=20, check=True)
    stream = json.loads(probed.stdout)["streams"][0]
    assert (stream["codec_name"], stream["width"], stream["height"],
            stream["r_frame_rate"], int(stream["nb_read_frames"])) == (
                "ffv1", 320, 180, f"{rate}/1", nframes)
    original = _frames([
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error",
        "-i", str(source), "-an", "-f", "rawvideo", "-pix_fmt",
        "rgb24", "pipe:1",
    ], *SOURCE)
    assert [_ordinal(frame, SOURCE[0], 1) for frame in original] == list(
        range(nframes)), "fixture failed its independent pixel oracle"

    clip = ClipInfo(source, source.stat().st_size, datetime(2026, 9, 24),
                    duration=3.0, width=320, height=180, fps=float(rate),
                    video_codec="ffv1")
    working = working_outputs(ordinary_pieces([clip]))[0]
    sequence = compile_sequence(
        working, resolution=Resolution.success(), revision=f"slow-{rate}")
    recipe = make_preview_recipe(
        PlannedOutput(working.target, "slowmo", ExportSettings(
            colour=PASSTHROUGH)), working, sequence,
        {sequence.occurrences[0].id: clip})
    assert recipe.cadence == expected_cadence and recipe.duration == 6
    command = build_recipe_command(
        tools, clip, 0.0, VIEW, recipe, recipe.occurrences[0])
    actual = _frames(command, VIEW.width, VIEW.height)
    ordinals = [_ordinal(frame, VIEW.width, 2) for frame in actual]
    assert ordinals == list(range(nframes)), (
        "actual recipe supply lost or duplicated recorded pictures",
        ordinals[:12], ordinals[-12:], len(ordinals))

    # The sequence lane starts at source time 0.2 for this seek. A correct
    # command's first decoded picture is the literal source ordinal there,
    # not an ordinal counted from a guessed fast-seek keyframe.
    seeked = _frames(build_recipe_command(
        tools, clip, 0.2, VIEW, recipe, recipe.occurrences[0]),
        VIEW.width, VIEW.height)
    seeked_ordinals = [_ordinal(frame, VIEW.width, 2) for frame in seeked]
    assert seeked_ordinals == list(range(rate // 5, nframes)), (
        "seeked recipe did not preserve the source ordinal suffix",
        seeked_ordinals[:10], seeked_ordinals[-10:])
    fast_seeked = _frames(build_recipe_command(
        tools, clip, 2.2, VIEW, recipe, recipe.occurrences[0]),
        VIEW.width, VIEW.height)
    fast_ordinals = [_ordinal(frame, VIEW.width, 2)
                     for frame in fast_seeked]
    assert fast_ordinals == list(range(rate * 11 // 5, nframes)), (
        "fast-plus-accurate recipe seek shifted the source ordinal suffix",
        fast_ordinals[:10], fast_ordinals[-10:])

    # Same nominal output cadence/count with a deliberately wrong ordering.
    control_filter = (
        f"scale={VIEW.width}:{VIEW.height}:flags=neighbor,"
        f"fps=30,setpts=2*PTS,fps={expected_cadence}")
    ffmpeg_help = subprocess.run(
        [str(tools.ffmpeg), "-hide_banner", "-h", "full"],
        capture_output=True, text=True, timeout=20)
    assert ffmpeg_help.returncode == 0, ffmpeg_help.stderr
    options = ffmpeg_help.stdout + ffmpeg_help.stderr
    if "-fps_mode" in options:
        passthrough = ["-fps_mode", "passthrough"]
    elif "-vsync" in options:
        passthrough = ["-vsync", "0"]
    else:
        pytest.skip("this ffmpeg exposes no passthrough muxing option")
    control = _frames([
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error",
        "-i", str(source), "-an", "-vf", control_filter,
        *passthrough, "-f", "rawvideo", "-pix_fmt",
        "rgb24", "pipe:1",
    ], VIEW.width, VIEW.height)
    control_ordinals = [_ordinal(frame, VIEW.width, 2) for frame in control]
    assert len(set(control_ordinals)) < nframes, (
        "negative control did not expose 30-first picture loss")


def test_actual_vertical_recipe_crops_source_before_preview_reduction(
        tmp_path: Path):
    tools = _tools()
    width, height = 1280, 720
    colours = {
        "background": (14, 14, 14), "far_left": (240, 30, 30),
        "centre": (30, 240, 30), "far_right": (30, 30, 240),
        "left_edge": (30, 240, 240), "right_edge": (240, 30, 240),
        "top_edge": (240, 240, 30), "bottom_edge": (240, 240, 240),
    }
    pixels = bytearray(bytes(colours["background"]) * (width * height))

    def paint(x0, y0, x1, y1, name):
        row = bytes(colours[name]) * (x1 - x0)
        for y in range(y0, y1):
            at = (y * width + x0) * 3
            pixels[at:at + len(row)] = row

    paint(40, 220, 200, 500, "far_left")
    paint(570, 220, 710, 500, "centre")
    paint(1080, 220, 1240, 500, "far_right")
    paint(442, 100, 448, 220, "left_edge")
    paint(832, 100, 838, 220, "right_edge")
    paint(500, 8, 780, 14, "top_edge")
    paint(500, 706, 780, 712, "bottom_edge")
    assert len(pixels) == width * height * 3
    source = tmp_path / "asymmetric.ppm"
    source.write_bytes(b"P6\n1280 720\n255\n" + pixels)
    clip = ClipInfo(source, source.stat().st_size, datetime(2026, 9, 24),
                    duration=1 / 25, width=width, height=height, fps=25.0,
                    video_codec="ppm")
    working = working_outputs(ordinary_pieces([clip]))[0]
    sequence = compile_sequence(
        working, resolution=Resolution.success(), revision="vertical")
    recipe = make_preview_recipe(
        PlannedOutput(working.target, "vertical", ExportSettings(
            colour=PASSTHROUGH, vertical_position=50)), working, sequence,
        {sequence.occurrences[0].id: clip})
    crop = recipe.occurrences[0].crop
    assert crop is not None
    assert (crop.width, crop.height, crop.x, crop.y) == (396, 704, 442, 8)
    assert recipe.canvas == (720, 1280)
    view = PreviewSize(270, 480)
    actual = _frames(build_recipe_command(
        tools, clip, 0, view, recipe, recipe.occurrences[0]),
        view.width, view.height)
    assert len(actual) == 1

    def count(frame: bytes, name: str) -> int:
        wanted = colours[name]
        return sum(frame[at:at + 3] == bytes(wanted)
                   for at in range(0, len(frame), 3))

    assert not count(actual[0], "far_left")
    assert not count(actual[0], "far_right")
    for name in ("centre", "left_edge", "right_edge", "top_edge",
                 "bottom_edge"):
        assert count(actual[0], name) >= 20, f"actual crop lost {name}"

    # Deliberately reduce before computing the portrait crop. It retains a
    # superficially credible canvas and centre, yet loses all four edges.
    negative = _frames([
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error",
        "-i", str(source), "-an", "-vf",
        "scale=480:270:flags=neighbor,crop=144:256:168:6,"
        "scale=270:480:flags=neighbor", "-f", "rawvideo", "-pix_fmt",
        "rgb24", "pipe:1",
    ], view.width, view.height)
    assert len(negative) == 1 and count(negative[0], "centre") >= 20
    assert all(count(negative[0], name) == 0 for name in (
        "left_edge", "right_edge", "top_edge", "bottom_edge"))


def test_colour_modes_match_their_export_picture_on_generated_swatches(
        tmp_path: Path):
    tools = _tools()
    filters = subprocess.run(
        [str(tools.ffmpeg), "-hide_banner", "-filters"],
        capture_output=True, text=True, timeout=20)
    assert filters.returncode == 0, filters.stderr
    if not any(line.split()[1:2] == ["zscale"]
               for line in filters.stdout.splitlines()):
        pytest.skip("Rec.709 export/preview needs this ffmpeg's zscale filter")
    width, height = 320, 180
    swatches = ((35, 80, 190), (210, 60, 35),
                (40, 180, 75), (195, 150, 35))
    pixels = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            colour = swatches[(y >= height // 2) * 2 + (x >= width // 2)]
            at = (y * width + x) * 3
            pixels[at:at + 3] = bytes(colour)
    source_rgb = tmp_path / "swatches.ppm"
    source_rgb.write_bytes(b"P6\n320 180\n255\n" + pixels)
    source = tmp_path / "full-range.mkv"
    encoded = subprocess.run([
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-i", str(source_rgb), "-vf", "format=yuv444p",
        "-color_range", "pc", "-c:v", "ffv1", str(source),
    ], capture_output=True, timeout=40)
    assert encoded.returncode == 0, encoded.stderr.decode("utf-8", "replace")
    probed = subprocess.run([
        str(tools.ffprobe), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=pix_fmt,color_range,width,height",
        "-of", "json", str(source),
    ], capture_output=True, text=True, check=True, timeout=20)
    stream = json.loads(probed.stdout)["streams"][0]
    assert (stream["width"], stream["height"], stream["color_range"]) == (
        width, height, "pc")
    clip = ClipInfo(source, source.stat().st_size, datetime(2026, 9, 24),
                    duration=1 / 25, width=width, height=height, fps=25.0,
                    video_codec="ffv1", pix_fmt=stream["pix_fmt"],
                    color_range="pc", color_space="bt470bg",
                    color_primaries="bt470bg", color_transfer="smpte170m")
    working = working_outputs(ordinary_pieces([clip]))[0]
    sequence = compile_sequence(
        working, resolution=Resolution.success(), revision="colour")
    view = PreviewSize(160, 90)
    samples = ((40, 22), (120, 22), (40, 67), (120, 67))

    def swatch_values(frame: bytes):
        return tuple(tuple(frame[(y * view.width + x) * 3:
                                 (y * view.width + x) * 3 + 3])
                     for x, y in samples)

    preview_values = {}
    for mode in (PASSTHROUGH, LEVELS, REC709):
        settings = ExportSettings(colour=mode)
        recipe = make_preview_recipe(
            PlannedOutput(working.target, "master", settings), working,
            sequence, {sequence.occurrences[0].id: clip})
        supplied = _frames(build_recipe_command(
            tools, clip, 0, view, recipe, recipe.occurrences[0]),
            view.width, view.height)
        assert len(supplied) == 1
        preview_values[mode] = swatch_values(supplied[0])
        exported = tmp_path / f"{mode}.mp4"
        commands = build_commands(
            tools, clip, "master", settings, exported, tmp_path)
        assert len(commands) == 1
        done = subprocess.run(commands[0], capture_output=True, timeout=40)
        assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
        reference = _frames([
            str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error",
            "-i", str(exported), "-an", "-vf", "scale=160:90",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ], view.width, view.height)
        assert len(reference) == 1
        actual_values = swatch_values(reference[0])
        errors = [abs(a - b) for left, right in zip(
            preview_values[mode], actual_values)
            for a, b in zip(left, right)]
        assert max(errors) <= 8, (mode, preview_values[mode], actual_values)
    assert max(abs(a - b) for left, right in zip(
        preview_values[REC709], preview_values[PASSTHROUGH])
        for a, b in zip(left, right)) > 8, (
            "Rec.709 negative mode control was indistinguishable")


def test_social_cadence_and_upload_upscale_use_selected_picture_intent(
        tmp_path: Path):
    tools = _tools()
    source = tmp_path / "social-upload-60.nut"
    raw = b"".join(_marked_frame(index) for index in range(60))
    made = subprocess.run([
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "320x180",
        "-r", "60", "-i", "pipe:0", "-an", "-c:v", "ffv1",
        "-level", "3", "-g", "1", str(source),
    ], input=raw, capture_output=True, timeout=40)
    assert made.returncode == 0, made.stderr.decode("utf-8", "replace")
    clip = ClipInfo(source, source.stat().st_size, datetime(2026, 9, 24),
                    duration=1.0, width=320, height=180, fps=60.0,
                    video_codec="ffv1")
    working = working_outputs(ordinary_pieces([clip]))[0]
    sequence = compile_sequence(
        working, resolution=Resolution.success(), revision="social-upload")
    for key, settings, canvas, cadence, expected in (
        ("social", ExportSettings(colour=PASSTHROUGH,
                                  social_height=90, social_fps=30),
         (160, 90), 30, list(range(0, 60, 2))),
        ("upload", ExportSettings(colour=PASSTHROUGH,
                                  upload_height=360),
         (640, 360), 60, list(range(60))),
    ):
        recipe = make_preview_recipe(
            PlannedOutput(working.target, key, settings), working, sequence,
            {sequence.occurrences[0].id: clip})
        assert recipe.canvas == canvas and recipe.cadence == cadence
        actual = _frames(build_recipe_command(
            tools, clip, 0, VIEW, recipe, recipe.occurrences[0]),
            VIEW.width, VIEW.height)
        ordinals = [_ordinal(frame, VIEW.width, 2) for frame in actual]
        assert ordinals == expected, (key, ordinals[:10], ordinals[-10:])


def test_mixed_size_join_uses_one_export_canvas_for_each_occurrence(
        tmp_path: Path):
    tools = _tools()

    def source(name: str, size: tuple[int, int], rgb: tuple[int, int, int]):
        width, height = size
        path = tmp_path / name
        path.write_bytes(
            f"P6\n{width} {height}\n255\n".encode("ascii")
            + bytes(rgb) * (width * height))
        return ClipInfo(path, path.stat().st_size, datetime(2026, 9, 24),
                        duration=1 / 25, width=width, height=height,
                        fps=25.0, video_codec="ppm")

    first = source("wide.ppm", (320, 180), (220, 30, 30))
    second = source("narrow.ppm", (160, 120), (220, 220, 220))
    working = working_outputs([first, second], joined=True)[0]
    sequence = compile_sequence(
        working, resolution=Resolution.success(), revision="mixed")
    recipe = make_preview_recipe(
        PlannedOutput(working.target, "master", ExportSettings(
            colour=PASSTHROUGH)), working, sequence,
        {one.id: clip for one, clip in zip(
            sequence.occurrences, (first, second))})
    assert recipe.canvas == (320, 180)
    view = PreviewSize(160, 90)

    def pixel(frame: bytes, x: int, y: int) -> tuple[int, int, int]:
        at = (y * view.width + x) * 3
        return tuple(frame[at:at + 3])

    wide = _frames(build_recipe_command(
        tools, first, 0, view, recipe, recipe.occurrences[0]),
        view.width, view.height)
    narrow = _frames(build_recipe_command(
        tools, second, 0, view, recipe, recipe.occurrences[1]),
        view.width, view.height)
    assert len(wide) == len(narrow) == 1
    assert pixel(wide[0], 5, 45)[0] > 170
    assert max(pixel(narrow[0], 5, 45)) < 35
    assert min(pixel(narrow[0], 80, 45)) > 170

    negative = _frames([
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error",
        "-i", str(second.path), "-an", "-vf",
        "scale=160:90:flags=neighbor", "-f", "rawvideo",
        "-pix_fmt", "rgb24", "pipe:1",
    ], view.width, view.height)
    assert len(negative) == 1
    assert min(pixel(negative[0], 5, 45)) > 170, (
        "stretch-without-join-canvas control did not expose missing side bars")
