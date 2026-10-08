"""Pure failure controls for pinned Qt inputs and source release preparation."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import struct
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "collect_qt_multimedia_sources", ROOT / "packaging" / "collect_qt_multimedia_sources.py")
qt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qt)
_scanner_spec = importlib.util.spec_from_file_location(
    "check_qt_multimedia_for_sources", ROOT / "packaging" / "check_qt_multimedia.py")
scanner = importlib.util.module_from_spec(_scanner_spec)
_scanner_spec.loader.exec_module(scanner)


def test_same_quartet_must_match_platform_url_version_and_hash():
    lock = qt.load_lock()
    wheels = lock["platforms"]["windows"]["wheels"]
    report = {"install": [{"metadata": {"name": w["package"], "version": w["version"]},
                           "download_info": {"url": w["url"],
                                             "archive_info": {"hashes": {"sha256": w["sha256"]}}}}
                          for w in wheels]}
    assert qt.match_inputs(report, lock)["platform"] == "windows"
    for field, value in (("sha256", "0" * 64), ("url", "https://wrong.invalid/a.whl")):
        bad = copy.deepcopy(report)
        if field == "sha256":
            bad["install"][0]["download_info"]["archive_info"]["hashes"]["sha256"] = value
        else:
            bad["install"][0]["download_info"]["url"] = value
        with pytest.raises(ValueError, match="differs from lock"):
            qt.match_inputs(bad, lock)
    bad = copy.deepcopy(report)
    bad["install"][0]["metadata"]["version"] = "6.11.1"
    with pytest.raises(ValueError, match="differs from lock"):
        qt.match_inputs(bad, lock)


def test_spec_selection_refuses_unpinned_multimedia_bytes(tmp_path):
    source = tmp_path / "avcodec-61.dll"
    source.write_bytes(b"wheel library")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    inputs = {"platform": "windows", "source_complete": False, "release_ready": False,
              "files": [{"package": "PySide6-Addons", "file": "PySide6/avcodec-61.dll",
                         "sha256": sha, "wheel_sha256": "a" * 64}]}
    entries = [("PySide6/avcodec-61.dll", str(source), "BINARY")]
    assert qt.spec_receipt(entries, inputs)["files"][0]["source_sha256"] == sha
    source.write_bytes(b"altered library")
    with pytest.raises(ValueError, match="not a locked wheel RECORD"):
        qt.spec_receipt(entries, inputs)


@pytest.mark.parametrize("framework, platform", [
    ("PySide6/Qt/lib/QtMultimedia.framework/Versions/A/QtMultimedia", "macos"),
    ("PySide6/Qt/lib/libavformat.so.61", "linux"),
])
def test_framework_source_resolves_only_an_exact_record_path(tmp_path, monkeypatch,
                                                               framework, platform):
    source = tmp_path / framework
    source.parent.mkdir(parents=True)
    source.write_bytes(b"framework bytes")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    inputs = {"platform": platform, "source_complete": False, "release_ready": False,
              "files": [{"package": "PySide6-Addons", "file": framework,
                         "sha256": sha, "wheel_sha256": "a" * 64}]}

    class Wheel:
        def locate_file(self, item):
            return tmp_path / item

    monkeypatch.setattr(qt.metadata, "distribution", lambda package: Wheel())
    entries = [(framework, framework, "BINARY")]
    assert qt.spec_receipt(entries, inputs)["files"][0]["source_sha256"] == sha
    with pytest.raises(ValueError, match="unresolved multimedia framework"):
        qt.spec_receipt([(framework, "other/QtMultimedia", "BINARY")], inputs)


def test_present_tag_and_fake_complete_manifest_cannot_cross_provenance_gate(tmp_path):
    manifest = {"schema_version": 1, "source_complete": True, "release_ready": True,
                "unresolved": [], "delivery": {"approved": True, "format": "single-companion"}}
    with pytest.raises(ValueError, match="provenance"):
        qt.release_check(manifest, tmp_path)


def test_source_archive_rejects_traversal_and_links(tmp_path):
    archive = tmp_path / "source.tar"
    for name, kind in (("../escape", "file"), ("root/link", "link")):
        with tarfile.open(archive, "w") as output:
            info = tarfile.TarInfo(name)
            if kind == "link":
                info.type = tarfile.SYMTYPE
                info.linkname = "../escape"
                output.addfile(info)
            else:
                info.size = 1
                output.addfile(info, io.BytesIO(b"x"))
        with pytest.raises(ValueError, match="unsafe|links"):
            qt.safe_archive(archive, [])


def test_source_archive_requires_declared_build_files_and_unique_members(tmp_path):
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as output:
        for _ in range(2):
            info = tarfile.TarInfo("root/configure")
            info.size = 1
            output.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="duplicate"):
        qt.safe_archive(archive, ["configure"])
    with tarfile.open(archive, "w") as output:
        info = tarfile.TarInfo("root/configure")
        info.size = 1
        output.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="required"):
        qt.safe_archive(archive, ["configure", "COPYING.LGPLv2.1"])


def test_prepare_records_missing_sources_without_clearance(tmp_path):
    lock = qt.load_lock()
    report = qt.prepare(tmp_path / "out", tmp_path / "cache", lock)
    assert report["source_complete"] is False
    assert report["release_ready"] is False
    assert report["assets"] == []
    assert len([x for x in report["sources"] if not x["verified"]]) == len(lock["sources"])
    assert any("U1 wheel-build provenance" in gap for gap in report["unresolved"])
    assert json.loads((tmp_path / "out" / "qt-multimedia-source.manifest.json").read_text()) == report
    first = (tmp_path / "out" / "qt-multimedia-source.manifest.json").read_bytes()
    assert qt.prepare(tmp_path / "out", tmp_path / "cache", lock) == report
    assert (tmp_path / "out" / "qt-multimedia-source.manifest.json").read_bytes() == first


def test_final_inventory_rejects_absent_library_and_unbound_extra(tmp_path):
    bundle = tmp_path / "bundle"
    plugin = bundle / "_internal" / "PySide6" / "Qt" / "plugins" / "multimedia" / "ffmpegmediaplugin.dll"
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(b"MZ" + b"\0" * 100)
    selected = {"path": "PySide6/Qt/plugins/multimedia/ffmpegmediaplugin.dll",
                "source_sha256": hashlib.sha256(plugin.read_bytes()).hexdigest(),
                "wheel_package": "PySide6-Addons", "wheel_file": "file", "wheel_sha256": "a" * 64}
    files = [{"package": "PySide6-Addons", "file": "file", "sha256": selected["source_sha256"]}]
    inputs = {"platform": "windows", "wheels": [{"package": "PySide6-Addons", "sha256": "a" * 64}],
              "files": files, "release_ready": False}
    collection = {"platform": "windows", "files": [selected]}
    failures = scanner.binding_check(bundle, {}, inputs, collection)
    assert any("missing FFmpeg shared library families" in x for x in failures)
    assert any("wrong or unreadable architecture" in x for x in failures)
    extra = plugin.parent / "mysterymultimediaplugin.dll"
    extra.write_bytes(b"unknown")
    failures = scanner.binding_check(bundle, {}, inputs, collection)
    assert any("lacks wheel collection origin" in x for x in failures)


def test_final_inventory_binds_complete_pe_library_set_to_wheel_bytes(tmp_path):
    bundle = tmp_path / "bundle"
    directory = bundle / "_internal" / "PySide6"
    directory.mkdir(parents=True)
    payload = bytearray(128)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3c, 64)
    payload[64:68] = b"PE\0\0"
    struct.pack_into("<H", payload, 68, 0x8664)
    payload.extend(b"\0LGPL version 2.1 or later\0FFmpeg n7.1.5/lib\0")
    names = ["avcodec-61.dll", "avformat-61.dll", "avutil-59.dll",
             "swresample-5.dll", "swscale-8.dll"]
    plugin = directory / "Qt" / "plugins" / "multimedia" / "ffmpegmediaplugin.dll"
    plugin.parent.mkdir(parents=True)
    paths = [directory / name for name in names] + [plugin]
    for path in paths:
        path.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    entries = [{"path": path.relative_to(bundle / "_internal").as_posix(),
                "source_sha256": sha, "wheel_package": "PySide6-Addons",
                "wheel_file": "PySide6/avcodec-61.dll", "wheel_sha256": "a" * 64}
               for path in paths]
    inputs = {"platform": "windows", "release_ready": False,
              "wheels": [{"package": "PySide6-Addons", "sha256": "a" * 64}],
              "files": [{"package": "PySide6-Addons", "file": "PySide6/avcodec-61.dll",
                         "sha256": sha}]}
    report, failures = scanner.check(bundle)
    assert failures == []
    assert scanner.binding_check(bundle, report, inputs,
                                 {"platform": "windows", "files": entries}) == []
    assert len(report["selected_files"]) == 6
    assert all(x["final_architecture"] == ["x86_64"] for x in report["selected_files"])


def test_selector_rejects_current_manifest_even_if_asset_bytes_exist(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "collect_linux_sources_for_qt", ROOT / "packaging" / "collect_linux_sources.py")
    selector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selector)
    downloads = tmp_path / "downloads"
    folder = downloads / "qt-multimedia-source"
    folder.mkdir(parents=True)
    (folder / "qt-multimedia-source.manifest.json").write_text(json.dumps({
        "schema_version": 1, "source_complete": True, "release_ready": True,
        "unresolved": [], "delivery": {"approved": True}}))
    with pytest.raises(SystemExit, match="provenance"):
        selector.select_release_files(downloads, tmp_path / "out")
    assert not (tmp_path / "out").exists()
