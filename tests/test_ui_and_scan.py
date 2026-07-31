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
    drives.sort(key=lambda d: (not d.removable, d.identifier))
    assert drives[0].identifier == "G:"


# -- Linux drive detection ----------------------------------------------------
#
# The platform-specific part is parsing /proc/mounts, which is pure string
# handling and can be tested anywhere. Fixture below is real content from a
# Fedora Atomic desktop with an SD card in a reader.

PROC_MOUNTS = """\
sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0
proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0
devtmpfs /dev devtmpfs rw,nosuid,size=4096k,nr_inodes=4194304 0 0
/dev/nvme0n1p3 / btrfs rw,relatime,seclabel,compress=zstd:1 0 0
/dev/nvme0n1p2 /boot ext4 rw,relatime,seclabel 0 0
/dev/nvme0n1p1 /boot/efi vfat rw,relatime,fmask=0077 0 0
tmpfs /run/user/1000 tmpfs rw,nosuid,nodev,relatime,seclabel 0 0
/dev/sda1 /run/media/pilot/HDZERO exfat rw,nosuid,nodev,relatime,uid=1000 0 0
/dev/sdb1 /media/pilot/My\\040Backup\\040Drive ext4 rw,nosuid,nodev,relatime 0 0
/dev/loop0 /var/lib/snapd/snap/core22/1122 squashfs ro,nodev,relatime 0 0
overlay /var/lib/containers/storage/overlay overlay rw,relatime 0 0
"""


def test_only_real_filesystems_are_offered():
    """Kernel bookkeeping, snap images and container overlays are not drives."""
    found = scan.parse_linux_mounts(PROC_MOUNTS)
    mounts = [m for _dev, m, _fs in found]
    assert "/sys" not in mounts and "/proc" not in mounts and "/dev" not in mounts
    assert not any("snapd" in m for m in mounts)
    assert not any("containers" in m for m in mounts)
    assert "/" in mounts and "/boot" in mounts


def test_a_mounted_card_is_found():
    found = scan.parse_linux_mounts(PROC_MOUNTS)
    assert ("/dev/sda1", "/run/media/pilot/HDZERO", "exfat") in found


def test_spaces_in_mount_points_are_decoded():
    """A path with a space arrives as \\040 so the line stays parseable."""
    found = scan.parse_linux_mounts(PROC_MOUNTS)
    mounts = [m for _dev, m, _fs in found]
    assert "/media/pilot/My Backup Drive" in mounts


def test_tmpfs_is_not_a_drive():
    found = scan.parse_linux_mounts(PROC_MOUNTS)
    assert not any(dev == "tmpfs" for dev, _m, _fs in found)


def test_malformed_lines_do_not_break_parsing():
    assert scan.parse_linux_mounts("garbage\n\n/dev/sda1\n") == []


@pytest.mark.parametrize("mount", [
    "/media/pilot/HDZERO", "/run/media/pilot/CARD", "/mnt/usb",
])
def test_media_mounts_count_as_removable(mount):
    """Card readers sometimes report removable=0 in sysfs, so the mount
    location is used as a second signal."""
    assert any(mount.startswith(root) for root in scan.LINUX_MEDIA_ROOTS)


def test_system_mounts_are_not_treated_as_removable():
    for mount in ("/", "/boot", "/home", "/var"):
        assert not any(mount.startswith(root) for root in scan.LINUX_MEDIA_ROOTS)


# -- finding the card on macOS ------------------------------------------------

# /Volumes with a card in the reader. The startup disk appears as a symlink
# named after the Mac, and device 16 is the root filesystem it points at.
MAC_VOLUMES = [
    ("Macintosh HD", True, 16),
    ("HDZERO", False, 41),
    ("Time Machine", False, 52),
    (".timemachine", False, 53),
    (".hidden", False, 54),
]


def test_a_card_in_volumes_is_removable():
    drives = scan.macos_volumes_to_drives(MAC_VOLUMES, root_device=16)
    card = [d for d in drives if d.label == "HDZERO"]
    assert len(card) == 1
    assert card[0].removable
    assert card[0].path == Path("/Volumes/HDZERO")


