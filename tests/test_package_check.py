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
    """The ffmpeg on PATH, treated as the bundled copy a packaged build has."""
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
    assert any("did not finish within" in f for f in receipt["failures"])
    assert not any("did not stop" in f for f in receipt["failures"])


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
