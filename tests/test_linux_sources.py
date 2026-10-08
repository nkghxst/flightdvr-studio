"""The Linux source bundle collector and the release file selection.

Pure checks on the collector's own decisions: which files a release
attaches (and that evidence and unrelated artifacts never are), how the
build system's stage list and configure flags are read, how a .dsc is read,
and that archive extraction refuses unsafe members. Fetching is exercised in
CI, not here.
"""

from __future__ import annotations

import importlib.util
import hashlib
import io
import json
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

def _qt_lock() -> dict:
    lock = json.loads((ROOT / "packaging" / "qt-multimedia-sources.json").read_text())
    for field in ("upstream_build_receipt", "dependency_source_closure",
                  "modification_record", "replacement_acceptance"):
        lock[field] = {"independently_verified": True, "evidence_sha256": "a" * 64}
    lock["delivery"]["approved"] = True
    lock["sources"] = [{"id": "synthetic", "filename": "synthetic.tar.xz",
                        "sha256": hashlib.sha256(b"source").hexdigest()}]
    return lock


def _downloads(tmp_path: Path, **changes) -> Path:
    root = tmp_path / "artifacts"
    lock = _qt_lock()
    qt_name = "FlightDVR_Studio-2.0.0-qt-multimedia-source.tar"
    declared = [{"id": x["id"], "verified": True, "sha256": x["sha256"]}
                for x in lock["sources"]]
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        payloads = {"sources/synthetic.tar.xz": b"source",
                    "MANIFEST.json": json.dumps({"source_complete": True,
                                                   "release_ready": True,
                                                   "sources": declared}).encode(),
                    "LICENSE.LGPL-2.1.txt": b"licence",
                    "THIRD-PARTY-NOTICES.md": b"notices",
                    "qt-multimedia-sources.json": b"lock"}
        for name, data in payloads.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    qt_body = stream.getvalue()
    qt_manifest = {"schema_version": 1, "source_complete": True,
                   "release_ready": True, "unresolved": [], "delivery": lock["delivery"],
                   "sources": declared,
                   "platform_payloads": {p: {} for p in lock["platforms"]},
                   "assets": [{"filename": qt_name, "bytes": len(qt_body),
                               "sha256": hashlib.sha256(qt_body).hexdigest()}]}
    files = {
        "linux-appimage/FlightDVR_Studio-2.0.0-x86_64.AppImage": b"appimage",
        "macos-dmg/FlightDVR_Studio-2.0.0-arm64.dmg": b"dmg",
        "windows-installer/FlightDVR_Studio-2.0.0-setup.exe": b"exe",
        "linux-ffmpeg-source/FlightDVR_Studio-2.0.0-linux-ffmpeg-source.tar": b"tar",
        "linux-ffmpeg-source/FlightDVR_Studio-2.0.0-linux-ffmpeg-source.manifest.json": b"{}",
        "qt-multimedia-source/" + qt_name: qt_body,
        "qt-multimedia-source/qt-multimedia-source.manifest.json":
            json.dumps(qt_manifest).encode(),
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
    chosen = sources.select_release_files(_downloads(tmp_path), tmp_path / "release-files", _qt_lock())
    assert sorted(p.name for p in chosen) == [
        "FlightDVR_Studio-2.0.0-arm64.dmg",
        "FlightDVR_Studio-2.0.0-linux-ffmpeg-source.tar",
        "FlightDVR_Studio-2.0.0-qt-multimedia-source.tar",
        "FlightDVR_Studio-2.0.0-setup.exe",
        "FlightDVR_Studio-2.0.0-x86_64.AppImage",
        "qt-multimedia-source.manifest.json",
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
        sources.select_release_files(_downloads(tmp_path, **changes), tmp_path / "out", _qt_lock())


def test_a_release_artifact_that_was_not_downloaded_fails(tmp_path):
    root = _downloads(tmp_path)
    for path in sorted((root / "windows-installer").rglob("*"), reverse=True):
        path.unlink()
    (root / "windows-installer").rmdir()
    with pytest.raises(SystemExit, match="windows-installer was not downloaded"):
        sources.select_release_files(root, tmp_path / "out", _qt_lock())


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


A, B, C = "a" * 40, "b" * 40, "c" * 40
REPO_A = "https://example.invalid/a.git"
REPO_B = "https://example.invalid/b.git"


def _git(path, url, commit):
    return {"path": path, "kind": "git", "url": url, "commit": commit}


def _statuses(bound):
    return [b["status"].split(":")[0] for b in bound]


def test_a_matching_submodule_never_stands_in_for_the_declared_repository():
    # Sol F1, first case: the primary is at the wrong commit; a submodule from
    # another remote happens to carry the declared one.
    bound = sources.bind_declared(
        "50-x", {"SCRIPT_REPO": REPO_A, "SCRIPT_COMMIT": A},
        [_git(".", REPO_A, B), _git("third_party/x", "https://other.invalid/x.git", A)],
        "git-mini-clone")
    assert _statuses(bound) == ["wrong"]
    assert sources.declared_status(bound).startswith("FAILED: declared source 1: wrong")


def test_every_numbered_declaration_is_checked_against_its_own_repository():
    # Sol F1, second case: the primary is right, the second declared source
    # is at another commit.
    declared = {"SCRIPT_REPO": REPO_A, "SCRIPT_COMMIT": A,
                "SCRIPT_REPO2": REPO_B, "SCRIPT_COMMIT2": B}
    bound = sources.bind_declared(
        "45-x", declared, [_git("headers", REPO_A, A), _git("loader", REPO_B, C)], "")
    assert _statuses(bound) == ["matched", "wrong"]
    assert "declared source 2: wrong" in sources.declared_status(bound)
    good = sources.bind_declared(
        "45-x", declared, [_git("headers", REPO_A, A), _git("loader", REPO_B, B)], "")
    assert [(b["status"], b["path"]) for b in good] == [("matched", "headers"),
                                                        ("matched", "loader")]
    assert sources.declared_status(good) == "collected"


def test_several_declarations_from_one_remote_each_need_their_own_tree():
    # nv-codec-headers: three branches of one repository in three folders.
    declared = {"SCRIPT_REPO": REPO_A, "SCRIPT_COMMIT": A, "SCRIPT_REPO2": REPO_A,
                "SCRIPT_COMMIT2": B, "SCRIPT_REPO3": REPO_A, "SCRIPT_COMMIT3": C}
    fetched = [_git("ffnvcodec", REPO_A, A), _git("ffnvcodec2", REPO_A, B),
               _git("ffnvcodec3", REPO_A, C)]
    bound = sources.bind_declared("50-ffnvcodec", declared, fetched, "")
    assert [b["path"] for b in bound] == ["ffnvcodec", "ffnvcodec2", "ffnvcodec3"]
    # One tree cannot satisfy two declarations.
    bound = sources.bind_declared("50-ffnvcodec", declared,
                                  [_git("ffnvcodec", REPO_A, A), _git("ffnvcodec2", REPO_A, B)], "")
    assert _statuses(bound) == ["matched", "matched", "missing"]
    assert sources.declared_status(bound).startswith("FAILED: declared source 3: missing")


def test_a_tag_is_resolved_on_its_own_remote():
    declared = {"SCRIPT_REPO": REPO_A, "SCRIPT_COMMIT": "v1.2.3"}
    fetched = [_git(".", REPO_A, A)]
    asked = []

    def resolve(remote, tag):
        asked.append((remote, tag))
        return {A}

    bound = sources.bind_declared("40-x", declared, fetched, "", resolve=resolve)
    assert asked == [(REPO_A, "v1.2.3")] and bound[0]["status"] == "matched"
    bound = sources.bind_declared("40-x", declared, fetched, "", resolve=lambda r, t: {B})
    assert bound[0]["status"].startswith("wrong")


def test_svn_revisions_and_mirrors_and_url_spelling():
    svn = sources.bind_declared(
        "50-lame", {"SCRIPT_REPO": "https://svn.invalid/lame", "SCRIPT_REV": "6531"},
        [{"path": "lame", "kind": "svn", "url": "https://svn.invalid/lame", "revision": "6531"}],
        "")
    assert svn[0]["status"] == "matched"
    other = sources.bind_declared(
        "50-lame", {"SCRIPT_REPO": "https://svn.invalid/lame", "SCRIPT_REV": "6531"},
        [{"path": "lame", "kind": "svn", "url": "https://svn.invalid/lame", "revision": "6530"}],
        "")
    assert other[0]["status"].startswith("wrong")
    mirrored = sources.bind_declared(
        "20-x", {"SCRIPT_REPO": "https://upstream.invalid/x.git",
                 "SCRIPT_MIRROR": "https://mirror.invalid/x", "SCRIPT_COMMIT": A},
        [_git("x", "https://mirror.invalid/x.git/", A)], "")
    assert mirrored[0]["status"] == "matched"


def test_recipe_only_is_allowed_only_where_named_and_where_the_recipe_does_it():
    declared = {"SCRIPT_REPO": REPO_A, "SCRIPT_COMMIT": A}
    amf = sources.bind_declared("50-amf", declared, [], "git-mini-clone .\nrm -rf .git Thirdparty")
    assert amf[0]["status"] == "recipe-only"
    assert sources.declared_status(amf).startswith("collected (declared source 1 pinned")
    # The same shape anywhere else is a missing source, not a quiet pass.
    other = sources.bind_declared("50-other", declared, [], "rm -rf .git")
    assert other[0]["status"].startswith("missing")
    # Named, but the recipe no longer removes it: then it must be found.
    stale = sources.bind_declared("50-amf", declared, [], "git-mini-clone .")
    assert stale[0]["status"].startswith("missing")
    # libiconv: the first source checked, the second (gnulib) recipe-only.
    iconv = sources.bind_declared(
        "20-libiconv",
        {"SCRIPT_MIRROR": REPO_A, "SCRIPT_COMMIT": A, "SCRIPT_MIRROR2": REPO_B,
         "SCRIPT_COMMIT2": B},
        [_git("iconv", REPO_A, A)], "... && rm -rf gnulib/.git")
    assert _statuses(iconv) == ["matched", "recipe-only"]


# -- Ubuntu sources must be authenticated by the signed index -------------------------

def _ubuntu(monkeypatch, tmp_path, listed: bool) -> dict:
    payload = b"source tarball"
    digest = __import__("hashlib").sha256(payload).hexdigest()
    dsc = ("Format: 3.0 (quilt)\nChecksums-Sha256:\n"
           f" {digest} {len(payload)} glibc_2.35.orig.tar.xz\nFiles:\n")
    served = {"glibc_2.35-0ubuntu3.15.dsc": dsc.encode(), "glibc_2.35.orig.tar.xz": payload}

    def get(url, target, timeout=900):
        target.write_bytes(served[url.rsplit("/", 1)[1]])

    monkeypatch.setattr(sources, "_get", get)
    monkeypatch.setattr(sources, "_archive_listing", lambda *a: (
        {"listed": True, "pocket": "jammy-security", "rationale": "verified"} if listed
        else {"listed": False, "rationale": "index unavailable"}))
    out = tmp_path / "ubuntu"
    out.mkdir(parents=True)
    return sources.fetch_ubuntu_source("glibc", "2.35-0ubuntu3.15", out, tmp_path)


def test_ubuntu_source_counts_only_when_the_signed_index_lists_it(monkeypatch, tmp_path):
    assert _ubuntu(monkeypatch, tmp_path / "ok", listed=True)["status"] == "collected"
    failed = _ubuntu(monkeypatch, tmp_path / "unlisted", listed=False)
    assert failed["files"][0]["matches_dsc"] is True          # self-consistent ...
    assert failed["status"] == ("FAILED: not authenticated by the signed Ubuntu "
                                "archive index: index unavailable")   # ... is not enough


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
