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
import hashlib
import os
import re
import struct
import sys
from pathlib import Path

LIBRARY = re.compile(
    r"^(?:lib)?(avcodec|avformat|avutil|avfilter|avdevice|swresample|swscale|postproc)"
    r"(?:[-.][0-9][0-9.]*)?\.(?:dll|dylib|so(?:\.[0-9]+)*)$", re.IGNORECASE)
LICENCE = re.compile(rb"\b(L?GPL) version ([0-9.]+) or later")
NONFREE = re.compile(rb"nonfree and unredistributable")
VERSION = re.compile(rb"\bn([0-9]+\.[0-9]+(?:\.[0-9]+)?)/lib")
EXPECTED = {"avcodec", "avformat", "avutil", "swresample", "swscale"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def architecture(data: bytes) -> list[str]:
    if data.startswith(b"MZ") and len(data) >= 0x40:
        offset = struct.unpack_from("<I", data, 0x3c)[0]
        if offset + 6 <= len(data) and data[offset:offset + 4] == b"PE\0\0":
            return [{0x8664: "x86_64", 0xaa64: "arm64"}.get(
                struct.unpack_from("<H", data, offset + 4)[0], "unknown")]
    if data.startswith(b"\x7fELF") and len(data) >= 20:
        order = "<" if data[5] == 1 else ">"
        return [{62: "x86_64", 183: "arm64"}.get(
            struct.unpack_from(order + "H", data, 18)[0], "unknown")]
    if len(data) >= 8:
        magic = data[:4]
        if magic in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
            order = ">" if magic == b"\xca\xfe\xba\xbe" else "<"
            count = struct.unpack_from(order + "I", data, 4)[0]
            if count > 16 or len(data) < 8 + count * 20:
                return ["unknown"]
            return sorted({{0x01000007: "x86_64", 0x0100000c: "arm64"}.get(
                struct.unpack_from(order + "I", data, 8 + i * 20)[0], "unknown")
                for i in range(count)})
        if magic in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"):
            order = "<" if magic == b"\xcf\xfa\xed\xfe" else ">"
            return [{0x01000007: "x86_64", 0x0100000c: "arm64"}.get(
                struct.unpack_from(order + "I", data, 4)[0], "unknown")]
    return ["unknown"]


def family(name: str) -> str | None:
    match = LIBRARY.match(name)
    return match.group(1).lower() if match else None


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
    return {"file": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "architecture": architecture(data),
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
        elif entry["licences"] != ["LGPL 2.1+"]:
            failures.append(f"{entry['file']} declares unexpected LGPL version {entry['licences']}")
    report = {"bundle": str(bundle),
              "plugins": [str(p.relative_to(bundle)) for p in found_plugins],
              "ffmpeg_libraries": found, "failures": failures}
    return report, failures


def binding_check(bundle: Path, report: dict, inputs: dict, collection: dict,
                  pre_sign: dict | None = None) -> list[str]:
    """Check the final payload against the wheel and PyInstaller selection receipts."""
    failures = []
    platform = inputs.get("platform")
    if platform != collection.get("platform") or platform not in ("windows", "linux", "macos"):
        return ["wheel and collection platforms differ"]
    expected_arch = "arm64" if platform == "macos" else "x86_64"
    if pre_sign is not None and (platform != "macos" or pre_sign.get("platform") != "macos"
                                 or pre_sign.get("failures")):
        return ["invalid macOS pre-sign inventory"]
    pre_files = {item["path"]: item for item in pre_sign.get("selected_files", [])} if pre_sign else {}
    selected = {item["path"]: dict(item) for item in collection.get("files", [])}
    selected_links = {item["path"]: dict(item) for item in collection.get("links", [])}
    if len(selected) != len(collection.get("files", [])) or not selected:
        failures.append("empty or duplicate PyInstaller multimedia selection")
    if len(selected_links) != len(collection.get("links", [])) or set(selected) & set(selected_links):
        failures.append("duplicate or overlapping PyInstaller multimedia link selection")
    if pre_sign is not None and set(pre_files) != set(selected):
        failures.append("pre-sign multimedia path set differs from collection")
    if pre_sign is not None and {x["path"] for x in pre_sign.get("selected_links", [])} != set(selected_links):
        failures.append("pre-sign multimedia link set differs from collection")
    wheel_hashes = {w["package"]: w["sha256"] for w in inputs.get("wheels", [])}
    wheel_files = {(x["package"], x["file"], x["sha256"])
                   for x in inputs.get("files", [])}
    actual_paths = {}
    actual_links = {}
    for path in bundle.rglob("*"):
        if not (path.is_file() or path.is_symlink()) or not (
                ("multimedia" in str(path).lower() and path.suffix.lower() not in (".json", ".md"))
                or "ffmpegstub" in path.name.lower() or family(path.name)):
            continue
        relative = path.relative_to(bundle).as_posix()
        if path.is_symlink():
            resolved = path.resolve()
            if not resolved.is_relative_to(bundle.resolve()) or not resolved.exists():
                failures.append(f"broken or escaping multimedia symlink: {relative}")
                continue
            actual_links[relative] = path
            continue
        actual_paths[relative] = path
    missing_selected = []
    for path, item in selected.items():
        # PyInstaller names Windows/Linux COLLECT entries relative to _internal;
        # macOS BUNDLE places them in Contents/Frameworks. Match those exact
        # roots, not any same-basename suffix elsewhere in the bundle.
        names = {prefix + path for prefix in ("", "_internal/", "Contents/Frameworks/",
                                               "Contents/MacOS/_internal/")}
        candidates = [(n, p) for n, p in actual_paths.items() if n in names]
        if not candidates:
            missing_selected.append((path, item))
            continue
        if len(candidates) != 1:
            failures.append(f"selected multimedia path missing/ambiguous: {path}")
            continue
        relative, payload = candidates[0]
        item["final_path"] = relative
        item["final_sha256"] = sha256(payload)
        item["final_bytes"] = payload.stat().st_size
        item["final_architecture"] = architecture(payload.read_bytes()[:4096])
        if item.get("kind", "BINARY") != "DATA" and expected_arch not in item["final_architecture"]:
            failures.append(f"wrong or unreadable architecture: {relative}")
        if ((item["wheel_package"], item["wheel_file"], item["source_sha256"]) not in wheel_files
                or wheel_hashes.get(item["wheel_package"]) != item["wheel_sha256"]):
            failures.append(f"unrecognized wheel origin: {relative}")
        if item["final_sha256"] != item["source_sha256"]:
            item["transformed"] = True
            prior = pre_files.get(path)
            if (prior is None or prior.get("final_sha256") != item["source_sha256"]
                    or prior.get("source_sha256") != item["source_sha256"]
                    or prior.get("final_path") != relative
                    or prior.get("final_architecture") != item["final_architecture"]):
                failures.append(f"unreconciled PyInstaller/signing transformation: {relative}")
            else:
                item["pre_sign_sha256"] = prior["final_sha256"]
                item["transformation"] = "ad-hoc codesign after verified wheel-byte collection"
        elif pre_sign is not None:
            prior = pre_files.get(path)
            if prior is None or prior.get("final_sha256") != item["source_sha256"]:
                failures.append(f"missing or mismatched pre-sign receipt: {relative}")
    for path, item in missing_selected:
        # PyInstaller may coalesce two selected destinations that carry the
        # same wheel file bytes. Name the surviving path explicitly; do not
        # turn an absent library or a different wheel file into a pass.
        matches = [other for other in selected.values()
                   if other is not item and other.get("final_path")
                   and other["wheel_package"] == item["wheel_package"]
                   and other["wheel_file"] == item["wheel_file"]
                   and other["source_sha256"] == item["source_sha256"]
                   and other["final_sha256"] == item["source_sha256"]]
        if len(matches) != 1:
            failures.append(f"selected multimedia path missing/ambiguous: {path}")
            continue
        survivor = matches[0]
        item.update(final_path=survivor["final_path"],
                    final_sha256=survivor["final_sha256"],
                    final_bytes=survivor["final_bytes"],
                    final_architecture=survivor["final_architecture"],
                    coalesced_to=survivor["path"])
    bound_paths = {item.get("final_path") for item in selected.values()}
    for relative in actual_paths.keys() - bound_paths:
        failures.append(f"shipped multimedia file lacks wheel collection origin: {relative}")
    bound_files = {path.resolve() for path in actual_paths.values()
                   if path.relative_to(bundle).as_posix() in bound_paths}
    bound_link_paths = set()
    final_links = []
    for path, item in selected_links.items():
        names = {prefix + path for prefix in ("", "_internal/", "Contents/Frameworks/",
                                               "Contents/MacOS/_internal/")}
        candidates = [(n, p) for n, p in actual_links.items() if n in names]
        if len(candidates) != 1:
            failures.append(f"selected multimedia symlink missing/ambiguous: {path}")
            continue
        relative, link = candidates[0]
        bound_link_paths.add(relative)
        resolved = link.resolve()
        if os.readlink(link).replace("\\", "/") != item["target"]:
            failures.append(f"multimedia symlink target changed: {relative}")
        if not (resolved in bound_files if resolved.is_file() else
                resolved.is_dir() and any(p.is_relative_to(resolved) for p in bound_files)):
            failures.append(f"multimedia symlink lacks selected wheel content: {relative}")
        final_links.append(dict(item, final_path=relative))
    for relative in actual_links.keys() - bound_link_paths:
        failures.append(f"shipped multimedia symlink lacks collection origin: {relative}")
    found_families = {family(p.name) for p in actual_paths.values()}
    missing = EXPECTED - found_families
    if missing:
        failures.append("missing FFmpeg shared library families: " + ", ".join(sorted(missing)))
    plugins = {p.name.lower() for p in actual_paths.values() if p.parent.name == "multimedia"}
    if not any("ffmpegmediaplugin" in name for name in plugins):
        failures.append("missing FFmpeg multimedia backend")
    readable_versions = {version for item in report.get("ffmpeg_libraries", [])
                         for version in item["versions"]}
    if not readable_versions or readable_versions != {"7.1.5"}:
        failures.append("FFmpeg library version evidence is absent or differs from 7.1.5")
    if not inputs.get("wheels") or inputs.get("release_ready") is not False:
        failures.append("invalid wheel receipt")
    report["platform"] = platform
    report["wheel_inputs"] = inputs["wheels"]
    report["selected_files"] = sorted(selected.values(), key=lambda x: x["path"])
    report["selected_links"] = sorted(final_links, key=lambda x: x["path"])
    report["source_complete"] = False
    report["release_ready"] = False
    return failures


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2, 5, 6) or (len(argv) >= 5 and argv[2] != "--inputs"):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    bundle = Path(argv[0])
    report, failures = check(bundle)
    if len(argv) >= 5:
        if argv[4].startswith("--collection="):
            collection_path = argv[4].split("=", 1)[1]
        else:
            print("expected --collection=PATH", file=sys.stderr)
            return 2
        pre_sign_path = None
        if len(argv) == 6:
            if not argv[5].startswith("--signed-pre="):
                print("expected --signed-pre=PATH", file=sys.stderr)
                return 2
            pre_sign_path = Path(argv[5].split("=", 1)[1])
        pre_sign = json.loads(pre_sign_path.read_text()) if pre_sign_path else None
        failures += binding_check(bundle, report, json.loads(Path(argv[3]).read_text()),
                                  json.loads(Path(collection_path).read_text()), pre_sign)
        if pre_sign_path:
            report["pre_sign_report_sha256"] = sha256(pre_sign_path)
        report["failures"] = failures
    for plugin in report["plugins"]:
        print(f"  multimedia plugin  {plugin}")
    for entry in report["ffmpeg_libraries"]:
        print(f"  {entry['file']:<24} {', '.join(entry['licences']) or 'no licence'}"
              f"  {', '.join('n' + v for v in entry['versions']) or 'version unread'}")
    if len(argv) in (2, 5, 6):
        Path(argv[1]).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    for failure in failures:
        print(f"  FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
