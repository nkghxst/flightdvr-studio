#!/usr/bin/env python3
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

"""What the audio output brought into a built bundle, read from its bytes.

    python packaging/check_qt_multimedia.py BUNDLE_DIR [REPORT_JSON]

The PCM audio output needs Qt Multimedia, and PySide6 ships that with a
multimedia backend plugin and, alongside it, FFmpeg's shared libraries
(libavcodec and friends) built by Qt. Those libraries are not the app's
decoder — ffmpeg runs as a separate program for that — but they are
redistributed in the package, so what they are has to be known rather than
assumed.

This lists every multimedia backend plugin and every FFmpeg shared library in
the bundle and reads, from each library's own bytes, the licence string FFmpeg
compiles in ("LGPL version 2.1 or later" and so on) and its version tag. It
fails when:

- no multimedia backend plugin is in the bundle (Listen cannot work);
- a library declares anything other than an LGPL licence, or says it is
  nonfree, or declares nothing readable.

It opens no device and runs nothing from the bundle. Text and bytes only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LIBRARY = re.compile(
    r"^(?:lib)?(avcodec|avformat|avutil|avfilter|avdevice|swresample|swscale|postproc)"
    r"(?:[-.][0-9][0-9.]*)?\.(?:dll|dylib|so(?:\.[0-9]+)*)$", re.IGNORECASE)
LICENCE = re.compile(rb"\b(L?GPL) version ([0-9.]+) or later")
NONFREE = re.compile(rb"nonfree and unredistributable")
VERSION = re.compile(rb"\bn([0-9]+\.[0-9]+(?:\.[0-9]+)?)/lib")


def plugins(bundle: Path) -> list[Path]:
    return sorted(p for p in bundle.rglob("*")
                  if p.is_file() and p.parent.name == "multimedia")


def libraries(bundle: Path) -> list[Path]:
    return sorted(p for p in bundle.rglob("*")
                  if p.is_file() and LIBRARY.match(p.name))


def read_library(path: Path) -> dict:
    data = path.read_bytes()
    licences = sorted({(kind.decode(), version.decode())
                       for kind, version in LICENCE.findall(data)})
    versions = sorted({v.decode() for v in VERSION.findall(data)})
    return {"file": path.name, "bytes": len(data),
            "licences": [f"{kind} {version}+" for kind, version in licences],
            "nonfree": bool(NONFREE.search(data)), "versions": versions}


def check(bundle: Path) -> tuple[dict, list[str]]:
    found_plugins = plugins(bundle)
    found = [read_library(path) for path in libraries(bundle)]
    failures = []
    if not found_plugins:
        failures.append("no Qt multimedia backend plugin in the bundle")
    for entry in found:
        kinds = {licence.split()[0] for licence in entry["licences"]}
        if entry["nonfree"]:
            failures.append(f"{entry['file']} says it is nonfree")
        if not kinds:
            failures.append(f"{entry['file']} declares no licence")
        elif kinds != {"LGPL"}:
            failures.append(f"{entry['file']} declares {entry['licences']}")
    report = {"bundle": str(bundle),
              "plugins": [str(p.relative_to(bundle)) for p in found_plugins],
              "ffmpeg_libraries": found, "failures": failures}
    return report, failures


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    bundle = Path(argv[0])
    report, failures = check(bundle)
    for plugin in report["plugins"]:
        print(f"  multimedia plugin  {plugin}")
    for entry in report["ffmpeg_libraries"]:
        print(f"  {entry['file']:<24} {', '.join(entry['licences']) or 'no licence'}"
              f"  {', '.join('n' + v for v in entry['versions']) or 'version unread'}")
    if len(argv) == 2:
        Path(argv[1]).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    for failure in failures:
        print(f"  FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
