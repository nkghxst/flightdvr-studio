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

"""Tests for command construction and colour decisions.

These run without ffmpeg present: they check the commands we would issue,
which is where the subtle mistakes live.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flightdvr.media import ClipInfo, Tools  # noqa: E402
from flightdvr.presets import (  # noqa: E402
    LEVELS, PASSTHROUGH, REC709, ExportSettings, build_commands, colour_filters,
    estimate_output_size, output_path, target_video_bitrate,
)

TOOLS = Tools(Path("ffmpeg"), Path("ffprobe"))


def boxpro_clip(**overrides) -> ClipInfo:
    """A clip matching what an HDZero Box Pro actually records."""
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


def flatten(command: list[str]) -> str:
    return " ".join(command)


# -- colour -------------------------------------------------------------------

def test_levels_mode_converts_full_range_footage():
    filters = colour_filters(LEVELS, boxpro_clip(), "yuv420p")
    assert "scale=in_range=full:out_range=limited" in filters
    assert filters[-1] == "format=yuv420p"


def test_levels_mode_leaves_limited_range_footage_alone():
    clip = boxpro_clip(pix_fmt="yuv420p", color_range="tv")
    assert colour_filters(LEVELS, clip, "yuv420p") == ["format=yuv420p"]


def test_levels_mode_does_not_touch_the_colour_matrix():
    """Retagging the matrix without converting measurably shifts the picture."""
    joined = " ".join(colour_filters(LEVELS, boxpro_clip(), "yuv420p"))
    assert "setparams" not in joined
    assert "matrix" not in joined


def test_passthrough_makes_no_range_conversion():
    assert colour_filters(PASSTHROUGH, boxpro_clip(), "yuv420p") == ["format=yuv420p"]


def test_rec709_converts_every_component_together():
    joined = " ".join(colour_filters(REC709, boxpro_clip(), "yuv420p"))
    # A partial conversion is worse than none, so all three must move at once.
    for expected in ("matrixin=470bg", "primariesin=bt470bg", "transferin=smpte170m"):
        assert expected in joined


def test_detects_full_range_from_either_signal():
    assert boxpro_clip(color_range="", pix_fmt="yuvj420p").is_full_range
    assert boxpro_clip(color_range="pc", pix_fmt="yuv420p").is_full_range
    assert not boxpro_clip(color_range="tv", pix_fmt="yuv420p").is_full_range


# -- command construction -----------------------------------------------------

def build(preset: str, settings: ExportSettings | None = None, clip: ClipInfo | None = None):
    return build_commands(
        TOOLS, clip or boxpro_clip(), preset, settings or ExportSettings(),
        Path("out.mp4"), Path("work"),
    )


def test_every_preset_forces_constant_frame_rate_except_remux():
    for preset in ("edit", "master", "social"):
        assert "-fps_mode cfr" in flatten(build(preset)[0]), preset


def test_transport_stream_timestamps_are_regenerated():
    for preset in ("edit", "master", "social", "remux"):
        assert "-fflags +genpts" in flatten(build(preset)[0]), preset


def test_remux_does_not_re_encode():
    commands = build("remux")
    assert len(commands) == 1
    assert "-c copy" in flatten(commands[0])
    assert "-vf" not in flatten(commands[0])


def test_edit_preset_uses_a_mezzanine_codec_and_pcm_audio():
    command = flatten(build("edit")[0])
    assert "dnxhd" in command
    assert "dnxhr_sq" in command
    assert "pcm_s16le" in command
    assert "yuv422p" in command


def test_edit_preset_can_switch_to_prores():
    settings = ExportSettings(edit_codec="prores_422")
    command = flatten(build("edit", settings)[0])
    assert "prores_ks" in command
    assert "yuv422p10le" in command


def test_master_uses_crf_and_faststart():
    command = flatten(build("master", ExportSettings(master_crf=16))[0])
    assert "libx264" in command
    assert "-crf 16" in command
    assert "+faststart" in command


def test_master_switches_to_hardware_encoder_on_request():
    settings = ExportSettings(use_gpu=True, hw_encoder="h264_amf")
    command = flatten(build("master", settings)[0])
    assert "h264_amf" in command
    assert "libx264" not in command


def test_hardware_is_never_assumed_without_a_detected_encoder():
    """Asking for hardware on a machine that has none must fall back to CPU.

    The first version hardcoded AMD's encoder, so ticking the box on a laptop
    with no Radeon produced a command that could not run.
    """
    settings = ExportSettings(use_gpu=True, hw_encoder="")
    command = flatten(build("master", settings)[0])
    assert "libx264" in command
    assert "_amf" not in command and "_nvenc" not in command and "_qsv" not in command


@pytest.mark.parametrize("encoder,expected", [
    ("h264_nvenc", "-rc constqp"),
    ("h264_qsv", "-global_quality"),
    ("h264_amf", "-rc cqp"),
])
def test_each_vendor_gets_its_own_quality_flags(encoder, expected):
    """Constant-quality is spelled differently by every vendor."""
    settings = ExportSettings(use_gpu=True, hw_encoder=encoder)
    command = flatten(build("master", settings)[0])
    assert encoder in command
    assert expected in command


def test_size_targeting_on_hardware_is_a_single_bitrate_pass():
    settings = ExportSettings(use_gpu=True, hw_encoder="h264_nvenc", social_mode="size")
    commands = build("social", settings)
    assert len(commands) == 1                      # no two-pass on hardware
    assert "-b:v" in flatten(commands[0])


def test_size_targeted_social_export_runs_two_passes():
    commands = build("social", ExportSettings(social_mode="size"))
    assert len(commands) == 2
    assert "-pass 1" in flatten(commands[0])
    assert "-pass 2" in flatten(commands[1])
    assert "-f null" in flatten(commands[0])


def test_both_passes_configure_identical_streams():
    """x264 rejects the stats file when the two passes disagree.

    Dropping audio from pass 1 shifts the video framing by one frame, which
    produced "2nd pass has more frames than 1st pass" and a failed export.
    """
    first, second = build("social", ExportSettings(social_mode="size"))
    # Compared as tokens: "-an" appears inside "-analyzeduration" as a substring.
    assert "-an" not in first
    for fragment in ("-c:a aac", "aresample=async=1", "-fps_mode cfr", "-r 60"):
        assert fragment in flatten(first), fragment
        assert fragment in flatten(second), fragment


def test_both_passes_share_one_stats_file():
    first, second = build("social", ExportSettings(social_mode="size"))
    log = [a for a in first if "pass_" in a]
    assert log and log == [a for a in second if "pass_" in a]


def test_quality_targeted_social_export_runs_once():
    commands = build("social", ExportSettings(social_mode="quality"))
    assert len(commands) == 1
    assert "-crf 23" in flatten(commands[0])


def test_social_downscale_keeps_width_even():
    settings = ExportSettings(social_mode="quality", social_height=540)
    assert "scale=-2:540" in flatten(build("social", settings)[0])


def test_social_does_not_upscale():
    settings = ExportSettings(social_mode="quality", social_height=720)
    assert "scale=-2:720" not in flatten(build("social", settings)[0])


# -- footage from other HDZero goggles ----------------------------------------
#
# Every HDZero goggle writes the same .ts format, but not at the same size:
# 720p90 and 1080p30 modes exist, and the Goggle 2 records 1080p. Nothing may
# assume the Box Pro's 720p60.

def goggle2_clip(**overrides) -> ClipInfo:
    return boxpro_clip(width=1920, height=1080, fps=30.0, **overrides)


def test_keeping_the_source_size_adds_no_scale_filter():
    settings = ExportSettings(social_mode="quality", social_height=0)
    assert "scale=-2:" not in flatten(build("social", settings, goggle2_clip())[0])


def test_1080p_footage_can_be_downscaled_to_720():
    settings = ExportSettings(social_mode="quality", social_height=720)
    assert "scale=-2:720" in flatten(build("social", settings, goggle2_clip())[0])


def test_frame_rate_is_never_raised_above_the_source():
    """Asking a 30 fps recording for 60 only duplicates frames."""
    settings = ExportSettings(social_mode="quality", social_fps=60)
    command = flatten(build("social", settings, goggle2_clip())[0])
    assert "-r 30" in command
    assert "-r 60" not in command


def test_frame_rate_can_still_be_lowered():
    settings = ExportSettings(social_mode="quality", social_fps=30)
    assert "-r 30" in flatten(build("social", settings, boxpro_clip())[0])


def test_mezzanine_bitrate_scales_with_the_footage():
    """DNxHR is a data rate per pixel, so 1080p30 is not 720p60's number."""
    from flightdvr.presets import edit_bitrate_mbps
    boxpro = edit_bitrate_mbps("dnxhr_sq", boxpro_clip())
    goggle2 = edit_bitrate_mbps("dnxhr_sq", goggle2_clip())
    assert boxpro == pytest.approx(130, abs=1)
    # 1920x1080x30 is about 1.125x the pixel rate of 1280x720x60.
    assert goggle2 == pytest.approx(130 * 1.125, rel=0.02)


