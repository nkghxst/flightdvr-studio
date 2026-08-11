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

"""Slow motion against real media, where the frame count can be counted.

The unit tests check that the two arguments agree. Only ffmpeg can say whether
the file that comes out holds the frames that went in, and that is the whole
promise of the preset: every recorded frame kept, shown for twice as long, with
nothing invented between them.

Every fixture here asserts its own rate and frame count first. A fixture that
silently encoded at the wrong rate would make these tests pass while measuring
nothing, which is how the earlier odd-sized fixture in this suite tested
nothing at all and passed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import CLEAN_PSNR, GOP_SECONDS, frame_psnr, probe_output
from flightdvr.jobs import ExportWorker, Job
from flightdvr.media import probe
from flightdvr.presets import (
    SLOW_FACTOR, ExportSettings, templated_output_path,
)

pytestmark = pytest.mark.integration


def slow_export(tools, work, clips, out_path, settings=None):
    """One slow job through the real worker, exactly as the queue runs it."""
    job = Job(
        clips=list(clips),
        preset_key="slowmo",
        settings=settings or ExportSettings(),
        out_path=out_path,
        concat_file=None,
    )
    return ExportWorker(tools, [job], work)._run_job(0, job)


def probed(tools, clip, trim_in=0.0, trim_out=0.0):
    info = probe(tools, clip.path)
    info.trim_in, info.trim_out = trim_in, trim_out
    return info


def target(work, stem):
    return templated_output_path(work, stem, "slowmo", subfolders=False)


def measured(tools, path: Path) -> tuple[int, float, float]:
    """(frames, duration, rate) of a finished file, counted rather than claimed.

    The rate is derived from the two rather than read from the header: a
    container can carry a nominal rate that the packets do not support, and it
    is the packets that decide what a player shows.
    """
    facts = probe_output(tools, path)
    frames, duration = facts["frames"], facts["duration"]
    return frames, duration, (frames / duration if duration else 0.0)


@pytest.fixture(scope="module")
def source_facts(tools, clip):
    """What the 60 fps fixture really contains, asserted before anything uses it."""
    frames, duration, rate = measured(tools, clip.path)
    assert 55 <= rate <= 65, f"the fixture is {rate:.1f} fps, not 60"
    assert frames >= 300, f"only {frames} frames to slow down"
    return frames, duration, rate


@pytest.fixture(scope="module")
def clip_at_90(tools, media_dir):
    """A 90 fps recording, which other HDZero goggle modes produce.

    Built here rather than in conftest because it is the only thing in the
    suite that needs a rate other than 60, and 90 is the case that produces an
    output rate — 45 — which no list of frame rates in the app offers.
    """
    path = media_dir / "hdz_090fps.ts"
    keyint = int(GOP_SECONDS * 90)
    if not path.exists():
        done = subprocess.run([
            str(tools.ffmpeg), "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi",
            "-i", "testsrc2=size=320x180:rate=90:duration=4",
            "-c:v", "libx265", "-preset", "ultrafast",
            "-x265-params",
            f"keyint={keyint}:min-keyint={keyint}:scenecut=0:log-level=none",
            "-vf", "scale=in_range=limited:out_range=full",
            "-pix_fmt", "yuv420p", "-color_range", "pc",
            "-an", "-f", "mpegts", str(path),
        ], capture_output=True, text=True)
        if done.returncode != 0 or not path.exists():
            pytest.skip("this ffmpeg cannot build a 90 fps clip")

    frames, duration, rate = measured(tools, path)
    if not 85 <= rate <= 95:
        pytest.skip(f"the 90 fps fixture encoded at {rate:.1f} fps")
    return path, frames, duration


# -- the promise: every frame, twice as long ----------------------------------

def test_sixty_frames_come_out_as_sixty_over_twice_the_time(tools, clip,
                                                            tmp_path,
                                                            source_facts):
    """The measurement the preset exists to pass.

    Frame count identical, runtime doubled, and therefore half the rate. The
    plausible wrong command — cfr at the source rate — fails the first of those
    three by producing twice the frames, and passes the other two.
    """
    source_frames, source_duration, _ = source_facts
    out = target(tmp_path, "whole")

    ok, message = slow_export(tools, tmp_path, [probed(tools, clip)], out)
    assert ok, message

    frames, duration, rate = measured(tools, out)
    assert frames == source_frames, (
        f"{frames} frames out of {source_frames} in — frames were "
        "duplicated or dropped"
    )
    assert duration == pytest.approx(source_duration * SLOW_FACTOR, rel=0.05)
    assert rate == pytest.approx(30.0, abs=1.0)


def test_ninety_frames_a_second_becomes_forty_five(tools, tmp_path, clip_at_90):
    """The rate no list in the app offers, measured rather than reasoned about.

    45 is not in FPS_STEPS, so this is the case where rounding onto a supported
    rate would have shown up: 50 would need frames that were never recorded and
    30 would throw away a third of them.
    """
    source, source_frames, source_duration = clip_at_90
    from flightdvr.media import probe as probe_clip

    info = probe_clip(tools, source)
    out = target(tmp_path, "ninety")
    ok, message = slow_export(tools, tmp_path, [info], out)
    assert ok, message

    frames, duration, rate = measured(tools, out)
    assert frames == source_frames, f"{frames} out of {source_frames}"
    assert duration == pytest.approx(source_duration * SLOW_FACTOR, rel=0.05)
    assert rate == pytest.approx(45.0, abs=1.5), f"{rate:.2f} fps"


def legacy_vsync_accepted(tools) -> bool:
    """Whether this ffmpeg still takes the pre-5.1 spelling at all.

    Asked rather than assumed, because the answer changed twice: `-fps_mode`
    arrived in 5.1, and `-vsync` has since been *removed* — a current Windows
    build rejects it outright with "Unrecognized option 'vsync'". So the legacy
    half of this test can only run where the legacy option exists, and the app
    is right to ask `frame_rate_mode()` rather than pick one.
    """
    done = subprocess.run(
        [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-f", "lavfi",
         "-i", "testsrc2=size=64x64:rate=30:duration=0.1",
         "-vsync", "cfr", "-frames:v", "1", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return "Unrecognized option" not in done.stderr


@pytest.mark.parametrize("fps_mode_supported", [True, False])
def test_both_ffmpeg_spellings_preserve_the_frame_count(tools, clip, tmp_path,
                                                        source_facts,
                                                        monkeypatch,
                                                        fps_mode_supported):
    """-vsync and -fps_mode are the same request to different ffmpeg versions.

    Asserted by running both against real media rather than by inspecting the
    arguments: the two spellings could plausibly differ in behaviour, and a
    distribution shipping the older one would be the last to find out.

    The legacy half is skipped where the legacy option no longer exists. That
    is not a gap being papered over — forcing `-vsync` onto a build that has
    removed it tests nothing about this app, which asks which spelling the
    installed ffmpeg takes and would never send that combination. The
    argument-level pairing is pinned without ffmpeg in test_slow_motion.py.
    """
    from flightdvr import media

    if not fps_mode_supported and not legacy_vsync_accepted(tools):
        pytest.skip("this ffmpeg has removed -vsync, so it cannot be measured")

    monkeypatch.setattr(media, "_fps_mode_supported",
                        lambda _, v=fps_mode_supported: v)
    source_frames, source_duration, _ = source_facts
    out = target(tmp_path, "spelling")

    ok, message = slow_export(tools, tmp_path, [probed(tools, clip)], out)
    assert ok, message

    frames, duration, _ = measured(tools, out)
    assert frames == source_frames
    assert duration == pytest.approx(source_duration * SLOW_FACTOR, rel=0.05)


# -- the pictures themselves --------------------------------------------------

def psnr_by_frame_index(tools, produced: Path, reference: Path,
                        work: Path) -> list[float]:
    """Per-frame PSNR pairing frames by index rather than by timestamp.

    conftest's `frame_psnr` pairs by timestamp, which is right everywhere else
    and wrong here: a slow export's timestamps are deliberately twice the
    reference's, so pairing by them compares frame 1 against frame 2 and scores
    every frame as corrupt. Renumbering both inputs by frame index compares the
    Nth picture with the Nth picture, which is exactly the claim being tested —
    the frame counts are asserted equal separately.
    """
    log = work / "psnr_index.log"
    graph = (f"[0:v]setpts=N/TB[a];[1:v]setpts=N/TB[b];"
             f"[a][b]psnr=stats_file={log.name}:shortest=1")
    subprocess.run(
        [str(tools.ffmpeg), "-hide_banner", "-nostdin",
         "-i", str(produced), "-i", str(reference),
         "-lavfi", graph, "-f", "null", "-"],
        capture_output=True, text=True, cwd=work,
    )
    if not log.exists():
        return []
    values = []
    for line in log.read_text().splitlines():
        if "psnr_avg:" in line:
            raw = line.split("psnr_avg:")[1].split()[0]
            values.append(float("inf") if raw == "inf" else float(raw))
    return values


def lossless_reference(tools, info, out_path: Path) -> Path:
    """The same frames, same seek, same colour chain — and no slowing.

    A Master export is the obvious reference and the wrong one: it forces a
    constant 60 fps, which can duplicate a frame the source never had, and one
    duplicated frame at the front shifts a picture-by-picture comparison by one
    and scores every frame as corrupt. Measured: that is exactly what happened,
    at a uniform 17 dB.

    So the reference is lossless and keeps the source's own frame timing. It
    reuses the app's input arguments deliberately: the difference between this
    file and the export under test is then the slowing and nothing else. Seek
    correctness is a separate claim, pinned for every preset in
    test_integration.py.
    """
    from flightdvr.presets import _input_args, colour_filters

    chain = colour_filters(ExportSettings().colour, info, "yuv420p")
    command = ([str(tools.ffmpeg), "-hide_banner", "-nostdin", "-y"]
               + [str(a) for a in _input_args([info.path], None, info)]
               + ["-vf", ",".join(chain), "-fps_mode", "passthrough",
                  "-c:v", "ffv1", "-an", str(out_path)])
    done = subprocess.run(command, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-400:]
    return out_path


def test_the_frames_are_the_recorded_ones_not_new_ones(tools, clip, tmp_path):
    """Slowing must not filter, blend or interpolate the picture.

    Compared against a Master export of the same clip, picture by picture, with
    no extra encode on either side: both are one generation from the same
    source through the same colour chain, so anything that invented or blended
    a frame shows as a low score on that frame. Per-frame rather than averaged,
    because the mid-GOP defect this suite was built around averaged out to
    something that merely looked mediocre.
    """
    slowed = target(tmp_path, "slowed")
    ok, message = slow_export(tools, tmp_path, [probed(tools, clip)], slowed)
    assert ok, message

    reference = lossless_reference(tools, probed(tools, clip),
                                   tmp_path / "reference.mkv")

    assert (measured(tools, slowed)[0] == measured(tools, reference)[0]), (
        "different frame counts, so a picture-by-picture comparison would be "
        "comparing the wrong pairs"
    )
    scores = psnr_by_frame_index(tools, slowed, reference, tmp_path)
    assert scores, "nothing was compared"
    assert min(scores) >= CLEAN_PSNR, (
        f"worst frame {min(scores):.1f} dB - the slowed export is not made of "
        "the recorded frames"
    )


def test_a_mid_gop_trim_starts_on_the_frame_that_was_asked_for(tools, clip,
                                                              tmp_path):
    """The seek path is the same one that used to emit a GOP of corrupt video,
    and slowing spreads any lead-in over twice as long.

    The runtime assertion here is the one that caught a real defect: the trim
    length is applied on the output side, where setpts has already doubled the
    timestamps, so a slow export of a two-second range contained one second of
    footage until the limit was doubled with it.
    """
    start, length = 2.5, 2.0
    assert clip.is_mid_gop(start), "this test is pointless on a keyframe"

    out = target(tmp_path, "trimmed")
    ok, message = slow_export(tools, tmp_path,
                              [probed(tools, clip, start, start + length)], out)
    assert ok, message

    frames, duration, rate = measured(tools, out)
    assert duration == pytest.approx(length * SLOW_FACTOR, rel=0.08), (
        f"{duration:.2f}s of output for a {length:.2f}s range"
    )
    assert rate == pytest.approx(30.0, abs=1.5)

    reference = lossless_reference(
        tools, probed(tools, clip, start, start + length),
        tmp_path / "reference_trim.mkv",
    )
    assert frames == measured(tools, reference)[0], (
        "the slow export holds a different number of frames than the same "
        "range decoded without slowing"
    )
    scores = psnr_by_frame_index(tools, out, reference, tmp_path)
    assert scores, "nothing was compared"
    assert min(scores) >= CLEAN_PSNR, (
        f"worst frame {min(scores):.1f} dB - a corrupt lead-in, or a range "
        "that does not start where it was asked to"
    )


# -- what the file does not contain -------------------------------------------

def test_no_audio_survives_even_from_a_clip_that_has_it(tools, clip, tmp_path):
    """The fixture has sound, which is what makes this worth asserting."""
    assert clip.has_audio, "this test needs a clip with audio"
    out = target(tmp_path, "silent")
    ok, message = slow_export(tools, tmp_path, [probed(tools, clip)], out)
    assert ok, message
    assert not probe_output(tools, out)["has_audio"]


def test_cancelling_leaves_no_partial_file(tools, clip, tmp_path):
    """A slow export runs for twice as long, so it is twice as likely to be the
    one somebody cancels. A half-written file left behind under the final name
    looks exactly like a finished export."""
    out = target(tmp_path, "cancelled")
    job = Job(clips=[probed(tools, clip)], preset_key="slowmo",
              settings=ExportSettings(), out_path=out)
    worker = ExportWorker(tools, [job], tmp_path)
    worker.cancel()

    ok, _message = worker._run_job(0, job)
    assert not ok
    assert not out.exists(), "a cancelled export left a file under its own name"
    leftovers = list(tmp_path.glob("*.flightdvr-part"))
    assert not leftovers, leftovers
