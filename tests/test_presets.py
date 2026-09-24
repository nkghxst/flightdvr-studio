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
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flightdvr.media import ClipInfo, Tools  # noqa: E402
from flightdvr.audio_plan import (  # noqa: E402
    AudioAsset, AudioMode, MusicChoice, OUTPUT_RATE, SampleSpan,
    resolve_audio_plan,
)
from flightdvr.presets import (  # noqa: E402
    LEVELS, PASSTHROUGH, PRESET_ORDER, REC709, SEEK_LEAD_IN, ExportSettings,
    build_commands, colour_filters, estimate_output_size, output_path,
    target_video_bitrate, vertical_crop,
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


def constant_frame_rate_requested(command) -> bool:
    """Whether this command pins the frame rate, in either spelling.

    Asserting the exact option was a mistake: ffmpeg 5.1 renamed -vsync to
    -fps_mode and the app now asks which one the installed build takes, so the
    literal string depends on the machine. These tests passed on a developer's
    ffmpeg 7 and failed on Ubuntu 22.04's 4.4 while the app was behaving
    correctly on both.
    """
    flat = flatten(command)
    return "-fps_mode cfr" in flat or "-vsync cfr" in flat


def test_every_preset_forces_constant_frame_rate_except_remux():
    for preset in ("edit", "master", "social", "upload"):
        assert constant_frame_rate_requested(build(preset)[0]), preset


def test_transport_stream_timestamps_are_regenerated():
    for preset in ("edit", "master", "social", "upload", "remux"):
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
    for fragment in ("-c:a aac", "aresample=async=1", "-r 60"):
        assert fragment in flatten(first), fragment
        assert fragment in flatten(second), fragment
    # Whichever spelling this ffmpeg takes, both passes must use it.
    assert constant_frame_rate_requested(first)
    assert constant_frame_rate_requested(second)


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
@pytest.mark.parametrize("preset", ["edit", "master", "social", "upload"])
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
    assert after == ["44.000"], "the lead-in must be discarded after decoding"
    assert command[command.index("-t") + 1] == "60.000"


def test_both_seeks_are_measured_from_the_start_of_the_file():
    """Whether the first seek rebases the timeline turns out to depend on the
    file and on whether audio is being written. Pinning it with -copyts and
    -start_at_zero is what makes the second seek's number mean one thing:
    without them, a trim asked to begin at 2.5 s on a file whose audio starts
    before its video began at 2.0 s instead, cleanly and at the right length."""
    command = build("master", clip=trimmed_clip())[0]
    i = command.index("-i")
    assert "-copyts" in command[:i]
    assert "-start_at_zero" in command[:i]


def test_an_untrimmed_export_does_not_touch_timestamps():
    """Nothing to seek to means nothing to pin, and every export that does not
    trim should carry on producing exactly what it did before."""
    command = build("master")[0]
    assert "-copyts" not in command
    assert "-start_at_zero" not in command


def test_a_lead_in_never_seeks_past_the_start_of_the_file():
    """An in point closer to zero than the lead-in has a shorter one."""
    command = build("master", clip=trimmed_clip(0.5, 60.0))[0]
    i = command.index("-i")
    assert "-ss" not in command[:i], "nothing to fast-seek to before 0.5s"
    after = [command[n + 1] for n, a in enumerate(command[i:], i) if a == "-ss"]
    assert after == ["0.500"], "the whole in point becomes an accurate seek"


def test_the_second_seek_always_lands_on_the_in_point():
    for start in (0.2, 1.0, 2.0, 2.5, 44.0, 180.0):
        command = build("master", clip=trimmed_clip(start, 200.0))[0]
        i = command.index("-i")
        after = [command[n + 1] for n, a in enumerate(command[i:], i)
                 if a == "-ss"]
        assert after == [f"{start:.3f}"], f"in point {start} drifted"
        before = [command[n + 1] for n, a in enumerate(command[:i])
                  if a == "-ss"]
        # And the fast seek is never more than the lead-in earlier, nor before
        # the beginning of the file.
        fast = float(before[0]) if before else 0.0
        assert 0.0 <= fast <= start
        assert start - fast <= SEEK_LEAD_IN + 0.001


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
    for preset in ("edit", "master", "social", "upload", "remux"):
        command = build(preset, clip=trimmed_clip())[0]
        assert "-ss" in command, preset
        assert "-t" in command, preset


def test_a_size_target_scales_with_the_length_it_is_given():
    """A joined export is as long as all its clips, and the bitrate has to
    follow. Sizing from the first clip alone overshot the target by roughly the
    number of clips joined."""
    clip = boxpro_clip()
    alone = target_video_bitrate(clip, 45, 128)
    joined = target_video_bitrate(clip, 45, 128, runtime=clip.duration * 2)
    # Twice the footage in the same file size means about half the bitrate.
    assert joined < alone
    assert (joined + 128) == pytest.approx((alone + 128) / 2, rel=0.05)


def test_a_joined_export_is_sized_by_its_total_duration():
    clip = boxpro_clip()
    commands = build_commands(
        TOOLS, clip, "social", ExportSettings(social_mode="size"),
        Path("out.mp4"), Path("work"),
        sources=[clip.path, clip.path], concat_file=Path("c.txt"),
        total_duration=clip.duration * 2,
    )
    joined = int(commands[0][commands[0].index("-b:v") + 1].rstrip("k"))

    single = build_commands(
        TOOLS, clip, "social", ExportSettings(social_mode="size"),
        Path("out.mp4"), Path("work"),
    )
    alone = int(single[0][single[0].index("-b:v") + 1].rstrip("k"))

    assert joined < alone, "a join must not reuse the single-clip bitrate"


def test_a_single_clip_is_unaffected_by_the_new_argument():
    clip = boxpro_clip()
    without = build_commands(TOOLS, clip, "social",
                             ExportSettings(social_mode="size"),
                             Path("out.mp4"), Path("work"))
    with_zero = build_commands(TOOLS, clip, "social",
                               ExportSettings(social_mode="size"),
                               Path("out.mp4"), Path("work"), total_duration=0.0)
    assert without == with_zero


def two_clips(**second):
    a = boxpro_clip()
    a.path = Path("a.ts")
    b = boxpro_clip()
    b.path = Path("b.ts")
    for field, value in second.items():
        setattr(b, field, value)
    return [a, b]


def test_an_unknown_preset_is_refused_rather_than_built_as_social():
    """Social used to be the unguarded fallthrough in both the command builder
    and the estimator, so a preset added to PRESETS without its own branch was
    silently built as Social — plausible output, wrong preset."""
    unknown = "not-a-preset"
    assert unknown not in PRESET_ORDER, "pick a key that is not a real preset"
    with pytest.raises(KeyError):
        build_commands(TOOLS, boxpro_clip(), unknown, ExportSettings(),
                       Path("out.mp4"), Path("work"))
    with pytest.raises(KeyError):
        estimate_output_size(boxpro_clip(), unknown, ExportSettings())


# -- the Upload preset, which is the only one allowed to enlarge --------------

def upload_command(height=1080, source_height=720, **settings):
    clip = boxpro_clip()
    clip.height, clip.width = source_height, round(source_height * 16 / 9)
    return build_commands(
        TOOLS, clip, "upload",
        ExportSettings(upload_height=height, **settings),
        Path("out.mp4"), Path("work"),
    )[0], clip


def test_upload_enlarges_where_every_other_preset_refuses():
    """The point is the resolution tier, not the pixels. These sites hand out
    bitrate by the resolution you arrive at, so 1080p buys a bigger allowance
    for their re-encode than 720p does."""
    command, _ = upload_command(height=1080, source_height=720)
    assert "scale=-2:1080:flags=lanczos" in flatten(command)


def test_upload_can_still_downscale():
    command, _ = upload_command(height=720, source_height=1080)
    assert "scale=-2:720:flags=lanczos" in flatten(command)


def test_upload_at_the_source_height_adds_no_scale():
    command, _ = upload_command(height=720, source_height=720)
    assert "scale=-2:720" not in flatten(command)


def test_upload_is_never_size_targeted():
    """A byte budget and an upscale pull against each other: the same bits over
    more pixels is worse than doing neither. Upload is quality-based only."""
    command, _ = upload_command()
    assert "-b:v" not in command
    assert "-pass" not in command
    assert "-crf" in command


def test_upload_is_a_single_pass():
    clip = boxpro_clip()
    commands = build_commands(TOOLS, clip, "upload", ExportSettings(),
                              Path("out.mp4"), Path("work"))
    assert len(commands) == 1


def test_the_upload_estimate_grows_with_the_upscale():
    """pixel_rate() clamps to the source height for every other preset. Without
    an exception the estimate would report a 720p size for a 1080p file, and
    that number is what people plan their card around."""
    clip = boxpro_clip()
    clip.height, clip.width = 720, 1280
    at_source = estimate_output_size(
        clip, "upload", ExportSettings(upload_height=720))
    upscaled = estimate_output_size(
        clip, "upload", ExportSettings(upload_height=1080))
    assert upscaled > at_source * 1.5, (at_source, upscaled)


def test_upload_keeps_the_colour_handling():
    """An upscaled export is still full-range footage and still needs the range
    conversion, or it looks wrong in exactly the way the app exists to fix."""
    command, _ = upload_command()
    assert "scale=in_range=full:out_range=limited" in flatten(command)


def test_every_preset_can_be_built_and_estimated():
    """The other half of the guard: the raise must not catch a real preset."""
    for key in PRESET_ORDER:
        assert build_commands(TOOLS, boxpro_clip(), key, ExportSettings(),
                              Path("out.mp4"), Path("work")), key
        assert estimate_output_size(boxpro_clip(), key, ExportSettings()) > 0, key


def test_a_joined_remux_still_reads_from_a_concat_list():
    """Stream copy has no filter graph to route through."""
    commands = build_commands(
        TOOLS, boxpro_clip(), "remux", ExportSettings(), Path("out.mp4"), Path("work"),
        sources=[Path("a.ts"), Path("b.ts")], concat_file=Path("list.txt"),
        clips=two_clips(),
    )
    command = flatten(commands[0])
    assert "-f concat" in command and "-safe 0" in command and "list.txt" in command


def test_a_joined_re_encode_opens_each_clip_separately():
    """The concat demuxer applies one clip's properties to all of them. Every
    clip gets its own input and its own place in the filter graph instead."""
    clips = two_clips()
    commands = build_commands(
        TOOLS, clips[0], "master", ExportSettings(), Path("out.mp4"), Path("work"),
        clips=clips,
    )
    command = commands[0]
    assert command.count("-i") == 2, "each clip needs its own input"
    assert "-f" not in command or "concat" not in flatten(command).split("-filter_complex")[0]
    assert "-filter_complex" in command
    graph = command[command.index("-filter_complex") + 1]
    assert "[0:v]" in graph and "[1:v]" in graph
    assert "concat=n=2" in graph
    assert "-map" in command


def test_a_join_brings_every_clip_to_one_size_and_rate():
    clips = two_clips(width=1920, height=1080, fps=30.0)
    graph = build_commands(
        TOOLS, clips[0], "master", ExportSettings(), Path("out.mp4"), Path("work"),
        clips=clips,
    )[0]
    filtergraph = graph[graph.index("-filter_complex") + 1]
    # The largest of each, so nothing is thrown away for the smaller clip.
    assert "scale=1920:1080" in filtergraph
    assert "fps=60" in filtergraph


def test_a_join_gives_silence_to_the_clips_that_have_none():
    """Applying -an because the first clip is silent removed the sound from
    every clip that had it."""
    clips = two_clips()
    clips[0].audio_codec = ""            # first clip silent, second has sound
    graph = build_commands(
        TOOLS, clips[0], "master", ExportSettings(), Path("out.mp4"), Path("work"),
        clips=clips,
    )[0]
    filtergraph = graph[graph.index("-filter_complex") + 1]
    assert "anullsrc" in filtergraph, "no silence was synthesised"
    assert "concat=n=2:v=1:a=1" in filtergraph, "audio was dropped from the join"
    assert "-an" not in graph


def test_a_join_of_silent_clips_carries_no_audio():
    clips = two_clips()
    for c in clips:
        c.audio_codec = ""
    command = build_commands(
        TOOLS, clips[0], "master", ExportSettings(), Path("out.mp4"), Path("work"),
        clips=clips,
    )[0]
    assert "-an" in command
    assert "anullsrc" not in command[command.index("-filter_complex") + 1]


def test_each_clip_in_a_join_is_trimmed_accurately():
    """The concat demuxer's inpoint lands mid-GOP, which is what corrupted the
    start of a trimmed export. Each input gets the same two-part seek a single
    clip gets."""
    clips = two_clips()
    clips[1].trim_in, clips[1].trim_out = 44.0, 104.0
    command = build_commands(
        TOOLS, clips[0], "master", ExportSettings(), Path("out.mp4"), Path("work"),
        clips=clips,
    )[0]
    assert "42.000" in command, "no fast seek to a lead-in before the in point"
    graph = command[command.index("-filter_complex") + 1]
    assert "trim=start=2.000:duration=60.000" in graph


def _joined_audio_case(mode: AudioMode):
    first = boxpro_clip(path=Path("a.ts"), duration=3.0)
    middle = boxpro_clip(path=Path("b.ts"), duration=2.0, audio_codec="")
    third = boxpro_clip(path=Path("a.ts"), duration=3.0)
    clips = [first, middle, third]
    spans = (
        SampleSpan(0, 144_000, OUTPUT_RATE),
        SampleSpan(144_000, 240_000, OUTPUT_RATE),
        SampleSpan(240_000, 384_000, OUTPUT_RATE),
    )
    sequence = SimpleNamespace(
        occurrences=tuple(SimpleNamespace(sample_span=span) for span in spans),
        total_samples=384_000,
    )
    if mode in (AudioMode.REPLACE, AudioMode.MIX):
        asset = AudioAsset(
            Path("music.wav"), "a" * 64, 0, 44_100, 2, 8 * 44_100)
        choice = MusicChoice(
            asset=asset, passage=SampleSpan(44_100, 3 * 44_100, 44_100),
            mode=mode, music_level=Fraction(3, 4),
            dvr_level=Fraction(1, 4))
    else:
        choice = MusicChoice(mode=mode)
    plan = resolve_audio_plan(
        choice, 384_000, source_has_audio=True,
        preset_key="master", joined=True)
    command = build_commands(
        TOOLS, first, "master", ExportSettings(), Path("out.mp4"),
        Path("work"), clips=clips, audio_plan=plan, sequence=sequence,
    )[0]
    graph = command[command.index("-filter_complex") + 1]
    return command, graph


def test_joined_music_follows_every_source_and_uses_its_real_input_index():
    command, graph = _joined_audio_case(AudioMode.MIX)
    inputs = [command[index + 1] for index, token in enumerate(command)
              if token == "-i"]
    assert inputs == ["a.ts", "b.ts", "a.ts", "music.wav"]
    assert "[0:v]" in graph and "[1:v]" in graph and "[2:v]" in graph
    assert "[3:a:0]" in graph
    assert "[1:a:0]atrim=start_sample" not in graph
    assert command.count("-filter_complex") == 1


@pytest.mark.parametrize("mode", list(AudioMode))
def test_joined_audio_modes_have_explicit_finished_time_graphs(mode):
    command, graph = _joined_audio_case(mode)
    if mode is AudioMode.NO_SOUND:
        assert "-an" in command
        assert "[planned_audio]" not in graph and "[ja]" not in graph
    elif mode is AudioMode.ORIGINAL:
        assert "[ja]" in graph and "[planned_audio]" not in graph
        assert "anullsrc" in graph
    elif mode is AudioMode.REPLACE:
        assert "[planned_audio]" in graph and "[ja]" not in graph
        assert "anullsrc" not in graph
    else:
        assert "[ja]" in graph and "[planned_audio]" in graph
        assert "[music][dvr]amix=" in graph
        assert "anullsrc" in graph

    if mode in (AudioMode.ORIGINAL, AudioMode.MIX):
        for samples in (144_000, 96_000):
            assert f"atrim=end_sample={samples}" in graph


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
    ("edit", ".mov"), ("master", ".mp4"), ("social", ".mp4"), ("upload", ".mp4"), ("remux", ".mp4"),
])
def test_containers_match_the_codec(preset, extension):
    assert output_path(Path("/out"), "x", preset, False).suffix == extension