def test_estimates_scale_with_resolution():
    settings = ExportSettings()
    small = estimate_output_size(boxpro_clip(), "master", settings)
    large = estimate_output_size(goggle2_clip(), "master", settings)
    assert large > small


def test_downscaling_reduces_the_estimate():
    keep = ExportSettings(social_mode="quality", social_height=0)
    small = ExportSettings(social_mode="quality", social_height=480)
    clip = goggle2_clip()
    assert (estimate_output_size(clip, "social", small)
            < estimate_output_size(clip, "social", keep))


def test_1080p_footage_still_gets_the_levels_fix():
    """The colour quirk is about range tagging, not about resolution."""
    filters = colour_filters(LEVELS, goggle2_clip(), "yuv420p")
    assert "scale=in_range=full:out_range=limited" in filters


def test_audio_is_dropped_when_the_clip_has_none():
    clip = boxpro_clip(audio_codec="")
    command = build("master", clip=clip)[0]
    assert "-an" in command          # token, not substring of -analyzeduration
    assert "-c:a" not in command


def test_audio_is_dropped_when_switched_off():
    command = build("master", ExportSettings(keep_audio=False))[0]
    assert "-an" in command


def test_audio_is_resampled_to_hold_sync():
    # DVR audio starts fractionally late and drifts over a long recording.
    assert "aresample=async=1" in flatten(build("master")[0])


