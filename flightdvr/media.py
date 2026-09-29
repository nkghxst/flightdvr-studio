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

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import functools
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from fractions import Fraction
from uuid import uuid4
from pathlib import Path

# One rule for what counts as the same path, shared with the session so
# the fingerprint and the autosave filename cannot disagree.
from .format import canonical_path

# Keeps console windows from flashing up for every ffprobe call on Windows.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# How long to let a child shut down politely before killing it.
TERMINATE_SECONDS = 5

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


def request_stop(proc) -> None:
    """Ask a child to stop, without waiting to find out whether it did.

    For calls made on the UI thread. `stop_process` waits — up to the timeout
    after terminate(), and again after kill() — which is correct on a worker
    thread and is a frozen window on this one. Cancelling an export, seeking,
    changing clip and closing all reach the same escalation, and a child that
    has stopped answering would hold the interface for several seconds.

    Safe because the worker owning the process runs the full escalation in its
    own cleanup: this only has to unblock the read that the worker is sitting
    in, and terminate() does that by closing the pipe.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        pass


def stop_process(proc, timeout: float = TERMINATE_SECONDS) -> None:
    """Stop a child process and make sure it has actually gone.

    terminate() is a request, not an instruction. An ffmpeg that ignores it
    used to be left running while the app carried on, and closing the window
    could orphan it entirely — still holding the card open. So: ask, wait a
    bounded time, then insist.

    Lives here rather than in jobs.py because both the export queue and the
    preview player need it, and the player importing the export queue would be
    the wrong direction entirely.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            pass


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
class Select:
    """One range worth keeping, out of a recording that may hold several.

    A four-minute flight usually has two or three moments in it. Naming them is
    what makes a list of ranges reviewable a week later, and what the naming
    templates planned for 1.6 have to work from.
    """

    start: float
    end: float
    name: str = ""

    # Identity, so an ordered assembly can name *this* range and still mean it
    # after the range is renamed, retrimmed, or has an earlier sibling deleted.
    # Position cannot do that job: removing range 1 would quietly retarget an
    # assembly item pointing at range 2, and the export would be wrong in a way
    # that only shows up on watching it. A fresh value per range rather than a
    # counter, because a counter has to remember which numbers it has retired.
    sid: str = field(default_factory=lambda: uuid4().hex[:12])

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def as_dict(self) -> dict:
        return {"start": round(self.start, 3),
                "end": round(self.end, 3),
                "name": self.name,
                "id": self.sid}

    @classmethod
    def from_dict(cls, raw: dict) -> "Select":
        # A missing id means a document older than schema 3 that reached here
        # without the migration, so generate rather than refuse: the range is
        # real and losing it would be worse than giving it a new identity.
        stored = str(raw.get("id", "")).strip()
        return cls(start=float(raw.get("start", 0.0)),
                   end=float(raw.get("end", 0.0)),
                   name=str(raw.get("name", "")),
                   **({"sid": stored} if stored else {}))


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

    # The ranges worth keeping out of this recording, and which of them is
    # being edited. Empty means nobody has decided anything, which is not the
    # same as a select covering the whole clip.
    selects: list[Select] = field(default_factory=list)
    current: int = 0

    # The browser's decision about the whole recording. Kept as a plain value
    # here because session.py imports ClipInfo; importing the review constants
    # back from there would make the model depend on its persistence layer.
    review: str = ""

    # Seconds from the file's start to its picture's first frame: ffprobe's
    # video-stream start_time minus the format start_time (the earliest of
    # all its streams). Zero when the picture starts first or with the file,
    # and whenever either value is absent, "N/A" or not a finite number —
    # never negative. The app's source clock starts at the picture (the
    # preview decodes without sound, so its zero is the first frame); a file
    # whose sound starts earlier has its own zero this far before that, which
    # is what an export that keeps the recording's sound has to measure from.
    # Measured: AAC's encoder priming alone gives 0.021333 s.
    video_start: float = 0.0

    # Seconds from the picture's first frame to the end of its last, on the
    # same clock: ffprobe's video-stream duration. Zero when that is absent,
    # "N/A", not finite or not positive (Matroska, for one, leaves it out).
    # `duration` is the whole file's, from its earliest stream to its latest,
    # so it also counts sound before the first picture and after the last.
    # Measured (P2d): a 600-frame picture, stream duration 20.000333, in
    # files whose `duration` was 20.019 to 20.072.
    video_duration: float = 0.0

    # Where `video_duration` came from: "stream" (ffprobe's video-stream
    # duration), "packets" (observed from the video packets at the end of the
    # file, when the stream gives none; see `observe_picture_end`), or "" when
    # it is not known. Provenance only: a caller that sets `video_duration`
    # itself is believed as before (`sequence_plan._whole_clip_end`).
    video_duration_origin: str = ""

    # -- trimming --------------------------------------------------------------

    # trim_in and trim_out are the select currently being edited, and mean
    # exactly what they meant when there was only ever one of them. Everything
    # downstream — the presets, the jobs, the whole export path — still sees a
    # clip with a single in point and a single out point, because at queueing
    # a clip with three selects becomes three copies each carrying one.
    #
    # Views rather than stored fields so there is one truth. Holding both a
    # list and a separate pair meant keeping them in step on every edit, and
    # the pair silently winning was the obvious way for a select to be lost.

    @property
    def _editing(self) -> Select | None:
        if 0 <= self.current < len(self.selects):
            return self.selects[self.current]
        return None

    def _editing_or_new(self) -> Select:
        found = self._editing
        if found is None:
            found = Select(0.0, 0.0)
            self.selects.append(found)
            self.current = len(self.selects) - 1
        return found

    @property
    def trim_in(self) -> float:
        found = self._editing
        return found.start if found else 0.0

    @trim_in.setter
    def trim_in(self, seconds: float) -> None:
        self._editing_or_new().start = seconds

    @property
    def trim_out(self) -> float:
        found = self._editing
        return found.end if found else 0.0

    @trim_out.setter
    def trim_out(self, seconds: float) -> None:
        self._editing_or_new().end = seconds

    @property
    def is_trimmed(self) -> bool:
        return self.trim_in > 0.01 or self.trim_out > 0.01

    @property
    def real_selects(self) -> list[Select]:
        """The selects that are actually a range.

        Clearing a trim leaves an empty select behind rather than deleting the
        row the interface is pointing at, so "has selects" and "has decisions"
        are not the same question.
        """
        return [s for s in self.selects
                if s.start > 0.01 or s.end > 0.01]

    def for_export(self) -> list["ClipInfo"]:
        """This clip as the export path wants to see it: one trim each.

        A clip with three selects becomes three ordinary clips, and everything
        downstream — presets, jobs, joining — carries on believing a recording
        has exactly one in point and one out point, because as far as it can
        tell one does. No select is one clip covering the whole recording.
        """
        ranges = self.real_selects
        if not ranges:
            return [self]
        # The Select is copied, not just the list holding it. A queued job
        # keeps its clip until it runs, and sharing the range meant adjusting a
        # select afterwards silently changed an export already in the queue.
        return [replace(self,
                        selects=[Select(one.start, one.end, one.name,
                                        sid=one.sid)],
                        current=0)
                for one in ranges]

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
    def fingerprint(self) -> str:
        """One name for this recording, as it is right now.

        Path, size and modification time together — never the path alone.
        Cards get reused and rewritten with the same filenames, so anything
        keyed on the name would confidently hand last week's trim points to
        this week's footage. Being wrong that way is worse than remembering
        nothing, so a rewritten card looks like new material and is.

        Everything that remembers something about a clip uses this: the
        filmstrip cache, and the session.
        """
        stamp = self.modified.timestamp() if self.modified else 0.0
        # Canonical, and by the same rule the session uses to name its autosave
        # file. Hashing the path as typed meant opening a card through
        # G:\Movies rather than g:\movies found the right session and then
        # reported every clip in it as missing.
        raw = f"{canonical_path(self.path)}|{self.size}|{stamp}"
        return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:20]

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


