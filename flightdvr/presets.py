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

"""Export presets and the ffmpeg command lines behind them.

Colour handling
---------------
HDZero DVR files decode as `yuvj420p` with `color_range=pc`: the luma really
does span 0-255, which was confirmed by measuring the source. They are also
tagged `bt470bg` primaries and `smpte170m` transfer, which is almost certainly
a firmware default rather than a genuine colorimetry claim.

Four candidate chains were measured against the source by rendering both to
RGB and comparing (see tools/compare_colour.py, which reproduces this):

    chain                                   PSNR      max delta
    passthrough (no colour handling)        99.00 dB      0
    range only, original matrix tags kept   50.08 dB      3
    matrix retag to bt709                   38.45 dB     75
    full bt709 conversion                   34.26 dB    108

So `LEVELS` is the default: it fixes the one defect that is provably real
(full-range data being clipped by anything that assumes limited range) and
changes nothing else. Rewriting the colour tags measurably shifts the picture,
because the conversion is only as trustworthy as the tags it reads, and these
tags are not trustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .media import ClipInfo, Tools, frame_rate_mode

# --- colour modes ------------------------------------------------------------

LEVELS = "levels"
PASSTHROUGH = "passthrough"
REC709 = "rec709"

COLOUR_MODES = [
    (
        LEVELS,
        "Fix levels (recommended)",
        "Converts the full-range 0-255 recording to standard 16-235 video and "
        "leaves the colour matrix untouched. Measured as visually identical to "
        "the source. Stops editors and players crushing the blacks.",
    ),
    (
        PASSTHROUGH,
        "Leave colour alone",
        "Hands the pixels over exactly as recorded, full range and all. Matches "
        "what a plain ffmpeg convert does. Use if you already have a grade built "
        "around the untouched footage.",
    ),
    (
        REC709,
        "Convert to Rec.709",
        "Takes the file's bt470bg/smpte170m tags at face value and converts to "
        "true Rec.709. Standards-clean tags, but it measurably shifts the colour "
        "because those tags look like a firmware default. A/B it before trusting it.",
    ),
]


def colour_filters(mode: str, clip: ClipInfo, pix_fmt: str) -> list[str]:
    """Video filters that implement a colour mode, ending in `pix_fmt`."""
    if mode == REC709:
        return [
            "zscale=rangein=full:range=limited"
            ":matrixin=470bg:matrix=709"
            ":primariesin=bt470bg:primaries=bt709"
            ":transferin=smpte170m:transfer=bt709"
            ":dither=error_diffusion",
            f"format={pix_fmt}",
        ]
    if mode == LEVELS and clip.is_full_range:
        return ["scale=in_range=full:out_range=limited", f"format={pix_fmt}"]
    # Passthrough, or a clip that was not full range to begin with.
    return [f"format={pix_fmt}"]


# --- settings ----------------------------------------------------------------

EDIT_CODECS = {
    # key: (label, ffmpeg args, pixel format, Mbps at the reference pixel rate)
    "dnxhr_sq": ("DNxHR SQ  (recommended)", ["-c:v", "dnxhd", "-profile:v", "dnxhr_sq"], "yuv422p", 130),
    "dnxhr_hq": ("DNxHR HQ  (larger)", ["-c:v", "dnxhd", "-profile:v", "dnxhr_hq"], "yuv422p", 195),
    "dnxhr_lb": ("DNxHR LB  (smaller)", ["-c:v", "dnxhd", "-profile:v", "dnxhr_lb"], "yuv422p", 43),
    "prores_422": ("ProRes 422  (for Mac editors)", ["-c:v", "prores_ks", "-profile:v", "2"], "yuv422p10le", 140),
    "prores_lt": ("ProRes 422 LT  (smaller)", ["-c:v", "prores_ks", "-profile:v", "1"], "yuv422p10le", 100),
}

# All the bitrate figures above are quoted for 720p60, which is what the Box Pro
# records. Other HDZero goggles record 720p90 and 1080p30, and the Goggle 2 goes
# to 1080p, so every estimate is scaled by how many pixels per second the clip
# actually carries rather than assuming the Box Pro's numbers.
REFERENCE_PIXEL_RATE = 1280 * 720 * 60

# Bitrates the H.264 lanes land on at the reference pixel rate, measured on real
# footage: CRF 18 gives about 26 Mbit/s, and social at CRF 23 about 8.
MASTER_REFERENCE_MBPS = 26.0
SOCIAL_REFERENCE_MBPS = 8.0


def pixel_rate(clip: ClipInfo, height: int = 0, fps: float = 0.0) -> float:
    """Pixels per second, optionally for a resized or retimed output."""
    source_height = clip.height or 720
    source_width = clip.width or 1280
    out_height = height if (height and height < source_height) else source_height
    # Width follows the source aspect ratio, as the scale filter keeps it.
    out_width = source_width * (out_height / source_height)
    out_fps = fps if fps else (clip.fps or 60.0)
    return max(1.0, out_width * out_height * out_fps)


def edit_bitrate_mbps(codec_key: str, clip: ClipInfo) -> float:
    """Mezzanine bitrate for this clip's real resolution and frame rate."""
    base = EDIT_CODECS[codec_key][3]
    return base * pixel_rate(clip) / REFERENCE_PIXEL_RATE