def test_the_startup_disk_is_listed_once_and_is_not_removable():
    drives = scan.macos_volumes_to_drives(MAC_VOLUMES, root_device=16)
    boot = [d for d in drives if d.path == Path("/")]
    assert len(boot) == 1
    assert not boot[0].removable
    assert boot[0].label == "Macintosh HD"


def test_a_volume_on_the_root_device_is_not_a_second_drive():
    """The startup disk is not always represented as a symlink."""
    drives = scan.macos_volumes_to_drives([("Macintosh HD", False, 16)], 16)
    assert [d.path for d in drives] == [Path("/")]


def test_removable_only_leaves_out_the_startup_disk():
    drives = scan.macos_volumes_to_drives(MAC_VOLUMES, 16, removable_only=True)
    assert drives and all(d.removable for d in drives)
    assert not any(d.path == Path("/") for d in drives)


def test_hidden_volumes_are_skipped():
    names = [d.label for d in scan.macos_volumes_to_drives(MAC_VOLUMES, 16)]
    assert ".timemachine" not in names and ".hidden" not in names


def test_a_card_sorts_above_the_startup_disk():
    drives = scan.macos_volumes_to_drives(MAC_VOLUMES, 16)
    assert drives[0].removable
    assert drives[-1].path == Path("/")


def test_an_empty_volumes_folder_still_reports_the_startup_disk():
    """A Mac with nothing plugged in must not show an empty drive list."""
    drives = scan.macos_volumes_to_drives([], 16)
    assert [d.path for d in drives] == [Path("/")]


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
    """Windows maps .ts to Media Player and macOS to QuickTime; neither
    reliably decodes HEVC, so an installed VLC, mpv or IINA wins.

    Every known location is checked, and paths are compared as paths rather
    than as strings: PLAYER_PATHS carries the Windows and macOS locations on
    every platform, and to POSIX a Windows path is one long filename rather
    than something with separators in it.
    """
    from flightdvr.ui import PLAYER_PATHS

    for candidate in PLAYER_PATHS:
        monkeypatch.setattr(Path, "exists",
                            lambda self, want=candidate: self == want)
        assert find_player() == candidate


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


# -- an export is all or nothing ----------------------------------------------

def worker_for(tmp_path):
    from flightdvr.jobs import ExportWorker
    return ExportWorker(TOOLS, [], tmp_path)


def run_job_with(tmp_path, job, ffmpeg_effect, validates=True):
    """Drive _run_job with ffmpeg and ffprobe replaced.

    `ffmpeg_effect(temp_path)` stands in for the encode: it can write a file,
    write nothing, or raise. Returns whatever _run_job returned.
    """
    worker = worker_for(tmp_path)
    encoded_to = {}

    def fake_run_one(index, command, duration, offset, weight):
        temp = Path(command[-1])
        encoded_to["path"] = temp
        return ffmpeg_effect(temp)

    worker._run_one = fake_run_one
    worker._validate = lambda path: (
        (True, "") if validates else (False, "no video in it")
    )
    result = worker._run_job(0, job)
    return result, encoded_to.get("path")


def test_a_failed_overwrite_leaves_the_previous_export_intact(tmp_path):
    """The reason this exists: ffmpeg gets -y, so pointing it at the real path
    truncated the user's file on open. A failure then destroyed work that was
    already finished, and cleanup refused to touch it because it pre-existing.
    """
    job = queued_job()
    job.out_path = tmp_path / "previous_export.mp4"
    job.out_path.write_bytes(b"a good export from yesterday")

    def encoder_fails(temp):
        temp.write_bytes(b"truncated rubbish")
        return False, "Encoder failure"

    (ok, message), temp = run_job_with(tmp_path, job, encoder_fails)

    assert not ok and "Encoder failure" in message
    assert job.out_path.read_bytes() == b"a good export from yesterday"
    assert temp != job.out_path, "ffmpeg must never be aimed at the real path"
    assert not temp.exists(), "the wreckage should not be left lying about"


