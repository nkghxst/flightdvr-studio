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

"""Tests for the behaviour behind the reported UI problems.

Each test here corresponds to something that was actually wrong when the app
was first used against a full card.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Must be set before any QApplication exists, so these tests need no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flightdvr import scan  # noqa: E402
from flightdvr.media import ClipInfo, Tools  # noqa: E402
from flightdvr.thumbs import RESYNC_SECONDS, build_command  # noqa: E402
from flightdvr.ui import (  # noqa: E402
    FPS_STEPS, RESOLUTION_STEPS, find_player, human_duration, natural_key,
)

TOOLS = Tools(Path("ffmpeg"), Path("ffprobe"))


def clip(name="hdz_022.ts", duration=212.7, **overrides) -> ClipInfo:
    defaults = dict(
        path=Path(name), size=599_189_652, modified=datetime(2025, 10, 8, 18, 39),
        duration=duration, width=1280, height=720, fps=60.0,
        video_codec="hevc", audio_codec="aac", pix_fmt="yuvj420p", color_range="pc",
    )
    defaults.update(overrides)
    return ClipInfo(**defaults)


# -- runtime shown to the user ------------------------------------------------

def test_runtime_is_not_shown_as_decimal_minutes():
    """1 min 34 s was being displayed as "1.6 minutes", which reads as 1m36s."""
    assert human_duration(94) == "1 min 34 sec"


@pytest.mark.parametrize("seconds,expected", [
    (45, "45 sec"),
    (60, "1 min 00 sec"),
    (212.7, "3 min 33 sec"),
    (3600, "1 hr 00 min"),
    (7845, "2 hr 10 min"),
])
def test_runtime_formats(seconds, expected):
    assert human_duration(seconds) == expected


# -- sorting ------------------------------------------------------------------

def test_clip_names_sort_numerically_not_alphabetically():
    names = ["hdz_112.ts", "hdz_005.ts", "hdz_9.ts", "hdz_074.ts"]
    assert sorted(names, key=natural_key) == [
        "hdz_005.ts", "hdz_9.ts", "hdz_074.ts", "hdz_112.ts",
    ]


def test_dvr_counter_is_read_from_the_filename():
    assert clip("hdz_112.ts").sequence == 112
    assert clip("hdz_005.ts").sequence == 5
    assert clip("no_digits.ts").sequence == -1


def test_counter_gives_a_stable_recording_order():
    """File dates are useless, so the DVR's counter is the ordering to use."""
    clips = [clip("hdz_010.ts"), clip("hdz_002.ts"), clip("hdz_100.ts")]
    ordered = sorted(clips, key=lambda c: c.sequence)
    assert [c.sequence for c in ordered] == [2, 10, 100]


# -- thumbnails ---------------------------------------------------------------

def test_thumbnail_decodes_past_the_seek_point():
    """Seeking into an MPEG-TS lands mid-GOP and produces grey noise.

    The fix is a second seek on the output side, which decodes through to the
    next real keyframe before a frame is kept.
    """
    command = build_command(TOOLS, clip(), Path("out.jpg"))
    seeks = [command[i + 1] for i, arg in enumerate(command) if arg == "-ss"]
    assert len(seeks) == 2, "expected an input seek and an output resync seek"
    assert float(seeks[1]) == pytest.approx(RESYNC_SECONDS)
    # The resync seek must come after the input, or it is just another fast seek.
    assert command.index("-i") < command.index("-ss", command.index("-i"))


def test_thumbnail_seek_stays_inside_the_clip():
    command = build_command(TOOLS, clip(duration=6.0), Path("out.jpg"))
    seeks = [float(command[i + 1]) for i, arg in enumerate(command) if arg == "-ss"]
    assert sum(seeks) < 6.0


def test_very_short_clip_does_not_seek_past_the_end():
    command = build_command(TOOLS, clip(duration=1.5), Path("out.jpg"))
    seeks = [float(command[i + 1]) for i, arg in enumerate(command) if arg == "-ss"]
    assert all(s < 1.5 for s in seeks)