# The pieces above are each covered on their own. What nothing covered until
# now is the two of them composed — select_stem feeding output_path — which is
# the whole of what a user sees and precisely what a naming template replaces.
# Pinned before that refactor rather than after it, so "byte for byte" is a
# claim something can fail.

def test_the_names_several_ranges_of_one_clip_actually_export_to():
    """Every part a template has to reproduce, in one filename each: the clip
    stem, the 1-based position, the typed range name, the preset suffix, the
    container, and a flight date that is added exactly once."""
    from dataclasses import replace

    from flightdvr.format import select_stem
    from flightdvr.media import Select

    clip = boxpro_clip(path=Path("hdz_048.ts"))
    ranges = [Select(12.0, 48.0, "Launch"), Select(96.0, 141.0, "Tree dive!"),
              Select(150.0, 190.0, "")]
    pieces = [replace(clip, selects=[one], current=0) for one in ranges]

    names = [
        output_path(Path("/out"), select_stem(piece, index, len(pieces)),
                    "upload", subfolders=False,
                    flight_date=date(2026, 7, 4)).name
        for index, piece in enumerate(pieces)
    ]

    assert names == [
        "2026-07-04_hdz_048_1_Launch_upload.mp4",
        # "!" survives: safe_name strips what Windows refuses, and "!" is
        # legal. Predicting this wrong is exactly why it is pinned.
        "2026-07-04_hdz_048_2_Tree-dive!_upload.mp4",
        "2026-07-04_hdz_048_3_upload.mp4",
    ]


