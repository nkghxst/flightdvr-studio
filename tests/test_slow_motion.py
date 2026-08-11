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

"""Half speed out of the frames that were recorded.

The whole preset rests on two arguments agreeing: `setpts=2*PTS` spreads the
frames over twice as long, and the output rate says what rate that stream now
has. Get the second wrong and ffmpeg duplicates every frame to fill the runtime
— twice the frames, each shown twice, which plays smoothly and is not the
footage. So these tests are about the *relationship* between the two arguments
rather than the presence of either.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flightdvr import media  # noqa: E402
from flightdvr.media import ClipInfo, Tools  # noqa: E402
from flightdvr.presets import (  # noqa: E402
    PRESETS, SLOW_FACTOR, ExportSettings, build_commands,
    describe_join_problems, estimate_output_size, join_problems,
    output_runtime, slow_output_rate, templated_output_path,
)

TOOLS = Tools(Path("ffmpeg"), Path("ffprobe"))


def boxpro_clip(**overrides) -> ClipInfo:
    defaults = dict(
        path=Path("hdz_022.ts"),
        size=599_189_652,
        modified=datetime(2025, 10, 8, 18, 53),
        duration=212.7,
        width=1280,
        height=720,
        fps=60.0,
        video_codec="hevc",
        audio_codec="aac",
        pix_fmt="yuvj420p",
        color_range="pc",
        color_space="bt470bg",
        color_primaries="bt470bg",
        color_transfer="smpte170m",
    )
    defaults.update(overrides)
    return ClipInfo(**defaults)


def slow_command(clip: ClipInfo, **settings) -> list[str]:
    return build_commands(
        TOOLS, clip, "slowmo", ExportSettings(**settings),
        Path("out/hdz_022_slow.mp4"), Path("work"),
    )[0]


def rate_asked_for(command: list[str]) -> float:
    """The -r value, read back rather than searched for as a string."""
    return float(command[command.index("-r") + 1])


# -- the relationship the preset is ------------------------------------------

@pytest.mark.parametrize("source_fps, expected", [
    (60.0, 30.0),
    (90.0, 45.0),
    (50.0, 25.0),
    (30.0, 15.0),
])
def test_the_output_rate_is_the_recorded_rate_halved(source_fps, expected):
    """Halved, and not rounded onto the rates the interface offers elsewhere.

    45 is deliberate: `FPS_STEPS` in the export panel is [90, 60, 50, 30, 25],
    so a 90 fps recording slowed to 45 has no entry there. Snapping it to 50
    would need frames the recording does not contain, and snapping it to 30
    would throw a third of them away.
    """
    command = slow_command(boxpro_clip(fps=source_fps))
    assert rate_asked_for(command) == expected
    assert f"setpts={SLOW_FACTOR}*PTS" in " ".join(command)


def test_the_two_arguments_agree_so_no_frame_is_added_or_dropped():
    """Stated as arithmetic rather than as two separate assertions.

    A recording's frames, spread over `SLOW_FACTOR` times the runtime, are
    exactly `fps / SLOW_FACTOR` frames per second. If the output rate ever
    stops being that, the export gains or loses frames — which is the one thing
    this preset promises not to do.
    """
    clip = boxpro_clip(fps=60.0, duration=10.0)
    command = slow_command(clip)

    source_frames = clip.fps * clip.duration
    output_seconds = clip.duration * SLOW_FACTOR
    assert rate_asked_for(command) * output_seconds == source_frames


def test_the_source_rate_is_never_used_as_the_output_rate():
    """The specific wrong command, named so a later change cannot reintroduce
    it quietly: cfr at the source rate duplicates every frame."""
    command = slow_command(boxpro_clip(fps=60.0))
    assert rate_asked_for(command) != 60.0


def test_a_clip_with_no_readable_rate_still_produces_a_usable_command():
    """fps is 0 when ffprobe could not tell us, and a nameless division is how
    that becomes a crash mid-queue rather than a sensible default."""
    assert slow_output_rate([boxpro_clip(fps=0.0)]) == 60.0 / SLOW_FACTOR


# -- sound, which is not slowed ----------------------------------------------

def test_the_source_audio_is_dropped_whatever_the_tickbox_says():
    """Motor noise at half pitch is not slow motion, and audio left at speed
    over doubled video drifts apart by the length of the clip. Refused in the
    command, so no combination of settings can ask for it."""
    command = slow_command(boxpro_clip(), keep_audio=True)
    assert "-an" in command
    assert "-c:a" not in command


# -- the ffmpeg 5.1 rename, on this path too ---------------------------------

@pytest.mark.parametrize("supported, flag", [(True, "-fps_mode"), (False, "-vsync")])
def test_both_frame_rate_options_are_asked_for_not_assumed(monkeypatch,
                                                           supported, flag):
    """-fps_mode replaced -vsync in ffmpeg 5.1 and Ubuntu 22.04 ships 4.4. The
    slow path forces a rate of its own, so it needs the same question asked."""
    monkeypatch.setattr(media, "_fps_mode_supported", lambda _, v=supported: v)
    command = slow_command(boxpro_clip())
    assert [flag, "cfr"] == command[command.index(flag):command.index(flag) + 2]
    assert ("-fps_mode" in command) != ("-vsync" in command), "both spellings sent"


# -- a join is brought to one rate first -------------------------------------

def test_a_join_slows_the_rate_the_graph_normalised_to():
    """Every clip in a join is already brought to the largest rate present, so
    the halving applies to that rather than to whichever clip sorted first.

    Whether a mixed-rate join can keep the one-frame-once promise at all is a
    different question, answered before anything is queued.
    """
    fast = boxpro_clip(path=Path("hdz_030.ts"), fps=90.0)
    slow = boxpro_clip(path=Path("hdz_031.ts"), fps=60.0)
    assert slow_output_rate([slow, fast]) == 45.0
    assert slow_output_rate([fast, slow]) == 45.0


# -- it is an ordinary preset everywhere else --------------------------------

def test_every_preset_has_a_command_builder():
    """The fallthrough guard in build_commands exists because a preset without
    a branch was silently built as Social. This is the test that keeps the
    guard honest as presets are added."""
    for key in PRESETS:
        command = build_commands(
            TOOLS, boxpro_clip(), key, ExportSettings(),
            Path(f"out/hdz_022{PRESETS[key].extension}"), Path("work"),
        )
        assert command and command[0][0] == "ffmpeg", key


# -- a mixed-rate assembly is refused, not quietly coerced -------------------

def test_clips_recorded_at_one_rate_join_normally():
    """The rule is about mixed rates, not about joining at all."""
    same = [boxpro_clip(path=Path("hdz_030.ts"), fps=60.0),
            boxpro_clip(path=Path("hdz_031.ts"), fps=60.0)]
    assert join_problems(same, slowing=True) == []


def test_a_mixed_rate_assembly_is_refused_rather_than_put_on_one_rate():
    """Neither direction can keep the promise, which is why this is a refusal
    and not a normalisation.

    The join graph brings every clip to the highest rate present, so a 30 fps
    clip beside a 60 fps one has frames duplicated before anything is slowed —
    invented frames in an export whose whole claim is that it invents none.
    Going the other way throws away frames the faster clip really recorded.
    """
    mixed = [boxpro_clip(path=Path("hdz_030.ts"), fps=60.0),
             boxpro_clip(path=Path("hdz_031.ts"), fps=30.0)]
    problems = join_problems(mixed, slowing=True)
    assert problems, "a mixed-rate slow join was allowed"
    assert "30 and 60 fps" in problems[0]
    # and the refusal says what to do about it, not what went wrong internally
    spoken = describe_join_problems(mixed, problems)
    assert "Exporting them separately works" in spoken


def test_the_same_mixed_rates_still_join_for_every_other_preset():
    """Normalising rates is right for Master or Upload and has been for
    releases. Slow motion is the only preset that cannot accept it."""
    mixed = [boxpro_clip(path=Path("hdz_030.ts"), fps=60.0),
             boxpro_clip(path=Path("hdz_031.ts"), fps=30.0)]
    assert join_problems(mixed) == []


def test_an_unreadable_rate_is_refused_for_a_slow_join():
    """fps of 0 means ffprobe could not say. Slowing that is a guess about how
    many frames the clip holds."""
    unknown = [boxpro_clip(path=Path("hdz_030.ts"), fps=60.0),
               boxpro_clip(path=Path("hdz_031.ts"), fps=0.0)]
    problems = join_problems(unknown, slowing=True)
    assert any("could not be read" in problem for problem in problems)


# -- the three places a runtime is read ---------------------------------------

def test_the_finished_file_is_twice_the_footage_that_went_in():
    """One function behind all three readers, because the app had been treating
    "how long is the footage" and "how long is the file" as one number."""
    assert output_runtime("slowmo", 30.0) == 60.0
    assert output_runtime("master", 30.0) == 30.0
    assert output_runtime("remux", 30.0) == 30.0


def test_the_progress_bar_measures_against_the_doubled_runtime():
    """ffmpeg reports out_time against the file it is writing. Measured against
    the source length, a slow export would show 100% at the halfway point and
    then sit there for as long again."""
    from flightdvr.jobs import Job

    clip = boxpro_clip(duration=10.0)
    slow = Job([clip], "slowmo", ExportSettings(), Path("out/a_slow.mp4"))
    fast = Job([clip], "master", ExportSettings(), Path("out/a_master.mp4"))
    assert slow.total_duration == 20.0
    assert fast.total_duration == 10.0


def test_the_estimate_is_about_the_file_not_the_footage():
    """Half the frame rate over twice the runtime is the same pictures, so a
    slow export lands close to a Master of the same clip — and nowhere near
    half or double it, which is what the two obvious mistakes produce."""
    clip = boxpro_clip(duration=20.0)
    settings = ExportSettings()
    slow = estimate_output_size(clip, "slowmo", settings)
    master = estimate_output_size(clip, "master", settings)
    assert 0.8 * master <= slow <= 1.2 * master, (slow, master)


# -- the control, and what the window does with it ----------------------------

@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)

    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow

    made = MainWindow(find_tools())
    made.export_panel.out_edit.setCurrentText(str(tmp_path / "out"))
    yield made
    made.close()


def queued_clip(name: str = "hdz_047.ts", **overrides) -> ClipInfo:
    return boxpro_clip(path=Path(name), **overrides)


def test_the_preset_has_its_own_quality_not_masters(app):
    """Sharing Master's setting would mean changing Master's quality silently
    changed a slow export nobody was looking at."""
    from flightdvr.export_panel import ExportPanel

    panel = ExportPanel()
    panel.master_quality.setCurrentIndex(0)          # Archive
    panel.slow_quality.setCurrentIndex(3)            # Compact
    settings = panel.settings("")
    assert settings.master_crf == 14
    assert settings.slow_crf == 24


def test_the_quality_choice_survives_capture_and_apply(app):
    """A session stores what capture() returns. A setting that did not survive
    it would revert on reopening a card and re-encode at a different quality."""
    from flightdvr.export_panel import ExportPanel

    panel = ExportPanel()
    panel.preset_buttons["slowmo"].setChecked(True)
    panel.slow_quality.setCurrentIndex(0)
    stored = panel.capture()

    other = ExportPanel()
    other.apply(stored)
    assert other.slow_quality.currentData() == 14
    assert other.preset_key() == "slowmo"


def test_choosing_it_shows_its_own_page(app):
    """The options stack is indexed by PRESET_ORDER, so a preset added to one
    and not the other shows another preset's controls."""
    from flightdvr.export_panel import ExportPanel
    from flightdvr.presets import PRESET_ORDER

    panel = ExportPanel()
    panel.preset_buttons["slowmo"].setChecked(True)
    assert panel.options_stack.currentIndex() == PRESET_ORDER.index("slowmo")


