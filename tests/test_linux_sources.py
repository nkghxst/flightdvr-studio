"""The Linux source bundle collector and the release file selection.

Pure checks on the collector's own decisions: which files a release
attaches (and that evidence and unrelated artifacts never are), how the
build system's stage list and configure flags are read, how a .dsc is read,
and that archive extraction refuses unsafe members. Fetching is exercised in
CI, not here.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "collect_linux_sources", ROOT / "packaging" / "collect_linux_sources.py")
sources = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("collect_linux_sources", sources)
_spec.loader.exec_module(sources)


# -- release file selection -------------------------------------------------------------

def _downloads(tmp_path: Path, **changes) -> Path:
    root = tmp_path / "artifacts"
    files = {
        "linux-appimage/FlightDVR_Studio-2.0.0-x86_64.AppImage": b"appimage",
        "macos-dmg/FlightDVR_Studio-2.0.0-arm64.dmg": b"dmg",
        "windows-installer/FlightDVR_Studio-2.0.0-setup.exe": b"exe",
        "linux-ffmpeg-source/FlightDVR_Studio-2.0.0-linux-ffmpeg-source.tar": b"tar",
        "linux-ffmpeg-source/FlightDVR_Studio-2.0.0-linux-ffmpeg-source.manifest.json": b"{}",
        # Present in the run, never meant for a release:
        "linux-bundle-evidence-ubuntu-22.04/baseline/appimage-check/appimage-check.json": b"{}",
        "linux-bundle-evidence-ubuntu-22.04/baseline/export-check/media/h264-aac.mp4": b"mp4",
        "linux-ffmpeg-source-inputs/source-material.json": b"{}",
        "unrelated-artifact/notes.txt": b"x",
    }
    files.update(changes)
    for name, data in files.items():
        if data is None:
            continue
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def test_only_the_four_release_files_are_selected(tmp_path):
    chosen = sources.select_release_files(_downloads(tmp_path), tmp_path / "release-files")
    assert sorted(p.name for p in chosen) == [
        "FlightDVR_Studio-2.0.0-arm64.dmg",
        "FlightDVR_Studio-2.0.0-linux-ffmpeg-source.tar",
        "FlightDVR_Studio-2.0.0-setup.exe",
        "FlightDVR_Studio-2.0.0-x86_64.AppImage",
    ]
    assert sorted(p.name for p in (tmp_path / "release-files").iterdir()) == sorted(
        p.name for p in chosen)                         # nothing else, no folders


@pytest.mark.parametrize("changes, message", [
    ({"macos-dmg/FlightDVR_Studio-2.0.0-arm64.dmg": None,
      "macos-dmg/README.txt": b"not a dmg"}, "macos-dmg: expected one"),
    ({"linux-appimage/second.AppImage": b"again"}, "linux-appimage: expected one"),
    ({"linux-ffmpeg-source/FlightDVR_Studio-2.0.0-linux-ffmpeg-source.tar": None},
     "linux-ffmpeg-source: expected one"),
], ids=["missing", "ambiguous", "source-bundle-missing"])
def test_a_missing_or_ambiguous_release_file_fails(tmp_path, changes, message):
    with pytest.raises(SystemExit, match=message):
        sources.select_release_files(_downloads(tmp_path, **changes), tmp_path / "out")


def test_a_release_artifact_that_was_not_downloaded_fails(tmp_path):
    root = _downloads(tmp_path)
    for path in sorted((root / "windows-installer").rglob("*"), reverse=True):
        path.unlink()
    (root / "windows-installer").rmdir()
    with pytest.raises(SystemExit, match="windows-installer was not downloaded"):
        sources.select_release_files(root, tmp_path / "out")


# -- reading the build system ---------------------------------------------------------------

DOCKERFILE = """FROM ghcr.io/btbn/ffmpeg-builds/base-linux64:latest AS base-layer
ENV SELF="scripts.d/25-gmp.sh" STAGENAME="25-gmp"
RUN --mount=src=scripts.d/25-gmp.sh,dst=/stage.sh run_stage /stage.sh
ENV SELF="scripts.d/47-vulkan/50-shaderc.sh" STAGENAME="50-shaderc"
ENV \\
    FF_CONFIGURE="--enable-gmp --enable-libshaderc --enable-vulkan" \\
    FF_CFLAGS=""