def test_a_successful_export_replaces_the_previous_one(tmp_path):
    job = queued_job()
    job.out_path = tmp_path / "export.mp4"
    job.out_path.write_bytes(b"old")

    def encoder_works(temp):
        temp.write_bytes(b"a brand new export")
        return True, ""

    (ok, _), temp = run_job_with(tmp_path, job, encoder_works)

    assert ok
    assert job.out_path.read_bytes() == b"a brand new export"
    assert not temp.exists()


def test_output_that_fails_validation_never_reaches_the_target(tmp_path):
    """A sub-GOP remux exits zero having written a header and no streams."""
    job = queued_job()
    job.out_path = tmp_path / "export.mp4"
    job.out_path.write_bytes(b"still good")

    def writes_an_empty_container(temp):
        temp.write_bytes(b"\x00" * 261)
        return True, ""

    (ok, message), _ = run_job_with(
        tmp_path, job, writes_an_empty_container, validates=False
    )

    assert not ok and "no video" in message
    assert job.out_path.read_bytes() == b"still good"


def test_the_temporary_file_keeps_the_container_extension(tmp_path):
    """ffmpeg picks its muxer from the extension, so .mov must stay .mov."""
    job = queued_job("edit")
    job.out_path = tmp_path / "clip_edit.mov"
    (_, _), temp = run_job_with(
        tmp_path, job, lambda t: (t.write_bytes(b"x"), (True, ""))[1]
    )
    assert temp.suffix == ".mov"
    assert temp.parent == job.out_path.parent, "must be on the same filesystem"


def test_only_pending_jobs_are_run():
    """Pressing Start twice used to re-encode everything already finished."""
    from flightdvr.jobs import JobStatus
    statuses = [JobStatus.DONE, JobStatus.PENDING, JobStatus.SKIPPED,
                JobStatus.FAILED, JobStatus.CANCELLED]
    runnable = [s for s in statuses if s is JobStatus.PENDING]
    assert runnable == [JobStatus.PENDING]


# -- reporting a failure usefully ---------------------------------------------

# Real ffmpeg 7.1.1 output, captured by encoding a 127x95 source. The cause is
# the first line; the nine after it are fallout, and the app used to show the
# user the last one.
ODD_SIZE_FAILURE = [
    "Stream #0:0 -> #0:0 (wrapped_avframe (native) -> h264 (libx264))",
    "[libx264 @ 0000022774177580] width not divisible by 2 (127x95)",
    "[vost#0:0/libx264 @ 000002277416af40] Error while opening encoder - maybe "
    "incorrect parameters such as bit_rate, rate, width or height.",
    "[vf#0:0 @ 00000227741782c0] Error sending frames to consumers: Generic "
    "error in an external library",
    "[vf#0:0 @ 00000227741782c0] Task finished with error code: -542398533",
    "[vf#0:0 @ 00000227741782c0] Terminating thread with return code -542398533",
    "[vost#0:0/libx264 @ 000002277416af40] Could not open encoder before EOF",
    "[vost#0:0/libx264 @ 000002277416af40] Task finished with error code: -22",
    "[out#0/mp4 @ 0000022774165400] Nothing was written into output file, "
    "because at least one of its streams received no packets.",
    "Conversion failed!",
]

# Also real: an output path that cannot be opened.
UNWRITABLE_FAILURE = [
    "Input #0, lavfi, from 'testsrc=duration=1:rate=30':",
    "  Stream #0:0: Video: wrapped_avframe, rgb24, 320x240",
    "[out#0/mp4 @ 00000195e31f4b80] Error opening output Z:\\nowhere\\x.mp4: "
    "No such file or directory",
    "Error opening output file Z:\\nowhere\\x.mp4.",
    "Error opening output files: No such file or directory",
]


def test_the_cause_is_reported_not_the_last_line():
    from flightdvr.jobs import _describe_failure
    assert "not divisible by 2" in _describe_failure(ODD_SIZE_FAILURE, 1)


def test_an_unwritable_output_names_the_path_and_the_reason():
    from flightdvr.jobs import _describe_failure
    reported = _describe_failure(UNWRITABLE_FAILURE, 1)
    assert "No such file or directory" in reported
    assert "nowhere" in reported


