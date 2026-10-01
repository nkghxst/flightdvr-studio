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

"""The environment handed to each child process (#131, from Rory Lambert).

A frozen Linux build points the dynamic loader at its own bundled libraries
and saves the pre-launch value in ``LD_LIBRARY_PATH_ORIG``. A *system* ffmpeg
that inherits the bundle's path loads the bundle's stale libstdc++/libz and
dies before it reads a frame, which a scan reports as "No readable video files
found here". ``child_env`` undoes that for programs from outside the bundle,
leaves a program shipped inside the bundle with the path it needs, and touches
nothing on other platforms or in a source run.

The first findings on the original change were that macOS lost a person's own
``DYLD_LIBRARY_PATH`` (macOS never sets an ``_ORIG``) and that a bundled ffmpeg
lost its own libraries. Both are tested against the state each platform really
produces, and against the original helper as a negative control.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

import flightdvr.media as media
from flightdvr.media import child_env

PACKAGE = Path(media.__file__).resolve().parent


def _frozen(monkeypatch, tmp_path, platform: str) -> Path:
    """A frozen app of the given platform whose bundle lives in tmp_path."""
    bundle = tmp_path / "bundle"
    (bundle / "ffmpeg").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "executable", str(bundle / "flightdvr-studio"))
    for name in ("LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG",
                 "DYLD_LIBRARY_PATH", "DYLD_LIBRARY_PATH_ORIG"):
        monkeypatch.delenv(name, raising=False)
    return bundle


def _system_ffmpeg(tmp_path) -> Path:
    system = tmp_path / "usr" / "bin"
    system.mkdir(parents=True)
    exe = system / "ffmpeg"
    exe.write_bytes(b"")
    return exe


def _bundled_ffmpeg(bundle: Path) -> Path:
    exe = bundle / "ffmpeg" / "ffmpeg"
    exe.write_bytes(b"")
    return exe


# -- Linux, frozen: a system program gets the system's loader path back ---------


def test_a_system_program_gets_the_saved_library_path_back(monkeypatch, tmp_path):
    bundle = _frozen(monkeypatch, tmp_path, "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/system/lib")

    env = child_env(_system_ffmpeg(tmp_path))

    assert env["LD_LIBRARY_PATH"] == "/opt/system/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in env


def test_a_system_program_with_nothing_saved_gets_no_loader_path(monkeypatch, tmp_path):
    """The bootloader saves _ORIG only when there was a value; none means none."""
    bundle = _frozen(monkeypatch, tmp_path, "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))

    env = child_env(_system_ffmpeg(tmp_path))

    assert "LD_LIBRARY_PATH" not in env
    assert "LD_LIBRARY_PATH_ORIG" not in env


def test_a_bundled_program_keeps_the_bundles_libraries(monkeypatch, tmp_path):
    """A bundled ffmpeg was collected with those libraries and needs them."""
    bundle = _frozen(monkeypatch, tmp_path, "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/system/lib")
    exe = _bundled_ffmpeg(bundle)
    assert media.is_bundled(exe)

    env = child_env(exe)

    assert env["LD_LIBRARY_PATH"] == str(bundle)
    assert env["LD_LIBRARY_PATH_ORIG"] == "/opt/system/lib"


def test_which_one_is_decided_by_where_the_program_resolves(monkeypatch, tmp_path):
    """Path spelling does not decide it: `..` into the bundle is still bundled,
    and a bare name is found on PATH before it is judged."""
    bundle = _frozen(monkeypatch, tmp_path, "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))
    bundled = _bundled_ffmpeg(bundle)
    system = _system_ffmpeg(tmp_path)

    roundabout = bundle / "ffmpeg" / ".." / "ffmpeg" / "ffmpeg"
    assert child_env(roundabout)["LD_LIBRARY_PATH"] == str(bundle)

    found = {"ffmpeg": None}
    monkeypatch.setattr(media.shutil, "which",
                        lambda name, path=None: found[name])
    found["ffmpeg"] = str(bundled)
    assert child_env("ffmpeg")["LD_LIBRARY_PATH"] == str(bundle)
    found["ffmpeg"] = str(system)
    assert "LD_LIBRARY_PATH" not in child_env("ffmpeg")
    found["ffmpeg"] = None
    assert "LD_LIBRARY_PATH" not in child_env("ffmpeg")


def test_external_openers_are_system_programs(monkeypatch, tmp_path):
    """xdg-open and an installed VLC never come from the bundle."""
    bundle = _frozen(monkeypatch, tmp_path, "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/system/lib")
    opener = tmp_path / "usr" / "bin" / "xdg-open"
    opener.parent.mkdir(parents=True)
    opener.write_bytes(b"")
    monkeypatch.setattr(media.shutil, "which", lambda name, path=None: str(opener))

    assert child_env("xdg-open")["LD_LIBRARY_PATH"] == "/opt/system/lib"
    assert child_env(Path("/usr/bin/vlc"))["LD_LIBRARY_PATH"] == "/opt/system/lib"


# -- Everything else is left exactly as it is ---------------------------------


def test_macos_keeps_a_persons_own_dyld_path(monkeypatch, tmp_path):
    """The macOS bootloader rewrites library references and saves no _ORIG,
    so a DYLD_LIBRARY_PATH in a frozen macOS app is the person's own."""
    _frozen(monkeypatch, tmp_path, "darwin")
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/opt/custom-ffmpeg/lib")

    env = child_env(_system_ffmpeg(tmp_path))

    assert env["DYLD_LIBRARY_PATH"] == "/opt/custom-ffmpeg/lib"
    assert "DYLD_LIBRARY_PATH_ORIG" not in env


