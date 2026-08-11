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


def pixel_rate(clip: ClipInfo, height: int = 0, fps: float = 0.0,
               allow_upscale: bool = False) -> float:
    """Pixels per second, optionally for a resized or retimed output.

    `allow_upscale` exists for the Upload preset. Without it this clamps to the
    source height, which would have quietly under-predicted every upscaled
    export — the estimate is the number people plan around, so a gate here is
    as damaging as one in the command itself, and much harder to notice.
    """
    source_height = clip.height or 720
    source_width = clip.width or 1280
    if height and (allow_upscale or height < source_height):
        out_height = height
    else:
        out_height = source_height
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

    # Upload is the one preset allowed to make the picture bigger, because the
    # point of it is the resolution tier rather than the pixels. Quality-based
    # only: a size target and an upscale pull against each other, and asking
    # for both produces a worse file than asking for neither.
    upload_height: int = 1080
    upload_crf: int = 20
    upload_speed: str = "slow"

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
        "_social", ".mp4",
    ),
    "upload": Preset(
        "upload", "Upload",
        "For YouTube, Instagram or Reddit, which re-encode whatever you send "
        "them and hand out bitrate by resolution. Uploading at 1080p wins a "
        "bigger allowance than 720p does, so the result survives their encode "
        "better — it does not add detail that was never recorded.",
        "_upload", ".mp4",
    ),
    "remux": Preset(
        "remux", "Remux",
        "Rewraps .ts into .mp4 with no re-encoding at all. Instant and lossless, "
        "but keeps HEVC, which the free DaVinci Resolve cannot read.",
        "", ".mp4",
    ),
    "slowmo": Preset(
        "slowmo", "Slow motion",
        "Half speed from the frames already recorded, so nothing is invented: "
        "every frame you shot is shown for twice as long. A 60 fps recording "
        "becomes 30 fps and runs twice as long.",
        "_slow", ".mp4",
    ),
}

# Deliberately not in PRESET_ORDER yet, which is what the export panel builds
# its buttons from: the command exists and is tested, the control and the
# estimate are not built. A preset that can be picked before its estimate is
# right would report the source runtime for a file twice that long.
PRESET_ORDER = ["edit", "master", "social", "upload", "remux"]


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

    seeking = concat_file is None and clip is not None and clip.trim_in > 0.01
    if seeking:
        # Never seek past the start of the file.
        start = max(0.0, clip.trim_in - SEEK_LEAD_IN)
        if start > 0.01:
            args += ["-ss", f"{start:.3f}"]
        # Without these, whether the timeline gets rebased by the first seek
        # depends on the file and on whether audio is being written, so the
        # second seek sometimes counts from the position asked for and
        # sometimes from the keyframe the first one landed on. Measured on a
        # file whose audio starts 23 ms before its video: with the sound turned
        # off, a trim asked to begin at 2.5 s produced a clean, correctly
        # lengthed export beginning at 2.0 s. HDZero recordings start at zero
        # and so never showed it.
        args += ["-copyts", "-start_at_zero"]

    if concat_file is not None:
        args += ["-f", "concat", "-safe", "0", "-i", str(concat_file)]
    else:
        args += ["-i", str(sources[0])]

    # Output-side seek: discards the lead-in after it has been decoded, which is
    # what makes the first output frame correct. Measured from the start of the
    # file, which is what -start_at_zero above guarantees.
    if seeking:
        args += ["-ss", f"{clip.trim_in:.3f}"]
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


# How much slower Slow motion runs. One value, named, because the output rate,
# the runtime, the estimate and every test have to derive from the same number
# rather than each carrying its own 2.
SLOW_FACTOR = 2


