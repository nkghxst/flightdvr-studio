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

"""The environment handed to a system ffmpeg/ffprobe child.

A PyInstaller build points the dynamic loader at its own bundled libraries and
saves the pre-launch value in ``<VAR>_ORIG``. A system ffmpeg spawned with that
inherited path loads the bundle's stale libstdc++/libz first and dies before it
reads a frame, which the scan reports as "No readable video files found here".
``child_env`` undoes that inheritance so the child links against the system
libraries it was built for. These oracles fail if it stops doing so.
"""

from __future__ import annotations

import pytest

from flightdvr.media import child_env


def _freeze(monkeypatch, frozen: bool) -> None:
    monkeypatch.setattr("flightdvr.media.sys.frozen", frozen, raising=False)


def test_frozen_run_restores_the_saved_library_path(monkeypatch):
    """The bundle's path is replaced by the value it displaced, not merely ours."""
    _freeze(monkeypatch, True)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/bundle/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/system/lib")

    env = child_env()

    assert env["LD_LIBRARY_PATH"] == "/opt/system/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in env


def test_frozen_run_without_an_original_drops_the_bundle_path(monkeypatch):
    """No saved value means there was none to begin with: leave the loader bare."""
    _freeze(monkeypatch, True)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/bundle/_internal")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

    env = child_env()

    assert "LD_LIBRARY_PATH" not in env


def test_frozen_run_cleans_the_macos_loader_path_too(monkeypatch):
    """The macOS app carries the same hazard under a different variable name."""
    _freeze(monkeypatch, True)
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/tmp/App.app/Contents/Frameworks")
    monkeypatch.setenv("DYLD_LIBRARY_PATH_ORIG", "/usr/local/lib")

    env = child_env()

    assert env["DYLD_LIBRARY_PATH"] == "/usr/local/lib"
    assert "DYLD_LIBRARY_PATH_ORIG" not in env


def test_source_run_leaves_a_users_own_library_path_untouched(monkeypatch):
    """`python -m flightdvr` is not frozen; a user's own path is not ours to strip."""
    _freeze(monkeypatch, False)
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/home/pilot/custom/lib")

    env = child_env()

    assert env["LD_LIBRARY_PATH"] == "/home/pilot/custom/lib"


def test_a_copy_is_returned_not_the_live_environment(monkeypatch):
    """Callers pass this straight to subprocess; mutating it must not leak back."""
    _freeze(monkeypatch, True)
    monkeypatch.setenv("FLIGHTDVR_MARKER", "keep")

    env = child_env()
    env["FLIGHTDVR_MARKER"] = "changed"

    import os

    assert os.environ["FLIGHTDVR_MARKER"] == "keep"