def test_windows_is_left_alone(monkeypatch, tmp_path):
    bundle = _frozen(monkeypatch, tmp_path, "win32")
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/system/lib")

    env = child_env(_system_ffmpeg(tmp_path))

    assert env["LD_LIBRARY_PATH"] == str(bundle)
    assert env["LD_LIBRARY_PATH_ORIG"] == "/opt/system/lib"


def test_a_source_run_leaves_a_persons_library_path_untouched(monkeypatch, tmp_path):
    """`python -m flightdvr` is not frozen; nothing in its environment is ours."""
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/home/pilot/custom/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/elsewhere")

    env = child_env(_system_ffmpeg(tmp_path))

    assert env["LD_LIBRARY_PATH"] == "/home/pilot/custom/lib"
    assert env["LD_LIBRARY_PATH_ORIG"] == "/elsewhere"


def test_each_caller_gets_a_copy_and_the_live_environment_is_untouched(
        monkeypatch, tmp_path):
    bundle = _frozen(monkeypatch, tmp_path, "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/system/lib")
    monkeypatch.setenv("FLIGHTDVR_MARKER", "keep")
    before = dict(os.environ)
    exe = _system_ffmpeg(tmp_path)

    env = child_env(exe)
    env["FLIGHTDVR_MARKER"] = "changed"

    assert dict(os.environ) == before
    assert child_env(exe) is not env


# -- Every launch carries it ---------------------------------------------------


def test_run_hidden_hands_its_program_the_childs_environment(monkeypatch, tmp_path):
    bundle = _frozen(monkeypatch, tmp_path, "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))
    exe = _system_ffmpeg(tmp_path)
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs, args=args)
        return None

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    media.run_hidden([str(exe), "-version"])

    assert seen["args"] == [str(exe), "-version"]
    assert "LD_LIBRARY_PATH" not in seen["env"]


def _program(node: ast.expr) -> str:
    """The program expression of an argv, with a `str(...)` wrapper removed."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "str" and len(node.args) == 1):
        node = node.args[0]
    return ast.unparse(node)


def test_every_launch_in_the_package_passes_its_own_programs_environment():
    """Read from the source: no subprocess launch without `env=child_env(...)`,
    and the program it is asked about is the one being launched."""
    launches = []
    for path in sorted(PACKAGE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"
                    and node.func.attr in ("Popen", "run", "call",
                                           "check_call", "check_output")):
                continue
            where = f"{path.name}:{node.lineno}"
            env = [k.value for k in node.keywords if k.arg == "env"]
            assert env, f"{where} launches without env="
            call = env[0]
            assert (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "child_env" and len(call.args) == 1), where
            argv = node.args[0]
            asked = ast.unparse(call.args[0])
            if isinstance(argv, ast.List):
                assert asked == _program(argv.elts[0]), where
            else:
                assert asked == f"{ast.unparse(argv)}[0]", where
            launches.append(path.name)
    # The current launch-bearing modules, each with at least one launch.
    assert set(launches) == {
        "audio_export.py", "audio_reader.py", "external.py", "jobs.py",
        "media.py", "player.py", "stills.py", "thumbs.py", "trim.py", "ui.py"}


# -- Negative control: the original helper fails both findings ------------------


def _original_child_env() -> dict[str, str]:
    """PR131's helper at 7906c66, verbatim apart from the name."""
    env = os.environ.copy()
    if not getattr(sys, "frozen", False):
        return env
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        original = env.pop(var + "_ORIG", None)
        if original:
            env[var] = original
        else:
            env.pop(var, None)
    return env


def test_the_original_helper_fails_what_these_tests_check(monkeypatch, tmp_path):
    """So the two findings are real and the tests above can tell."""
    _frozen(monkeypatch, tmp_path, "darwin")
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/opt/custom-ffmpeg/lib")
    assert "DYLD_LIBRARY_PATH" not in _original_child_env()
    assert child_env(_system_ffmpeg(tmp_path))["DYLD_LIBRARY_PATH"] == (
        "/opt/custom-ffmpeg/lib")

    bundle = _frozen(monkeypatch, tmp_path / "linux", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))
    assert "LD_LIBRARY_PATH" not in _original_child_env()
    assert child_env(_bundled_ffmpeg(bundle))["LD_LIBRARY_PATH"] == str(bundle)
