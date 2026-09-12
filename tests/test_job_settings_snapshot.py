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

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from flightdvr.bundle import frozen_settings
from flightdvr.jobs import Job, JobStatus
from flightdvr.media import ClipInfo
from flightdvr.presets import ExportSettings


@dataclass
class FutureMusicSettings(ExportSettings):
    """Test the nested readiness boundary without shipping audio fields yet."""

    music: dict[str, list[float]] = field(
        default_factory=lambda: {"fades": [0.25, 0.5]})


def clip(name: str) -> ClipInfo:
    return ClipInfo(Path(name), 100, datetime(2026, 9, 8), duration=12.0)


def test_job_owns_a_deep_snapshot_of_future_nested_settings():
    """A shallow copy would let later music edits rewrite submitted work."""
    caller = FutureMusicSettings(social_size_mb=25)
    job = Job([clip("a.ts")], "social", caller, Path("a.mp4"))

    caller.social_size_mb = 99
    caller.music["fades"].append(0.75)

    assert job.settings.social_size_mb == 25
    assert job.settings.music == {"fades": [0.25, 0.5]}
    assert job.settings is not caller
    assert job.settings.music is not caller.music


def test_job_owns_the_submitted_source_metadata():
    """A later browser trim must not rewrite a waiting whole-clip job."""
    source = clip("whole.ts")
    source.duration = 240.0
    job = Job([source], "master", ExportSettings(), Path("whole.mp4"))

    source.trim_in, source.trim_out = 12.0, 30.0

    submitted = job.clips[0]
    assert submitted is not source
    assert submitted.selects == []
    assert (submitted.trim_in, submitted.out_point) == (0.0, 240.0)
    assert job.total_duration == 240.0


def test_ordinary_assembly_and_bundle_shaped_jobs_do_not_share_settings():
    caller = FutureMusicSettings(social_crf=20)
    ordinary = Job([clip("ordinary.ts")], "social", caller,
                   Path("ordinary.mp4"))
    assembly = Job([clip("a.ts"), clip("b.ts")], "master", caller,
                   Path("joined.mp4"), concat_file=Path("joined.txt"))

    bundle_member = frozen_settings(caller)
    bundle_first = Job([clip("a.ts")], "social", bundle_member,
                       Path("bundle-a.mp4"), frozen=True)
    bundle_second = Job([clip("b.ts")], "social", bundle_member,
                        Path("bundle-b.mp4"), frozen=True)

    caller.social_crf = 51
    caller.music["fades"].append(1.0)
    bundle_member.social_crf = 45
    bundle_member.music["fades"].append(2.0)

    jobs = (ordinary, assembly, bundle_first, bundle_second)
    assert [job.settings.social_crf for job in jobs] == [20, 20, 20, 20]
    assert all(job.settings.music == {"fades": [0.25, 0.5]} for job in jobs)
    assert len({id(job.settings) for job in jobs}) == len(jobs)
    assert len({id(job.settings.music) for job in jobs}) == len(jobs)


def test_snapshot_does_not_freeze_job_status_progress_or_path_policy(tmp_path):
    ordinary = Job([clip("a.ts")], "master", ExportSettings(),
                   tmp_path / "old.mp4", out_dir=tmp_path, stem="a",
                   subfolders=False)
    frozen = Job([clip("b.ts")], "master", ExportSettings(),
                 tmp_path / "promised.mp4", out_dir=tmp_path, stem="b",
                 subfolders=False, frozen=True)
    promised = frozen.out_path

    ordinary.retarget(date(2026, 9, 8))
    frozen.retarget(date(2026, 9, 8))
    ordinary.status = JobStatus.CANCELLED
    ordinary.progress = 0.4
    ordinary.message = "Cancelled"

    assert ordinary.out_path != tmp_path / "old.mp4"
    assert frozen.out_path == promised
    assert ordinary.status is JobStatus.CANCELLED
    assert ordinary.progress == 0.4
    assert ordinary.message == "Cancelled"