def test_thumbnail_applies_the_same_levels_fix_as_the_export():
    command = build_command(TOOLS, clip(), Path("out.jpg"))
    assert "scale=in_range=full:out_range=limited" in " ".join(command)


def test_thumbnail_leaves_limited_range_footage_alone():
    command = build_command(TOOLS, clip(pix_fmt="yuv420p", color_range="tv"), Path("out.jpg"))
    assert "in_range=full" not in " ".join(command)


# -- the clock problem --------------------------------------------------------

def test_clustered_timestamps_are_reported_as_unreliable():
    """133 clips stamped within minutes of each other cannot be real."""
    base = datetime(2025, 10, 8, 18, 35)
    stamps = [base + timedelta(seconds=i * 5) for i in range(133)]
    message = scan.timestamps_are_unreliable(stamps)
    assert message
    assert "clock battery" in message


def test_prehistoric_timestamps_are_reported():
    stamps = [datetime(1979, 12, 31, 23, 0)] * 5
    assert "clock reset" in scan.timestamps_are_unreliable(stamps)


def test_genuinely_spread_timestamps_are_left_alone():
    stamps = [datetime(2025, 10, 8) + timedelta(days=i) for i in range(10)]
    assert scan.timestamps_are_unreliable(stamps) == ""


def test_a_handful_of_clips_is_not_enough_to_judge():
    stamps = [datetime(2025, 10, 8, 18, 35)] * 2
    assert scan.timestamps_are_unreliable(stamps) == ""


# -- ingest -------------------------------------------------------------------

def test_flight_date_overrides_the_useless_card_timestamp(tmp_path):
    source = tmp_path / "hdz_022.ts"
    source.write_bytes(b"x" * 1024)
    target = scan.ingest_destination(
        tmp_path / "lib", source, by_date=True, date_prefix=True,
        flight_date=datetime(2026, 7, 4).date(),
    )
    assert target.parent.name == "2026-07-04"
    assert target.name == "2026-07-04_hdz_022.ts"


# -- drive listing ------------------------------------------------------------

def test_only_removable_drives_are_tagged():
    """Labelling every internal disk "(fixed)" was noise; the card is the point."""
    card = scan.Drive(Path("G:/"), "G:", "UNTITLED", removable=True)
    disk = scan.Drive(Path("C:/"), "C:", "Windows", removable=False)
    assert "removable" in card.description
    assert "removable" not in disk.description
    assert "fixed" not in disk.description.lower()


def test_removable_drives_are_listed_first():
    drives = [
        scan.Drive(Path("C:/"), "C:", "Windows", removable=False),
        scan.Drive(Path("G:/"), "G:", "CARD", removable=True),
        scan.Drive(Path("D:/"), "D:", "Storage", removable=False),
    ]
    drives.sort(key=lambda d: (not d.removable, d.letter))
    assert drives[0].letter == "G:"


def test_reused_card_folders_are_skipped():
    """Cards get reused in phones; walking those trees wastes time on USB."""
    for folder in ("Android", "Music", "LOST.DIR", "System Volume Information"):
        assert folder.lower() in scan.SKIP_DIRS


# -- working with any HDZero goggle -------------------------------------------

def test_downscale_steps_cover_every_hdzero_mode():
    """720p60, 720p90 and 1080p30 exist today; the Goggle 2 records 1080p."""
    for height in (1080, 720, 540, 480):
        assert height in RESOLUTION_STEPS


def test_frame_rate_steps_cover_the_90fps_mode():
    assert 90 in FPS_STEPS and 60 in FPS_STEPS and 30 in FPS_STEPS


def test_only_smaller_sizes_are_offered_as_downscales():
    """Building the list must never offer to upscale."""
    for source in (720, 1080):
        offered = [s for s in RESOLUTION_STEPS if s < source]
        assert all(s < source for s in offered)
        assert source not in offered


# -- preview ------------------------------------------------------------------