def test_the_queue_takes_a_slow_export_end_to_end(window):
    """Through the window rather than the command builder: the preset is picked,
    the clip is ticked, and what lands in the queue is checked."""
    from PySide6.QtCore import Qt

    window.export_panel.preset_buttons["slowmo"].setChecked(True)
    window._add_clip(window._scan_generation, queued_clip(duration=10.0))
    window.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    window.jobs.clear()
    window._add_to_queue()

    assert len(window.jobs) == 1
    job = window.jobs[0]
    assert job.out_path.name == "hdz_047_slow.mp4"
    # The progress bar reads ffmpeg's out_time against this.
    assert job.total_duration == 20.0


def test_the_estimate_says_what_comes_out_and_what_went_in(window):
    """"20s of footage" is true and useless for a file that runs for 40. Both
    numbers, because the second is what makes the first make sense."""
    from PySide6.QtCore import Qt

    window.export_panel.preset_buttons["slowmo"].setChecked(True)
    window._add_clip(window._scan_generation, queued_clip(duration=20.0))
    window.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    window._update_estimate()

    shown = window.export_panel.estimate_label.text()
    assert "40 sec from 20 sec of footage" in shown, shown


def test_every_other_preset_still_reports_one_runtime(window):
    """The estimate must not start explaining itself for presets that write a
    file exactly as long as the footage."""
    from PySide6.QtCore import Qt

    window.export_panel.preset_buttons["master"].setChecked(True)
    window._add_clip(window._scan_generation, queued_clip(duration=20.0))
    window.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    window._update_estimate()

    shown = window.export_panel.estimate_label.text()
    assert "20 sec of footage" in shown and "from" not in shown, shown


def test_the_filename_says_slow_without_a_second_naming_rule():
    """Naming templates put the preset suffix in the name, so this preset needs
    no naming code of its own — `{preset}` resolves to it."""
    target = templated_output_path(Path("/out"), "hdz_022_slow", "slowmo",
                                   subfolders=False)
    assert target.name == "hdz_022_slow.mp4"