def _finite(value) -> float | None:
    """A number ffprobe gave, or None for absent, "N/A", NaN or infinity."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def video_origin(format_start, video_start) -> float:
    """`ClipInfo.video_start` from ffprobe's two start times; see there."""
    begins, picture = _finite(format_start), _finite(video_start)
    if begins is None or picture is None:
        return 0.0
    return max(0.0, round(picture - begins, 6))


def _fps_from(rate: str | None) -> float:
    if not rate or rate in ("0/0", "0"):
        return 0.0
    try:
        return float(Fraction(rate))
    except (ZeroDivisionError, ValueError):
        return 0.0


PROBE_TIMEOUT_SECONDS = 120
_PROBE_WAIT_SECONDS = 0.1


def _probe_once(
    tools: Tools, path: Path, info: ClipInfo, extra: list[str],
    should_stop=None, register=None, timeline: dict | None = None,
) -> ClipInfo:
    args = [
        str(tools.ffprobe), "-v", "error", *extra,
        "-print_format", "json", "-show_format", "-show_streams", str(path),
    ]
    if should_stop is not None and should_stop():
        info.error = "probe cancelled"
        return info

    proc = None
    try:
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=NO_WINDOW,
        )
    except OSError as exc:
        info.error = str(exc)
        return info
    if register is not None:
        register(proc)

    try:
        deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
        while True:
            if should_stop is not None and should_stop():
                info.error = "probe cancelled"
                return info
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                info.error = "ffprobe timed out"
                return info
            try:
                stdout, stderr = proc.communicate(
                    timeout=min(_PROBE_WAIT_SECONDS, remaining))
                break
            except subprocess.TimeoutExpired:
                continue

        if should_stop is not None and should_stop():
            info.error = "probe cancelled"
            return info
        if proc.returncode != 0:
            info.error = (
                (stderr or "ffprobe failed").strip().splitlines()[-1][:200]
            )
            return info
    except OSError as exc:
        info.error = str(exc)
        return info
    finally:
        # The probe thread owns the bounded wait/escalation.  A UI-side stop
        # only terminates to unblock communicate(), then returns immediately.
        stop_process(proc)
        if register is not None:
            register(None)

    try:
        data = json.loads(stdout)
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
            info.video_start = video_origin(fmt.get("start_time"),
                                            stream.get("start_time"))
            picture = _finite(stream.get("duration"))
            info.video_duration = picture if picture and picture > 0 else 0.0
            info.video_duration_origin = "stream" if info.video_duration else ""
            if timeline is not None:
                # For `observe_picture_end`: the raw time the video's packets
                # start at, and the file's length. None where ffprobe gave
                # nothing.
                timeline.update(
                    format_duration=_finite(fmt.get("duration")),
                    video_start=_finite(stream.get("start_time")),
                )
        elif kind == "audio" and not info.audio_codec:
            info.audio_codec = stream.get("codec_name", "")

    if not info.video_codec:
        info.error = "no video stream found"
    return info