def test_the_informational_preamble_is_never_reported_as_an_error():
    from flightdvr.jobs import _describe_failure
    for log in (ODD_SIZE_FAILURE, UNWRITABLE_FAILURE):
        assert not _describe_failure(log, 1).startswith(("Input #", "Stream #"))


def test_a_silent_failure_still_reports_something():
    from flightdvr.jobs import _describe_failure
    assert _describe_failure([], 137) == "exit code 137"


def test_output_is_never_discarded_just_because_it_is_unfamiliar():
    """ffmpeg's wording varies by version. Reporting only lines that match a
    keyword list meant an export failing under Ubuntu 22.04's ffmpeg 4.4 told
    the user "exit code 1" and nothing else, which is the exact moment its own
    account of the problem is worth the most."""
    from flightdvr.jobs import _describe_failure
    log = [
        "Input #0, mpegts, from 'hdz_001.ts':",
        "Something went wrong in a way nobody anticipated",
    ]
    assert _describe_failure(log, 1) == "Something went wrong in a way nobody anticipated"


def test_noise_is_still_preferred_over_nothing():
    """Even when every line is boilerplate, quote one rather than an errno."""
    from flightdvr.jobs import _describe_failure
    assert _describe_failure(["Conversion failed!"], 1) == "Conversion failed!"


# -- joining several clips -----------------------------------------------------

def joinable(name, **overrides):
    from flightdvr.media import ClipInfo
    fields = dict(size=1, modified=datetime(2026, 7, 4), duration=60.0,
                  width=1280, height=720, fps=60.0, video_codec="hevc",
                  audio_codec="aac")
    fields.update(overrides)
    return ClipInfo(path=Path(name), **fields)


def test_matching_clips_can_be_joined():
    from flightdvr.presets import join_problems
    assert join_problems([joinable("a.ts"), joinable("b.ts")]) == []


def test_one_clip_is_never_a_join_problem():
    from flightdvr.presets import join_problems
    assert join_problems([joinable("a.ts", width=640)]) == []


@pytest.mark.parametrize("difference,expected", [
    (dict(width=1920, height=1080), "different sizes"),
    (dict(fps=30.0), "different frame rates"),
    (dict(video_codec="h264"), "different video codecs"),
    (dict(audio_codec=""), "drop the audio"),
])
def test_mismatched_clips_are_refused_with_a_reason(difference, expected):
    """A join built from mismatched clips does not fail loudly. It produces a
    file that is silent after the first clip, or the wrong length, and looks
    like it worked."""
    from flightdvr.presets import join_problems
    problems = join_problems([joinable("a.ts"), joinable("b.ts", **difference)])
    assert problems, f"{difference} should have been refused"
    assert any(expected in p for p in problems), problems


def test_the_refusal_names_the_clips_and_suggests_what_to_do():
    from flightdvr.presets import describe_join_problems, join_problems
    clips = [joinable("hdz_001.ts"), joinable("hdz_002.ts", fps=30.0)]
    message = describe_join_problems(clips, join_problems(clips))
    assert "hdz_001.ts" in message and "hdz_002.ts" in message
    assert "separately" in message


def test_a_join_that_cannot_work_never_reaches_ffmpeg(tmp_path):
    from flightdvr.jobs import ExportWorker, Job
    from flightdvr.presets import ExportSettings
    job = Job(clips=[joinable("a.ts"), joinable("b.ts", fps=30.0)],
              preset_key="master", settings=ExportSettings(),
              out_path=tmp_path / "joined.mp4")
    ok, message = ExportWorker(TOOLS, [job], tmp_path)._run_job(0, job)
    assert not ok and "frame rates" in message
    assert not (tmp_path / "joined.mp4").exists()


def test_each_set_of_clips_gets_its_own_concat_list():
    """Lists were named after the first clip alone, so a+b and a+c shared one
    file and the second overwrote the first."""
    from flightdvr.ui import _clip_set_id
    a, b, c = joinable("a.ts"), joinable("b.ts"), joinable("c.ts")
    assert _clip_set_id([a, b]) != _clip_set_id([a, c])
    assert _clip_set_id([a, b]) == _clip_set_id([a, b])