def test_one_range_exports_to_the_name_it_always_did():
    """The compatibility that matters most: a clip trimmed the way every
    version before ranges trimmed it must not gain a position number."""
    from dataclasses import replace

    from flightdvr.format import select_stem
    from flightdvr.media import Select

    clip = boxpro_clip(path=Path("hdz_048.ts"))
    piece = replace(clip, selects=[Select(12.0, 48.0, "Launch")], current=0)

    name = output_path(Path("/out"), select_stem(piece, 0, 1), "upload",
                       subfolders=False, flight_date=None).name
    assert name == "hdz_048_upload.mp4"



# -- stage A: configured audio on Master, Edit, Upload and Vertical -------------

# Captured from `build_commands` at the unchanged base 27ee42c for Master's
# ordinary configured audio, before stage A moved that code into a shared
# helper. Literals, not recomputed: moving the code must not change a byte.
MASTER_ORDINARY_AUDIO_AT_BASE = {'mix': [['ffmpeg',
          '-hide_banner',
          '-nostdin',
          '-y',
          '-fflags',
          '+genpts',
          '-analyzeduration',
          '100M',
          '-probesize',
          '100M',
          '-ss',
          '10.000',
          '-copyts',
          '-start_at_zero',
          '-i',
          'hdz_022.ts',
          '-i',
          'music.wav',
          '-ss',
          '12.000',
          '-t',
          '6.000',
          '-vf',
          'scale=in_range=full:out_range=limited,format=yuv420p',
          '-c:v',
          'libx264',
          '-preset',
          'slow',
          '-crf',
          '18',
          '-profile:v',
          'high',
          '-fps_mode',
          'cfr',
          '-r',
          '60',
          '-filter_complex',
          '[1:a:0]atrim=start_sample=176400:end_sample=441000,asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=end_sample=288000,aloop=loop=-1:size=288000:start=0,atrim=end_sample=288000,afade=t=in:ss=0:ns=48000:curve=tri,afade=t=out:ss=192000:ns=96000:curve=tri,volume=0.75,asetpts=PTS+576000/48000/TB[music];[0:a:0]aresample=48000:async=1:first_pts=0,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=start_sample=576000:end_sample=864000,asetpts=PTS-STARTPTS,apad=whole_len=288000,atrim=end_sample=288000,volume=0.25,asetpts=PTS+576000/48000/TB[dvr];[music][dvr]amix=inputs=2:duration=longest:normalize=0,atrim=end_sample=288000[planned_audio]',
          '-map',
          '0:v:0',
          '-map',
          '[planned_audio]',
          '-c:a',
          'aac',
          '-b:a',
          '192k',
          '-ac',
          '2',
          '-movflags',
          '+faststart',
          'out.mp4']],
 'no_sound': [['ffmpeg',
               '-hide_banner',
               '-nostdin',
               '-y',
               '-fflags',
               '+genpts',
               '-analyzeduration',
               '100M',
               '-probesize',
               '100M',
               '-ss',
               '10.000',
               '-copyts',
               '-start_at_zero',
               '-i',
               'hdz_022.ts',
               '-ss',
               '12.000',
               '-t',
               '6.000',
               '-vf',
               'scale=in_range=full:out_range=limited,format=yuv420p',
               '-c:v',
               'libx264',
               '-preset',
               'slow',
               '-crf',
               '18',
               '-profile:v',
               'high',
               '-fps_mode',
               'cfr',
               '-r',
               '60',
               '-an',
               '-movflags',
               '+faststart',
               'out.mp4']],
 'original': [['ffmpeg',
               '-hide_banner',
               '-nostdin',
               '-y',
               '-fflags',
               '+genpts',
               '-analyzeduration',
               '100M',
               '-probesize',
               '100M',
               '-ss',
               '10.000',
               '-copyts',
               '-start_at_zero',
               '-i',
               'hdz_022.ts',
               '-ss',
               '12.000',
               '-t',
               '6.000',
               '-vf',
               'scale=in_range=full:out_range=limited,format=yuv420p',
               '-c:v',
               'libx264',
               '-preset',
               'slow',
               '-crf',
               '18',
               '-profile:v',
               'high',
               '-fps_mode',
               'cfr',
               '-r',
               '60',
               '-c:a',
               'aac',
               '-b:a',
               '192k',
               '-ac',
               '2',
               '-af',
               'aresample=48000:async=1:first_pts=0,apad=whole_len=864000,atrim=end_sample=864000',
               '-movflags',
               '+faststart',
               'out.mp4']],
 'replace': [['ffmpeg',
              '-hide_banner',
              '-nostdin',
              '-y',
              '-fflags',
              '+genpts',
              '-analyzeduration',
              '100M',
              '-probesize',
              '100M',
              '-ss',
              '10.000',
              '-copyts',
              '-start_at_zero',
              '-i',
              'hdz_022.ts',
              '-i',
              'music.wav',
              '-ss',
              '12.000',
              '-t',
              '6.000',
              '-vf',
              'scale=in_range=full:out_range=limited,format=yuv420p',
              '-c:v',
              'libx264',
              '-preset',
              'slow',
              '-crf',
              '18',
              '-profile:v',
              'high',
              '-fps_mode',
              'cfr',
              '-r',
              '60',
              '-filter_complex',
              '[1:a:0]atrim=start_sample=176400:end_sample=441000,asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=end_sample=288000,aloop=loop=-1:size=288000:start=0,atrim=end_sample=288000,afade=t=in:ss=0:ns=48000:curve=tri,afade=t=out:ss=192000:ns=96000:curve=tri,volume=0.75,asetpts=PTS+576000/48000/TB[music];[music]anull[planned_audio]',
              '-map',
              '0:v:0',
              '-map',
              '[planned_audio]',
              '-c:a',
              'aac',
              '-b:a',
              '192k',
              '-ac',
              '2',
              '-movflags',
              '+faststart',
              'out.mp4']]}


