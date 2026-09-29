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

"""P2e: a joined preview crosses an occurrence's end exactly once.

Measured: a source whose last picture starts at 3.967 s (a 1 ms timestamp
grid) covers its occurrence up to 4.000333 s. The 30 Hz preview gave slots up
to 3.966667 only, so playback ran out 0.33 ms more than one slot before the
boundary and was reported as a source that ended early. That happened in both
preview routes, whenever a tick landed in that last interval.

The decoder now also emits the last slot the final picture still covers, and a
joined preview without a recipe judges its end at the cadence its decoder
runs at. A source missing its final picture still stops a whole slot short,
and still fails.
"""

from __future__ import annotations

import subprocess
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

from flightdvr.media import ClipInfo, find_tools, probe
from flightdvr.output_plan import PlannedOutput, working_outputs
from flightdvr.player import PREVIEW_FPS, PreviewPlayer, PreviewSize, build_command
from flightdvr.presets import PASSTHROUGH, ExportSettings
from flightdvr.preview_recipe import make_preview_recipe
from flightdvr.sequence_plan import Resolution, compile_sequence
from tests.test_player import (
    TOOLS,
    FakeClock,
    FakeFrameWorker,
    FakeWorker,
    RecipeWorker,
    fill_sequence_lane,
)

_W, _H, _PICTURES = 320, 180, 120
_BOUNDARY = "before its occurrence boundary"


# -- the decoder's last slots, by literal picture ------------------------------


@pytest.fixture(scope="module")
def endings(tmp_path_factory):
    """Four endings, each picture carrying its index: a 1 ms timestamp grid
    (Matroska, then a TS) and an exact 90 kHz one, complete and missing only
    their final picture."""
    tools = find_tools()
    root = tmp_path_factory.mktemp("endings")
    geq = ("geq=lum='if(bitand(floor(N/pow(2,floor(X/32))),1),235,16)'"
           ":cb=128:cr=128")
    encode = ["-f", "lavfi", "-i", f"color=black:s={_W}x{_H}:r=30:d=4",
              "-vf", geq, "-c:v", "libx264", "-preset", "ultrafast",
              "-qp", "0", "-g", "30"]

    def run(*args):
        subprocess.run([str(tools.ffmpeg), "-v", "error", "-y",
                        *map(str, args)], check=True)

    mkv = root / "pictures.mkv"
    run(*encode, mkv)
    made = {}
    for name, count in (("ms", _PICTURES), ("ms-missing", _PICTURES - 1)):
        made[name] = root / f"{name}.ts"
        run("-i", mkv, "-map", "0:v", "-c", "copy", "-frames:v", count,
            "-f", "mpegts", made[name])
    for name, count in (("exact", _PICTURES), ("exact-missing", _PICTURES - 1)):
        made[name] = root / f"{name}.ts"
        run(*encode, "-frames:v", count, "-f", "mpegts", made[name])
    return tools, made