def slow_output_rate(clips: list[ClipInfo]) -> float:
    """The frame rate a slow export writes: what was recorded, halved.

    Taken from the source rather than from a list of rates the interface
    offers, because the promise is about the frames in this recording. 60 gives
    30 and 90 gives 45 — and 45 is not one of the rates the Social frame-rate
    box offers, so rounding a slow export onto that list would drop or repeat
    frames to reach a number nobody asked for.

    A join is already brought to one rate by `join_target_format`, so the same
    halving applies to the normalised rate. Whether every clip in it can keep
    the one-frame-once promise is a separate question, answered before anything
    is queued.
    """
    if len(clips) > 1:
        _, _, fps = join_target_format(clips)
    else:
        fps = clips[0].fps
    return (fps or 60.0) / SLOW_FACTOR


def _scale_filter(clip: ClipInfo, target_height: int) -> list[str]:
    if not target_height or not clip.height or target_height >= clip.height:
        return []
    # -2 keeps the width even, which H.264 requires.
    return [f"scale=-2:{target_height}:flags=lanczos"]


def _resize_filter(clip: ClipInfo, target_height: int) -> list[str]:
    """Scale to `target_height` in either direction, unlike _scale_filter.

    Every other preset refuses to enlarge, and rightly: making the picture
    bigger cannot recover detail that was never recorded. Upload does it anyway
    for a reason that has nothing to do with detail — the platforms it targets
    hand out bitrate by resolution tier, so arriving at 1080p buys a larger
    allowance for their own re-encode than arriving at 720p does.

    Lanczos going up as well as down: it is the sharpest of the usual choices,
    and softness here would waste the allowance the upscale was meant to win.
    """
    if not target_height or not clip.height or target_height == clip.height:
        return _even_size_filter(clip)
    return [f"scale=-2:{target_height}:flags=lanczos"]


def _even_size_filter(clip: ClipInfo) -> list[str]:
    """Round an odd frame size down to an even one.

    H.264 and HEVC in 4:2:0 cannot represent an odd width or height, and
    libx264 does not cope — it stops with "width not divisible by 2" and writes
    nothing. Even sizing was previously applied only to the width computed
    during an explicit downscale, so a source that was already odd reached the
    encoder untouched.

    Nothing a Box Pro records is odd, but the app opens any folder. Emitted
    only when it is needed, so the ordinary chain stays exactly as measured.
    """
    if not clip.width or not clip.height:
        return []
    if clip.width % 2 == 0 and clip.height % 2 == 0:
        return []
    return ["scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=lanczos"]


# --- joining several clips into one export -----------------------------------

# Audio every joined clip is brought to, so the pieces can be concatenated.
JOIN_SAMPLE_RATE = 48000
JOIN_CHANNELS = "stereo"


def join_target_format(clips: list[ClipInfo]) -> tuple[int, int, float]:
    """The frame size and rate every clip in a join is brought to.

    The largest of each, so nothing is thrown away to accommodate the smallest
    clip. Dimensions are forced even because the H.264 and HEVC encoders
    refuse odd ones in 4:2:0.
    """
    width = max((c.width for c in clips if c.width), default=1280)
    height = max((c.height for c in clips if c.height), default=720)
    fps = max((c.fps for c in clips if c.fps), default=60.0)
    return width - (width % 2), height - (height % 2), fps


def _clip_timing(clip: ClipInfo) -> tuple[float, float, float]:
    """(input seek, where the wanted part starts after it, how much to keep).

    Split the same way a single-clip export splits it: a fast seek that lands
    somewhere before the in point, then an accurate cut once the decoder has
    caught up. The concat demuxer's `inpoint` cannot do the second half, which
    is why a joined trim used to begin with corrupt frames.
    """
    duration = clip.trimmed_duration or clip.duration
    if clip.trim_in <= 0.01:
        return 0.0, 0.0, duration
    lead_in = min(clip.trim_in, SEEK_LEAD_IN)
    return clip.trim_in - lead_in, lead_in, duration


def join_inputs(clips: list[ClipInfo]) -> list[str]:
    """Input arguments for a join: one -i per clip, each with its own seek."""
    args: list[str] = []
    for clip in clips:
        seek, _, _ = _clip_timing(clip)
        args += ["-fflags", "+genpts", "-analyzeduration", "100M",
                 "-probesize", "100M"]
        if seek > 0.01:
            args += ["-ss", f"{seek:.3f}"]
        args += ["-i", str(clip.path)]
    return args