# -- trimming -----------------------------------------------------------------

def trimmed_clip(start=44.0, end=104.0, **overrides) -> ClipInfo:
    clip = boxpro_clip(**overrides)
    clip.trim_in, clip.trim_out = start, end
    return clip


# -- ffmpeg 5.1 renamed -vsync to -fps_mode, and 22.04 still ships 4.4 --------

def test_a_modern_ffmpeg_gets_fps_mode(monkeypatch):
    from flightdvr import media
    monkeypatch.setattr(media, "_fps_mode_supported", lambda _: True)
    assert media.frame_rate_mode(TOOLS, "cfr") == ["-fps_mode", "cfr"]


def test_an_older_ffmpeg_gets_vsync(monkeypatch):
    """Ubuntu 22.04 ships ffmpeg 4.4, the AppImage is built for 22.04 on
    purpose and carries no ffmpeg of its own, and every re-encoding export used
    -fps_mode. Every export on that distribution failed outright."""
    from flightdvr import media
    monkeypatch.setattr(media, "_fps_mode_supported", lambda _: False)
    assert media.frame_rate_mode(TOOLS, "cfr") == ["-vsync", "cfr"]
    assert media.frame_rate_mode(TOOLS, "passthrough") == ["-vsync", "passthrough"]


@pytest.mark.parametrize("supported,flag", [(True, "-fps_mode"), (False, "-vsync")])
@pytest.mark.parametrize("preset", ["edit", "master", "social"])
def test_re_encoding_presets_pin_the_frame_rate_either_way(
    monkeypatch, supported, flag, preset
):
    from flightdvr import media
    monkeypatch.setattr(media, "_fps_mode_supported", lambda _, v=supported: v)
    command = build(preset)[0]
    assert flag in command, f"{preset} lost its frame rate mode"
    assert ("-fps_mode" in command) != ("-vsync" in command), "both spellings sent"


