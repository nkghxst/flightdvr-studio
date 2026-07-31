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

"""Locating the ffmpeg tools and reading what is actually inside a DVR clip.

Everything else depends on `probe()` returning honest information, because the
export presets make decisions (notably about colour range) based on how the
source is tagged rather than on assumptions about the hardware.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import functools
import sys
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path

# Keeps console windows from flashing up for every ffprobe call on Windows.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Checked after PATH. On Windows these are where people unpack the gyan.dev
# builds; on Linux a package manager puts ffmpeg on PATH already, so those are
# only for a manually installed or Flatpak-exported copy. The Homebrew prefixes
# matter more on macOS: a GUI app launched from Finder inherits a bare PATH that
# does not include either of them.
_EXTRA_DIRS = [
    Path(r"C:\ffmpeg\bin"),
    Path(r"C:\Program Files\ffmpeg\bin"),
    Path(r"C:\Program Files (x86)\ffmpeg\bin"),
    Path("/opt/homebrew/bin"),          # Homebrew on Apple Silicon
    Path("/usr/local/bin"),             # Homebrew on Intel, and manual installs
    Path("/opt/local/bin"),             # MacPorts
    Path("/var/lib/flatpak/exports/bin"),
    Path.home() / ".local" / "bin",
]

if os.name == "nt":
    INSTALL_HINT = (
        "Install the full ffmpeg build (it includes ffprobe) and make sure its "
        "bin folder is on PATH, or unpack it to C:\\ffmpeg\\bin."
    )
elif sys.platform == "darwin":
    INSTALL_HINT = (
        "Install ffmpeg with Homebrew: run 'brew install ffmpeg' in Terminal. "
        "If you do not have Homebrew yet, the one-line installer is at "
        "https://brew.sh."
    )
else:
    INSTALL_HINT = (
        "Install ffmpeg with your package manager, for example "
        "'sudo apt install ffmpeg' or 'sudo dnf install ffmpeg'."
    )


class ToolsMissing(RuntimeError):
    """Raised when ffmpeg or ffprobe cannot be found anywhere."""


def _bundled_dirs() -> list[Path]:
    """Folders to search inside a packaged build, before anything on PATH.

    A bundled copy is preferred so the packaged app behaves identically on a
    machine that has never had ffmpeg installed.
    """
    dirs: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", None)          # PyInstaller
    if bundle:
        dirs += [Path(bundle) / "ffmpeg", Path(bundle)]
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).parent
        dirs += [here / "ffmpeg", here]
    return dirs


@functools.lru_cache(maxsize=8)
def _fps_mode_supported(ffmpeg: str) -> bool:
    """Whether this ffmpeg knows -fps_mode, which replaced -vsync in 5.1.

    Tested rather than inferred from the version string, for the same reason
    the hardware encoders are: what a build advertises and what it accepts are
    not always the same thing.

    This matters more than it looks. Ubuntu 22.04 ships ffmpeg 4.4, the
    AppImage is built for 22.04 on purpose and does not carry its own ffmpeg,
    and every re-encoding export used -fps_mode. Every export on that
    distribution failed with "Unrecognized option 'fps_mode'".
    """
    try:
        result = run_hidden(
            [ffmpeg, "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "nullsrc=s=16x16:d=0.04",
             "-fps_mode", "cfr", "-f", "null", "-"],
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def frame_rate_mode(tools: "Tools", mode: str) -> list[str]:
    """The frame-rate-sync option this ffmpeg actually understands.

    `mode` is the modern spelling — "cfr" or "passthrough". Both are accepted
    by -vsync on older builds, so only the option name changes.
    """
    if _fps_mode_supported(str(tools.ffmpeg)):
        return ["-fps_mode", mode]
    return ["-vsync", mode]


def is_bundled(path: Path) -> bool:
    """True when this tool came from inside the packaged app.

    Only the Windows installer carries its own ffmpeg; the AppImage and the
    macOS app use whatever the system has. The About box has to say which,
    because the licensing position differs.
    """
    for folder in _bundled_dirs():
        try:
            path.resolve().relative_to(folder.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def packaged_file(name: str) -> Path | None:
    """Find a file that was packaged alongside the app.

    Every format puts these somewhere different: _internal beside the exe on
    Windows, Contents/Frameworks inside a macOS app, usr/bin inside an
    AppImage. sys._MEIPASS is whichever of those is in use at runtime.
    """
    folders: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        folders.append(Path(bundle))
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).parent
        folders += [here, here / "_internal", here.parent, here.parent.parent]
    folders.append(Path(__file__).resolve().parents[1])

    for folder in folders:
        candidate = folder / name
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def _locate(name: str) -> Path | None:
    exe = name + (".exe" if os.name == "nt" else "")
    for folder in _bundled_dirs():
        candidate = folder / exe
        if candidate.exists():
            return candidate
    found = shutil.which(name)
    if found:
        return Path(found)
    for folder in _EXTRA_DIRS:
        candidate = folder / exe
        if candidate.exists():
            return candidate
    return None


@dataclass(frozen=True)
class Tools:
    ffmpeg: Path
    ffprobe: Path


def find_tools() -> Tools:
    ffmpeg, ffprobe = _locate("ffmpeg"), _locate("ffprobe")
    missing = [n for n, v in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if v is None]
    if missing:
        raise ToolsMissing(
            "Could not find " + " and ".join(missing) + ".\n\n" + INSTALL_HINT
        )
    return Tools(ffmpeg, ffprobe)  # type: ignore[arg-type]


def run_hidden(args: list[str], timeout: float | None = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=NO_WINDOW,
    )


@dataclass
class ClipInfo:
    """What a single DVR recording contains."""

    path: Path
    size: int
    modified: datetime
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    pix_fmt: str = ""
    color_range: str = ""
    color_space: str = ""
    color_primaries: str = ""
    color_transfer: str = ""
    bit_rate: int = 0
    error: str = ""

    # In and out points in seconds. out_point of 0 means "to the end", so an
    # untrimmed clip is all zeroes and needs no special casing.
    trim_in: float = 0.0
    trim_out: float = 0.0

    # -- trimming --------------------------------------------------------------

    @property
    def is_trimmed(self) -> bool:
        return self.trim_in > 0.01 or self.trim_out > 0.01

    @property
    def out_point(self) -> float:
        """Where the export should stop, in seconds from the start of the file."""
        if self.trim_out > 0.01:
            return min(self.trim_out, self.duration or self.trim_out)
        return self.duration

    @property
    def trimmed_duration(self) -> float:
        """How much footage an export of this clip will actually contain."""
        if not self.duration:
            return 0.0
        return max(0.0, self.out_point - self.trim_in)

    @property
    def trim_label(self) -> str:
        if not self.is_trimmed:
            return ""
        return f"{_clock(self.trim_in)}–{_clock(self.out_point)}"

    # -- convenience for the UI ------------------------------------------------

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_codec)

    @property
    def is_full_range(self) -> bool:
        """True when the file stores 0-255 luma rather than the usual 16-235.

        HDZero DVR files are recorded this way. If it is not corrected on
        export, anything that assumes limited range clips the blacks and
        whites, which is the single most common complaint about this footage.
        """
        return self.color_range == "pc" or self.pix_fmt.startswith("yuvj")

    @property
    def sequence(self) -> int:
        """The DVR's own counter, e.g. 112 from hdz_112.ts.

        The goggles have no clock worth trusting, so this counter is the only
        reliable record of the order the recordings were made in.
        """
        match = re.search(r"(\d+)\s*$", self.path.stem)
        return int(match.group(1)) if match else -1

    @property
    def duration_label(self) -> str:
        if self.duration <= 0:
            return "?"
        total = int(round(self.duration))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @property
    def size_label(self) -> str:
        mb = self.size / (1024 * 1024)
        return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.0f} MB"

    @property
    def format_label(self) -> str:
        """Short form, e.g. "720p60 HEVC".

        The long "1280x720 60p HEVC" spelling ate enough width in the clip list
        to squeeze the thumbnail down to nothing.
        """
        if not self.height:
            return "unreadable"
        fps = f"{self.fps:g}" if self.fps else "?"
        return f"{self.height}p{fps} {self.video_codec.upper()}"

    @property
    def format_detail(self) -> str:
        """The full spelling, for tooltips."""
        if not self.width:
            return "unreadable"
        fps = f"{self.fps:g}" if self.fps else "?"
        return f"{self.width}x{self.height} {fps} fps {self.video_codec.upper()}"

    @property
    def stem(self) -> str:
        return self.path.stem


def _clock(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fps_from(rate: str | None) -> float:
    if not rate or rate in ("0/0", "0"):
        return 0.0
    try:
        return float(Fraction(rate))
    except (ZeroDivisionError, ValueError):
        return 0.0


def _probe_once(tools: Tools, path: Path, info: ClipInfo, extra: list[str]) -> ClipInfo:
    args = [
        str(tools.ffprobe), "-v", "error", *extra,
        "-print_format", "json", "-show_format", "-show_streams", str(path),
    ]
    try:
        result = run_hidden(args, timeout=120)
    except subprocess.TimeoutExpired:
        info.error = "ffprobe timed out"
        return info

    if result.returncode != 0:
        info.error = (result.stderr or "ffprobe failed").strip().splitlines()[-1][:200]
        return info

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        info.error = "could not parse ffprobe output"
        return info

    fmt = data.get("format", {})
    info.duration = _to_float(fmt.get("duration"))
    info.bit_rate = int(_to_float(fmt.get("bit_rate")))

    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and not info.video_codec:
            info.video_codec = stream.get("codec_name", "")
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            info.pix_fmt = stream.get("pix_fmt", "")
            info.color_range = stream.get("color_range", "")
            info.color_space = stream.get("color_space", "")
            info.color_primaries = stream.get("color_primaries", "")
            info.color_transfer = stream.get("color_transfer", "")
            info.fps = _fps_from(stream.get("avg_frame_rate")) or _fps_from(
                stream.get("r_frame_rate")
            )
            if not info.duration:
                info.duration = _to_float(stream.get("duration"))
        elif kind == "audio" and not info.audio_codec:
            info.audio_codec = stream.get("codec_name", "")

    if not info.video_codec:
        info.error = "no video stream found"
    return info


def probe(tools: Tools, path: Path) -> ClipInfo:
    """Read stream details. Never raises: failures come back on `.error`.

    ffprobe's own defaults are tried first. Forcing a large probe size costs
    about 0.73 s per clip reading from an SD card over USB, against 0.09 s at
    the defaults, and on these recordings both return exactly the same answer.
    The expensive settings are kept only as a fallback for a file the quick
    pass could not make sense of.
    """
    stat = path.stat()
    info = ClipInfo(
        path=path,
        size=stat.st_size,
        modified=datetime.fromtimestamp(stat.st_mtime),
    )

    _probe_once(tools, path, info, [])
    if info.error or not info.width or info.duration <= 0:
        # A stubborn transport stream: pay for a deeper look this time.
        retry = ClipInfo(path=path, size=info.size, modified=info.modified)
        _probe_once(tools, path, retry, ["-analyzeduration", "100M", "-probesize", "100M"])
        if not retry.error and retry.width:
            return retry
    return info


def available_encoders(tools: Tools) -> set[str]:
    """Encoder names this ffmpeg build actually supports."""
    try:
        result = run_hidden([str(tools.ffmpeg), "-hide_banner", "-encoders"], timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return set()
    names = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        # Encoder lines look like: " V....D libx264   libx264 H.264 ..."
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return names


# Hardware H.264 encoders, best first. Whichever one this machine can actually
# run is the one the app offers.
HW_ENCODERS = [
    ("h264_nvenc", "NVIDIA NVENC"),
    ("h264_qsv", "Intel Quick Sync"),
    ("h264_amf", "AMD AMF"),
    ("h264_videotoolbox", "Apple VideoToolbox"),
]


def _encoder_runs(tools: Tools, name: str) -> bool:
    """Try a token encode.

    An encoder being compiled into ffmpeg says nothing about whether the
    hardware is present: a build with NVENC support still fails on a machine
    with no NVIDIA card, so the only reliable test is to run it once.
    """
    args = [
        str(tools.ffmpeg), "-hide_banner", "-v", "error", "-nostdin",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=0.2",
        "-c:v", name, "-frames:v", "3", "-f", "null", "-",
    ]
    try:
        return run_hidden(args, timeout=45).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def detect_hardware_encoder(
    tools: Tools, encoders: set[str] | None = None
) -> tuple[str, str] | None:
    """The hardware encoder this machine can really use, or None."""
    if encoders is None:
        encoders = available_encoders(tools)
    for name, label in HW_ENCODERS:
        if name in encoders and _encoder_runs(tools, name):
            return name, label
    return None