def join_filtergraph(
    clips: list[ClipInfo],
    settings: ExportSettings,
    pix_fmt: str,
    tail: list[str] | None = None,
) -> tuple[str, str, str]:
    """A filter_complex that normalises every clip and then joins them.

    Returns (graph, video label, audio label). The audio label is empty when
    the export carries no sound.

    This replaces the concat demuxer, which needed every input to present
    identical streams and took its encoder settings from the first clip, so a
    join of clips that differed produced a file that was silent, or stretched,
    or the wrong size, without ever reporting a problem. Here each clip is
    decoded on its own, cut accurately, brought to a common frame size, rate
    and range, given silence if it has none, and only then concatenated.

    Colour is handled per clip rather than once for all of them, because
    whether a recording is full range is a property of that recording.
    """
    width, height, fps = join_target_format(clips)
    want_audio = settings.keep_audio and any(c.has_audio for c in clips)

    chains: list[str] = []
    labels: list[str] = []

    for index, clip in enumerate(clips):
        _, start, duration = _clip_timing(clip)

        # A seek before -i already rebases timestamps to zero, so the trim is
        # measured from the start of what was decoded.
        #
        # Known imprecision: a joined segment can come out one frame short,
        # measured at 359 frames where 360 were expected across two three-
        # second cuts. The seam itself is correct — the frames on either side
        # of it match a standalone export at 44.7 dB — so this is a sixtieth of
        # a second lost at each join, against the corrupt frames the concat
        # demuxer produced at every trimmed seam.
        video = [f"trim=start={start:.3f}:duration={duration:.3f}",
                 "setpts=PTS-STARTPTS"]
        # Range conversion folded into the scale that resizes the clip, so the
        # picture is only resampled once.
        scale = f"scale={width}:{height}:force_original_aspect_ratio=decrease"
        if settings.colour == LEVELS and clip.is_full_range:
            scale += ":in_range=full:out_range=limited"
        video += [
            scale,
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            "setsar=1",
            f"fps={fps:g}",
            f"format={pix_fmt}",
        ]
        chains.append(f"[{index}:v]{','.join(video)}[v{index}]")
        labels.append(f"[v{index}]")

        if not want_audio:
            continue
        if clip.has_audio:
            audio = [f"atrim=start={start:.3f}:duration={duration:.3f}",
                     "asetpts=PTS-STARTPTS",
                     f"aresample={JOIN_SAMPLE_RATE}:async=1:first_pts=0"]
            chains.append(f"[{index}:a]{','.join(audio)}[a{index}]")
        else:
            # Silence of exactly this clip's length, so the ones that do have
            # sound keep theirs instead of the whole join being silenced.
            chains.append(
                f"anullsrc=channel_layout={JOIN_CHANNELS}:"
                f"sample_rate={JOIN_SAMPLE_RATE},"
                f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[a{index}]"
            )
        labels.append(f"[a{index}]")

    streams = "1" if want_audio else "0"
    chains.append(
        f"{''.join(labels)}concat=n={len(clips)}:v=1:a={streams}"
        f"[jv]{'[ja]' if want_audio else ''}"
    )

    video_label = "[jv]"
    if tail:
        chains.append(f"[jv]{','.join(tail)}[vout]")
        video_label = "[vout]"

    return ";".join(chains), video_label, "[ja]" if want_audio else ""