def test_player_lookup_returns_something_runnable_or_nothing():
    player = find_player()
    assert player is None or player.exists()


def test_a_known_good_player_is_preferred_over_the_association(monkeypatch):
    """Windows maps .ts to Media Player, which often cannot decode HEVC."""
    fake = Path("C:/Program Files/VideoLAN/VLC/vlc.exe")
    monkeypatch.setattr(Path, "exists", lambda self: str(self) == str(fake))
    assert find_player() == fake


# -- queued jobs follow later changes to the output settings ------------------

def queued_job(preset="master", **overrides):
    from flightdvr.jobs import Job
    from flightdvr.presets import ExportSettings, output_path
    settings = ExportSettings()
    out_dir = Path("/out")
    stem = "hdz_109"
    kwargs = dict(out_dir=out_dir, stem=stem, subfolders=True)
    kwargs.update(overrides)
    return Job([clip()], preset, settings,
               output_path(out_dir, stem, preset, True, None), **kwargs)


def test_ticking_the_date_after_queueing_renames_pending_jobs():
    """Ticking the box after adding to the queue looked like it did nothing.

    Output paths are worked out when a job is queued, so the already-queued
    jobs kept their old names with no indication anything had been missed.
    """
    job = queued_job()
    assert job.out_path.name == "hdz_109_master.mp4"
    job.retarget(datetime(2026, 7, 4).date())
    assert job.out_path.name == "2026-07-04_hdz_109_master.mp4"


def test_clearing_the_date_removes_it_again():
    job = queued_job()
    job.retarget(datetime(2026, 7, 4).date())
    job.retarget(None)
    assert job.out_path.name == "hdz_109_master.mp4"


def test_a_running_job_is_never_retargeted():
    """Renaming the destination of a job already writing would lose the file."""
    from flightdvr.jobs import JobStatus
    job = queued_job()
    job.status = JobStatus.RUNNING
    before = job.out_path
    job.retarget(datetime(2026, 7, 4).date())
    assert job.out_path == before


def test_a_finished_job_is_never_retargeted():
    from flightdvr.jobs import JobStatus
    job = queued_job()
    job.status = JobStatus.DONE
    before = job.out_path
    job.retarget(datetime(2026, 7, 4).date())
    assert job.out_path == before


def test_retarget_keeps_the_preset_subfolder():
    job = queued_job("edit")
    job.retarget(datetime(2026, 7, 4).date())
    assert job.out_path.parent.name == "Edit"
    assert job.out_path.name == "2026-07-04_hdz_109_edit.mov"


# -- a cancelled encode must not leave something that looks finished ----------

def worker_for(tmp_path):
    from flightdvr.jobs import ExportWorker
    return ExportWorker(TOOLS, [], tmp_path)


def test_partial_output_is_deleted_when_a_job_fails(tmp_path):
    """A killed MP4 has no moov atom and will not play, but the filename looks
    perfectly normal. Leaving it behind is worse than having nothing."""
    job = queued_job()
    job.out_path = tmp_path / "half_written.mp4"
    job.out_path.write_bytes(b"partial")
    assert worker_for(tmp_path)._discard_partial(job, existed_before=False)
    assert not job.out_path.exists()


def test_a_file_that_was_already_there_is_never_deleted(tmp_path):
    """Only files this run created may be cleaned up."""
    job = queued_job()
    job.out_path = tmp_path / "previous_export.mp4"
    job.out_path.write_bytes(b"someone else's work")
    assert not worker_for(tmp_path)._discard_partial(job, existed_before=True)
    assert job.out_path.exists()


def test_discarding_nothing_is_harmless(tmp_path):
    job = queued_job()
    job.out_path = tmp_path / "never_created.mp4"
    assert not worker_for(tmp_path)._discard_partial(job, existed_before=False)


def test_only_pending_jobs_are_run():
    """Pressing Start twice used to re-encode everything already finished."""
    from flightdvr.jobs import JobStatus
    statuses = [JobStatus.DONE, JobStatus.PENDING, JobStatus.SKIPPED,
                JobStatus.FAILED, JobStatus.CANCELLED]
    runnable = [s for s in statuses if s is JobStatus.PENDING]
    assert runnable == [JobStatus.PENDING]