def _ordinary_audio_case(mode: AudioMode, preset: str = "master",
                         settings: ExportSettings | None = None,
                         source_has_audio: bool = True):
    clip = boxpro_clip(duration=20.0)
    if not source_has_audio:
        clip.audio_codec = ""
    clip.trim_in, clip.trim_out = 12.0, 18.0
    if mode in (AudioMode.REPLACE, AudioMode.MIX):
        asset = AudioAsset(Path("music.wav"), "a" * 64, 0, 44_100, 2,
                           20 * 44_100)
        choice = MusicChoice(asset=asset, mode=mode,
                             passage=SampleSpan(4 * 44_100, 10 * 44_100,
                                                44_100),
                             music_level=Fraction(3, 4),
                             dvr_level=Fraction(1, 4))
    else:
        choice = MusicChoice(mode=mode)
    plan = resolve_audio_plan(choice, 6 * OUTPUT_RATE,
                              source_has_audio=source_has_audio,
                              preset_key=preset)
    return build_commands(TOOLS, clip, preset, settings or ExportSettings(),
                          Path("out.mp4" if preset != "edit" else "out.mov"),
                          Path("work"), audio_plan=plan)


@pytest.mark.parametrize("mode", list(AudioMode))
def test_master_ordinary_audio_commands_are_unchanged_from_the_base(
        mode, monkeypatch):
    # The literals were captured with an ffmpeg that has -fps_mode; an older
    # one (4.4) spells the same option -vsync. Pin the spelling, not the
    # machine's ffmpeg, so the comparison is about this change alone.
    import flightdvr.media as media
    monkeypatch.setattr(media, "_fps_mode_supported", lambda _ffmpeg: True)
    assert _ordinary_audio_case(mode) == MASTER_ORDINARY_AUDIO_AT_BASE[mode.value]


