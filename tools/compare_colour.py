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

"""Reproduce the colour-mode comparison on your own footage.

The default colour mode was chosen by measurement rather than taste, and this
script is how that measurement was made. Point it at one of your own clips and
it will encode a single frame through each mode, render every result to RGB,
and report how far each one drifts from the source.

    python tools/compare_colour.py "F:\\FPV clips\\hdz_022.ts"

Higher PSNR means closer to how the source actually looks. A large maximum
channel delta means the picture has been shifted, not merely re-encoded.
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flightdvr.media import NO_WINDOW, find_tools, probe  # noqa: E402
from flightdvr.presets import COLOUR_MODES, colour_filters  # noqa: E402


def run(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True, creationflags=NO_WINDOW)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg failed:\n{result.stderr[-800:]}")


def rgb_of(ffmpeg: str, source: Path, seek: float, out: Path, extra_vf: str | None) -> None:
    args = [ffmpeg, "-y", "-v", "error", "-ss", f"{seek:.2f}", "-i", str(source), "-frames:v", "1"]
    args += ["-vf", extra_vf or "format=rgb24"]
    args += ["-f", "rawvideo", str(out)]
    run(args)


def compare(reference: bytes, other: bytes) -> tuple[float, int]:
    size = min(len(reference), len(other))
    if size == 0:
        return 0.0, 255
    total = 0
    worst = 0
    samples = 0
    for i in range(0, size, 5):
        delta = reference[i] - other[i]
        total += delta * delta
        worst = max(worst, abs(delta))
        samples += 1
    mse = total / samples
    psnr = 10 * math.log10(65025 / mse) if mse else 99.0
    return psnr, worst


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip", type=Path)
    parser.add_argument("--at", type=float, default=None, help="seconds into the clip")
    args = parser.parse_args()

    if not args.clip.exists():
        raise SystemExit(f"No such file: {args.clip}")

    tools = find_tools()
    info = probe(tools, args.clip)
    if info.error:
        raise SystemExit(f"Could not read clip: {info.error}")

    seek = args.at if args.at is not None else max(1.0, info.duration * 0.15)
    ffmpeg = str(tools.ffmpeg)

    print(f"\n{args.clip.name}")
    print(f"  {info.format_label}, pix_fmt={info.pix_fmt}, range={info.color_range or 'unset'}, "
          f"primaries={info.color_primaries or 'unset'}, transfer={info.color_transfer or 'unset'}")
    print(f"  full range: {info.is_full_range}\n")

    with tempfile.TemporaryDirectory() as temporary:
        tmp = Path(temporary)
        reference_raw = tmp / "ref.raw"
        rgb_of(ffmpeg, args.clip, seek, reference_raw, None)
        reference = reference_raw.read_bytes()

        print(f"{'mode':28s} {'PSNR':>9s} {'max delta':>10s}   tags written")
        print("-" * 78)
        for key, label, _ in COLOUR_MODES:
            filters = colour_filters(key, info, "yuv420p")
            encoded = tmp / f"{key}.mp4"
            run([
                ffmpeg, "-y", "-v", "error", "-ss", f"{seek:.2f}", "-i", str(args.clip),
                "-frames:v", "1", "-vf", ",".join(filters),
                # Lossless, so the only difference measured is the colour handling.
                "-c:v", "libx264", "-preset", "veryslow", "-qp", "0", "-an", str(encoded),
            ])
            rendered = tmp / f"{key}.raw"
            rgb_of(ffmpeg, encoded, 0.0, rendered, None)
            psnr, worst = compare(reference, rendered.read_bytes())

            tags = subprocess.run(
                [str(tools.ffprobe), "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=color_range,color_space,color_primaries",
                 "-of", "csv=p=0", str(encoded)],
                capture_output=True, text=True, creationflags=NO_WINDOW,
            ).stdout.strip()

            verdict = "identical" if psnr > 55 else ("excellent" if psnr > 45 else "shifted")
            print(f"{label[:28]:28s} {psnr:6.2f} dB {worst:10d}   {tags}   {verdict}")

    print("\nAnything below roughly 45 dB is a visible colour change, not just re-encoding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