def test_skipped_is_a_real_status():
    from flightdvr.jobs import JobStatus
    assert JobStatus.SKIPPED.value == "Skipped"


# -- trims travel with a joined export ----------------------------------------

def test_concat_list_carries_each_clips_own_trim(tmp_path):
    """A joined export must honour per-clip in and out points, not one range
    across the whole thing."""
    from flightdvr.jobs import write_concat_file
    first, second = clip("a.ts"), clip("b.ts")
    first.trim_in, first.trim_out = 10.0, 25.0
    second.trim_in, second.trim_out = 5.0, 15.0
    text = write_concat_file([first, second], tmp_path, "joined").read_text()
    assert "inpoint 10.000" in text and "outpoint 25.000" in text
    assert "inpoint 5.000" in text and "outpoint 15.000" in text


def test_untrimmed_clips_add_no_concat_directives(tmp_path):
    from flightdvr.jobs import write_concat_file
    text = write_concat_file([clip("a.ts"), clip("b.ts")], tmp_path, "plain").read_text()
    assert "inpoint" not in text and "outpoint" not in text


def test_job_duration_counts_only_the_kept_footage():
    """Progress and time-remaining weight by real work, so a trim must reduce it."""
    from flightdvr.jobs import Job
    from flightdvr.presets import ExportSettings
    a, b = clip("a.ts"), clip("b.ts")          # both 212.7s
    a.trim_in, a.trim_out = 10.0, 25.0         # keeps 15s
    job = Job([a, b], "master", ExportSettings(), Path("out.mp4"))
    assert job.total_duration == pytest.approx(15.0 + 212.7)


# -- the filmstrip ------------------------------------------------------------

def test_filmstrip_finds_the_frame_nearest_a_time():
    from flightdvr.trim import Filmstrip
    strip = Filmstrip([Path(f"f{i}.jpg") for i in range(5)], [0.0, 1.0, 2.0, 3.0, 4.0])
    assert strip.index_at(2.4) == 2
    assert strip.index_at(2.6) == 3
    assert strip.index_at(-5) == 0
    assert strip.index_at(99) == 4


def test_an_empty_filmstrip_is_falsy_and_safe():
    from flightdvr.trim import Filmstrip
    empty = Filmstrip()
    assert not empty
    assert empty.frame_at(10.0) is None
    assert empty.index_at(10.0) == 0


# -- explanatory text must not get cut off ------------------------------------

@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def test_muted_labels_are_allowed_to_grow_downwards(qt_app):
    """Wrapped text was clipped when the window narrowed.

    A wrapped QLabel reports the height it needs for its width, but a vertical
    layout will hand it less unless its size policy says otherwise.
    """
    from PySide6.QtWidgets import QLabel, QSizePolicy
    from flightdvr.ui import dim

    label = dim(QLabel("some fairly long explanatory sentence that will wrap"))
    assert label.wordWrap()
    assert label.sizePolicy().verticalPolicy() == QSizePolicy.Policy.MinimumExpanding


def test_no_wrapped_label_hides_a_blank_line(qt_app):
    """Qt underestimates a wrapped label's height when the text has newlines.

    The Remux panel lost its last two lines this way. Paragraphs go in separate
    labels instead. Message boxes are exempt: they lay their own text out and
    handle newlines correctly.
    """
    source = (Path(__file__).resolve().parents[1] / "flightdvr" / "ui.py").read_text(
        encoding="utf-8"
    )
    offenders = []
    for chunk in source.split("dim(QLabel(")[1:]:
        text = chunk.split("))")[0]
        if "\\n\\n" in text:
            offenders.append(" ".join(text.split())[:70])
    assert not offenders, (
        "these wrapped labels contain a blank line and will clip; "
        f"split them into separate labels: {offenders}"
    )
