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

"""Exports run for real, and the result is inspected.

Every test here runs ffmpeg and looks at the file that comes out. That is the
difference between this module and the rest of the suite, and it is the point:
an independent review found eighteen defects, and not one of them was visible
in the arguments the app assembles. Several were guarded by tests that passed.

Tests marked `xfail(strict=True)` describe defects that are still real. They
are the known-defects list in executable form. When one is fixed the test stops
failing, pytest reports XPASS as an error, and whoever fixed it is told to
delete the marker — so this file cannot quietly fall out of date.

    python -m pytest tests/ -m "not integration"    # the fast loop
    python -m pytest tests/ -m integration          # these only
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

from conftest import FPS, CLEAN_PSNR, frame_psnr, probe_output
from flightdvr.jobs import ExportWorker, Job
from flightdvr.media import probe
from flightdvr.player import PreviewSize
from flightdvr.presets import ExportSettings, output_path

pytestmark = pytest.mark.integration


def export(tools, work, clips, preset, out_path, settings=None, concat=None):
    """Run one job through the real worker and return (ok, message)."""
    job = Job(
        clips=list(clips),
        preset_key=preset,
        settings=settings or ExportSettings(),
        out_path=out_path,
        concat_file=concat,
    )
    return ExportWorker(tools, [job], work)._run_job(0, job)


def target(work, stem, preset):
    """Where the app itself would put this export, extension included.

    Naming the file by hand meant passing ffmpeg a path with no extension,
    which gives it no muxer to infer.
    """
    return output_path(work, stem, preset, subfolders=False, flight_date=None)


def probed(tools, clip, trim_in=0.0, trim_out=0.0):
    info = probe(tools, clip.path)
    info.trim_in, info.trim_out = trim_in, trim_out
    return info


def whole_and_trimmed(tools, clip, tmp_path, start, length, preset="master"):
    """Export a clip twice, once entire and once trimmed, and line them up.

    Comparing against a hand-built reference compared the wrong things: the app
    applies a full-to-limited range conversion, so a reference encoded without
    it differs on every frame and even a correct trim scores badly. Exporting
    the same clip through the same preset leaves the seek as the only
    difference between the two files.
    """
    entire = target(tmp_path, "entire", preset)
    ok, message = export(tools, tmp_path, [probed(tools, clip)], preset, entire)
    assert ok, f"the untrimmed control export failed: {message}"

    cut = target(tmp_path, "cut", preset)
    ok, message = export(tools, tmp_path,
                         [probed(tools, clip, start, start + length)], preset, cut)
    assert ok, message

    scores = frame_psnr(tools, cut, entire, tmp_path,
                        skip_reference_frames=round(start * FPS))
    assert scores, "could not compare the two exports"
    return scores


# -- the defect that made 1.1.1 urgent ----------------------------------------

@pytest.mark.parametrize("start", [2.5, 3.25, 4.75])
def test_a_mid_gop_trim_produces_no_corrupt_frames(tools, clip, tmp_path, start):
    """The one that shipped.

    Seeking into an MPEG-TS lands on an estimated byte offset rather than a
    keyframe, so decoding began with no reference picture and every frame from
    the in point to the next keyframe was garbage. On real footage that was 30
    frames at 12 dB, silently, with the correct frame count and no ffmpeg error.
    """
    assert clip.is_mid_gop(start), "this test is pointless on a keyframe"

    scores = whole_and_trimmed(tools, clip, tmp_path, start, 1.0)
    damaged = [i for i, value in enumerate(scores) if value < CLEAN_PSNR]
    assert not damaged, (
        f"{len(damaged)} of {len(scores)} frames are corrupt, starting at frame "
        f"{damaged[0]} — worst {min(scores):.1f} dB. The in point at {start}s is "
        f"{start - clip.keyframe_before(start):.2f}s past a keyframe."
    )


def test_a_mid_gop_trim_is_still_right_with_the_sound_turned_off(
        tools, clip, tmp_path):
    """Dropping the audio changed where the export started.

    Found while measuring the preview, which always passes -an. With the audio
    kept, the seek after the input counts from the position asked for; with it
    dropped, from the keyframe the seek before the input landed on. Asking for
    2.5 s gave a clean, complete, correctly-lengthed export of 2.0 s onwards.

    It needs a file whose start time is not zero, which is why nothing caught
    it: HDZero recordings start at zero, and every other test here keeps the
    sound.
    """
    entire = target(tmp_path, "entire_silent", "master")
    settings = ExportSettings(keep_audio=False)
    ok, message = export(tools, tmp_path, [probed(tools, clip)], "master",
                         entire, settings=settings)
    assert ok, f"the untrimmed control export failed: {message}"

    cut = target(tmp_path, "cut_silent", "master")
    ok, message = export(tools, tmp_path, [probed(tools, clip, 2.5, 3.5)],
                         "master", cut, settings=settings)
    assert ok, message

    scores = frame_psnr(tools, cut, entire, tmp_path,
                        skip_reference_frames=round(2.5 * FPS))
    assert scores, "could not compare the two exports"
    assert min(scores) >= CLEAN_PSNR, (
        f"worst frame {min(scores):.1f} dB — the trim did not start where it "
        f"was asked to"
    )


def test_a_trim_on_a_keyframe_is_also_clean(tools, clip, tmp_path):
    """The case that always worked, kept so a fix cannot regress it."""
    scores = whole_and_trimmed(tools, clip, tmp_path, 2.0, 1.0)
    assert min(scores) >= CLEAN_PSNR, f"worst frame {min(scores):.1f} dB"


def test_a_trim_produces_the_length_that_was_asked_for(tools, clip, tmp_path):
    out = target(tmp_path, "length", "master")
    ok, message = export(tools, tmp_path, [probed(tools, clip, 1.5, 3.5)],
                         "master", out)
    assert ok, message
    assert probe_output(tools, out)["duration"] == pytest.approx(2.0, abs=0.15)


# -- an export is a transaction -----------------------------------------------

def test_a_failed_export_leaves_the_earlier_one_untouched(tools, clip, tmp_path):
    """ffmpeg gets -y, so aiming it at the final path truncated the previous
    export the moment it opened the file."""
    out = tmp_path / "precious.mp4"
    out.write_bytes(b"the export I already had" * 50)
    before = out.read_bytes()

    unreadable = tmp_path / "damaged.ts"
    unreadable.write_bytes(b"not a transport stream" * 2000)
    broken = probed(tools, clip, 1.0, 2.0)
    broken.path = unreadable

    ok, _ = export(tools, tmp_path, [broken], "master", out)
    assert not ok, "a rubbish source should not export successfully"
    assert out.read_bytes() == before, "the previous export was damaged"
    assert not list(tmp_path.glob("*.flightdvr-part*")), "temporary file left behind"


def test_a_successful_export_replaces_the_earlier_one(tools, clip, tmp_path):
    out = tmp_path / "export.mp4"
    out.write_bytes(b"older, smaller, worse")
    ok, message = export(tools, tmp_path, [probed(tools, clip, 1.0, 2.0)],
                         "master", out)
    assert ok, message
    assert probe_output(tools, out)["has_video"]


def test_an_export_reported_as_done_contains_video(tools, clip, tmp_path):
    out = tmp_path / "done.mp4"
    ok, message = export(tools, tmp_path, [probed(tools, clip)], "master", out)
    assert ok, message
    result = probe_output(tools, out)
    assert result["has_video"] and result["frames"] > 0
    assert result["duration"] == pytest.approx(6.0, abs=0.3)


def test_a_remux_with_no_keyframe_in_range_fails_rather_than_lying(
    tools, clip, tmp_path
):
    """Copying without re-encoding can only start at a keyframe. Asking for a
    slice between two of them exits successfully having written a container
    header and nothing else, which the queue used to call "Done, 0 MB"."""
    out = tmp_path / "subgop.mp4"
    ok, message = export(tools, tmp_path, [probed(tools, clip, 2.2, 2.6)],
                         "remux", out)
    assert not ok, "an empty container was reported as a successful export"
    assert "keyframe" in message.lower() or "no video" in message.lower(), message


@pytest.mark.parametrize("preset", ["edit", "master", "social", "upload", "remux"])
def test_every_preset_produces_something_playable(tools, clip, tmp_path, preset):
    out = target(tmp_path, "whole", preset)
    ok, message = export(tools, tmp_path, [probed(tools, clip)], preset, out)
    assert ok, f"{preset}: {message}"
    result = probe_output(tools, out)
    assert result["has_video"], f"{preset} produced no video"
    assert result["frames"] > 0, f"{preset} produced no frames"


def test_upload_actually_enlarges_the_picture(tools, clip, tmp_path):
    """The whole point of the preset, checked on the file rather than the
    command. Every other preset refuses to enlarge, in four separate places,
    and it would be easy to leave one of them in the way."""
    out = target(tmp_path, "upscaled", "upload")
    settings = ExportSettings()
    settings.upload_height = 360           # the fixture is 320x180
    settings.upload_speed = "veryfast"     # this is a test, not a release
    ok, message = export(tools, tmp_path, [probed(tools, clip)], "upload", out,
                         settings=settings)
    assert ok, message
    result = probe_output(tools, out)
    assert result["height"] == 360, result
    assert result["width"] == 640, result
    assert result["frames"] > 0


def test_upload_does_not_lose_the_colour_correction(tools, clip, tmp_path):
    """An enlarged export is still full-range footage. If the upscale filter
    displaced the range conversion the picture would come out wrong in exactly
    the way this app exists to prevent."""
    settings = ExportSettings()
    settings.upload_height = 180           # same size as the source, no resize
    settings.upload_speed = "veryfast"
    upload = target(tmp_path, "colour_upload", "upload")
    ok, message = export(tools, tmp_path, [probed(tools, clip)], "upload",
                         upload, settings=settings)
    assert ok, message

    master = target(tmp_path, "colour_master", "master")
    ok, message = export(tools, tmp_path, [probed(tools, clip)], "master", master)
    assert ok, message

    scores = frame_psnr(tools, upload, master, tmp_path)
    assert scores, "could not compare the two exports"
    assert min(scores) >= CLEAN_PSNR, (
        f"upload and master disagree on colour, worst frame {min(scores):.1f} dB"
    )


def test_audio_survives_an_ordinary_export(tools, clip, tmp_path):
    out = target(tmp_path, "sound", "master")
    ok, message = export(tools, tmp_path, [probed(tools, clip)], "master", out)
    assert ok, message
    assert probe_output(tools, out)["has_audio"]


def test_a_silent_source_exports_without_inventing_audio(tools, silent_clip, tmp_path):
    out = target(tmp_path, "silent", "master")
    ok, message = export(tools, tmp_path, [probed(tools, silent_clip)], "master", out)
    assert ok, message
    assert not probe_output(tools, out)["has_audio"]


def test_a_size_target_is_respected_for_one_clip(tools, clip, tmp_path):
    """Two-pass targeting on one clip. The joined case is broken and has its
    own test below."""
    out = target(tmp_path, "small", "social")
    settings = ExportSettings()
    settings.social_size_mb = 2
    ok, message = export(tools, tmp_path, [probed(tools, clip)], "social", out,
                         settings=settings)
    assert ok, message
    produced = out.stat().st_size / (1024 * 1024)
    assert produced <= 2 * 1.35, f"asked for 2 MB, produced {produced:.2f} MB"


# -- defects that are still real ----------------------------------------------
#
# These fail on purpose. Delete the marker when you fix one; strict xfail turns
# an unexpected pass into an error so nobody has to remember.

def test_a_joined_size_target_is_respected(tools, clip, second_clip, tmp_path):
    """Two six-second clips joined, targeted at 3 MB.

    The bitrate used to be computed from the first clip alone, so the finished
    file came out at roughly the number of clips times the target.
    """
    from flightdvr.jobs import write_concat_file
    clips = [probed(tools, clip), probed(tools, second_clip)]
    concat = write_concat_file(clips, tmp_path, "joined")
    out = target(tmp_path, "joined_small", "social")
    settings = ExportSettings()
    settings.social_size_mb = 3
    ok, message = export(tools, tmp_path, clips, "social", out,
                         settings=settings, concat=concat)
    assert ok, message
    produced = out.stat().st_size / (1024 * 1024)
    assert produced <= 3 * 1.35, f"asked for 3 MB, produced {produced:.2f} MB"
    assert probe_output(tools, out)["duration"] == pytest.approx(12.0, abs=0.5), \
        "both clips should be in the output"


def test_joining_a_silent_clip_first_keeps_the_others_audio(
    tools, silent_clip, clip, tmp_path
):
    """Audio presence used to be read off the first clip and applied to all of
    them, so a silent clip at the front added -an and took the sound out of
    every clip that had it. Silence is synthesised for the ones that lack it
    instead."""
    from flightdvr.jobs import write_concat_file
    clips = [probed(tools, silent_clip), probed(tools, clip)]
    concat = write_concat_file(clips, tmp_path, "joined")
    out = tmp_path / "mixed.mp4"
    ok, message = export(tools, tmp_path, clips, "master", out, concat=concat)
    assert ok, message
    assert probe_output(tools, out)["has_audio"], "the second clip's audio was dropped"


def test_a_mixed_audio_join_runs_for_its_full_length(
    tools, silent_clip, clip, tmp_path
):
    """Synthesised silence has to be the right length, or the clip that does
    have sound ends up out of step with its own picture."""
    from flightdvr.jobs import write_concat_file
    clips = [probed(tools, silent_clip), probed(tools, clip)]
    concat = write_concat_file(clips, tmp_path, "joined")
    out = target(tmp_path, "mixed", "master")
    ok, message = export(tools, tmp_path, clips, "master", out, concat=concat)

    assert ok, message
    result = probe_output(tools, out)
    assert result["has_audio"]
    assert result["duration"] == pytest.approx(12.0, abs=0.5)


def test_a_joined_remux_of_mismatched_clips_is_refused(
    tools, clip, odd_sized_clip, tmp_path
):
    """Copying without re-encoding cannot bring anything to a common format,
    so it still has to say no."""
    from flightdvr.jobs import write_concat_file
    clips = [probed(tools, clip), probed(tools, odd_sized_clip)]
    concat = write_concat_file(clips, tmp_path, "joined")
    out = target(tmp_path, "badremux", "remux")
    ok, message = export(tools, tmp_path, clips, "remux", out, concat=concat)

    assert not ok
    assert "other presets" in message, message


def test_matching_clips_still_join(tools, clip, second_clip, tmp_path):
    """The refusal must not catch clips that are genuinely compatible."""
    from flightdvr.jobs import write_concat_file
    clips = [probed(tools, clip), probed(tools, second_clip)]
    concat = write_concat_file(clips, tmp_path, "joined")
    out = target(tmp_path, "ok", "master")
    ok, message = export(tools, tmp_path, clips, "master", out, concat=concat)
    assert ok, message
    result = probe_output(tools, out)
    assert result["has_audio"]
    assert result["duration"] == pytest.approx(12.0, abs=0.5)


def test_an_odd_sized_source_still_exports(tools, odd_sized_clip, tmp_path):
    """libx264 refuses an odd width or height outright rather than coping.
    Nothing a Box Pro records is odd, but the app opens any folder."""
    out = target(tmp_path, "odd", "master")
    ok, message = export(tools, tmp_path, [probed(tools, odd_sized_clip)],
                         "master", out)
    assert ok, message
    result = probe_output(tools, out)
    assert result["width"] % 2 == 0 and result["height"] % 2 == 0
    assert result["frames"] > 0


def test_an_output_folder_beginning_with_a_dash_works(tools, clip, tmp_path):
    """ffmpeg reads a leading dash as the start of an option, so a relative
    folder called "-exports" produced "Unrecognized option"."""
    folder = tmp_path / "-exports"
    folder.mkdir()
    here = Path.cwd()
    try:
        os.chdir(tmp_path)
        out = Path("-exports") / "clip.mp4"
        ok, message = export(tools, tmp_path, [probed(tools, clip, 1.0, 2.0)],
                             "master", out)
        assert ok, message
    finally:
        os.chdir(here)


# -- the preview player --------------------------------------------------------
#
# The unit tests fake the decoder, so everything below is the first thing that
# runs a real one. The failure being guarded against is the one the whole
# integration suite exists for: a well-formed command that produces nothing, or
# produces frames nobody can tell are wrong.

PREVIEW = PreviewSize(320, 180)          # the fixture's own size: no rescaling,
                                         # so corruption is not blurred away


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def decode(tools, info, start, size=PREVIEW):
    """Run one real DecodeWorker to completion and collect what it produced.

    run() is called rather than start(): it is an ordinary method, and calling
    it directly runs the decode in this thread, where a test can wait for it
    without an event loop. The queue is given no practical limit because the
    bounded one exists for back-pressure against a UI that is not here — left
    at its usual depth this would block after twenty-four frames and hang.
    """
    import queue as queue_module
    from flightdvr.player import DecodeWorker

    frames: queue_module.Queue = queue_module.Queue(maxsize=10000)
    worker = DecodeWorker(tools, info, start, size, 1, frames)
    problems, over = [], []
    worker.failed.connect(lambda _gen, message: problems.append(message))
    worker.ended.connect(over.append)
    worker.run()

    collected = []
    while not frames.empty():
        collected.append(frames.get_nowait())
    return collected, problems, over


def mean_abs_diff(left: bytes, right: bytes) -> float:
    """Average per-byte difference between two raw RGB frames."""
    assert len(left) == len(right)
    return sum(abs(a - b) for a, b in zip(left, right)) / len(left)


def preview_frame(tools, info, start, size=PREVIEW):
    """One raw frame, through exactly the command the player would issue."""
    from flightdvr.player import build_command, read_frames

    command = build_command(tools, info, start, size)
    proc = subprocess.Popen(command, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, bufsize=1 << 20)
    try:
        for frame in read_frames(proc.stdout, size.frame_bytes):
            return frame
    finally:
        proc.stdout.close()
        proc.terminate()
        proc.wait(timeout=5)
    return b""


def decode_frame_window(tools, info, position):
    """Run the native-rate precise worker and return its bounded cache."""
    from flightdvr.player import FrameWindowWorker, plan_frame_window

    window = plan_frame_window(position, info.duration, info.fps)
    worker = FrameWindowWorker(tools, info, window, PREVIEW, 1)
    ready, problems = [], []
    worker.ready.connect(lambda _generation, frames: ready.append(frames))
    worker.failed.connect(lambda _generation, message: problems.append(message))
    worker.run()
    return (ready[0] if ready else ()), problems, window


def test_the_preview_decoder_actually_produces_frames(tools, clip, qt_app):
    """The whole point of this file. Every argument can be right and the
    result still be an empty pipe."""
    frames, problems, over = decode(tools, probed(tools, clip), 0.0)

    assert not problems, problems
    assert over == [1], "the stream never reported a clean end"
    # Six seconds at thirty, give or take how the fps filter rounds the ends.
    assert 170 <= len(frames) <= 182, len(frames)
    assert all(len(data) == PREVIEW.frame_bytes for _when, data in frames)
    assert frames[0][0] == pytest.approx(0.0)


def test_a_preview_seek_lands_on_the_right_frame_not_a_torn_one(tools, clip,
                                                                qt_app):
    """The defect this design exists to avoid, in the preview path.

    2.5 seconds is deliberately mid-GOP: the fixture has a keyframe every
    1.000 s, so a single input seek there decodes torn macroblocks until 3.0.
    Compared frame by frame, and the first one especially — an average hides
    exactly the damage being looked for.
    """
    info = probed(tools, clip)
    from_start, _p, _e = decode(tools, info, 0.0)
    from_seek, problems, _e2 = decode(tools, info, 2.5)

    assert not problems, problems
    assert from_seek, "seeking produced no frames at all"

    # 2.5 s at 30 fps is frame 75 of the run that started at zero.
    offset = 75
    pairs = list(zip(from_seek, from_start[offset:]))[:30]
    assert len(pairs) == 30

    differences = [mean_abs_diff(seek[1], whole[1]) for seek, whole in pairs]
    assert differences[0] < 2.0, (
        f"the first frame after a seek is wrong: {differences[0]:.1f}")
    assert max(differences) < 2.0, f"worst frame differs by {max(differences):.1f}"
    assert from_seek[0][0] == pytest.approx(2.5)


def test_precise_stepping_lands_on_the_source_frame_it_displays(
        tools, clip, qt_app):
    """The native-rate window must identify and display the same frame.

    The control starts at zero; the second decode seeks mid-GOP to the window
    around 2.5 s. Comparing the frame numbered 150 catches both a torn seek and
    a timestamp/frame-number disagreement.
    """
    info = probed(tools, clip)
    control, control_problems, _ = decode_frame_window(tools, info, 0.0)
    sought, sought_problems, window = decode_frame_window(tools, info, 2.5)

    assert not control_problems and not sought_problems, (
        control_problems, sought_problems)
    assert len(sought) <= window.frame_count
    assert len(sought) <= 361

    control_by_number = {frame.frame_number: frame for frame in control}
    sought_by_number = {frame.frame_number: frame for frame in sought}
    assert 150 in control_by_number and 150 in sought_by_number
    assert sought_by_number[150].seconds == pytest.approx(2.5)
    difference = mean_abs_diff(
        control_by_number[150].pixels, sought_by_number[150].pixels)
    assert difference < 2.0, f"the displayed source frame differs by {difference:.1f}"


def test_the_preview_agrees_with_the_export_about_colour(tools, clip, tmp_path,
                                                         qt_app):
    """Nothing proved this before, and it is the preview's entire job.

    The source is full range and the export converts it to limited. The
    preview does no conversion at all, because measurement showed range
    filters are inert on the way to rgb24 — the conversion out of YUV reads
    the source's range tag either way. This is what says that reasoning holds
    end to end rather than only in the one case it was measured on.
    """
    info = probed(tools, clip)
    out = target(tmp_path, "colour", "master")
    ok, message = export(tools, tmp_path, [probed(tools, clip, 2.0, 3.0)],
                         "master", out)
    assert ok, message

    exported = probe(tools, out)
    reference = preview_frame(tools, exported, 0.0)
    assert reference, "could not read a frame back out of the export"

    live = preview_frame(tools, info, 2.0)
    agreed = mean_abs_diff(live, reference)
    # What is left is H.264's own loss. A range mismatch would put this in the
    # teens, and would show as the preview being visibly flatter than the file.
    assert agreed < 6.0, f"preview and export differ by {agreed:.1f}"

    # Levels, separately from the pictures: a squashed preview reaches neither
    # end of the scale even when the shapes still line up.
    assert abs(min(live) - min(reference)) <= 8
    assert abs(max(live) - max(reference)) <= 8


def test_a_corrupt_source_fails_rather_than_hanging(tools, tmp_path, qt_app):
    """Preview is the feature most likely to be pointed at a recording cut
    short by a flat battery, which is where the stderr flood comes from."""
    from flightdvr.media import ClipInfo

    broken = tmp_path / "hdz_broken.ts"
    broken.write_bytes(b"\x47" + os.urandom(400_000))
    info = ClipInfo(
        path=broken, size=broken.stat().st_size,
        modified=datetime.fromtimestamp(broken.stat().st_mtime),
        duration=10.0, width=320, height=180, fps=60.0,
        video_codec="hevc", audio_codec="", color_range="pc",
    )

    began = time.monotonic()
    frames, problems, over = decode(tools, info, 0.0)
    took = time.monotonic() - began

    assert took < 30, f"took {took:.1f}s to give up"
    assert problems, "a file that decodes to nothing reported success"
    assert problems[0] and "exit code" not in problems[0].lower(), problems
    assert not frames


# -- what a stream copy cannot do ----------------------------------------------

def decode_complaints(tools, path: Path) -> str:
    """What ffmpeg says while decoding a file all the way through.

    Frame counts and durations do not catch this: the corrupt file has the
    right length and reports success. The decoder is the thing that notices.
    """
    return subprocess.run(
        [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error",
         "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr.strip()


def test_a_trimmed_joined_remux_would_be_corrupt(tools, clip, second_clip,
                                                 tmp_path):
    """The measurement behind refusing it, kept so the reason stays checkable.

    This deliberately builds the export the app now refuses to build, and
    asserts that it really is broken. If a future ffmpeg starts handling this
    correctly, this test fails and the refusal can be reconsidered — which is
    the only honest way to hold a restriction in place.
    """
    from flightdvr.jobs import write_concat_file
    from flightdvr.presets import build_commands

    clips = [probed(tools, clip, 2.5, 4.5), probed(tools, second_clip, 2.5, 4.5)]
    assert clip.is_mid_gop(2.5), "this test is pointless on a keyframe"

    concat = write_concat_file(clips, tmp_path, "trimmed_join")
    out = target(tmp_path, "trimmed_join", "remux")

    # Around the guard on purpose: the export path refuses this now, and the
    # point here is to keep checking that the refusal is still deserved.
    command = build_commands(
        tools, clips[0], "remux", ExportSettings(), out, tmp_path,
        clips=clips, concat_file=concat, total_duration=4.0,
    )[0]
    done = subprocess.run([str(c) for c in command], capture_output=True,
                          text=True)
    assert done.returncode == 0, done.stderr[-400:]
    assert out.exists(), "ffmpeg produced nothing to examine"

    complaints = decode_complaints(tools, out)
    assert complaints, (
        "a trimmed joined remux decoded cleanly — ffmpeg may have changed, and "
        "the refusal in join_problems should be reconsidered"
    )
    assert "POC" in complaints or "ref" in complaints.lower(), complaints


def test_an_untrimmed_joined_remux_is_clean(tools, clip, second_clip, tmp_path):
    """Stitching a flight recorded across several files, untouched, is what the
    preset is for and has to keep working."""
    from flightdvr.jobs import write_concat_file

    clips = [probed(tools, clip), probed(tools, second_clip)]
    concat = write_concat_file(clips, tmp_path, "plain_join")
    out = target(tmp_path, "plain_join", "remux")
    ok, message = export(tools, tmp_path, clips, "remux", out, concat=concat)
    assert ok, message
    assert decode_complaints(tools, out) == ""
    assert probe_output(tools, out)["duration"] == pytest.approx(12.0, abs=0.5)


def test_a_trimmed_single_clip_remux_is_clean(tools, clip, tmp_path):
    """Only the joined path has this. A single clip's trim is a seek, and
    ffmpeg snaps to the keyframe before it — the "second or so out" the README
    already promises, rather than a torn picture."""
    out = target(tmp_path, "single_remux", "remux")
    ok, message = export(tools, tmp_path, [probed(tools, clip, 2.5, 4.5)],
                         "remux", out)
    assert ok, message
    assert decode_complaints(tools, out) == ""
