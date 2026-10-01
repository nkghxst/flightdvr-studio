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
import sys
import tarfile
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
