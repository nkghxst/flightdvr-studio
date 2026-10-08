"""Pure failure controls for pinned Qt inputs and source release preparation."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import struct
import sys
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
    alias = framework.replace("/Versions/A/QtMultimedia", "/QtMultimedia")

    class Wheel:
        def locate_file(self, item):
            if item == alias:
                return source  # stand-in for a framework symlink to the RECORD target
            return tmp_path / item

    monkeypatch.setattr(qt.metadata, "distribution", lambda package: Wheel())
    entries = [(framework, framework, "BINARY")]
    assert qt.spec_receipt(entries, inputs)["files"][0]["source_sha256"] == sha
    if alias != framework:
        assert qt.spec_receipt([(alias, alias, "BINARY")], inputs)["files"][0][
            "source_sha256"] == sha
        class WheelWithoutAlias:
            def locate_file(self, item):
                return tmp_path / item

        monkeypatch.setattr(qt.metadata, "distribution", lambda package: WheelWithoutAlias())
        assert qt.spec_receipt([(alias, alias, "BINARY")], inputs)["files"][0][
            "source_sha256"] == sha
        with pytest.raises(ValueError, match="unresolved multimedia framework"):
            qt.spec_receipt([(alias, "other/QtMultimedia", "BINARY")], inputs)
    with pytest.raises(ValueError, match="unresolved multimedia framework"):
        qt.spec_receipt([(framework, "other/QtMultimedia", "BINARY")], inputs)
    with pytest.raises(ValueError, match="unsafe multimedia source path"):
        qt.spec_receipt([(framework, "../outside", "BINARY")], inputs)


def test_observed_macos_framework_toc_binds_binary_and_separate_links(tmp_path):
    framework = "PySide6/Qt/lib/QtMultimedia.framework"
    binary_name = f"{framework}/Versions/A/QtMultimedia"
    binary = tmp_path / "QtMultimedia"
    binary.write_bytes(b"recorded framework binary")
    sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    inputs = {"platform": "macos", "source_complete": False, "release_ready": False,
              "files": [{"package": "PySide6-Addons", "file": binary_name,
                         "sha256": sha, "wheel_sha256": "a" * 64}]}
    # These are the exact BINARY/SYMLINK shapes printed by CI diagnostic
    # 37837725044. Neither framework alias exists in the installed wheel.
    entries = [
        (binary_name, str(binary), "BINARY"),
        ("QtMultimedia", binary_name, "SYMLINK"),
        (f"{framework}/QtMultimedia", "Versions/Current/QtMultimedia", "SYMLINK"),
        (f"{framework}/Resources", "Versions/Current/Resources", "SYMLINK"),
        (f"{framework}/Versions/Current", "A", "SYMLINK"),
    ]
    collection = qt.spec_receipt(entries, inputs)
    assert [item["path"] for item in collection["files"]] == [binary_name]
    assert {(item["path"], item["target"]) for item in collection["links"]} == {
        (dest, source) for dest, source, kind in entries if kind == "SYMLINK"}
    with pytest.raises(ValueError, match="unsafe multimedia symlink target"):
        qt.spec_receipt(entries[:-1] + [(entries[-1][0], "../A", "SYMLINK")], inputs)
    with pytest.raises(ValueError, match="unsafe multimedia collection path"):
        qt.spec_receipt(entries + [("../QtMultimedia", "A", "SYMLINK")], inputs)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX framework symlink topology")
def test_macos_framework_links_require_exact_targets_and_bound_content(tmp_path):
    bundle = tmp_path / "app"
    root = bundle / "Contents" / "Frameworks"
    framework = "PySide6/Qt/lib/QtMultimedia.framework"
    binary_name = f"{framework}/Versions/A/QtMultimedia"
    binary = root / binary_name
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\xcf\xfa\xed\xfe" + struct.pack("<I", 0x0100000c))
    info_name = f"{framework}/Versions/A/Resources/Info.plist"
    info = root / info_name
    info.parent.mkdir()
    info.write_bytes(b"plist")
    links = [
        ("QtMultimedia", binary_name),
        (f"{framework}/QtMultimedia", "Versions/Current/QtMultimedia"),
        (f"{framework}/Resources", "Versions/Current/Resources"),
        (f"{framework}/Versions/Current", "A"),
    ]
    try:
        for name, target in links:
            path = root / name
            path.symlink_to(target, target_is_directory=name.endswith(("Resources", "Current")))
    except OSError as exc:
        pytest.skip(f"symlinks unavailable on this host: {exc}")
    sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    info_sha = hashlib.sha256(info.read_bytes()).hexdigest()
    wheel = "a" * 64
    collection = {"platform": "macos", "files": [
        {"path": binary_name, "kind": "BINARY", "source_sha256": sha,
         "wheel_package": "PySide6-Addons", "wheel_file": binary_name, "wheel_sha256": wheel},
        {"path": info_name, "kind": "DATA", "source_sha256": info_sha,
         "wheel_package": "PySide6-Addons", "wheel_file": info_name, "wheel_sha256": wheel},
    ], "links": [{"path": name, "target": target} for name, target in links]}
    inputs = {"platform": "macos", "release_ready": False,
              "wheels": [{"package": "PySide6-Addons", "sha256": wheel}],
              "files": [{"package": "PySide6-Addons", "file": binary_name, "sha256": sha},
                        {"package": "PySide6-Addons", "file": info_name, "sha256": info_sha}]}
    report = {"ffmpeg_libraries": []}
    failures = scanner.binding_check(bundle, report, inputs, collection)
    assert not any("symlink" in failure or "wheel collection origin" in failure
                   or "architecture" in failure for failure in failures), failures
    assert len(report["selected_links"]) == 4
    alias = root / "QtMultimedia"
    alias.unlink()
    alias.symlink_to(info_name)
    failures = scanner.binding_check(bundle, {"ffmpeg_libraries": []}, inputs, collection)
    assert any("multimedia symlink target changed" in failure for failure in failures)


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


def test_macos_signing_requires_a_matching_pre_sign_wheel_receipt(tmp_path):
    bundle = tmp_path / "app"
    directory = bundle / "Contents" / "Frameworks" / "PySide6"
    directory.mkdir(parents=True)
    payload = b"\xcf\xfa\xed\xfe" + struct.pack("<I", 0x0100000c)
    payload += b"\0LGPL version 2.1 or later\0FFmpeg n7.1.5/lib\0"
    names = ["libavcodec.61.dylib", "libavformat.61.dylib", "libavutil.59.dylib",
             "libswresample.5.dylib", "libswscale.8.dylib"]
    plugin = directory / "plugins" / "multimedia" / "libffmpegmediaplugin.dylib"
    plugin.parent.mkdir(parents=True)
    paths = [directory / name for name in names] + [plugin]
    for path in paths:
        path.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    entries = [{"path": path.relative_to(bundle / "Contents" / "Frameworks").as_posix(),
                "source_sha256": sha, "wheel_package": "PySide6-Addons",
                "wheel_file": "PySide6/libavcodec.61.dylib", "wheel_sha256": "a" * 64}
               for path in paths]
    inputs = {"platform": "macos", "release_ready": False,
              "wheels": [{"package": "PySide6-Addons", "sha256": "a" * 64}],
              "files": [{"package": "PySide6-Addons", "file": "PySide6/libavcodec.61.dylib",
                         "sha256": sha}]}
    collection = {"platform": "macos", "files": entries}
    pre, failures = scanner.check(bundle)
    assert failures == []
    assert scanner.binding_check(bundle, pre, inputs, collection) == []
    pre["failures"] = []
    paths[0].write_bytes(payload + b"signed")
    post, _ = scanner.check(bundle)
    assert scanner.binding_check(bundle, post, inputs, collection, pre) == []
    assert next(x for x in post["selected_files"] if x["path"] == entries[0]["path"])[
        "transformation"] == "ad-hoc codesign after verified wheel-byte collection"
    unsigned, _ = scanner.check(bundle)
    assert any("unreconciled" in failure
               for failure in scanner.binding_check(bundle, unsigned, inputs, collection))


def test_linux_collection_coalesces_only_identical_wheel_destinations(tmp_path):
    bundle = tmp_path / "bundle"
    internal = bundle / "_internal"
    payload = b"\x7fELF\x02\x01" + b"\0" * 12 + struct.pack("<H", 62)
    payload += b"\0LGPL version 2.1 or later\0FFmpeg n7.1.5/lib\0"
    sha = hashlib.sha256(payload).hexdigest()
    files = []
    entries = []

    def add(dest: str, wheel_file: str, present: bool = True):
        if present:
            path = internal / dest
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        if not any(x["file"] == wheel_file for x in files):
            files.append({"package": "PySide6-Addons", "file": wheel_file, "sha256": sha})
        entries.append({"path": dest, "source_sha256": sha,
                        "wheel_package": "PySide6-Addons", "wheel_file": wheel_file,
                        "wheel_sha256": "a" * 64})

    for name in ("libavcodec.so.61", "libavformat.so.61", "libavutil.so.59",
                 "libswresample.so.5", "libswscale.so.8"):
        add(name, "PySide6/Qt/lib/" + name)
    add("PySide6/Qt/lib/libavcodec.so.61", "PySide6/Qt/lib/libavcodec.so.61", False)
    add("libQt6FFmpegStub-ssl.so.3", "PySide6/Qt/lib/libQt6FFmpegStub-ssl.so.3")
    add("PySide6/Qt/lib/libQt6FFmpegStub-ssl.so.3",
        "PySide6/Qt/lib/libQt6FFmpegStub-ssl.so.3", False)
    add("PySide6/Qt/plugins/multimedia/libffmpegmediaplugin.so",
        "PySide6/Qt/plugins/multimedia/libffmpegmediaplugin.so")
    inputs = {"platform": "linux", "release_ready": False,
              "wheels": [{"package": "PySide6-Addons", "sha256": "a" * 64}], "files": files}
    collection = {"platform": "linux", "files": entries}
    report, failures = scanner.check(bundle)
    assert failures == []
    assert scanner.binding_check(bundle, report, inputs, collection) == []
    coalesced = [x for x in report["selected_files"] if x.get("coalesced_to")]
    assert {x["path"] for x in coalesced} == {
        "PySide6/Qt/lib/libavcodec.so.61", "PySide6/Qt/lib/libQt6FFmpegStub-ssl.so.3"}
    (internal / "libavcodec.so.61").unlink()
    missing, _ = scanner.check(bundle)
    assert any("selected multimedia path missing" in failure
               for failure in scanner.binding_check(bundle, missing, inputs, collection))


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