"""


def test_the_enabled_stages_and_configure_flags_are_read_from_generate_sh_output():
    assert sources.enabled_stages(DOCKERFILE) == [
        {"script": "scripts.d/25-gmp.sh", "stage": "25-gmp"},
        {"script": "scripts.d/47-vulkan/50-shaderc.sh", "stage": "50-shaderc"},
    ]
    assert sources.ff_configure(DOCKERFILE) == [
        "--enable-gmp", "--enable-libshaderc", "--enable-vulkan"]


def test_a_stage_flag_missing_from_the_shipped_binary_is_named():
    shipped = "configuration: --enable-gpl --enable-gmp --enable-vulkan"
    assert sources.configure_cross_check(
        ["--enable-gmp", "--enable-libshaderc", "--enable-vulkan"], shipped) == [
        "--enable-libshaderc"]


def test_the_dsc_checksums_are_read_and_signature_lines_ignored():
    dsc = """-----BEGIN PGP SIGNED MESSAGE-----
Hash: SHA512

Format: 3.0 (quilt)
Source: glibc
Checksums-Sha1:
 aaaa 10 glibc_2.35.orig.tar.xz
Checksums-Sha256:
 %s 18000000 glibc_2.35.orig.tar.xz
 %s 900000 glibc_2.35-0ubuntu3.15.debian.tar.xz
Files:
 cccc 18000000 glibc_2.35.orig.tar.xz
-----BEGIN PGP SIGNATURE-----
""" % ("1" * 64, "2" * 64)
    assert sources.parse_dsc_files(dsc) == [
        {"name": "glibc_2.35.orig.tar.xz", "size": 18000000, "sha256": "1" * 64},
        {"name": "glibc_2.35-0ubuntu3.15.debian.tar.xz", "size": 900000, "sha256": "2" * 64},
    ]


@pytest.mark.parametrize("identities, declared, expected", [
    ([{"commit": "a" * 40}], {"SCRIPT_COMMIT": "a" * 40}, True),
    ([{"commit": "b" * 40}], {"SCRIPT_COMMIT": "a" * 40}, False),
    ([{"revision": "6531"}], {"SCRIPT_REV": "6531"}, True),
    ([{"revision": "6530"}], {"SCRIPT_REV": "6531"}, False),
    ([], {"SCRIPT_COMMIT": "a" * 40}, None),
], ids=["commit", "other-commit", "svn", "other-revision", "no-vcs-left"])
def test_a_fetched_tree_must_be_at_its_declared_commit(identities, declared, expected):
    assert sources._matches_declared(declared, identities) is expected


# -- extraction ------------------------------------------------------------------------------

def _tar(path: Path, members) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, kind, target in members:
            info = tarfile.TarInfo(name)
            if kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, target
                tar.addfile(info)
            else:
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
    return path


@pytest.mark.parametrize("member", [
    ("../evil", "file", ""),
    ("/etc/evil", "file", ""),
    ("repo/link", "symlink", "/etc/passwd"),
    ("repo/link", "symlink", "../../outside"),
], ids=["traversal", "absolute", "absolute-link", "climbing-link"])
def test_extraction_refuses_unsafe_members(tmp_path, member):
    archive = _tar(tmp_path / "bad.tar.gz", [("repo/ok", "file", ""), member])
    with pytest.raises(tarfile.TarError):
        sources.safe_extract(archive, tmp_path / "out")
    assert not (tmp_path / "evil").exists()


def test_extraction_keeps_a_safe_tree(tmp_path):
    archive = _tar(tmp_path / "good.tar.gz", [("repo/ok", "file", ""),
                                              ("repo/inner", "symlink", "ok")])
    sources.safe_extract(archive, tmp_path / "out")
    assert (tmp_path / "out" / "repo" / "ok").read_bytes() == b"x"