def join_problems(clips: list[ClipInfo], re_encoding: bool = True,
                  slowing: bool = False) -> list[str]:
    """Why these clips cannot be joined into one file, in words a pilot can act on.

    Clips of different sizes, frame rates, codecs and colour ranges join
    perfectly well now, and so do clips with no sound alongside clips that have
    it — join_filtergraph() brings each one to a common format first. What is
    left here is what no amount of normalising can rescue: a clip whose
    properties could not be read, and a clip with nothing in it.

    `slowing` adds the one thing normalising cannot rescue for Slow motion. The
    graph brings every clip to the highest rate present, which means a 30 fps
    clip beside a 60 fps one has frames duplicated before anything is slowed —
    so the export would show invented frames while promising it never does.

    Messages are written to be read by someone deciding what to do next, not
    to describe the internals.
    """
    if len(clips) < 2:
        return []

    def distinct(key):
        seen = []
        for clip in clips:
            value = key(clip)
            if value not in seen:
                seen.append(value)
        return seen

    problems: list[str] = []

    # Differences that used to be refused are now normalised in the filter
    # graph instead: frame size, frame rate, codec, colour range, and clips
    # with no sound among clips that have it. See join_filtergraph().

    unreadable = [c for c in clips if not c.width or not c.height]
    if unreadable:
        listed = ", ".join(c.path.name for c in unreadable[:3])
        problems.append(
            f"{len(unreadable)} of them could not be read properly ({listed}), "
            "so there is no way to tell what joining them would produce"
        )

    empty = [c for c in clips if (c.trimmed_duration or c.duration) <= 0.05]
    if empty:
        listed = ", ".join(c.path.name for c in empty[:3])
        problems.append(f"{len(empty)} of them are empty or trimmed to nothing ({listed})")

    if slowing:
        # Refused rather than normalised, which is the choice #59 leaves open.
        # Normalising to the lowest rate would throw away frames the faster
        # clips recorded; normalising to the highest invents frames for the
        # slower ones. Either breaks the one promise the preset makes, and a
        # documented common rate cannot be honest about both clips at once.
        unreadable_rate = [c for c in clips if not c.fps]
        if unreadable_rate:
            listed = ", ".join(c.path.name for c in unreadable_rate[:3])
            problems.append(
                f"the frame rate of {len(unreadable_rate)} of them could not "
                f"be read ({listed}), so there is no way to slow them without "
                "guessing how many frames they hold"
            )
        rates = sorted({c.fps for c in clips if c.fps})
        if len(rates) > 1:
            spoken = " and ".join(f"{rate:g}" for rate in rates)
            problems.append(
                f"they were recorded at different frame rates ({spoken} fps), "
                "and slow motion shows every recorded frame once — putting "
                "them on one rate would have to invent frames for the slower "
                "clips or throw away frames from the faster ones"
            )

    if re_encoding:
        return problems

    # Copying without re-encoding puts the clips end to end untouched, so
    # anything that differs between them has to match already.

    # And it cannot cut inside one at all. A stream copy can only begin at a
    # keyframe, so the concat demuxer's inpoint hands the muxer frames whose
    # reference picture was never written — measured on a mid-GOP trim, the
    # result decodes with "Could not find ref with POC 64" and shows torn
    # macroblocks. It produces a file, reports success, and is wrong, which is
    # the failure mode this project exists to avoid.
    #
    # A single clip is fine: there the trim is a seek, and ffmpeg snaps to the
    # keyframe before it, which is the "a second or so out" the README already
    # promises. Only the joined path has this.
    trimmed = [c for c in clips if c.is_trimmed]
    if trimmed:
        listed = ", ".join(c.path.name for c in trimmed[:3])
        problems.append(
            f"{len(trimmed)} of them are trimmed ({listed}), and joining "
            "without re-encoding cannot cut inside a clip — it can only start "
            "one where a keyframe already is. Reset the trims to join them "
            "untouched, or pick a re-encoding preset to keep the trims"
        )

    sizes = distinct(lambda c: (c.width, c.height))
    if len(sizes) > 1:
        listed = ", ".join(f"{w}×{h}" for w, h in sizes)
        problems.append(f"they are different sizes ({listed})")

    rates = distinct(lambda c: round(c.fps, 2) if c.fps else 0.0)
    if len(rates) > 1:
        listed = ", ".join(f"{r:g}" for r in rates)
        problems.append(f"they were recorded at different frame rates ({listed} fps)")

    codecs = distinct(lambda c: c.video_codec or "unknown")
    if len(codecs) > 1:
        problems.append(f"they use different video codecs ({', '.join(codecs)})")

    if len({c.has_audio for c in clips}) > 1:
        with_sound = sum(1 for c in clips if c.has_audio)
        problems.append(
            f"only {with_sound} of {len(clips)} have sound"
        )

    if problems:
        problems.append(
            "copying without re-encoding cannot change any of that — one of "
            "the other presets can"
        )
    return problems