SPEEDS = ["veryfast", "faster", "fast", "medium", "slow", "slower"]

# Named quality levels, so nobody has to know what a CRF number means.
# CRF is x264's constant-rate-factor: lower keeps more detail and produces a
# bigger file, and each 6 points roughly halves or doubles the size.
QUALITY_LEVELS = [
    (14, "Archive", "Keeps everything, including detail you cannot see. "
                    "Bigger than the original recording."),
    (18, "High (recommended)", "Indistinguishable from the original in normal "
                              "viewing. This is the one to hand to an editor."),
    (21, "Good", "Very close to the original. Roughly half the size of High."),
    (24, "Compact", "Slight softening in fast, busy footage. Useful when you "
                    "need to move a lot of flights at once."),
]

SOCIAL_QUALITY_LEVELS = [
    (20, "Sharper", "Larger file, holds up better on a big screen."),
    (23, "Balanced (recommended)", "The usual choice for messaging apps."),
    (26, "Smaller", "Noticeably softer in fast footage, but easy to send."),
]


@dataclass
class ExportSettings:
    colour: str = LEVELS

    edit_codec: str = "dnxhr_sq"

    master_crf: int = 18
    master_speed: str = "slow"

    social_mode: str = "size"          # "size" targets a file size, "quality" uses CRF
    social_size_mb: int = 45
    social_crf: int = 23
    social_height: int = 0             # 0 keeps the source height, lower downscales
    social_fps: int = 0                # 0 keeps the source frame rate

    use_gpu: bool = False              # hardware encode for the H.264 lanes
    hw_encoder: str = ""               # whichever one this machine really has
    keep_audio: bool = True

    @property
    def hardware(self) -> str:
        """The hardware encoder to use, or "" to stay on the CPU."""
        return self.hw_encoder if (self.use_gpu and self.hw_encoder) else ""


def hardware_video_args(encoder: str, quality: int, speed: str = "quality") -> list[str]:
    """Encoder settings for a hardware H.264 encoder.

    Each vendor spells constant-quality differently, so the CRF-like number is
    translated per encoder rather than assumed to be portable.
    """
    q = max(0, min(51, quality))
    if encoder.endswith("_nvenc"):
        return ["-c:v", encoder, "-preset", "p5", "-rc", "constqp",
                "-qp", str(q), "-profile:v", "high"]
    if encoder.endswith("_qsv"):
        return ["-c:v", encoder, "-global_quality", str(q), "-preset", "slow",
                "-profile:v", "high"]
    if encoder.endswith("_amf"):
        return ["-c:v", encoder, "-quality", speed, "-rc", "cqp",
                "-qp_i", str(q), "-qp_p", str(min(51, q + 2)),
                "-qp_b", str(min(51, q + 4))]
    if encoder.endswith("_videotoolbox"):
        return ["-c:v", encoder, "-q:v", str(max(1, min(100, 100 - q * 2)))]
    return ["-c:v", encoder]


