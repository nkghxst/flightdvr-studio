"""The Linux AppImage bundles exactly the pinned ffmpeg pair, or nothing at all.

THIRD-PARTY-NOTICES.md names one Linux build and points at its corresponding
source. These tests are about the guards that keep that true: the fetcher's
checks on a real-shaped archive, the spec refusing to bundle anything but the
pinned pair on Linux while Windows and macOS keep their own rules, and the pin
agreeing with the notices.

Each refusal is asserted by its reason, not just by an exception, so a guard
that fails for the wrong cause (a tar error, a missing tool) does not pass as
one that worked. The archives are built here, small and synthetic; nothing is
downloaded and no ffmpeg is run.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"

_spec = importlib.util.spec_from_file_location("verify_ffmpeg_linux",
                                               PACKAGING / "verify_ffmpeg_linux.py")
verify = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("verify_ffmpeg_linux", verify)
_spec.loader.exec_module(verify)
verify = sys.modules["verify_ffmpeg_linux"]

TOP = "ffmpeg-test-linux64-gpl"
FFMPEG = b"\x7fELF fake ffmpeg " * 64
FFPROBE = b"\x7fELF fake ffprobe " * 48
LICENCE = b"GNU GENERAL PUBLIC LICENSE\nVersion 3\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _member(name, data=None, kind="file", link=""):
    return {"name": name, "data": data, "kind": kind, "link": link}


def _good_members():
    return [_member(TOP, kind="dir"), _member(f"{TOP}/bin", kind="dir"),
            _member(f"{TOP}/bin/ffmpeg", FFMPEG), _member(f"{TOP}/bin/ffprobe", FFPROBE),
            _member(f"{TOP}/bin/ffplay", b"player"),
            _member(f"{TOP}/doc/ffmpeg.html", b"<html>"),
            _member(f"{TOP}/LICENSE.txt", LICENCE)]


def _archive(path: Path, members) -> Path:
    with tarfile.open(path, "w:xz") as tar:
        for m in members:
            info = tarfile.TarInfo(m["name"])
            info.mode = 0o755
            if m["kind"] == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif m["kind"] in ("symlink", "hardlink"):
                info.type = tarfile.SYMTYPE if m["kind"] == "symlink" else tarfile.LNKTYPE
                info.linkname = m["link"]
                tar.addfile(info)
            else:
                info.size = len(m["data"])
                tar.addfile(info, io.BytesIO(m["data"]))
    with tarfile.open(path, "r:xz") as tar:     # the case really is in the archive
        assert [i.name for i in tar.getmembers()] == [m["name"] for m in members]
    return path


def _pin(archive: Path, **changes) -> dict:
    data = archive.read_bytes()
    pin = {"version": "n-test", "archive": archive.name, "url": "https://example.invalid/a",
           "release_tag": "tag", "build_system_commit": "b" * 40,
           "ffmpeg_git_commit_full": "f" * 40, "archive_root": TOP,
           "archive_size": len(data), "archive_sha256": _sha(data),
           "binaries": {"ffmpeg": _sha(FFMPEG), "ffprobe": _sha(FFPROBE)},
           "binary_sizes": {"ffmpeg": len(FFMPEG), "ffprobe": len(FFPROBE)},
           "licence_member": "LICENSE.txt", "licence_size": len(LICENCE),
           "licence_sha256": _sha(LICENCE)}
    pin.update(changes)
    return pin


def _refused(archive, pin, dest) -> str:
    with pytest.raises(verify.PinError) as caught:
        verify.unpack(archive, dest, pin)
    # Nothing that looks usable is left behind, partial or otherwise.
    assert not dest.exists()
    assert not [p for p in dest.parent.iterdir() if p.name.startswith(f".{dest.name}.partial-")]
    return caught.value.reason


# -- the fetcher on a well-formed archive -----------------------------------------

def test_a_matching_archive_publishes_the_pair_licence_and_provenance(tmp_path):
    archive = _archive(tmp_path / "a.tar.xz", _good_members())
    dest = verify.unpack(archive, tmp_path / "bin", _pin(archive))
    assert sorted(p.name for p in dest.iterdir()) == [
        "LICENSE.txt", "ffmpeg", "ffmpeg-linux-provenance.json", "ffprobe"]
    assert (dest / "ffmpeg").read_bytes() == FFMPEG
    assert (dest / "ffprobe").read_bytes() == FFPROBE
    assert not (dest / "ffplay").exists()       # only the pair, not every program
    record = json.loads((dest / verify.PROVENANCE).read_text(encoding="utf-8"))
    assert record["files"]["ffmpeg"]["sha256"] == _sha(FFMPEG)
    assert record["archive_sha256"] == _sha(archive.read_bytes())
    if os.name != "nt":
        assert os.access(dest / "ffmpeg", os.X_OK) and os.access(dest / "ffprobe", os.X_OK)
    assert verify.check_dir(dest, _pin(archive)) == {"ffmpeg": _sha(FFMPEG),
                                                    "ffprobe": _sha(FFPROBE)}


# -- the archive is judged before it is opened -------------------------------------

def test_a_wrong_archive_hash_is_refused_before_the_archive_is_opened(tmp_path):
    # Not even a tar: if anything tried to open it, the error would be a tar
    # error rather than the hash refusal asserted here.
    archive = tmp_path / "a.tar.xz"
    archive.write_bytes(b"not an archive at all")
    pin = _pin(archive, archive_sha256="0" * 64)
    assert _refused(archive, pin, tmp_path / "bin") == "archive-hash"


def test_a_wrong_archive_size_is_refused(tmp_path):
    archive = _archive(tmp_path / "a.tar.xz", _good_members())
    pin = _pin(archive)
    pin["archive_size"] += 1
    assert _refused(archive, pin, tmp_path / "bin") == "archive-size"


# -- members ------------------------------------------------------------------------

def test_an_incomplete_pair_is_refused(tmp_path):
    members = [m for m in _good_members() if not m["name"].endswith("/ffprobe")]
    archive = _archive(tmp_path / "a.tar.xz", members)
    with pytest.raises(verify.PinError, match="ffprobe") as caught:
        verify.unpack(archive, tmp_path / "bin", _pin(archive))
    assert caught.value.reason == "missing-member"
    assert not (tmp_path / "bin").exists()


def test_a_missing_licence_is_refused(tmp_path):
    members = [m for m in _good_members() if not m["name"].endswith("LICENSE.txt")]
    archive = _archive(tmp_path / "a.tar.xz", members)
    assert _refused(archive, _pin(archive), tmp_path / "bin") == "missing-member"


def test_a_wrong_executable_hash_is_refused_and_nothing_is_published(tmp_path):
    archive = _archive(tmp_path / "a.tar.xz", _good_members())
    pin = _pin(archive)
    pin["binaries"]["ffprobe"] = "0" * 64
    assert _refused(archive, pin, tmp_path / "bin") == "binary-hash"


def test_a_wrong_executable_size_is_refused(tmp_path):
    archive = _archive(tmp_path / "a.tar.xz", _good_members())
    pin = _pin(archive)
    pin["binary_sizes"]["ffmpeg"] += 1
    assert _refused(archive, pin, tmp_path / "bin") == "binary-size"


@pytest.mark.parametrize("extra, reason", [
    (_member("/etc/evil", b"x"), "unsafe-path"),
    (_member(f"{TOP}/../evil", b"x"), "unsafe-path"),
    (_member(f"{TOP}/bin/ffmpeg2", kind="symlink", link="/usr/bin/ffmpeg"), "link"),
    (_member(f"{TOP}/bin/linked", kind="hardlink", link=f"{TOP}/bin/ffmpeg"), "link"),
    (_member(f"{TOP}/other/ffmpeg", b"second ffmpeg"), "duplicate"),
    (_member(f"{TOP}/bin/ffprobe", FFPROBE), "duplicate"),
], ids=["absolute", "traversal", "symlink", "hardlink", "second-candidate", "repeated"])
def test_an_unsafe_or_ambiguous_archive_is_refused_outright(tmp_path, extra, reason):
    archive = _archive(tmp_path / "a.tar.xz", _good_members() + [extra])
    assert _refused(archive, _pin(archive), tmp_path / "bin") == reason


def test_the_wanted_name_as_a_link_is_refused(tmp_path):
    members = [m for m in _good_members() if not m["name"].endswith("/ffmpeg")]
    members.append(_member(f"{TOP}/bin/ffmpeg", kind="symlink", link="/usr/bin/ffmpeg"))
    archive = _archive(tmp_path / "a.tar.xz", members)
    assert _refused(archive, _pin(archive), tmp_path / "bin") == "link"


def test_an_existing_destination_is_never_overwritten(tmp_path):
    archive = _archive(tmp_path / "a.tar.xz", _good_members())
    dest = tmp_path / "bin"
    dest.mkdir()
    (dest / "keep").write_text("mine")
    with pytest.raises(verify.PinError) as caught:
        verify.unpack(archive, dest, _pin(archive))
    assert caught.value.reason == "destination-exists"
    assert (dest / "keep").read_text() == "mine"


# -- the folder about to be bundled ----------------------------------------------------

def _pair_dir(tmp_path, ffmpeg=FFMPEG, ffprobe=FFPROBE) -> Path:
    folder = tmp_path / "pair"
    folder.mkdir()
    for name, data in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        if data is not None:
            (folder / name).write_bytes(data)
            (folder / name).chmod(0o755)
    return folder


def _dir_pin(tmp_path) -> dict:
    archive = _archive(tmp_path / "a.tar.xz", _good_members())
    return _pin(archive)


def test_check_dir_refuses_a_folder_missing_ffprobe(tmp_path):
    pin = _dir_pin(tmp_path)
    with pytest.raises(verify.PinError) as caught:
        verify.check_dir(_pair_dir(tmp_path, ffprobe=None), pin)
    assert caught.value.reason == "missing-member"


def test_check_dir_refuses_different_bytes_of_the_same_size(tmp_path):
    pin = _dir_pin(tmp_path)
    altered = bytes(reversed(FFMPEG))
    with pytest.raises(verify.PinError) as caught:
        verify.check_dir(_pair_dir(tmp_path, ffmpeg=altered), pin)
    assert caught.value.reason == "binary-hash"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_check_dir_refuses_a_linked_program(tmp_path):
    pin = _dir_pin(tmp_path)
    folder = _pair_dir(tmp_path, ffmpeg=None)
    real = tmp_path / "real-ffmpeg"
    real.write_bytes(FFMPEG)
    (folder / "ffmpeg").symlink_to(real)
    with pytest.raises(verify.PinError) as caught:
        verify.check_dir(folder, pin)
    assert caught.value.reason == "link"


@pytest.mark.skipif(os.name == "nt", reason="no execute bit on Windows")
def test_check_dir_refuses_a_program_that_cannot_run(tmp_path):
    pin = _dir_pin(tmp_path)
    folder = _pair_dir(tmp_path)
    (folder / "ffprobe").chmod(0o644)
    with pytest.raises(verify.PinError) as caught:
        verify.check_dir(folder, pin)
    assert caught.value.reason == "not-executable"


# -- the spec, run for each platform ------------------------------------------------------

class _Analysis:
    def __init__(self, scripts, **kwargs):
        self.binaries = list(kwargs["binaries"])
        self.datas = list(kwargs["datas"])
        self.scripts, self.pure = [], []


def _run_spec(monkeypatch, platform: str, ffmpeg_dir: str | None) -> list:
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "path", list(sys.path))
    if ffmpeg_dir is None:
        monkeypatch.delenv("FFMPEG_DIR", raising=False)
    else:
        monkeypatch.setenv("FFMPEG_DIR", ffmpeg_dir)
    captured = {}

    def analysis(*args, **kwargs):
        captured["a"] = _Analysis(*args, **kwargs)
        return captured["a"]

    stub = lambda *a, **k: None  # noqa: E731
    code = (PACKAGING / "flightdvr_studio.spec").read_text(encoding="utf-8")
    exec(compile(code, "flightdvr_studio.spec", "exec"),
         {"Analysis": analysis, "PYZ": stub, "EXE": stub, "COLLECT": stub, "BUNDLE": stub,
          "__name__": "__main__"})
    return captured["a"].binaries


def test_the_linux_spec_requires_ffmpeg_dir(monkeypatch):
    with pytest.raises(SystemExit, match="fetch-ffmpeg.sh"):
        _run_spec(monkeypatch, "linux", None)


def test_the_linux_spec_refuses_an_unpinned_pair(monkeypatch, tmp_path):
    # Real pin, fake bytes: what bundling "whatever ffmpeg is installed" looks like.
    folder = _pair_dir(tmp_path)
    with pytest.raises(SystemExit, match="binary-size|binary-hash"):
        _run_spec(monkeypatch, "linux", str(folder))


def test_the_linux_spec_bundles_a_pinned_pair(monkeypatch, tmp_path):
    pin = _dir_pin(tmp_path)
    monkeypatch.setattr(verify, "load_pin", lambda path=None: pin)
    folder = _pair_dir(tmp_path)
    binaries = _run_spec(monkeypatch, "linux", str(folder))
    assert sorted(binaries) == [(str(folder / "ffmpeg"), "ffmpeg"),
                                (str(folder / "ffprobe"), "ffmpeg")]


def test_the_windows_spec_is_unchanged(monkeypatch, tmp_path):
    folder = tmp_path / "win"
    folder.mkdir()
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        (folder / name).write_bytes(b"MZ")
    assert sorted(_run_spec(monkeypatch, "win32", str(folder))) == [
        (str(folder / "ffmpeg.exe"), "ffmpeg"), (str(folder / "ffprobe.exe"), "ffmpeg")]
    (folder / "ffprobe.exe").unlink()
    with pytest.raises(SystemExit, match="ffprobe.exe not found"):
        _run_spec(monkeypatch, "win32", str(folder))


def test_the_macos_spec_still_bundles_nothing_by_default(monkeypatch):
    assert _run_spec(monkeypatch, "darwin", None) == []


# -- the pin, the notices and the build ------------------------------------------------------

LINUX_PIN = json.loads((PACKAGING / "ffmpeg-build-linux.json").read_text(encoding="utf-8"))


def test_the_linux_pin_is_the_verified_build():
    """Values measured from the archive and independently verified (Sol 1776)."""
    assert LINUX_PIN["archive_size"] == 119007364
    assert LINUX_PIN["archive_sha256"] == (
        "c1e6caf48923dd8e6bc5e54d51ba70c321175b8162ae9c414c392990e72f0e79")
    assert LINUX_PIN["binaries"] == {
        "ffmpeg": "be59d8a5989ce0593343c0a8e3c36dd02ce523ecdc4b9ebc04437b2aa9ad2fe6",
        "ffprobe": "716620defe0abbfead89c7c3895cbdebc7a530dd4646f9967c5234abc7cccbad"}
    assert LINUX_PIN["binary_sizes"] == {"ffmpeg": 139397096, "ffprobe": 139261288}
    assert LINUX_PIN["build_system_commit"] == "a99e8230eae00d1cee38f23076a7a1f55cd984e2"
    assert LINUX_PIN["ffmpeg_git_commit_full"] == "1fdbca85aaea513c9cc6c14d347f76543346d3da"
    assert LINUX_PIN["url"].endswith("/" + LINUX_PIN["archive"])
    assert LINUX_PIN["archive"] == LINUX_PIN["archive_root"] + ".tar.xz"


def test_the_windows_pin_is_untouched():
    pin = json.loads((PACKAGING / "ffmpeg-build.json").read_text(encoding="utf-8"))
    assert pin["archive_sha256"] == (
        "c067a1ca58f4fc4449f4bab0890fbcd65cbb3e5f46e066cf9c768e06c0c1d4d9")
    assert pin["binaries"]["ffmpeg.exe"] == (
        "efabedca4b599c13e073bb08f66369619e9deb1db1dc1f32822583379d6513de")


def test_the_notices_name_the_linux_build_and_its_source():
    notices = (ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    for value in (LINUX_PIN["archive"], LINUX_PIN["archive_sha256"],
                  LINUX_PIN["build_system_commit"], LINUX_PIN["ffmpeg_git_commit_full"],
                  LINUX_PIN["binaries"]["ffmpeg"], LINUX_PIN["binaries"]["ffprobe"],
                  "ffmpeg-configuration-linux.txt", "libgcc_s.so.1"):
        assert value in notices, f"the notices do not mention {value}"


def test_the_recorded_linux_configuration_is_a_redistributable_gpl_build():
    lines = (PACKAGING / "ffmpeg-configuration-linux.txt").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert lines[0].startswith(f"ffmpeg version {LINUX_PIN['version']}-")
    assert lines[2].startswith("configuration: ")
    for flag in ("--target-os=linux", "--enable-gpl", "--enable-version3", "--enable-libx264",
                 "--enable-libzimg"):
        assert flag in lines[2].split()
    assert "--enable-nonfree" not in lines[2]


def test_the_appimage_build_accepts_only_the_bundled_pair():
    script = (PACKAGING / "build-appimage.sh").read_text(encoding="utf-8")
    assert "fetch-ffmpeg.sh" in script
    assert script.count("verify_ffmpeg_linux.py check-dir") == 2   # given, then bundled
    assert "ffmpeg-configuration-linux.txt" in script
    assert "(bundled)" in script
    assert "no ffmpeg on this machine to find" not in script        # exit 3 no longer passes


def test_ci_gates_the_linux_bundle_without_softening_failures():
    workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    assert "continue-on-error" not in workflow
    assert "packaging/fetch-ffmpeg.sh" in workflow
    assert workflow.count("check_linux_bundle.py appimage-check") == 2
    assert "Remove the system ffmpeg" in workflow


def test_a_release_waits_for_the_current_linux_check():
    workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    assert ("needs: [appimage, appimage-current-linux, linux-ffmpeg-source, macos, "
            "windows-installer]") in workflow


def test_the_release_attaches_only_the_named_artifacts():
    workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    release = workflow[workflow.index("  release:"):]
    assert "merge-multiple" not in release
    for name in ("linux-appimage", "macos-dmg", "windows-installer", "linux-ffmpeg-source"):
        assert f"name: {name}\n          path: artifacts/{name}" in release
    assert release.count("actions/download-artifact@v4") == 4
    assert "select-release-files artifacts release-files" in release
    assert 'gh release upload "$GITHUB_REF_NAME" release-files/*' in release
    assert "--draft" in release
    # The source bundle is built in its own job from the AppImage job's inputs.
    assert "name: linux-ffmpeg-source-inputs" in workflow
    assert "collect_linux_sources.py collect" in workflow


# -- the CI helper's own verdict on the actual AppImage ---------------------------------------

_cspec = importlib.util.spec_from_file_location("check_linux_bundle",
                                                PACKAGING / "check_linux_bundle.py")
check_bundle = importlib.util.module_from_spec(_cspec)
_cspec.loader.exec_module(check_bundle)


def _fake_appimage(tmp_path, monkeypatch, pin, run_decoy: bool):
    """Stand in for the AppImage at the subprocess boundary.

    Everything else in appimage_check runs for real: the decoy scripts are
    written and found on PATH, and the receipts and marker are read from disk.
    `run_decoy` makes the packaged app run whatever is first on PATH, which is
    exactly the failure the decoy exists to catch.
    """
    inside = "/tmp/appimage_extracted_test/usr/bin/_internal/ffmpeg"

    def run(argv, **kwargs):
        env = kwargs.get("env") or {}
        first = Path(env.get("PATH", "").split(os.pathsep)[0])
        if argv[1] == "--appimage-extract":
            pair = Path(kwargs["cwd"]) / "squashfs-root/usr/bin/_internal/ffmpeg"
            pair.mkdir(parents=True)
            for name, data in (("ffmpeg", FFMPEG), ("ffprobe", FFPROBE)):
                (pair / name).write_bytes(data)
                (pair / name).chmod(0o755)
            (pair.parent / "libgcc_s.so.1").write_bytes(b"gcc runtime")
            return check_bundle.subprocess.CompletedProcess(argv, 0, "", "")
        if run_decoy and first.name == "decoy":
            (first.parent / "decoy-was-run").write_text("decoy\n")
        if argv[1] == "--check":
            return check_bundle.subprocess.CompletedProcess(
                argv, 0, f"ffmpeg  {inside}/ffmpeg  (bundled)\nffprobe {inside}/ffprobe\n", "")
        raise AssertionError(f"unexpected command {argv}")

    def contained(argv, env, log_path, **_kwargs):
        first = Path(env.get("PATH", "").split(os.pathsep)[0])
        if run_decoy and first.name == "decoy":
            (first.parent / "decoy-was-run").write_text("decoy\n")
        child = Path(argv[2]) / "flightdvr-check-export-test"
        child.mkdir()
        receipt = {"result": "PASS", "failures": [], "app": {"frozen": True},
                   "tools": {t: {"bundled": True, "path": f"{inside}/{t}",
                                 "sha256": pin["binaries"][t]} for t in ("ffmpeg", "ffprobe")},
                   "exports": {n: {"status": "Done", "probe": {}}
                               for n in ("master", "edit", "remux")}}
        (child / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        if "LD_DEBUG_OUTPUT" in env:            # the loader-trace run
            for pid, tool in ((900, "ffmpeg"), (901, "ffprobe")):
                Path(f"{env['LD_DEBUG_OUTPUT']}.{pid}").write_text(_loader_log(
                    f"{inside}/{tool}", {"libgcc_s.so.1": [f"{inside}/../libgcc_s.so.1"],
                                         "libmvec.so.1": ["/lib/x86_64-linux-gnu/libmvec.so.1"]}))
        return {"exit": 0, "timed_out": False, "owned": {}, "ambiguous": [],
                "remaining": [], "deadline_hit": False}

    monkeypatch.setattr(check_bundle.subprocess, "run", run)
    monkeypatch.setattr(check_bundle, "contained_run", contained)
    monkeypatch.setattr(check_bundle, "load_pin", lambda path=None: pin)
    monkeypatch.setattr(check_bundle, "_machine", lambda ev: None)
    monkeypatch.setattr(check_bundle, "_absent_system_tools", lambda: {})
    appimage = tmp_path / "FlightDVR_Studio-test.AppImage"
    appimage.write_bytes(b"never executed")
    return appimage


@pytest.mark.skipif(os.name == "nt", reason="the decoy is a POSIX script found on PATH by name")
@pytest.mark.parametrize("run_decoy", [False, True], ids=["decoy-idle", "decoy-run"])
def test_appimage_check_fails_if_the_conflicting_pair_is_ever_run(tmp_path, monkeypatch,
                                                                   run_decoy):
    pin = _dir_pin(tmp_path)
    appimage = _fake_appimage(tmp_path, monkeypatch, pin, run_decoy)
    out = tmp_path / "evidence"
    code = check_bundle.appimage_check(appimage, out)
    record = json.loads((out / "appimage-check.json").read_text(encoding="utf-8"))
    assert record["decoy_was_run"] is run_decoy
    assert record["bundle_carries_declared_dependencies"]["libgcc_s.so.1"]["size"] == 11
    if run_decoy:
        assert code == 1 and record["result"] == "FAIL"
        assert record["failures"] == ["conflicting PATH: decoy never run"]
    else:
        assert code == 0 and record["result"] == "PASS" and record["failures"] == []


# -- containment of one --check-export invocation (Linux only) --------------------------------
#
# A /bin/sh stand-in takes the AppImage's place and starts only `sleep`, so
# nothing real is run. Every pid the stand-in starts is written to a pidfile,
# so the test knows it independently of the helper. Test teardown cleans up
# only what these tests started, through pidfds validated against the
# recorded start time, or through the test's own Popen handles.

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="containment uses pidfd, /proc and a subreaper")


def _stand_in(tmp_path, body: str) -> list[str]:
    script = tmp_path / "stand-in.sh"
    script.write_text("#!/bin/sh\n" + body + "\n")
    return ["/bin/sh", str(script)]


def _pids(pidfile: Path) -> list[int]:
    return [int(x) for x in pidfile.read_text().split()] if pidfile.exists() else []


def _start(pid: int) -> int | None:
    stat = check_bundle._stat(pid)
    return None if stat is None or stat["state"] == "Z" else stat["start"]


def _still_running(pid: int, start: int) -> bool:
    return _start(pid) == start


def _clean_up(pid: int, start: int) -> None:
    """Teardown for a process this test started: by validated pidfd only."""
    import signal as signals
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        if _start(pid) != start:
            return                         # not the process this test started
        signals.pidfd_send_signal(fd, signals.SIGKILL)
        check_bundle._exited(fd, 5)
        try:
            os.waitid(os.P_PIDFD, fd, os.WEXITED)
        except ChildProcessError:
            pass
    finally:
        os.close(fd)


def _run(tmp_path, body, **kwargs):
    pidfile = tmp_path / "pids"
    env = dict(os.environ, PIDFILE=str(pidfile))
    began = time.monotonic()
    record = check_bundle.contained_run(_stand_in(tmp_path, body), env, tmp_path / "log",
                                        **kwargs)
    return record, _pids(pidfile), time.monotonic() - began


@linux_only
def test_containment_clean_run(tmp_path):
    record, _, _ = _run(tmp_path, "exit 0")
    assert record["exit"] == 0 and not record["timed_out"]
    assert record["owned"] == {} and record["ambiguous"] == [] and record["remaining"] == []
    assert not record["deadline_hit"]


@linux_only
def test_containment_unstopped_exit_3_kills_and_reaps_its_descendant(tmp_path):
    record, (pid,), _ = _run(tmp_path, 'sleep 300 &\necho $! >> "$PIDFILE"\nexit 3')
    assert record["exit"] == 3
    entry = record["owned"][str(pid)]
    assert entry["signals"][0][0] == "SIGTERM"
    assert entry["reaped"] == "by the helper, through its pidfd"
    assert record["remaining"] == [] and record["ambiguous"] == []
    assert not _still_running(pid, entry["start"])


@linux_only
def test_containment_follows_its_token_out_of_the_session(tmp_path):
    record, (pid,), _ = _run(tmp_path,
                             'setsid sleep 300 &\necho $! >> "$PIDFILE"\nsleep 1\nexit 1')
    entry = record["owned"][str(pid)]
    assert entry["session"] != record["session"]          # it really left
    assert entry["reaped"] == "by the helper, through its pidfd"
    assert record["remaining"] == [] and not _still_running(pid, entry["start"])


@linux_only
def test_containment_a_cleared_token_escape_is_ambiguous_and_untouched(tmp_path):
    record, (pid,), _ = _run(tmp_path,
                             'env -i setsid sleep 300 &\necho $! >> "$PIDFILE"\nsleep 1\nexit 1')
    start = _start(pid)
    try:
        assert str(pid) not in record["owned"]
        [found] = [a for a in record["ambiguous"] if a["pid"] == pid]
        assert found["reason"] == "no token"
        assert _still_running(pid, found["scan"]["start"])     # never signalled
    finally:
        _clean_up(pid, start)


@linux_only
def test_containment_timeout_stops_everything_within_the_deadline(tmp_path):
    record, (pid,), elapsed = _run(
        tmp_path, 'sleep 300 &\necho $! >> "$PIDFILE"\nsleep 60',
        invocation_seconds=2, cleanup_seconds=30, term_grace=2)
    assert record["timed_out"] and record["exit"] is None
    assert str(record["session"]) in record["owned"]        # the stand-in itself
    assert str(pid) in record["owned"]
    assert record["remaining"] == [] and not record["deadline_hit"]
    assert elapsed < 2 + 30
    for entry in record["owned"].values():
        assert not _still_running(entry["pid"], entry["start"])


@linux_only
def test_containment_without_a_receipt_still_cleans_up_and_fails(tmp_path, monkeypatch):
    appimage = tmp_path / "FlightDVR_Studio-stand-in.AppImage"
    appimage.write_text('#!/bin/sh\nsleep 300 &\necho $! >> "$PIDFILE"\nexit 3\n')
    appimage.chmod(0o755)
    pidfile = tmp_path / "pids"
    monkeypatch.setenv("PIDFILE", str(pidfile))
    ev = check_bundle.Evidence(tmp_path / "out", "receipt-oracle")
    check_bundle.run_check_export(ev, appimage, "check-export", dict(os.environ),
                                  _dir_pin(tmp_path))
    (pid,) = _pids(pidfile)
    run = ev.record["check-export"]["containment"]
    assert "check-export: one receipt, reporting PASS" in ev.record["failures"]
    assert "check-export: --check-export exits 0 in time" in ev.record["failures"]
    assert "check-export: no process of its own outlived it" in ev.record["failures"]
    assert run["owned"][str(pid)]["reaped"] == "by the helper, through its pidfd"
    assert run["remaining"] == []
    assert "check-export: nothing of its own remains" not in ev.record["failures"]


@linux_only
def test_containment_never_touches_a_process_started_before_it(tmp_path):
    unrelated = subprocess.Popen(["sleep", "300"])
    try:
        record, _, _ = _run(tmp_path, "exit 0")
        assert unrelated.pid in record["helper_children_before"]
        assert unrelated.poll() is None                     # neither signalled nor reaped
        assert str(unrelated.pid) not in record["owned"]
        assert all(a["pid"] != unrelated.pid for a in record["ambiguous"])
    finally:
        unrelated.kill()
        unrelated.wait()


@linux_only
def test_containment_never_touches_a_helper_child_started_during_it(tmp_path):
    import threading
    started: list = []
    timer = threading.Timer(0.5, lambda: started.append(subprocess.Popen(["sleep", "300"])))
    timer.start()
    try:
        record, _, _ = _run(tmp_path, "sleep 2\nexit 0")
        timer.join()
        (unrelated,) = started
        [found] = [a for a in record["ambiguous"] if a["pid"] == unrelated.pid]
        assert found["reason"] == "no token"                # ambiguous: the run fails
        assert unrelated.poll() is None                     # never signalled, never reaped
        assert str(unrelated.pid) not in record["owned"]
    finally:
        timer.join()
        for proc in started:
            proc.kill()
            proc.wait()


@linux_only
def test_containment_owned_survivors_fail_at_the_deadline(tmp_path):
    sent = []
    record, (pid,), elapsed = _run(
        tmp_path, 'sleep 300 &\necho $! >> "$PIDFILE"\nexit 3',
        cleanup_seconds=2, term_grace=0.5, _signal=lambda fd, sig: sent.append(sig))
    try:
        assert record["deadline_hit"] and record["remaining"] == [pid]
        assert len(sent) == 2                               # TERM, then KILL, both no-ops
        assert elapsed < 2 + 2
    finally:
        _clean_up(pid, record["owned"][str(pid)]["start"])


@linux_only
def test_containment_never_signals_a_process_whose_identity_changed(tmp_path):
    sent = []
    pidfile = tmp_path / "pids"

    def read(pid):
        identity = check_bundle._proc_identity(pid)
        if identity is not None and pid in _pids(pidfile):
            identity["start"] += 1                      # as if the pid were reused
        return identity

    record, (pid,), _ = _run(tmp_path, 'sleep 300 &\necho $! >> "$PIDFILE"\nexit 3',
                             _signal=lambda fd, sig: sent.append(sig), _read=read)
    start = _start(pid)
    try:
        assert sent == []
        [found] = [a for a in record["ambiguous"] if a["pid"] == pid]
        assert found["reason"] == "identity changed between the scan and the handle"
        assert str(pid) not in record["owned"]
        assert _still_running(pid, found["scan"]["start"])
    finally:
        _clean_up(pid, start)


@linux_only
def test_containment_a_scan_that_crosses_the_deadline_fails(tmp_path):
    # Sol C1: the clock jumps past the 30 s budget during the cleanup scan,
    # which finds nothing. That must be a hit deadline, not a quiet pass.
    offset = [0.0]
    scans = []

    def clock():
        return time.monotonic() + offset[0]

    def scan():
        scans.append(1)
        found = check_bundle._all_stats()
        if len(scans) == 2:              # the first cleanup scan
            offset[0] += 31
        return found

    record, _, _ = _run(tmp_path, "exit 0", _clock=clock, _scan=scan)
    assert record["exit"] == 0 and record["owned"] == {}
    assert record["deadline_hit"] is True
    assert record["cleanup_seconds"] >= 31


@linux_only
def test_containment_an_unopenable_process_does_not_abandon_the_owned_one(tmp_path):
    # Sol C2: after one process is proven owned, opening the next fails. The
    # owned one must still be stopped and reaped; the other is ambiguous and
    # never signalled; nothing escapes as an exception.
    pidfile = tmp_path / "pids"

    def open_handle(pid):
        pids = _pids(pidfile)
        if len(pids) == 2 and pid == pids[1]:
            raise PermissionError(13, "Permission denied")
        return os.pidfd_open(pid)

    record, (first, second), _ = _run(
        tmp_path, 'sleep 300 &\necho $! >> "$PIDFILE"\nsleep 300 &\necho $! >> "$PIDFILE"\nexit 3',
        _open=open_handle)
    second_start = _start(second)
    try:
        owned = record["owned"][str(first)]
        assert owned["signals"][0][0] == "SIGTERM"
        assert owned["reaped"] == "by the helper, through its pidfd"
        assert not _still_running(first, owned["start"])
        [found] = [a for a in record["ambiguous"] if a["pid"] == second]
        assert found["reason"].startswith("could not be opened: PermissionError")
        assert str(second) not in record["owned"]
        assert _still_running(second, second_start)          # never signalled
        assert record["remaining"] == []
    finally:
        _clean_up(second, second_start)


@linux_only
def test_containment_a_failed_setup_scan_restores_the_subreaper_and_reports(tmp_path):
    before = check_bundle._subreaper()

    def scan():
        raise PermissionError(13, "/proc unreadable")

    record, pids, _ = _run(tmp_path, 'sleep 300 &\necho $! >> "$PIDFILE"\nexit 0', _scan=scan)
    assert check_bundle._subreaper() == before
    assert record["errors"] and record["errors"][0].startswith("containment: PermissionError")
    assert pids == [] and record.get("session") is None      # nothing was started


@linux_only
def test_containment_a_failed_read_is_ambiguous_and_never_signalled(tmp_path):
    sent = []
    pidfile = tmp_path / "pids"

    def read(pid):
        if pid in _pids(pidfile):
            raise OSError(5, "Input/output error")
        return check_bundle._proc_identity(pid)

    record, (pid,), _ = _run(tmp_path, 'sleep 300 &\necho $! >> "$PIDFILE"\nexit 3',
                             _signal=lambda fd, sig: sent.append(sig), _read=read)
    start = _start(pid)
    try:
        assert sent == []
        [found] = [a for a in record["ambiguous"] if a["pid"] == pid]
        assert found["reason"].startswith("could not be read: OSError")
        assert _still_running(pid, start)
    finally:
        _clean_up(pid, start)


@linux_only
def test_containment_starts_nothing_once_the_deadline_passes_while_settling(tmp_path):
    # Sol C1 (second part): the first SIGTERM uses up the rest of the budget.
    # The second owned process must then be neither signalled nor reaped,
    # and both must be reported as remaining.
    offset = [0.0]
    sent = []

    def clock():
        return time.monotonic() + offset[0]

    def signal_(fd, sig):
        sent.append(sig)                 # nothing is really sent
        offset[0] += 31

    record, pids, _ = _run(
        tmp_path, 'sleep 300 &\necho $! >> "$PIDFILE"\nsleep 300 &\necho $! >> "$PIDFILE"\nexit 3',
        _clock=clock, _signal=signal_)
    starts = {pid: _start(pid) for pid in pids}
    try:
        assert len(pids) == 2 and sorted(record["owned"]) == sorted(str(p) for p in pids)
        assert len(sent) == 1                                 # no second, late signal
        assert sum(bool(e["signals"]) for e in record["owned"].values()) == 1
        assert all(e["reaped"] is None for e in record["owned"].values())   # no late reap
        assert record["deadline_hit"] is True
        assert sorted(record["remaining"]) == sorted(pids)
    finally:
        for pid, start in starts.items():
            _clean_up(pid, start)


# -- runtime library selection and source material: the parsers ---------------------

_BUNDLE = "/tmp/appimage_extracted_x/usr/bin/_internal"


def _loader_log(program: str, choices: dict[str, list[str]]) -> str:
    """An LD_DEBUG=libs,files log in glibc's shape: every path tried for a
    library, then the link map for the one that opened."""
    lines = [f"      4242:\tfile=libc.so.6 [0];  needed by {program} [0]"]
    for name, tried in choices.items():
        lines.append(f"      4242:\tfind library={name} [0]; searching")
        lines.append(f"      4242:\t search path={_BUNDLE}:/lib\t\t(LD_LIBRARY_PATH)")
        for path in tried:
            lines.append(f"      4242:\t  trying file={path}")
        lines.append(f"      4242:\tfile={name} [0];  generating link map")
    lines.append(f"      4242:\tinitialize program: {program}")
    return "\n".join(lines) + "\n"


def test_the_loader_log_names_the_file_each_library_came_from():
    text = _loader_log(f"{_BUNDLE}/ffmpeg/ffmpeg", {
        "libgcc_s.so.1": [f"{_BUNDLE}/glibc-hwcaps/x86-64-v3/libgcc_s.so.1",
                          f"{_BUNDLE}/libgcc_s.so.1"],
        "libmvec.so.1": [f"{_BUNDLE}/libmvec.so.1"],
        "libc.so.6": [f"{_BUNDLE}/libc.so.6", "/lib/x86_64-linux-gnu/libc.so.6"],
    })
    parsed = check_bundle.parse_loader_log(text)
    assert parsed["program"] == f"{_BUNDLE}/ffmpeg/ffmpeg"
    assert parsed["chosen"] == {
        "libgcc_s.so.1": f"{_BUNDLE}/libgcc_s.so.1",       # the last one tried
        "libmvec.so.1": f"{_BUNDLE}/libmvec.so.1",
        "libc.so.6": "/lib/x86_64-linux-gnu/libc.so.6",
    }


def test_loader_selection_keeps_only_the_pair_and_says_bundle_or_host(tmp_path):
    (tmp_path / "loader.100").write_text(_loader_log(f"{_BUNDLE}/ffmpeg/ffmpeg", {
        "libgcc_s.so.1": [f"{_BUNDLE}/libgcc_s.so.1"],
        "libmvec.so.1": ["/lib/x86_64-linux-gnu/libmvec.so.1"]}))
    (tmp_path / "loader.101").write_text(_loader_log(f"{_BUNDLE}/ffmpeg/ffprobe", {
        "libgcc_s.so.1": [f"{_BUNDLE}/libgcc_s.so.1"]}))
    (tmp_path / "loader.102").write_text(_loader_log(f"{_BUNDLE}/FlightDVRStudio", {
        "libgcc_s.so.1": [f"{_BUNDLE}/libgcc_s.so.1"]}))
    found = check_bundle.loader_selection(tmp_path)
    assert [(e["tool"], e["bundled_program"]) for e in found] == [
        ("ffmpeg", True), ("ffprobe", True)]               # the app itself is not counted
    ffmpeg, ffprobe = found
    assert ffmpeg["libraries"]["libgcc_s.so.1"]["origin"] == "bundle"
    assert ffmpeg["libraries"]["libmvec.so.1"]["origin"] == "host"
    assert ffprobe["libraries"]["libmvec.so.1"] == {"path": None, "origin": "not loaded"}


def test_the_build_systems_declared_sources_are_listed(tmp_path):
    archive = tmp_path / "builds.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, text in (
                ("FFmpeg-Builds-abc/scripts.d/25-gmp.sh",
                 'SCRIPT_REPO="https://example.invalid/gmp.git"\nSCRIPT_COMMIT="1234"\nffbuild_dockerbuild() {\n}\n'),
                ("FFmpeg-Builds-abc/scripts.d/50-x264.sh",
                 "SCRIPT_REPO='https://example.invalid/x264.git'\nSCRIPT_COMMIT=5678\n"),
                ("FFmpeg-Builds-abc/README.md", "SCRIPT_REPO=not-a-script\n")):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    assert check_bundle.build_system_sources(archive) == [
        {"script": "scripts.d/25-gmp.sh", "SCRIPT_REPO": "https://example.invalid/gmp.git",
         "SCRIPT_COMMIT": "1234"},
        {"script": "scripts.d/50-x264.sh", "SCRIPT_REPO": "https://example.invalid/x264.git",
         "SCRIPT_COMMIT": "5678"},
    ]


def test_ci_collects_the_source_material_and_traces_the_loader():
    workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    assert "check_linux_bundle.py source-material" in workflow
    assert "name: linux-ffmpeg-source" in workflow
    helper = (PACKAGING / "check_linux_bundle.py").read_text(encoding="utf-8")
    assert "run_loader_trace(ev, appimage, env, pin)" in helper