def _ids(raw: bytes) -> list[int]:
    size, bar, row = _W * _H * 3, _W // 10, (_H // 2) * _W * 3
    return [sum(1 << bit for bit in range(10)
                if raw[at + row + (bit * bar + bar // 2) * 3] > 128)
            for at in range(0, len(raw), size)]


def _source_pts(tools, path) -> list[float]:
    found = subprocess.run(
        [str(tools.ffprobe), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "frame=best_effort_timestamp_time", "-of",
         "csv=p=0", str(path)], check=True, capture_output=True,
        text=True).stdout.replace(",", " ").split()
    return [float(one) - float(found[0]) for one in found]


@pytest.mark.parametrize("name, grid, pictures, slots, advertised", [
    # The 4.000 slot is still inside the last picture's own interval.
    ("ms", 0.001, 120, [*range(120), 119], 4.000333),
    ("ms-missing", 0.001, 119, list(range(119)), 4.000333),
    ("exact", 1 / 30, 120, list(range(120)), 4.0),
    ("exact-missing", 1 / 30, 119, list(range(119)), 4.0),
])
def test_the_decoder_shows_each_picture_only_inside_its_own_interval(
        endings, name, grid, pictures, slots, advertised):
    tools, made = endings
    pts = _source_pts(tools, made[name])
    assert len(pts) == pictures, "fixture"
    # ffprobe prints microseconds: on the grid means within that.
    assert all(abs(t - grid * round(t / grid)) < 2e-6 for t in pts), "grid"
    if grid == 0.001:
        assert pts[-1] != pytest.approx((pictures - 1) / 30, abs=1e-5)

    raw = subprocess.run(
        build_command(tools, probe(tools, made[name]), 0.0,
                      PreviewSize(_W, _H)),
        check=True, capture_output=True).stdout
    shown = _ids(raw)
    assert shown == slots

    for index, picture in enumerate(shown):
        slot = index / PREVIEW_FPS
        # Each slot shows the picture whose own interval contains it.
        assert pts[picture] - 0.5 / PREVIEW_FPS <= slot
        assert slot < pts[picture] + 1 / PREVIEW_FPS
    last = (len(shown) - 1) / PREVIEW_FPS
    # Against the occurrence's unchanged advertised end: a complete source
    # leaves at most one slot, one missing its final picture leaves two.
    gap = advertised - last
    if pictures == _PICTURES:
        assert gap <= 1 / PREVIEW_FPS + 1e-6
    else:
        assert gap > 1.9 / PREVIEW_FPS


# -- the player's end of an occurrence, tick by tick ---------------------------


def _clip(extent: float) -> ClipInfo:
    clip = ClipInfo(Path("late.ts"), 1, datetime(2026, 9, 29), 4.126,
                    _W, _H, 30.0, "h264", "aac", "yuv420p", "tv")
    clip.video_duration = extent
    return clip


def _sequence_player(route: str, extent: float):
    fake = FakeClock()
    made = []
    kind = RecipeWorker if route == "recipe" else FakeWorker

    def factory(*args, **kwargs):
        made.append(kind(*args, **kwargs))
        return made[-1]

    clips = [_clip(extent), _clip(extent)]
    output = working_outputs(clips, joined=True)[0]
    plan = compile_sequence(output, revision=f"p2e-{route}-{extent}",
                            resolution=Resolution.success())
    resolved = {one.id: clip for one, clip in zip(plan.occurrences, clips)}
    p = PreviewPlayer(TOOLS, clock=fake, worker_factory=factory,
                      frame_worker_factory=FakeFrameWorker)
    shown, failures, ended = [], [], []
    if route == "recipe":
        recipe = make_preview_recipe(
            PlannedOutput(output.target, "master",
                          ExportSettings(colour=PASSTHROUGH)),
            output, plan, resolved)
        assert recipe.cadence == PREVIEW_FPS and recipe.time_factor == 1
        p.load_recipe(recipe, resolved)
        p.output_frame_ready.connect(
            lambda _i, _k, _b, occurrence, output_s, source_s:
            shown.append((occurrence.ordinal, output_s, source_s)))
    else:
        p.load_sequence(plan, resolved)
        p.sequence_frame_ready.connect(
            lambda _i, _r, occurrence, output_s, source_s:
            shown.append((occurrence.ordinal, output_s, source_s)))
    p.failed.connect(failures.append)
    p.ended.connect(lambda: ended.append(p.position))
    return p, fake, plan, shown, failures, ended


def _tick_to(p, fake, when: float) -> None:
    fake.tick(when - p.position)
    p._tick()


@pytest.mark.parametrize("route", ["direct", "recipe"])
@pytest.mark.parametrize("extent, last_slots", [
    (4.000333, (3.9, 3.9 + 1 / 30, 3.9 + 2 / 30, 4.0)),   # 1 ms grid
    (4.0, (3.9, 3.9 + 1 / 30, 3.9 + 2 / 30)),              # exact grid
])
def test_the_last_picture_carries_the_clock_across_the_seam_once(
        qt_app, route, extent, last_slots):
    p, fake, plan, shown, failures, ended = _sequence_player(route, extent)
    boundary = float(plan.occurrences[0].output.end)
    assert boundary == pytest.approx(extent)
    p.seek(3.9)
    p.play(view_width=_W)
    first = p._sequence_active
    fill_sequence_lane(p, first, *last_slots)
    for when in (3.9, 3.95, 3.99):
        _tick_to(p, fake, when)
    first.worker.ended.emit(first.generation)
    # Every tick inside the last interval, the one that used to fail.
    for when in (3.999, 4.0, boundary - 0.0001):
        if when < boundary:
            _tick_to(p, fake, when)
    assert not failures, failures
    assert [s for o, s, _src in shown if o == 0][-1] == pytest.approx(
        last_slots[-1])

    following = p._sequence_next
    assert following is not None and following.occurrence.ordinal == 1
    fill_sequence_lane(p, following, 0.0, 1 / 30)
    _tick_to(p, fake, boundary + 0.001)
    assert not failures, failures
    assert all(out < boundary for o, out, _src in shown if o == 0)
    seam = [(o, out, src) for o, out, src in shown if o == 1]
    assert seam[0] == (1, pytest.approx(boundary), pytest.approx(0.0))
    assert not ended


@pytest.mark.parametrize("route", ["direct", "recipe"])
@pytest.mark.parametrize("extent, slots_without_final", [
    (4.000333, (3.9, 3.9 + 1 / 30)),   # 1 ms grid, 119 pictures
    (4.0, (3.9, 3.9 + 1 / 30)),        # exact grid, 119 pictures
])
def test_a_source_missing_only_its_final_picture_still_fails(
        qt_app, route, extent, slots_without_final):
    """The occurrence still says the source runs to its end; the decoder, as
    measured above, stops a whole slot before the last it could show."""
    p, fake, _plan, shown, failures, _ended = _sequence_player(route, extent)
    p.seek(3.9)
    p.play(view_width=_W)
    lane = p._sequence_active
    fill_sequence_lane(p, lane, *slots_without_final)
    for when in (3.9, 3.94):
        _tick_to(p, fake, when)
    lane.worker.ended.emit(lane.generation)
    _tick_to(p, fake, 3.97)
    assert failures and _BOUNDARY in failures[0]
    assert all(o == 0 for o, _out, _src in shown)


@pytest.mark.parametrize("route", ["direct", "recipe"])
def test_an_overtaken_lane_s_end_does_not_touch_the_one_after_a_seek(
        qt_app, route):
    """A stale `ended` from before a restart must not end, or fail, the
    lane that replaced it, inside or outside the last interval."""
    p, fake, plan, _shown, failures, _ended = _sequence_player(
        route, 4.000333)
    p.seek(3.9)
    p.play(view_width=_W)
    old = p._sequence_active
    fill_sequence_lane(p, old, 3.9, 3.9 + 1 / 30)
    _tick_to(p, fake, 3.92)
    p.seek(3.95)                       # a restart: a new lane, a new decoder
    new = p._sequence_active
    assert new is not old and new.generation != old.generation
    old.worker.ended.emit(old.generation)
    assert not new.ended
    fill_sequence_lane(p, new, 3.9 + 2 / 30, 4.0)
    for when in (3.97, 4.0, 4.0002):
        _tick_to(p, fake, when)
    assert not failures, failures
    new.worker.ended.emit(new.generation)
    _tick_to(p, fake, 4.0003)
    assert not failures, failures


@pytest.mark.parametrize("route", ["direct", "recipe"])
def test_pausing_inside_the_last_interval_holds_then_resumes_across(
        qt_app, route):
    p, fake, plan, shown, failures, _ended = _sequence_player(
        route, 4.000333)
    boundary = float(plan.occurrences[0].output.end)
    p.seek(3.9)
    p.play(view_width=_W)
    lane = p._sequence_active
    fill_sequence_lane(p, lane, 3.9, 3.9 + 1 / 30, 3.9 + 2 / 30, 4.0)
    for when in (3.9, 3.99, 4.0):
        _tick_to(p, fake, when)
    lane.worker.ended.emit(lane.generation)
    p.pause()
    fake.tick(5.0)                     # a long pause costs no position
    assert not p.is_playing and not failures
    assert p.position == pytest.approx(4.0)
    p.play(view_width=_W)
    assert not failures, failures
    p.stop()
    assert not p.is_playing and not failures
    assert all(out < boundary for o, out, _src in shown if o == 0)


# -- the same through a real decoder --------------------------------------------


@pytest.mark.parametrize("route", ["direct", "recipe"])
@pytest.mark.parametrize("name, advertised_by, fails", [
    ("ms", None, False),
    ("ms-missing", "ms", True),
])
def test_a_real_decoder_crosses_both_seams_or_reports_a_short_source(
        qt_app, endings, name, advertised_by, fails, route):
    """Ticks are placed, not raced: the fake clock only moves when the queue
    has what the next tick needs, or the lane has ended."""
    from PySide6.QtWidgets import QApplication
    import time

    tools, made = endings
    info = probe(tools, made[name])
    if advertised_by is not None:
        # The occurrence still says what the complete file says.
        complete = probe(tools, made[advertised_by])
        info.duration = complete.duration
        info.video_duration = complete.video_duration
    clips = [deepcopy(info), deepcopy(info)]
    output = working_outputs(clips, joined=True)[0]
    plan = compile_sequence(output, revision=f"p2e-real-{name}",
                            resolution=Resolution.success())
    fake = FakeClock()
    p = PreviewPlayer(tools, clock=fake)
    shown, failures, ended = [], [], []
    resolved = {o.id: c for o, c in zip(plan.occurrences, clips)}
    p.sequence_frame_ready.connect(
        lambda image, _r, occurrence, output_s, source_s: shown.append(
            (occurrence.ordinal, round(output_s, 6), _picture_id(image))))
    p.output_frame_ready.connect(
        lambda image, _k, _b, occurrence, output_s, source_s: shown.append(
            (occurrence.ordinal, round(output_s, 6), _picture_id(image))))
    p.failed.connect(failures.append)
    p.ended.connect(lambda: ended.append(p.position))
    if route == "recipe":
        p.load_recipe(make_preview_recipe(
            PlannedOutput(output.target, "master",
                          ExportSettings(colour=PASSTHROUGH)),
            output, plan, resolved), resolved)
    else:
        p.load_sequence(plan, resolved)
    app = QApplication.instance()
    p.play(view_width=_W)
    total = float(plan.total_duration)
    steps = [k / 120 for k in range(1, int(total * 120) + 3)]
    try:
        for when in steps:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not (failures or ended):
                app.processEvents()
                lane = p._sequence_active
                if lane is None or not lane.frames.empty() or lane.ended:
                    break
                time.sleep(0.002)
            if failures or ended:
                break
            fake.tick(max(0.0, when - p.position))
            p._tick()
    finally:
        p.shutdown()
        deadline = time.monotonic() + 5
        while p._streaming_workers_alive() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
    assert p._streaming_workers_alive() == 0
    boundary = float(plan.occurrences[0].output.end)
    if fails:
        assert failures and _BOUNDARY in failures[0]
        assert all(o == 0 for o, _out, _id in shown)
        return
    assert not failures, failures
    assert ended == [pytest.approx(total)]
    first = [(out, pid) for o, out, pid in shown if o == 0]
    second = [(out, pid) for o, out, pid in shown if o == 1]
    assert all(out < boundary for out, _ in first)
    assert all(out >= boundary for out, _ in second)
    assert first[-1] == (pytest.approx(4.0), 119), "the covered last slot"
    assert second[0] == (pytest.approx(boundary), 0)
    assert second[-1][1] == 119


def _picture_id(image) -> int:
    row = _H // 2
    bar = image.width() // 10
    return sum(1 << bit for bit in range(10)
               if image.pixelColor(bit * bar + bar // 2, row).red() > 128)


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])