def hardware_bitrate_args(encoder: str, kbps: int) -> list[str]:
    """Encoder settings for targeting a bitrate on hardware."""
    peak = f"{int(kbps * 1.5)}k"
    if encoder.endswith("_nvenc"):
        return ["-c:v", encoder, "-preset", "p5", "-rc", "vbr",
                "-b:v", f"{kbps}k", "-maxrate", peak]
    if encoder.endswith("_qsv"):
        return ["-c:v", encoder, "-b:v", f"{kbps}k", "-maxrate", peak]
    if encoder.endswith("_amf"):
        return ["-c:v", encoder, "-quality", "balanced", "-rc", "vbr_peak",
                "-b:v", f"{kbps}k", "-maxrate", peak]
    return ["-c:v", encoder, "-b:v", f"{kbps}k", "-maxrate", peak]


@dataclass
class Preset:
    key: str
    label: str
    blurb: str
    suffix: str
    extension: str
    two_pass: bool = False


PRESETS: dict[str, Preset] = {
    "edit": Preset(
        "edit", "Edit",
        "Mezzanine for DaVinci or to hand to an editor. Scrubs instantly on a "
        "timeline. Large files.",
        "_edit", ".mov",
    ),
    "master": Preset(
        "master", "Master",
        "High-bitrate MP4. Visually transparent and plays anywhere, small enough "
        "to send. Good for archiving and for sharing with editors online.",
        "_master", ".mp4",
    ),
    "social": Preset(
        "social", "Social",
        "Compact MP4 for WhatsApp, Instagram or Discord. Can hit an exact file "
        "size using a two-pass encode.",
        "_social", ".mp4", two_pass=True,
    ),
    "remux": Preset(
        "remux", "Remux",
        "Rewraps .ts into .mp4 with no re-encoding at all. Instant and lossless, "
        "but keeps HEVC, which the free DaVinci Resolve cannot read.",
        "", ".mp4",
    ),
}

PRESET_ORDER = ["edit", "master", "social", "remux"]


# --- command construction ----------------------------------------------------

# How far before the in point to start decoding. A seek into an MPEG-TS lands
# on an estimated byte offset rather than a keyframe, so decoding from there
# produces frames with no reference picture: the app used to emit up to a full
# GOP of corrupt video at the start of every trim, silently, with the correct
# frame count and no ffmpeg error.
#
# Measured on real Box Pro footage, which writes a keyframe every 1.000s at
# 60fps. A trim at 10.500s produced 30 frames at 12 dB PSNR against an accurate
# reference — exactly the distance to the next keyframe. Decoding from 2s
# earlier and discarding the lead-in is bit-identical to an accurate seek, and
# still 2.2x faster than one. Two seconds gives double the margin a Box Pro
# needs, at a cost of decoding two seconds of video per export.
SEEK_LEAD_IN = 2.0


def _input_args(
    sources: list[Path], concat_file: Path | None, clip: ClipInfo | None = None
) -> list[str]:
    """Input side, hardened for the loose timestamps DVR transport streams have.

    An in point is split across two seeks: a fast one before the input that
    lands somewhere before the target, and an accurate one after it that trims
    the lead-in away. See SEEK_LEAD_IN for why the second seek is not optional.

    A joined export carries its trims in the concat list instead, one per file,
    and has the mid-GOP problem this avoids.
    """
    args = ["-fflags", "+genpts", "-analyzeduration", "100M", "-probesize", "100M"]

    lead_in = 0.0
    if concat_file is None and clip is not None and clip.trim_in > 0.01:
        # Never seek past the start of the file.
        lead_in = min(clip.trim_in, SEEK_LEAD_IN)
        start = clip.trim_in - lead_in
        if start > 0.01:
            args += ["-ss", f"{start:.3f}"]

    if concat_file is not None:
        args += ["-f", "concat", "-safe", "0", "-i", str(concat_file)]
    else:
        args += ["-i", str(sources[0])]

    # Output-side seek: discards the lead-in after it has been decoded, which is
    # what makes the first output frame correct.
    if lead_in > 0.01:
        args += ["-ss", f"{lead_in:.3f}"]
    if concat_file is None and clip is not None and clip.is_trimmed:
        args += ["-t", f"{clip.trimmed_duration:.3f}"]
    return args


