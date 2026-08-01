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

import subprocess
from pathlib import Path

import pytest

from conftest import FPS, CLEAN_PSNR, frame_psnr, probe_output
from flightdvr.jobs import ExportWorker, Job
from flightdvr.media import probe
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


@pytest.mark.parametrize("preset", ["edit", "master", "social", "remux"])
def test_every_preset_produces_something_playable(tools, clip, tmp_path, preset):
    out = target(tmp_path, "whole", preset)
    ok, message = export(tools, tmp_path, [probed(tools, clip)], preset, out)
    assert ok, f"{preset}: {message}"
    result = probe_output(tools, out)
    assert result["has_video"], f"{preset} produced no video"
    assert result["frames"] > 0, f"{preset} produced no frames"


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
    import os
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