def _without_audio(command: list[str]) -> list[str]:
    """The command with every audio decision removed, to compare picture."""
    out, skip = [], 0
    audio_flags = {"-c:a", "-b:a", "-ac", "-af", "-filter_complex"}
    for index, token in enumerate(command):
        if skip:
            skip -= 1
            continue
        if token in audio_flags:
            skip = 1
            continue
        if token == "-map":
            skip = 1
            continue
        if token in ("-an",):
            continue
        if token == "-i" and command[index + 1] == "music.wav":
            skip = 1
            continue
        out.append(token)
    return out


@pytest.mark.parametrize("preset,codec", [
    ("edit", ["-c:a", "pcm_s16le", "-ac", "2"]),
    ("upload", ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]),
    ("vertical", ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]),
])
@pytest.mark.parametrize("mode", list(AudioMode))
def test_stage_a_presets_carry_configured_audio_and_keep_their_picture(
        preset, codec, mode):
    command = _ordinary_audio_case(mode, preset)[0]
    legacy = build_commands(
        TOOLS, _legacy_clip(), preset, ExportSettings(),
        Path("out.mp4" if preset != "edit" else "out.mov"), Path("work"))[0]
    # The picture arguments are the preset's own, exactly as before.
    assert _without_audio(command) == _without_audio(legacy)
    inputs = [command[i + 1] for i, token in enumerate(command) if token == "-i"]
    if mode in (AudioMode.REPLACE, AudioMode.MIX):
        # Music is input 1, before the source's output-side seek and length.
        assert inputs == ["hdz_022.ts", "music.wav"]
        assert command.index("music.wav") < command.index("-t")
        graph = command[command.index("-filter_complex") + 1]
        assert graph.startswith("[1:a:0]atrim=start_sample=176400:end_sample=441000")
        # 12 s of source seek compensated on the music's timestamps.
        assert "asetpts=PTS+576000/48000/TB" in graph
        assert command[command.index("-map"):command.index("-map") + 4] == [
            "-map", "0:v:0", "-map", "[planned_audio]"]
        at = command.index("-c:a")
        assert command[at:at + len(codec)] == codec
    elif mode is AudioMode.NO_SOUND:
        assert inputs == ["hdz_022.ts"] and "-an" in command
        assert "-c:a" not in command
    else:
        assert inputs == ["hdz_022.ts"]
        at = command.index("-c:a")
        assert command[at:at + len(codec)] == codec
        assert command[at + len(codec)] == "-af"
        assert "apad=whole_len=864000,atrim=end_sample=864000" in command[
            at + len(codec) + 1]
    if preset == "edit":
        assert "aac" not in command
        assert command[-1] == "out.mov"