def _audio_args(settings: ExportSettings, clip: ClipInfo, bitrate: str, pcm: bool = False) -> list[str]:
    if not settings.keep_audio or not clip.has_audio:
        return ["-an"]
    if pcm:
        codec = ["-c:a", "pcm_s16le"]
    else:
        codec = ["-c:a", "aac", "-b:a", bitrate]
    # DVR audio starts slightly after video and can drift; resample to hold sync.
    return codec + ["-ac", "2", "-af", "aresample=async=1:first_pts=0"]


def _fps_args(tools: Tools, clip: ClipInfo, override: int = 0) -> list[str]:
    """Force constant frame rate. NLEs handle variable frame rate badly.

    The option name is asked for rather than assumed: -fps_mode replaced
    -vsync in ffmpeg 5.1, and Ubuntu 22.04 still ships 4.4.
    """
    mode = frame_rate_mode(tools, "cfr")
    fps = override or clip.fps
    # Never ask for more frames than the source has; that only duplicates them.
    if override and clip.fps and override > clip.fps:
        fps = clip.fps
    if fps <= 0:
        return mode
    return mode + ["-r", f"{fps:g}"]


def _scale_filter(clip: ClipInfo, target_height: int) -> list[str]:
    if not target_height or not clip.height or target_height >= clip.height:
        return []
    # -2 keeps the width even, which H.264 requires.
    return [f"scale=-2:{target_height}:flags=lanczos"]


def target_video_bitrate(clip: ClipInfo, size_mb: int, audio_kbps: int,
                         runtime: float = 0.0) -> int:
    """Video bitrate in kbit/s that lands a clip on `size_mb`.

    `runtime` overrides the clip's own length, which is what a joined export
    needs: the file being produced is as long as all its clips together, and
    sizing it from the first one alone overshot the target by roughly the
    number of clips joined.
    """
    runtime = runtime or clip.trimmed_duration or clip.duration
    if runtime <= 0:
        return 2500
    total_kbits = (size_mb * 1024 * 1024 * 8) / 1000.0
    total_kbps = total_kbits / runtime
    # Leave 3% for container overhead so we land under the limit, not over it.
    video = int(total_kbps * 0.97) - audio_kbps
    return max(300, video)


def build_commands(
    tools: Tools,
    clip: ClipInfo,
    preset_key: str,
    settings: ExportSettings,
    out_path: Path,
    work_dir: Path,
    sources: list[Path] | None = None,
    concat_file: Path | None = None,
    total_duration: float = 0.0,
) -> list[list[str]]:
    """Full ffmpeg command list. Two entries when a two-pass encode is needed.

    `clip` describes the first source and supplies the stream properties.
    `total_duration` is how long the finished file will be, which differs from
    that clip's length whenever several are joined; leave it at zero for a
    single clip.
    """
    sources = sources or [clip.path]
    ff = str(tools.ffmpeg)
    head = [ff, "-hide_banner", "-nostdin", "-y"] + _input_args(sources, concat_file, clip)
    preset = PRESETS[preset_key]

    if preset_key == "remux":
        return [head + ["-c", "copy", "-movflags", "+faststart", str(out_path)]]

    if preset_key == "edit":
        label, codec_args, pix_fmt, _ = EDIT_CODECS[settings.edit_codec]
        vf = colour_filters(settings.colour, clip, pix_fmt)
        return [
            head
            + ["-vf", ",".join(vf)]
            + codec_args
            + _fps_args(tools, clip)
            + _audio_args(settings, clip, "192k", pcm=True)
            + [str(out_path)]
        ]

    if preset_key == "master":
        vf = colour_filters(settings.colour, clip, "yuv420p")
        if settings.hardware:
            video = hardware_video_args(settings.hardware, settings.master_crf)
        else:
            video = [
                "-c:v", "libx264", "-preset", settings.master_speed,
                "-crf", str(settings.master_crf), "-profile:v", "high",
            ]
        return [
            head
            + ["-vf", ",".join(vf)]
            + video
            + _fps_args(tools, clip)
            + _audio_args(settings, clip, "192k")
            + ["-movflags", "+faststart", str(out_path)]
        ]

    # social
    audio_kbps = 128 if (settings.keep_audio and clip.has_audio) else 0
    vf = _scale_filter(clip, settings.social_height) + colour_filters(
        settings.colour, clip, "yuv420p"
    )
    fps_args = _fps_args(tools, clip, settings.social_fps)
    audio_args = _audio_args(settings, clip, "128k")

    if settings.social_mode == "quality":
        if settings.hardware:
            video = hardware_video_args(settings.hardware, settings.social_crf, "balanced")
        else:
            video = ["-c:v", "libx264", "-preset", "medium",
                     "-crf", str(settings.social_crf), "-profile:v", "high"]
        return [
            head + ["-vf", ",".join(vf)] + video + fps_args + audio_args
            + ["-movflags", "+faststart", str(out_path)]
        ]

    # Size-targeted. Two passes on CPU gets far closer to the target than one.
    kbps = target_video_bitrate(clip, settings.social_size_mb, audio_kbps,
                                runtime=total_duration)
    if settings.hardware:
        # Hardware encoders have no two-pass mode worth using, so this is a
        # single bitrate-targeted pass and lands less precisely on the number.
        video = hardware_bitrate_args(settings.hardware, kbps)
        return [
            head + ["-vf", ",".join(vf)] + video + fps_args + audio_args
            + ["-movflags", "+faststart", str(out_path)]
        ]

    log_prefix = work_dir / f"pass_{out_path.stem}"
    common = ["-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k",
              "-profile:v", "high", "-passlogfile", str(log_prefix)]
    # Both passes must present ffmpeg with the same streams. Dropping audio from
    # pass 1 with -an, which is the usual advice, shifts the video framing by a
    # frame here, and x264 then refuses the mismatched stats file.
    pass1 = (
        head + ["-vf", ",".join(vf)] + common
        + ["-pass", "1"] + fps_args + audio_args + ["-f", "null", "-"]
    )
    pass2 = (
        head + ["-vf", ",".join(vf)] + common
        + ["-pass", "2"] + fps_args + audio_args
        + ["-movflags", "+faststart", str(out_path)]
    )
    return [pass1, pass2]