def probe(tools: Tools, path: Path, should_stop=None,
          register=None) -> ClipInfo:
    """Read stream details. Never raises: failures come back on `.error`.

    A concurrent scan supplies `should_stop` and `register`. The first keeps a
    cancelled probe from launching either pass; the second exposes this
    thread's one active child so the scan can make a prompt termination request.

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

    timeline: dict = {}
    _probe_once(
        tools, path, info, [], should_stop=should_stop, register=register,
        timeline=timeline)
    if should_stop is not None and should_stop():
        return info
    if info.error or not info.width or info.duration <= 0:
        # A stubborn transport stream: pay for a deeper look this time.
        retry = ClipInfo(path=path, size=info.size, modified=info.modified)
        timeline = {}
        _probe_once(
            tools, path, retry,
            ["-analyzeduration", "100M", "-probesize", "100M"],
            should_stop=should_stop, register=register, timeline=timeline,
        )
        if not retry.error and retry.width:
            info = retry
        else:
            return info
    if not info.error and info.video_codec and not info.video_duration:
        # Only here: the stream gave no length for its picture (Matroska,
        # for one). One more bounded child on this same probe thread.
        end = observe_picture_end(tools, path, timeline,
                                  should_stop=should_stop, register=register)
        if end:
            info.video_duration, info.video_duration_origin = end, "packets"
    return info


# A recording no longer than this is read whole, from its start, to find where
# its picture ends; a longer one is not read at all (see observe_picture_end).
# The time and output bounds below still limit the one read.
PICTURE_END_WHOLE_READ_SECONDS = 10.0
PICTURE_END_TIMEOUT_SECONDS = 20.0
PICTURE_END_MAX_PACKETS = 20_000
PICTURE_END_MAX_LINE = 200


def observe_picture_end(tools: Tools, path: Path, timeline: dict,
                        should_stop=None, register=None) -> float:
    """Seconds from the first picture to the end of the last, or 0.0.

    Read from the video stream's own packets, for a recording whose stream
    gives no picture length: the largest presentation time plus that
    packet's duration, on the clock the stream's start time is on. Anything
    short of clean, complete evidence is 0.0, not an estimate: no start time
    or file length; a packet with no time or no positive duration, the last
    one included; an end longer than the file; any error ffprobe reports (a
    truncated file exits 0 but says "File ended prematurely"); a failed exit;
    a timeout; too much output; a stop request.

    Only a recording no longer than PICTURE_END_WHOLE_READ_SECONDS is read,
    whole and without a seek: those reads agreed every time they were
    measured. A longer one stays unknown. Reading only its end means a seek,
    and with FFmpeg 7.1.5 the same seeking read of one file returned, with
    exit 0 and no error, every packet in most runs, none in some (4 of 30 on
    a 5-minute file's last 10 s), and once only its first two -- an end that
    looks complete and is not. Two reads agreeing does not prove either one
    reached the end, and no exact test of that was found: the last packet of
    any stream ended 1 ms short of a Matroska file's stated length, 0.107 s
    past a transport stream's, and matched a third exactly (P2f).

    Measured (P2f): a Matroska file with no video-stream duration and a file
    duration of 4.230 has packets ending at 4.000, which a whole-clip join
    needs (240 pictures, not 247). Its DURATION tag is not used: it is an end
    time rather than a length, and a truncated copy still carries the full one.
    Same child ownership as `_probe_once`: registered, stoppable, reaped here.
    """
    start = timeline.get("video_start")
    length = timeline.get("format_duration")
    if (start is None or length is None or length <= 0
            or length > PICTURE_END_WHOLE_READ_SECONDS):
        return 0.0
    read = _read_packet_end(tools, path, should_stop, register)
    if read is None:
        return 0.0
    picture = round(read[1] - start, 6)
    # Not longer than the whole file: the bound `sequence_plan` also keeps.
    # Measured against the duration, not start + duration: a Matroska file
    # whose sound starts 23 ms early reports start -0.023 and a duration
    # counted from zero. ffprobe prints microseconds, hence the 1e-6.
    if picture <= 0 or picture > length + 1e-6:
        return 0.0
    return picture


def _read_packet_end(tools: Tools, path: Path, should_stop=None,
                     register=None) -> tuple[int, float] | None:
    """(packets, last packet end) from one owned ffprobe read of all the
    video packets, from the start of the file without a seek; None unless
    that read was clean and complete within its bounds."""
    if should_stop is not None and should_stop():
        return None
    args = [str(tools.ffprobe), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "packet=pts_time,duration_time", "-of", "csv=p=0",
            str(path)]
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    except OSError:
        return None
    if register is not None:
        register(proc)

    seen = {"packets": 0, "last": None, "bad": False, "over": False,
            "complained": False}

    def read_packets():
        # Parsed as it arrives: only the running maximum is kept, so memory
        # does not grow with however much the demuxer reads.
        for raw in iter(lambda: proc.stdout.readline(PICTURE_END_MAX_LINE), b""):
            if seen["over"]:
                continue            # drained, so the child is never blocked
            fields = [one for one in
                      raw.decode("ascii", "replace").strip().split(",") if one]
            if not fields:
                continue
            seen["packets"] += 1
            if seen["packets"] > PICTURE_END_MAX_PACKETS:
                seen["over"] = True
                continue
            when = _finite(fields[0])
            span = _finite(fields[1]) if len(fields) == 2 else None
            if when is None or span is None or span <= 0:
                seen["bad"] = True
                continue
            if seen["last"] is None or when + span > seen["last"]:
                seen["last"] = when + span

    def read_errors():
        for raw in iter(lambda: proc.stderr.read(4096), b""):
            if raw.strip():
                seen["complained"] = True

    readers = [threading.Thread(target=read_packets, daemon=True),
               threading.Thread(target=read_errors, daemon=True)]
    for reader in readers:
        reader.start()
    finished = False
    try:
        deadline = time.monotonic() + PICTURE_END_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if should_stop is not None and should_stop():
                break
            if seen["over"]:
                break
            if proc.poll() is not None and not any(
                    reader.is_alive() for reader in readers):
                finished = True
                break
            time.sleep(_PROBE_WAIT_SECONDS)
    finally:
        stop_process(proc)
        for reader in readers:
            reader.join(timeout=TERMINATE_SECONDS)
        if register is not None:
            register(None)

    if (not finished or proc.returncode != 0 or seen["over"] or seen["bad"]
            or seen["complained"] or not seen["packets"]
            or seen["last"] is None):
        return None
    return seen["packets"], seen["last"]


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


# A test encode is three frames of 320x240. Anything still going after this is
# a driver that has hung rather than an encoder that is merely slow.
PROBE_SECONDS = 45

# Hardware H.264 encoders, best first. Whichever one this machine can actually
# run is the one the app offers.
HW_ENCODERS = [
    ("h264_nvenc", "NVIDIA NVENC"),
    ("h264_qsv", "Intel Quick Sync"),
    ("h264_amf", "AMD AMF"),
    ("h264_videotoolbox", "Apple VideoToolbox"),
]


def _encoder_runs(tools: Tools, name: str, register=None,
                  should_stop=None) -> bool:
    """Try a token encode.

    An encoder being compiled into ffmpeg says nothing about whether the
    hardware is present: a build with NVENC support still fails on a machine
    with no NVIDIA card, so the only reliable test is to run it once.

    Popen rather than run_hidden because the caller has to be able to reach the
    process: a test encode can sit there for the best part of a minute, and
    somebody closing the window in the meantime should not be made to wait for
    it. `register` is handed the process while it runs and None afterwards.
    """
    args = [
        str(tools.ffmpeg), "-hide_banner", "-v", "error", "-nostdin",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=0.2",
        "-c:v", name, "-frames:v", "3", "-f", "null", "-",
    ]
    if should_stop is not None and should_stop():
        return False
    try:
        proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
    except OSError:
        return False
    if register is not None:
        register(proc)
    try:
        deadline = time.monotonic() + PROBE_SECONDS
        while True:
            if should_stop is not None and should_stop():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                return proc.wait(
                    timeout=min(_PROBE_WAIT_SECONDS, remaining)) == 0
            except subprocess.TimeoutExpired:
                continue
    finally:
        # Includes cancellation and timeout: the worker that launched this
        # child is the one that waits for termination and reaps it.
        stop_process(proc)
        if register is not None:
            register(None)


def detect_hardware_encoder(
    tools: Tools, encoders: set[str] | None = None,
    should_stop=None, register=None,
) -> tuple[str, str] | None:
    """The hardware encoder this machine can really use, or None.

    `should_stop` is consulted before each candidate and `register` is passed
    through to the test encode, so a caller that is going away can stop this
    between candidates and part way through one.
    """
    if encoders is None:
        encoders = available_encoders(tools)
    for name, label in HW_ENCODERS:
        if should_stop is not None and should_stop():
            return None
        if name in encoders and _encoder_runs(
            tools, name, register, should_stop,
        ):
            return name, label
    return None
