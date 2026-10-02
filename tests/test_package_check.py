"""`--check-export`: the packaged app's own probe and export, headless.

CI runs this inside the built AppImage; here it runs from source. What matters
is that it cannot pass by accident: it must refuse to write anywhere but a new
folder of its own, refuse to report on an ffmpeg that is not the bundled one,
fail when an export fails or overruns, and leave a receipt either way. The
integration case runs the real export path against the ffmpeg on PATH, with
"is this the bundled copy" answered yes, since a source run has no bundle.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import flightdvr.jobs as jobs  # noqa: E402
import flightdvr.media as media  # noqa: E402
import flightdvr.package_check as package_check  # noqa: E402
from flightdvr.media import Tools, ToolsMissing  # noqa: E402


def _receipt(parent: Path) -> dict:
    found = sorted(parent.glob("flightdvr-check-export-*/receipt.json"))
    assert len(found) == 1, found
    return json.loads(found[0].read_text(encoding="utf-8"))


def _never_export(*_args, **_kwargs):
    raise AssertionError("no export may start once the check has failed closed")


# -- where it writes ------------------------------------------------------------------

def test_no_folder_is_refused():
    report, code = package_check.check_export(None)
    assert code == 2 and "needs a folder" in report


def test_a_folder_that_does_not_exist_is_refused_and_not_created(tmp_path):
    missing = tmp_path / "nowhere"
    report, code = package_check.check_export(str(missing))
    assert code == 2 and not missing.exists()


def test_an_existing_child_is_never_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(package_check.time, "strftime", lambda *_: "FIXED")
    taken = tmp_path / f"flightdvr-check-export-FIXED-{os.getpid()}"
    taken.mkdir()
    (taken / "mine").write_text("keep")
    report, code = package_check.check_export(str(tmp_path))
    assert code == 2 and "could not create a new folder" in report
    assert sorted(p.name for p in taken.iterdir()) == ["mine"]


# -- failing closed ----------------------------------------------------------------------

def test_missing_tools_fail_closed_with_a_receipt(tmp_path, monkeypatch):
    def missing():
        raise ToolsMissing("Could not find ffmpeg.")
    monkeypatch.setattr(media, "find_tools", missing)
    monkeypatch.setattr(jobs, "ExportWorker", _never_export)
    report, code = package_check.check_export(str(tmp_path))
    receipt = _receipt(tmp_path)
    assert code == 1 and receipt["result"] == "FAIL"
    assert receipt["failures"][0].startswith("ffmpeg not found")


def test_a_system_ffmpeg_fails_closed_before_anything_runs(tmp_path, monkeypatch):
    system = tmp_path / "system"
    system.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        (system / name).write_bytes(b"not the bundled build")
    monkeypatch.setattr(media, "find_tools",
                        lambda: Tools(system / "ffmpeg", system / "ffprobe"))
    monkeypatch.setattr(media, "is_bundled", lambda _path: False)
    monkeypatch.setattr(media, "run_hidden", _never_export)
    monkeypatch.setattr(jobs, "ExportWorker", _never_export)
    out = tmp_path / "out"
    out.mkdir()
    report, code = package_check.check_export(str(out))
    receipt = _receipt(out)
    assert code == 1 and receipt["result"] == "FAIL"
    assert [f.split(" at ")[0] for f in receipt["failures"]] == ["ffmpeg", "ffprobe"]
    assert all("not the bundled copy" in f for f in receipt["failures"])
    assert receipt["tools"]["ffmpeg"]["bundled"] is False


# -- the real path -------------------------------------------------------------------------

@pytest.fixture
def as_bundled(tools, monkeypatch):
    """The ffmpeg on PATH, treated as the bundled copy a packaged build has.

    The check's Master export is the Rec.709 conversion, which needs zscale.
    The pinned Linux and Windows builds have it (CI records that for Linux);
    Homebrew's ffmpeg does not, so there is nothing to prove with it here.
    """
    filters = subprocess.run([str(tools.ffmpeg), "-hide_banner", "-filters"],
                             capture_output=True, text=True, timeout=20)
    assert filters.returncode == 0, filters.stderr
    if not any(line.split()[1:2] == ["zscale"] for line in filters.stdout.splitlines()):
        pytest.skip("the check's Rec.709 export needs this ffmpeg's zscale filter")
    monkeypatch.setattr(media, "find_tools", lambda: tools)
    monkeypatch.setattr(media, "is_bundled", lambda _path: True)
    return tools


@pytest.mark.integration
def test_the_real_probe_and_exports_pass_and_are_measured(tmp_path, as_bundled):
    report, code = package_check.check_export(str(tmp_path))
    receipt = _receipt(tmp_path)
    assert code == 0, report
    assert receipt["result"] == "PASS" and receipt["failures"] == []
    assert receipt["source"]["probe"]["video_codec"] == "hevc"
    assert len(receipt["source"]["sha256"]) == 64
    measured = {name: (e["probe"]["video_codec"], e["probe"]["audio_codec"])
                for name, e in receipt["exports"].items()}
    assert measured == {"master": ("h264", "aac"), "edit": ("dnxhd", "pcm_s16le"),
                        "remux": ("hevc", "aac")}
    assert abs(receipt["exports"]["master"]["probe"]["duration"] - 2.0) <= 0.15
    for entry in receipt["exports"].values():
        assert Path(entry["path"]).is_file() and len(entry["sha256"]) == 64
    assert receipt["unfinished_files_left"] == []


@pytest.mark.integration
@pytest.mark.skipif(os.name == "nt", reason="Windows export cancellation is an open "
                                            "release blocker and is not exercised here")
def test_an_export_that_overruns_its_limit_fails(tmp_path, as_bundled):
    report, code = package_check.check_export(str(tmp_path), export_seconds=0.001)
    receipt = _receipt(tmp_path)
    assert code == 1 and receipt["result"] == "FAIL"
    assert receipt["failures"][0] == "exports did not finish within 0.001 s"
    assert receipt["worker"] == {"started": True, "cancel_requested": True, "stopped": True}
    assert not any("confirmed stopped" in f for f in receipt["failures"])


# -- a worker that will not stop, or an error once it has started --------------------------------
#
# A stand-in worker at the ExportWorker boundary: no thread, process or ffmpeg
# exists, so nothing here exercises (or touches) real cancellation. What is
# under test is the diagnostic's own ownership: the worker is settled before
# the receipt is written, a worker that cannot be confirmed stopped is kept
# referenced, and the process is left by the hard-exit path only after the
# receipt is on disk.

class _Exited(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _StandInWorker:
    made: list = []

    def __init__(self, tools, jobs, work, *, waits=(), raise_on_first_wait=False):
        self.waits_requested: list[int] = []
        self.answers = list(waits)
        self.raise_on_first_wait = raise_on_first_wait
        self.cancelled = False
        self.running = False
        _StandInWorker.made.append(self)

    def start(self):
        self.running = True

    def wait(self, ms):
        self.waits_requested.append(ms)
        if self.raise_on_first_wait and len(self.waits_requested) == 1:
            raise RuntimeError("injected failure after the worker started")
        stopped = self.answers.pop(0)
        self.running = not stopped
        return stopped

    def cancel(self):
        self.cancelled = True

    def isRunning(self):
        return self.running


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    """Everything before the worker answered without ffmpeg."""
    from datetime import datetime
    from flightdvr.media import ClipInfo

    tools_dir = tmp_path / "bundle"
    tools_dir.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        (tools_dir / name).write_bytes(b"stand-in")
    monkeypatch.setattr(media, "find_tools",
                        lambda: Tools(tools_dir / "ffmpeg", tools_dir / "ffprobe"))
    monkeypatch.setattr(media, "is_bundled", lambda _path: True)

    def run_hidden(args, timeout=60):
        if "-version" not in args:
            Path(args[-1]).write_bytes(b"generated")
        return subprocess.CompletedProcess(args, 0, "ffmpeg version stand-in\n", "")

    def probe(_tools, path, *a, **k):
        return ClipInfo(path=path, size=1, modified=datetime(2026, 10, 1), duration=3.0,
                        width=640, height=360, fps=60.0, video_codec="hevc",
                        audio_codec="aac", pix_fmt="yuvj420p", color_range="pc")

    monkeypatch.setattr(media, "run_hidden", run_hidden)
    monkeypatch.setattr(media, "probe", probe)
    monkeypatch.setattr(package_check, "_UNSTOPPED", [])
    _StandInWorker.made = []
    out = tmp_path / "out"
    out.mkdir()
    return out


def _use_worker(monkeypatch, **behaviour):
    monkeypatch.setattr(jobs, "ExportWorker",
                        lambda tools, js, work: _StandInWorker(tools, js, work, **behaviour))


def _exit_after_receipt(out):
    def exit_(code):
        # The receipt must already be on disk when the process is left.
        receipt = _receipt(out)
        assert receipt["result"] == "FAIL"
        raise _Exited(code)
    return exit_


def test_a_worker_that_will_not_stop_is_kept_and_the_process_left_after_the_receipt(
        stand_in, monkeypatch):
    _use_worker(monkeypatch, waits=[False, False])
    with pytest.raises(_Exited) as left:
        package_check.check_export(str(stand_in), export_seconds=0.001,
                                   _exit=_exit_after_receipt(stand_in))
    assert left.value.code == package_check.UNSTOPPED_EXIT
    worker = _StandInWorker.made[0]
    assert worker.waits_requested == [1, package_check.STOP_SECONDS * 1000]
    assert worker.cancelled
    assert package_check._UNSTOPPED == [worker]       # never released while running
    receipt = _receipt(stand_in)
    assert receipt["worker"] == {"started": True, "cancel_requested": True, "stopped": False}
    assert receipt["failures"][0] == "exports did not finish within 0.001 s"
    assert "could not be confirmed stopped" in receipt["failures"][1]


def test_an_error_after_start_still_settles_the_worker_before_the_receipt(
        stand_in, monkeypatch):
    _use_worker(monkeypatch, waits=[True], raise_on_first_wait=True)
    report, code = package_check.check_export(str(stand_in), _exit=_never_export)
    worker = _StandInWorker.made[0]
    assert code == 1 and worker.cancelled and not worker.running
    receipt = _receipt(stand_in)
    assert receipt["worker"] == {"started": True, "cancel_requested": True, "stopped": True}
    assert "injected failure after the worker started" in receipt["failures"][0]
    assert package_check._UNSTOPPED == []


def test_an_error_after_start_with_a_worker_that_will_not_stop_fails_closed(
        stand_in, monkeypatch):
    _use_worker(monkeypatch, waits=[False], raise_on_first_wait=True)
    with pytest.raises(_Exited) as left:
        package_check.check_export(str(stand_in), _exit=_exit_after_receipt(stand_in))
    assert left.value.code == package_check.UNSTOPPED_EXIT
    assert package_check._UNSTOPPED == [_StandInWorker.made[0]]
    receipt = _receipt(stand_in)
    assert receipt["worker"]["stopped"] is False
    assert "injected failure" in receipt["failures"][0]
    assert "could not be confirmed stopped" in receipt["failures"][1]


@pytest.mark.integration
def test_a_wrong_output_fails_the_check(tmp_path, as_bundled, monkeypatch):
    # The real exports, judged against an expectation they cannot meet.
    expected = dict(package_check.EXPECTED)
    expected["remux"] = dict(expected["remux"], video_codec="h264")
    monkeypatch.setattr(package_check, "EXPECTED", expected)
    report, code = package_check.check_export(str(tmp_path))
    receipt = _receipt(tmp_path)
    assert code == 1
    assert receipt["failures"] == ["remux: video_codec is 'hevc', expected 'h264'"]


# -- the launch dispatch ----------------------------------------------------------------------

def test_launch_dispatches_before_any_window_exists(monkeypatch, tmp_path):
    import flightdvr.ui as ui
    calls = []
    monkeypatch.setattr(package_check, "check_export",
                        lambda folder: (calls.append(folder), ("stub report", 7))[1])
    monkeypatch.setattr(ui, "MainWindow", _never_export)
    assert ui.launch(["--check-export", str(tmp_path)]) == 7
    assert ui.launch(["--check-export"]) == 7
    assert calls == [str(tmp_path), None]


def _unwritable(*_args, **_kwargs):
    raise OSError(28, "No space left on device")


def test_a_receipt_that_cannot_be_written_still_holds_a_running_worker_and_leaves(
        stand_in, monkeypatch):
    _use_worker(monkeypatch, waits=[False, False])
    monkeypatch.setattr(package_check, "_write_receipt", _unwritable)
    reported = []

    def exit_(code):
        reported.append(list(package_check._UNSTOPPED))
        raise _Exited(code)

    with pytest.raises(_Exited) as left:
        package_check.check_export(str(stand_in), export_seconds=0.001, _exit=exit_)
    assert left.value.code == package_check.UNSTOPPED_EXIT
    assert reported == [[_StandInWorker.made[0]]]      # held when the process leaves
    assert not list(stand_in.glob("flightdvr-check-export-*/receipt.json"))


def test_a_receipt_that_cannot_be_written_fails_the_check(stand_in, monkeypatch):
    _use_worker(monkeypatch, waits=[True])
    monkeypatch.setattr(package_check, "_write_receipt", _unwritable)
    report, code = package_check.check_export(str(stand_in), _exit=_never_export)
    assert code == 1
    assert report.startswith("check-export FAIL\nreceipt NOT WRITTEN in ")
    assert "the receipt could not be written: OSError" in report
    assert package_check._UNSTOPPED == []


# -- --check-environment: the child-process environment (#131) ------------------------------

def _environment_receipt(parent: Path) -> dict:
    found = sorted(parent.glob("flightdvr-check-environment-*/receipt.json"))
    assert len(found) == 1, found
    return json.loads(found[0].read_text(encoding="utf-8"))


def _no_tools():
    raise ToolsMissing("none")


def _frozen_linux(monkeypatch, tmp_path, orig: str | None) -> Path:
    """The frozen Linux app as the bootloader leaves it: the bundle first on
    LD_LIBRARY_PATH, and whatever was there before saved as _ORIG."""
    bundle = tmp_path / "bundle"
    (bundle / "ffmpeg").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "executable", str(bundle / "flightdvr-studio"))
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))
    if orig is None:
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    else:
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", orig)
    monkeypatch.setenv(package_check.ENVIRONMENT_SENTINEL, "sentinel-7f3a")
    return bundle


def test_check_environment_needs_an_existing_folder(tmp_path):
    assert package_check.check_environment(None)[1] == 2
    missing = tmp_path / "nowhere"
    _report, code = package_check.check_environment(str(missing))
    assert code == 2 and not missing.exists()


def test_check_environment_outside_the_frozen_linux_app_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(media, "find_tools", _no_tools)
    report, code = package_check.check_environment(str(tmp_path))
    receipt = _environment_receipt(tmp_path)
    assert code == 1 and report.startswith("check-environment FAIL")
    assert receipt["failures"] == [
        "the stand-in phase needs the frozen Linux app (sys.frozen on Linux)",
        "ffmpeg not found: none"]
    assert "stand_in" not in receipt and receipt["parent_unchanged"] is True


posix_stand_in = pytest.mark.skipif(os.name == "nt", reason="the stand-in is a POSIX shell script")


@posix_stand_in
@pytest.mark.parametrize("orig", [None, "/opt/system/lib"], ids=["orig-absent", "orig-present"])
def test_the_stand_in_gets_the_saved_loader_path_and_keeps_the_sentinel(
        tmp_path, monkeypatch, orig):
    _frozen_linux(monkeypatch, tmp_path, orig)
    monkeypatch.setattr(media, "find_tools", _no_tools)
    out = tmp_path / "out"
    out.mkdir()
    package_check.check_environment(str(out))
    receipt = _environment_receipt(out)
    assert receipt["stand_in"]["exit"] == 0
    assert receipt["stand_in"]["saw"] == {
        "LD_LIBRARY_PATH": orig or "<unset>", "LD_LIBRARY_PATH_ORIG": "<unset>",
        "SENTINEL": "sentinel-7f3a"}
    assert receipt["failures"] == ["ffmpeg not found: none"]     # the stand-in phase passed
    assert receipt["parent_unchanged"] is True


@posix_stand_in
def test_a_stand_in_given_the_bundles_path_fails(tmp_path, monkeypatch):
    bundle = _frozen_linux(monkeypatch, tmp_path, "/opt/system/lib")
    monkeypatch.setattr(media, "find_tools", _no_tools)
    monkeypatch.setattr(media, "child_env", lambda _program: dict(os.environ))  # #131 undone
    out = tmp_path / "out"
    out.mkdir()
    package_check.check_environment(str(out))
    failures = _environment_receipt(out)["failures"]
    assert (f"the stand-in got LD_LIBRARY_PATH={str(bundle)!r}, expected '/opt/system/lib'"
            in failures)
    assert "the stand-in still received LD_LIBRARY_PATH_ORIG" in failures


@posix_stand_in
def test_a_change_to_the_apps_own_environment_fails(tmp_path, monkeypatch):
    _frozen_linux(monkeypatch, tmp_path, None)
    monkeypatch.setattr(media, "find_tools", _no_tools)
    real = media.run_hidden

    def leaky(args, timeout=60):
        monkeypatch.setenv("FLIGHTDVR_LEAKED", "1")
        return real(args, timeout=timeout)

    monkeypatch.setattr(media, "run_hidden", leaky)
    out = tmp_path / "out"
    out.mkdir()
    package_check.check_environment(str(out))
    receipt = _environment_receipt(out)
    assert receipt["parent_unchanged"] is False
    assert "the app's own environment changed: ['FLIGHTDVR_LEAKED']" in receipt["failures"]


def test_external_tools_must_exist_and_be_outside_the_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "find_tools", _no_tools)
    named = tmp_path / "named"
    named.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    package_check.check_environment(str(out), str(named))
    assert (f"--external-tools {named} does not hold ffmpeg and ffprobe"
            in _environment_receipt(out)["failures"])
    for name in ("ffmpeg", "ffprobe"):
        (named / name).write_bytes(b"")
    monkeypatch.setattr(media, "is_bundled", lambda _path: True)
    again = tmp_path / "again"
    again.mkdir()
    package_check.check_environment(str(again), str(named))
    assert (f"--external-tools {named} is inside the bundle; it must be external"
            in _environment_receipt(again)["failures"])


@pytest.mark.integration
@posix_stand_in
@pytest.mark.parametrize("orig", [None, "/opt/system/lib"], ids=["orig-absent", "orig-present"])
def test_the_real_external_and_bundled_readers_pass(tmp_path, monkeypatch, tools, orig):
    """The ffmpeg on PATH plays the bundled pair; links to it in another
    folder play the named external pair. Both go through the app's own
    probe, inspection and streaming reader."""
    bundle = _frozen_linux(monkeypatch, tmp_path, orig)
    pair = {Path(tools.ffmpeg), Path(tools.ffprobe)}
    monkeypatch.setattr(media, "find_tools", lambda: tools)
    monkeypatch.setattr(media, "is_bundled", lambda path: Path(path) in pair)
    external = tmp_path / "system"
    external.mkdir()
    for name, path in (("ffmpeg", tools.ffmpeg), ("ffprobe", tools.ffprobe)):
        (external / name).symlink_to(Path(path).resolve())
    out = tmp_path / "out"
    out.mkdir()
    report, code = package_check.check_environment(str(out), str(external))
    receipt = _environment_receipt(out)
    assert code == 0, report
    assert receipt["result"] == "PASS" and receipt["failures"] == []
    assert receipt["stand_in"]["saw"]["LD_LIBRARY_PATH"] == (orig or "<unset>")
    assert receipt["bundled_child_LD_LIBRARY_PATH"] == str(bundle)
    assert receipt["external"]["probe"]["video_codec"] == "h264"
    assert receipt["external"]["probe"]["audio_codec"] == "aac"
    for track in (receipt["external"]["track"], receipt["bundled_track"]):
        assert track["block"]["values"] == 2 * track["block"]["frames"] > 0
        assert track["reader_closed"] is True
    assert receipt["parent_unchanged"] is True


def test_launch_dispatches_the_environment_check_before_any_window_exists(
        monkeypatch, tmp_path):
    import flightdvr.ui as ui
    calls = []

    def stub(folder, external=None):
        calls.append((folder, external))
        return "stub report", 5

    monkeypatch.setattr(package_check, "check_environment", stub)
    monkeypatch.setattr(ui, "MainWindow", _never_export)
    assert ui.launch(["--check-environment", str(tmp_path)]) == 5
    assert ui.launch(["--check-environment", str(tmp_path), "--external-tools", "/usr/bin"]) == 5
    assert ui.launch(["--check-environment"]) == 5
    assert calls == [(str(tmp_path), None), (str(tmp_path), "/usr/bin"), (None, None)]