def estimate_output_size(clip: ClipInfo, preset_key: str, settings: ExportSettings) -> int:
    """Rough output size in bytes, for showing before anyone commits to a queue.

    Everything scales with the clip's real pixel rate, so a 1080p recording from
    a Goggle 2 is not estimated using the Box Pro's 720p60 numbers.
    """
    runtime = clip.trimmed_duration or clip.duration
    if runtime <= 0:
        return 0
    if preset_key == "remux":
        share = runtime / clip.duration if clip.duration else 1.0
        return int(clip.size * share)
    if preset_key == "edit":
        mbps = edit_bitrate_mbps(settings.edit_codec, clip)
        return int(mbps * 1_000_000 / 8 * runtime)

    scale = pixel_rate(clip) / REFERENCE_PIXEL_RATE
    if preset_key == "master":
        # Each 6 points of CRF roughly halves or doubles the size.
        mbps = MASTER_REFERENCE_MBPS * scale * (2 ** ((18 - settings.master_crf) / 6.0))
        return int(mbps * 1_000_000 / 8 * runtime)

    if settings.social_mode == "size":
        return settings.social_size_mb * 1024 * 1024

    out_scale = pixel_rate(clip, settings.social_height, settings.social_fps)
    mbps = (SOCIAL_REFERENCE_MBPS * (out_scale / REFERENCE_PIXEL_RATE)
            * (2 ** ((23 - settings.social_crf) / 6.0)))
    return int(mbps * 1_000_000 / 8 * runtime)


def output_path(
    out_dir: Path,
    clip_stem: str,
    preset_key: str,
    subfolders: bool,
    flight_date=None,
) -> Path:
    """Where an export lands.

    `flight_date` prefixes the filename, because the date on the file itself is
    whatever the goggles' unbacked clock happened to say.
    """
    preset = PRESETS[preset_key]
    folder = out_dir / preset.label if subfolders else out_dir
    stem = clip_stem
    if flight_date is not None:
        stamp = flight_date.strftime("%Y-%m-%d")
        if not stem.startswith(stamp):
            stem = f"{stamp}_{stem}"
    return folder / f"{stem}{preset.suffix}{preset.extension}"