def _legacy_clip():
    clip = boxpro_clip(duration=20.0)
    clip.trim_in, clip.trim_out = 12.0, 18.0
    return clip


def test_vertical_music_keeps_the_source_crop_on_the_picture():
    command = _ordinary_audio_case(AudioMode.REPLACE, "vertical")[0]
    picture = command[command.index("-vf") + 1]
    crop = vertical_crop(_legacy_clip(), 50)
    assert picture.startswith(
        f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y},"
        f"scale={crop.output_width}:{crop.output_height}:flags=lanczos")


def test_upload_music_keeps_its_enlargement():
    command = _ordinary_audio_case(
        AudioMode.MIX, "upload", ExportSettings(upload_height=1080))[0]
    assert "scale=-2:1080:flags=lanczos" in command[command.index("-vf") + 1]


@pytest.mark.parametrize("preset", ["edit", "upload", "vertical"])
def test_joined_stage_a_presets_put_music_after_every_source(preset):
    first = boxpro_clip(path=Path("a.ts"), duration=3.0)
    middle = boxpro_clip(path=Path("b.ts"), duration=2.0, audio_codec="")
    third = boxpro_clip(path=Path("a.ts"), duration=3.0)
    spans = (SampleSpan(0, 144_000, OUTPUT_RATE),
             SampleSpan(144_000, 240_000, OUTPUT_RATE),
             SampleSpan(240_000, 384_000, OUTPUT_RATE))
    sequence = SimpleNamespace(
        occurrences=tuple(SimpleNamespace(sample_span=span) for span in spans),
        total_samples=384_000)
    asset = AudioAsset(Path("music.wav"), "a" * 64, 0, 44_100, 2, 8 * 44_100)
    plan = resolve_audio_plan(
        MusicChoice(asset=asset, passage=SampleSpan(44_100, 3 * 44_100, 44_100),
                    mode=AudioMode.MIX),
        384_000, source_has_audio=True, preset_key=preset, joined=True)
    command = build_commands(
        TOOLS, first, preset, ExportSettings(), Path("out"), Path("work"),
        clips=[first, middle, third], audio_plan=plan, sequence=sequence)[0]
    inputs = [command[i + 1] for i, token in enumerate(command) if token == "-i"]
    assert inputs == ["a.ts", "b.ts", "a.ts", "music.wav"]
    graph = command[command.index("-filter_complex") + 1]
    assert "[3:a:0]" in graph and "[1:a:0]atrim" not in graph
    at = command.index("-c:a")
    assert command[at + 1] == ("pcm_s16le" if preset == "edit" else "aac")