def describe_join_problems(clips: list[ClipInfo], problems: list[str]) -> str:
    """The refusal, as the user should read it."""
    names = ", ".join(c.path.name for c in clips[:3])
    if len(clips) > 3:
        names += f" and {len(clips) - 3} more"
    reasons = "".join(f"\n  • {problem}" for problem in problems)
    return (
        f"These {len(clips)} clips cannot be joined into one file because"
        f"{reasons}\n\n"
        f"Exporting them separately works and produces the same footage — "
        f"only as {len(clips)} files instead of one.\n\n({names})"
    )


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
    clips: list[ClipInfo] | None = None,
) -> list[list[str]]:
    """Full ffmpeg command list. Two entries when a two-pass encode is needed.

    `clip` describes the first source. `clips` is every clip in the job, which
    a join needs in full: each one is decoded separately and brought to a
    common format, rather than one clip's properties being applied to all of
    them. `total_duration` is how long the finished file will be.

    Leave `clips` and `total_duration` alone for a single clip.
    """
    clips = clips or [clip]
    joined = len(clips) > 1
    sources = sources or [c.path for c in clips]
    ff = str(tools.ffmpeg)
    preset = PRESETS[preset_key]

    if preset_key == "remux":
        # Stream copy cannot normalise anything, so a joined remux still reads
        # through the concat demuxer and still needs matching clips. That is
        # why join_problems() is stricter when re_encoding is False.
        head = ([ff, "-hide_banner", "-nostdin", "-y"]
                + _input_args(sources, concat_file, clip))
        return [head + ["-c", "copy", "-movflags", "+faststart", str(out_path)]]

    if joined:
        head = [ff, "-hide_banner", "-nostdin", "-y"] + join_inputs(clips)
    else:
        head = ([ff, "-hide_banner", "-nostdin", "-y"]
                + _input_args(sources, None, clip))

    def picture(pix_fmt: str, tail: list[str] | None = None):
        """Filter arguments, and whether the result carries audio.

        A join routes through filter_complex so every clip can be brought to a
        common format first; a single clip keeps the simpler -vf chain.
        """
        if joined:
            graph, video_label, audio_label = join_filtergraph(
                clips, settings, pix_fmt, tail
            )
            args = ["-filter_complex", graph, "-map", video_label]
            if audio_label:
                args += ["-map", audio_label]
            return args, bool(audio_label)
        chain = (_even_size_filter(clip) + (tail or [])
                 + colour_filters(settings.colour, clip, pix_fmt))
        return ["-vf", ",".join(chain)], None

    def sound(bitrate: str, mapped, pcm: bool = False) -> list[str]:
        """Audio arguments for whichever route the picture took."""
        if mapped is None:
            return _audio_args(settings, clip, bitrate, pcm=pcm)
        if not mapped:
            return ["-an"]
        # Resampling and channel layout are already done in the graph.
        return ["-c:a", "pcm_s16le"] if pcm else ["-c:a", "aac", "-b:a", bitrate]

    def timing(override: int = 0) -> list[str]:
        if not joined:
            return _fps_args(tools, clip, override)
        # The graph has already put every clip on one rate; state it plainly
        # rather than deriving it from the first clip, whose rate may differ.
        _, _, fps = join_target_format(clips)
        rate = min(override, fps) if override else fps
        return frame_rate_mode(tools, "cfr") + ["-r", f"{rate:g}"]

    if preset_key == "edit":
        label, codec_args, pix_fmt, _ = EDIT_CODECS[settings.edit_codec]
        filters, mapped = picture(pix_fmt)
        return [
            head + filters + codec_args + timing()
            + sound("192k", mapped, pcm=True) + [str(out_path)]
        ]

    if preset_key == "master":
        filters, mapped = picture("yuv420p")
        if settings.hardware:
            video = hardware_video_args(settings.hardware, settings.master_crf)
        else:
            video = [
                "-c:v", "libx264", "-preset", settings.master_speed,
                "-crf", str(settings.master_crf), "-profile:v", "high",
            ]
        return [
            head + filters + video + timing() + sound("192k", mapped)
            + ["-movflags", "+faststart", str(out_path)]
        ]

    if preset_key == "upload":
        # The only preset that may enlarge the picture. Quality-based, never
        # size-targeted: spreading a fixed budget over more pixels than the
        # camera recorded is worse than doing neither.
        filters, mapped = picture(
            "yuv420p", _resize_filter(clip, settings.upload_height)
        )
        if settings.hardware:
            video = hardware_video_args(settings.hardware, settings.upload_crf)
        else:
            video = [
                "-c:v", "libx264", "-preset", settings.upload_speed,
                "-crf", str(settings.upload_crf), "-profile:v", "high",
            ]
        return [
            head + filters + video + timing() + sound("192k", mapped)
            + ["-movflags", "+faststart", str(out_path)]
        ]

    if preset_key == "slowmo":
        # Half speed out of the frames that were recorded, never out of frames
        # invented to fill the gap. Two arguments have to agree for that, and
        # the whole correctness of the preset is in their relationship:
        #
        #   setpts=2*PTS   doubles every presentation time. No frame is added
        #                  and none is dropped; the same pictures are simply
        #                  spread over twice as long.
        #   -r fps/2       states the rate that stream already has. 60 frames
        #                  spread over two seconds ARE 30 fps.
        #
        # Leaving the output rate at the source's is the mistake this comment
        # exists to prevent: -fps_mode cfr would then duplicate every frame to
        # fill 60 fps across the doubled runtime, and the result — twice the
        # frames, each shown twice — looks correct in a player and is not what
        # was recorded. Frame count is asserted rather than assumed, in
        # test_slow_motion.py, against real media.
        rate = slow_output_rate(clips)
        filters, mapped = picture("yuv420p", [f"setpts={SLOW_FACTOR}*PTS"])
        if settings.hardware:
            video = hardware_video_args(settings.hardware, settings.master_crf)
        else:
            video = [
                "-c:v", "libx264", "-preset", settings.master_speed,
                "-crf", str(settings.master_crf), "-profile:v", "high",
            ]
        return [
            head + filters + video
            + frame_rate_mode(tools, "cfr") + ["-r", f"{rate:g}"]
            # Never the source audio. Sound at half pitch is not slow motion,
            # and keeping it at speed over doubled video would drift apart by
            # the length of the clip. Refused here rather than left to the
            # Keep audio tickbox, so no combination of settings can produce it.
            + ["-an"]
            + ["-movflags", "+faststart", str(out_path)]
        ]

    # Guarded rather than left as the fallthrough it used to be. A preset added
    # to PRESETS without a branch here would otherwise have been built as
    # Social, silently and plausibly, which is the worst way for a mistake to
    # present itself.
    if preset_key != "social":
        raise KeyError(f"no command builder for the {preset_key!r} preset")

    filters, mapped = picture("yuv420p", _scale_filter(clip, settings.social_height))
    carries_audio = mapped if mapped is not None else (
        settings.keep_audio and clip.has_audio
    )
    audio_kbps = 128 if carries_audio else 0
    fps_args = timing(settings.social_fps)
    audio_args = sound("128k", mapped)

    if settings.social_mode == "quality":
        if settings.hardware:
            video = hardware_video_args(settings.hardware, settings.social_crf, "balanced")
        else:
            video = ["-c:v", "libx264", "-preset", "medium",
                     "-crf", str(settings.social_crf), "-profile:v", "high"]
        return [
            head + filters + video + fps_args + audio_args
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
            head + filters + video + fps_args + audio_args
            + ["-movflags", "+faststart", str(out_path)]
        ]

    log_prefix = work_dir / f"pass_{out_path.stem}"
    common = ["-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k",
              "-profile:v", "high", "-passlogfile", str(log_prefix)]
    # Both passes must present ffmpeg with the same streams. Dropping audio from
    # pass 1 with -an, which is the usual advice, shifts the video framing by a
    # frame here, and x264 then refuses the mismatched stats file.
    pass1 = (
        head + filters + common
        + ["-pass", "1"] + fps_args + audio_args + ["-f", "null", "-"]
    )
    pass2 = (
        head + filters + common
        + ["-pass", "2"] + fps_args + audio_args
        + ["-movflags", "+faststart", str(out_path)]
    )
    return [pass1, pass2]