def test_remux_does_not_pin_the_frame_rate():
    """It copies the stream, so there is nothing to synchronise."""
    command = build("remux")[0]
    assert "-fps_mode" not in command and "-vsync" not in command


def test_an_untrimmed_clip_adds_no_seek():
    command = build("master")[0]
    assert "-ss" not in command
    assert "-t" not in command


def test_trim_decodes_a_lead_in_before_the_in_point():
    """The in point is split across two seeks, and the second one is what makes
    the first output frame correct.

    A single seek before -i lands on an estimated byte offset in an MPEG-TS,
    not a keyframe, so decoding starts without a reference picture. Measured on
    real footage that produced 30 frames of garbage at 12 dB PSNR — every frame
    from the in point to the next keyframe — with no ffmpeg error and the right
    frame count. See SEEK_LEAD_IN.
    """
    command = build("master", clip=trimmed_clip())[0]
    i = command.index("-i")
    before = [command[n + 1] for n, a in enumerate(command[:i]) if a == "-ss"]
    after = [command[n + 1] for n, a in enumerate(command[i:], i) if a == "-ss"]

    assert before == ["42.000"], "fast seek should land SEEK_LEAD_IN early"
    assert after == ["2.000"], "the lead-in must be discarded after decoding"
    assert float(before[0]) + float(after[0]) == pytest.approx(44.0)
    assert command[command.index("-t") + 1] == "60.000"


def test_a_lead_in_never_seeks_past_the_start_of_the_file():
    """An in point closer to zero than the lead-in has a shorter one."""
    command = build("master", clip=trimmed_clip(0.5, 60.0))[0]
    i = command.index("-i")
    assert "-ss" not in command[:i], "nothing to fast-seek to before 0.5s"
    after = [command[n + 1] for n, a in enumerate(command[i:], i) if a == "-ss"]
    assert after == ["0.500"], "the whole in point becomes an accurate seek"


def test_the_two_seeks_always_sum_to_the_in_point():
    for start in (0.2, 1.0, 2.0, 2.5, 44.0, 180.0):
        command = build("master", clip=trimmed_clip(start, 200.0))[0]
        i = command.index("-i")
        total = sum(
            float(command[n + 1]) for n, a in enumerate(command) if a == "-ss"
        )
        assert total == pytest.approx(start), f"in point {start} drifted"


def test_trimmed_duration_is_the_kept_footage():
    assert trimmed_clip(44.0, 104.0).trimmed_duration == pytest.approx(60.0)
    assert trimmed_clip(30.0, 0.0).trimmed_duration == pytest.approx(212.7 - 30)
    assert boxpro_clip().trimmed_duration == pytest.approx(212.7)


def test_an_out_point_of_zero_means_the_end():
    clip = trimmed_clip(10.0, 0.0)
    assert clip.out_point == pytest.approx(212.7)
    assert clip.is_trimmed


def test_a_clip_with_no_trim_is_not_trimmed():
    assert not boxpro_clip().is_trimmed
    assert boxpro_clip().trim_label == ""


def test_trim_label_reads_as_a_range():
    assert trimmed_clip(44.0, 104.0).trim_label == "0:44–1:44"


def test_size_target_uses_the_trimmed_length_not_the_whole_clip():
    """A 45 MB target on a one minute trim is not the same bitrate as on the
    full three and a half minutes."""
    whole = target_video_bitrate(boxpro_clip(), 45, 128)
    part = target_video_bitrate(trimmed_clip(44.0, 104.0), 45, 128)
    assert part > whole * 3


def test_estimates_shrink_with_the_trim():
    settings = ExportSettings()
    whole = estimate_output_size(boxpro_clip(), "master", settings)
    part = estimate_output_size(trimmed_clip(44.0, 104.0), "master", settings)
    assert part == pytest.approx(whole * 60 / 212.7, rel=0.02)


