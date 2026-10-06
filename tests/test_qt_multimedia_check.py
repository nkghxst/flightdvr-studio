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

"""The package check for what Qt Multimedia brings into a bundle. No Qt."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_qt_multimedia", ROOT / "packaging" / "check_qt_multimedia.py")
check_qt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_qt)

LGPL = b"\0FFmpeg n7.1.3/libavutil/buffer.c\0LGPL version 2.1 or later\0"


def bundle(tmp_path, libraries: dict[str, bytes], plugin: bool = True) -> Path:
    root = tmp_path / "bundle"
    (root / "_internal" / "PySide6" / "plugins" / "multimedia").mkdir(parents=True)
    if plugin:
        (root / "_internal" / "PySide6" / "plugins" / "multimedia" /
         "ffmpegmediaplugin.dll").write_bytes(b"plugin")
    for name, data in libraries.items():
        (root / "_internal" / "PySide6" / name).write_bytes(data)
    # The app's own ffmpeg pair is a program, not a library, and is not read.
    (root / "_internal" / "ffmpeg").mkdir()
    (root / "_internal" / "ffmpeg" / "ffmpeg.exe").write_bytes(b"GPL version 3 or later")
    return root


def test_lgpl_libraries_and_a_backend_pass(tmp_path):
    root = bundle(tmp_path, {"avcodec-61.dll": LGPL, "libavutil.so.59": LGPL,
                             "libswscale.8.dylib": LGPL})
    report, failures = check_qt.check(root)
    assert failures == []
    assert [e["file"] for e in report["ffmpeg_libraries"]] == [
        "avcodec-61.dll", "libavutil.so.59", "libswscale.8.dylib"]
    assert all(e["licences"] == ["LGPL 2.1+"] and e["versions"] == ["7.1.3"]
               for e in report["ffmpeg_libraries"])
    assert check_qt.main([str(root), str(tmp_path / "r.json")]) == 0
    assert json.loads((tmp_path / "r.json").read_text())["failures"] == []


@pytest.mark.parametrize("data, why", [
    (b"GPL version 3 or later", "declares ['GPL 3+']"),
    (LGPL + b"nonfree and unredistributable", "says it is nonfree"),
    (b"no licence here", "declares no licence"),
])
def test_anything_but_lgpl_fails(tmp_path, data, why):
    root = bundle(tmp_path, {"avcodec-61.dll": LGPL, "avformat-61.dll": data})
    _report, failures = check_qt.check(root)
    assert any(f.startswith("avformat-61.dll") and why in f for f in failures)
    assert check_qt.main([str(root)]) == 1


def test_a_bundle_without_a_backend_fails(tmp_path):
    root = bundle(tmp_path, {}, plugin=False)
    _report, failures = check_qt.check(root)
    assert failures == ["no Qt multimedia backend plugin in the bundle"]