def output_runtime(preset_key: str, seconds: float) -> float:
    """How long the finished file is, which is not always how long the source is.

    Every other preset writes a file the length of the footage that went in, so
    the app read the two as the same number in three places: the estimate, the
    progress bar, and the runtime reported for a queued job. Slow motion is the
    first preset for which they differ, and each of those three is wrong by half
    without asking here.
    """
    if preset_key == "slowmo":
        return seconds * SLOW_FACTOR
    return seconds


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

    if preset_key == "slowmo":
        # Half the frame rate over twice the runtime is the same pictures at
        # the same quality, so this lands close to a Master export of the same
        # footage — which is the honest answer, and not the one you get by
        # estimating the source runtime at the source rate and doubling
        # nothing, or by doubling the runtime while leaving the rate alone.
        out_scale = pixel_rate(clip, fps=slow_output_rate([clip]))
        mbps = (MASTER_REFERENCE_MBPS * (out_scale / REFERENCE_PIXEL_RATE)
                * (2 ** ((18 - settings.master_crf) / 6.0)))
        return int(mbps * 1_000_000 / 8 * output_runtime(preset_key, runtime))

    if preset_key == "upload":
        # allow_upscale, or this reports the size of a 720p file for a 1080p
        # export and everyone plans around the wrong number.
        out_scale = pixel_rate(clip, settings.upload_height, allow_upscale=True)
        mbps = (MASTER_REFERENCE_MBPS * (out_scale / REFERENCE_PIXEL_RATE)
                * (2 ** ((18 - settings.upload_crf) / 6.0)))
        return int(mbps * 1_000_000 / 8 * runtime)

    # Guarded for the same reason as build_commands: an unknown preset must not
    # quietly inherit Social's estimate.
    if preset_key != "social":
        raise KeyError(f"no size estimate for the {preset_key!r} preset")

    if settings.social_mode == "size":
        return settings.social_size_mb * 1024 * 1024

    out_scale = pixel_rate(clip, settings.social_height, settings.social_fps)
    mbps = (SOCIAL_REFERENCE_MBPS * (out_scale / REFERENCE_PIXEL_RATE)
            * (2 ** ((23 - settings.social_crf) / 6.0)))
    return int(mbps * 1_000_000 / 8 * runtime)


def templated_output_path(out_dir: Path, stem: str, preset_key: str,
                          subfolders: bool) -> Path:
    """Where an export lands when its whole name came from a template.

    Deliberately not `output_path`. That one appends the preset suffix and
    prefixes the flight date itself, which is right when the caller passed a
    bare clip stem and wrong once a template has already placed both: the
    default template rendered `hdz_047_master` and `output_path` turned it into
    `hdz_047_master_master.mp4`.

    Remux hid it, because its suffix is empty.
    """
    preset = PRESETS[preset_key]
    folder = out_dir / preset.label if subfolders else out_dir
    return folder / f"{stem}{preset.extension}"


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