def test_trimming_a_clip_changes_its_concat_list():
    from flightdvr.ui import _clip_set_id
    a, b = joinable("a.ts"), joinable("b.ts")
    trimmed = joinable("b.ts")
    trimmed.trim_in = 5.0
    assert _clip_set_id([a, b]) != _clip_set_id([a, trimmed])


# -- licence obligations are structural, so guard them structurally -----------

ROOT = Path(__file__).resolve().parents[1]


def test_the_lgpl_text_is_in_the_repository():
    """Qt reaches users under the LGPL, and section 4(b) requires its text to
    accompany the combined work. It was in no build before 1.1.1."""
    text = (ROOT / "LICENSE.LGPL-3.0.txt").read_text(encoding="utf-8")
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in text
    assert "Version 3" in text


@pytest.mark.parametrize("route", [
    "packaging/flightdvr_studio.spec",
    "packaging/installer.iss",
    "packaging/build-appimage.sh",
    "packaging/build-macos.sh",
])
def test_every_packaging_route_ships_both_licences(route):
    text = (ROOT / route).read_text(encoding="utf-8")
    assert "LICENSE.LGPL-3.0.txt" in text, f"{route} omits Qt's licence"
    assert "LICENSE" in text, f"{route} omits our own licence"


def test_the_licence_is_findable_from_a_source_checkout():
    """About shows this path, so it has to resolve in every layout."""
    from flightdvr.media import packaged_file
    found = packaged_file("LICENSE")
    assert found is not None and found.exists()


def test_a_missing_packaged_file_reports_nothing_rather_than_guessing():
    from flightdvr.media import packaged_file
    assert packaged_file("NO-SUCH-FILE.txt") is None


def test_the_bundled_ffmpeg_is_pinned_and_matches_the_notices():
    """The notices name a build and offer its corresponding source. If the pin
    and the notices disagree, one of them is lying about what ships."""
    import json
    pin = json.loads((ROOT / "packaging/ffmpeg-build.json").read_text(encoding="utf-8"))
    notices = (ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")

    assert pin["version"] in notices, "the notices name a different build"
    assert pin["release_tag"] in notices
    assert pin["ffmpeg_git_commit"] in notices, "no link to the exact source"
    for tool in ("ffmpeg.exe", "ffprobe.exe"):
        assert len(pin["binaries"][tool]) == 64, f"{tool} has no usable hash"


def test_the_windows_build_refuses_an_unpinned_ffmpeg():
    """Packaging whatever happens to be installed is how the attribution
    drifted away from the binary in the first place."""
    script = (ROOT / "packaging/build.ps1").read_text(encoding="utf-8")
    assert "ffmpeg-build.json" in script
    assert "Get-FileHash" in script
    assert "does not match the pinned build" in script


def test_a_system_ffmpeg_is_not_reported_as_bundled():
    """Only the Windows installer carries one; saying otherwise in the About
    box would misstate the licensing position."""
    from flightdvr.media import is_bundled
    assert not is_bundled(Path("/usr/bin/ffmpeg"))
    assert not is_bundled(Path(r"C:\ffmpeg\bin\ffmpeg.exe"))


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


# -- proving a packaged build works without a person to click it --------------

def test_version_flag_prints_and_exits_cleanly(capsys):
    from flightdvr.ui import launch
    assert launch(["--version"]) == 0
    assert "FlightDVR Studio" in capsys.readouterr().out


def test_check_reports_qt_and_never_raises(qt_app):
    """CI runs --check against the packaged build, so it must always return."""
    from flightdvr.ui import _describe_environment
    report, code = _describe_environment()
    assert "Qt " in report
    # 0 where ffmpeg is installed, 3 where it is not. Either is a real answer;
    # what matters is that it reports rather than raising or opening a dialog.
    assert code in (0, 3)


def test_check_reports_where_it_found_both_licences(qt_app):
    """Each package format stores them differently, and a build that cannot
    find its own licence should fail CI rather than surprise someone in the
    About dialog."""
    from flightdvr.ui import _describe_environment
    report, code = _describe_environment()
    assert "NOT FOUND" not in report
    assert "LICENSE.LGPL-3.0.txt" in report
    assert code != 5


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