def test_remux_estimate_scales_with_the_trim():
    settings = ExportSettings()
    part = estimate_output_size(trimmed_clip(44.0, 104.0), "remux", settings)
    assert part == pytest.approx(boxpro_clip().size * 60 / 212.7, rel=0.02)


def test_trim_applies_to_every_re_encoding_preset():
    for preset in ("edit", "master", "social", "remux"):
        command = build(preset, clip=trimmed_clip())[0]
        assert "-ss" in command, preset
        assert "-t" in command, preset


def test_joined_export_reads_from_a_concat_list():
    commands = build_commands(
        TOOLS, boxpro_clip(), "master", ExportSettings(), Path("out.mp4"), Path("work"),
        sources=[Path("a.ts"), Path("b.ts")], concat_file=Path("list.txt"),
    )
    command = flatten(commands[0])
    assert "-f concat" in command
    assert "-safe 0" in command
    assert "list.txt" in command


# -- size targeting -----------------------------------------------------------

def test_bitrate_targets_the_requested_size():
    clip = boxpro_clip(duration=141.03)
    kbps = target_video_bitrate(clip, size_mb=45, audio_kbps=128)
    predicted_mb = ((kbps + 128) * 141.03 / 8) / 1024
    # Two-pass x264 lands within a few percent; allow generous slack here.
    assert 40 < predicted_mb < 46


def test_bitrate_never_collapses_to_nothing():
    clip = boxpro_clip(duration=3600)
    assert target_video_bitrate(clip, size_mb=1, audio_kbps=128) >= 300


def test_estimated_size_grows_with_quality():
    clip = boxpro_clip()
    better = estimate_output_size(clip, "master", ExportSettings(master_crf=14))
    worse = estimate_output_size(clip, "master", ExportSettings(master_crf=24))
    assert better > worse


def test_size_targeted_social_estimate_is_the_target():
    settings = ExportSettings(social_mode="size", social_size_mb=45)
    assert estimate_output_size(boxpro_clip(), "social", settings) == 45 * 1024 * 1024


# -- output naming ------------------------------------------------------------

def test_output_paths_use_subfolders_and_suffixes():
    path = output_path(Path("/out"), "hdz_022", "edit", subfolders=True)
    assert path.parent.name == "Edit"
    assert path.name == "hdz_022_edit.mov"


def test_remux_keeps_the_original_name():
    path = output_path(Path("/out"), "hdz_022", "remux", subfolders=False)
    assert path.name == "hdz_022.mp4"


# Exports were originally the only route off the card that could not be dated,
# because the flight date lived solely in the copy-to-library dialog.

def test_exports_can_carry_the_flight_date():
    path = output_path(Path("/out"), "hdz_022", "master", False,
                       flight_date=date(2026, 7, 4))
    assert path.name == "2026-07-04_hdz_022_master.mp4"


def test_export_date_is_optional():
    path = output_path(Path("/out"), "hdz_022", "master", False, flight_date=None)
    assert path.name == "hdz_022_master.mp4"


def test_export_date_is_not_doubled_on_already_dated_clips():
    """Files copied to the library already start with a date."""
    path = output_path(Path("/out"), "2026-07-04_hdz_022", "master", False,
                       flight_date=date(2026, 7, 4))
    assert path.name == "2026-07-04_hdz_022_master.mp4"


def test_dated_exports_still_go_into_preset_subfolders():
    path = output_path(Path("/out"), "hdz_022", "edit", True, date(2026, 7, 4))
    assert path.parent.name == "Edit"
    assert path.name == "2026-07-04_hdz_022_edit.mov"


@pytest.mark.parametrize("preset,extension", [
    ("edit", ".mov"), ("master", ".mp4"), ("social", ".mp4"), ("remux", ".mp4"),
])
def test_containers_match_the_codec(preset, extension):
    assert output_path(Path("/out"), "x", preset, False).suffix == extension
