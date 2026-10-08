#!/usr/bin/env python3
"""Static Qt input receipts and preparatory source companions, never legal clearance.

  inputs PIP_REPORT OUTPUT_JSON
  prepare OUTPUT_DIR [--cache DIR] [--fetch]
  release-check MANIFEST_JSON

`prepare` succeeds when it records evidence, including unavailable inputs. It
does not assert source completeness. `release-check` fails for every unresolved
gate; the present lock deliberately cannot authorize a release. No Qt import,
device enumeration, build recipe execution, or archive extraction occurs here.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata as metadata
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

LOCK = Path(__file__).with_name("qt-multimedia-sources.json")
PACKAGES = {"pyside6", "pyside6_addons", "pyside6_essentials", "shiboken6"}
ALLOWED_HOSTS = {"download.qt.io", "codeload.github.com", "raw.githubusercontent.com"}
ASSET_LIMIT = 2 * 1024 ** 3


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def normalized(name: str) -> str:
    return name.lower().replace("-", "_")


def load_lock(path: Path = LOCK) -> dict:
    lock = json.loads(path.read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1 or lock.get("source_asset_limit_bytes") != ASSET_LIMIT:
        raise ValueError("unrecognized lock schema or source asset limit")
    for platform, entry in lock["platforms"].items():
        if {normalized(w["package"]) for w in entry["wheels"]} != PACKAGES:
            raise ValueError(f"{platform}: missing/duplicate quartet")
        if len(entry["wheels"]) != 4:
            raise ValueError(f"{platform}: expected exactly four wheels")
        for wheel in entry["wheels"]:
            if wheel["version"] != lock["pyside_version"] or not re.fullmatch(r"[a-f0-9]{64}", wheel["sha256"]):
                raise ValueError("wheel version/hash mismatch in lock")
    return lock


def match_inputs(report: dict, lock: dict) -> dict:
    """Bind pip's actual downloads to ONE exact platform and quartet."""
    taken = [x for x in report.get("install", [])
             if normalized(x["metadata"]["name"]) in PACKAGES]
    if len(taken) != 4 or {normalized(x["metadata"]["name"]) for x in taken} != PACKAGES:
        raise ValueError("pip report must contain exactly the complete quartet")
    for platform, entry in lock["platforms"].items():
        expected = {normalized(w["package"]): w for w in entry["wheels"]}
        actual = []
        for item in taken:
            wheel = expected[normalized(item["metadata"]["name"])]
            download = item["download_info"]
            sha = download.get("archive_info", {}).get("hashes", {}).get("sha256")
            filename = download["url"].rsplit("/", 1)[-1]
            if (item["metadata"]["version"] != wheel["version"]
                    or filename != wheel["filename"] or sha != wheel["sha256"]
                    or download["url"] != wheel["url"]):
                break
            actual.append(dict(package=wheel["package"], version=wheel["version"],
                               filename=filename, url=download["url"], sha256=sha))
        else:
            return dict(platform=platform, wheels=sorted(actual, key=lambda x: x["package"]))
    raise ValueError("tested/packaged wheel version, hash, URL or platform differs from lock")


def multimedia_file(name: str) -> bool:
    p = PurePosixPath(name.replace("\\", "/"))
    return ("multimedia" in [part.lower() for part in p.parts]
            or "multimedia" in p.name.lower() or "ffmpegstub" in p.name.lower()
            or bool(re.match(r"^(lib)?(avcodec|avformat|avutil|swresample|swscale)[-.]", p.name, re.I)))


def spec_receipt(entries: list[tuple], inputs: dict) -> dict:
    """Bind Analysis' selected multimedia files to installed wheel RECORD bytes."""
    if inputs.get("source_complete") is not False or inputs.get("release_ready") is not False:
        raise ValueError("unexpected input receipt readiness")
    by_hash = {}
    for item in inputs["files"]:
        by_hash.setdefault(item["sha256"], []).append(item)
    selected = []
    names = set()
    for dest, source, _kind in entries:
        if not multimedia_file(str(dest)):
            continue
        relative = str(dest).replace("\\", "/")
        if relative in names:
            raise ValueError(f"duplicate multimedia collection path: {relative}")
        names.add(relative)
        source_path = Path(source)
        if not source_path.is_file():
            # PyInstaller names Qt libraries relative to site-packages; a
            # macOS framework may use a top-level symlink alias. Resolve the
            # entry within its installed distribution and accept it only if
            # the target is an exact, hashed wheel RECORD file.
            name = PurePosixPath(str(source).replace("\\", "/"))
            if (not name.parts or name.is_absolute() or ".." in name.parts
                    or ":" in name.parts[0]):
                raise ValueError(f"unsafe multimedia source path: {relative}")
            wheel_matches = []
            for origin in inputs["files"]:
                dist = metadata.distribution(origin["package"])
                root = Path(dist.locate_file("")).resolve()
                alias = Path(dist.locate_file(str(name)))
                target = Path(dist.locate_file(origin["file"]))
                try:
                    resolved = alias.resolve(strict=True)
                    recorded = target.resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                if (resolved.is_file() and resolved.is_relative_to(root)
                        and recorded.is_relative_to(root) and resolved == recorded):
                    wheel_matches.append((alias, origin))
            if not wheel_matches or len({x[1]["package"] for x in wheel_matches}) != 1:
                raise ValueError(f"unresolved multimedia framework source: {relative}")
            source_path = wheel_matches[0][0]
        sha = digest(source_path)
        matches = by_hash.get(sha, [])
        if not matches:
            raise ValueError(f"multimedia file is not a locked wheel RECORD byte: {relative}")
        packages = {m["package"] for m in matches}
        if len(packages) != 1:
            raise ValueError(f"ambiguous multimedia wheel origin: {relative}")
        origin = sorted(matches, key=lambda x: x["file"])[0]
        selected.append(dict(path=relative, source_sha256=sha,
                             wheel_package=origin["package"], wheel_file=origin["file"],
                             wheel_sha256=origin["wheel_sha256"]))
    if not selected:
        raise ValueError("no multimedia files selected by PyInstaller")
    return dict(schema_version=1, platform=inputs["platform"],
                files=sorted(selected, key=lambda x: x["path"]),
                source_complete=False, release_ready=False,
                transformation="Analysis source bytes; final package bytes checked separately")


def write_spec_receipt(entries: list[tuple], inputs_path: Path, output_path: Path) -> None:
    if not inputs_path.is_file():
        raise ValueError("locked Qt input receipt missing; run the CI input step")
    inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
    collection = spec_receipt(entries, inputs)
    output_path.write_text(json.dumps(collection, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")


def installed_receipt(report: dict, lock: dict) -> dict:
    receipt = match_inputs(report, lock)
    files = []
    for wheel in receipt["wheels"]:
        dist = metadata.distribution(wheel["package"])
        if dist.version != wheel["version"]:
            raise ValueError("installed quartet differs from pip receipt")
        for item in dist.files or []:
            if not multimedia_file(str(item)):
                continue
            path = Path(dist.locate_file(item))
            sha = digest(path)
            encoded = base64.urlsafe_b64encode(bytes.fromhex(sha)).decode().rstrip("=")
            if item.hash is None or item.hash.mode != "sha256" or item.hash.value != encoded:
                raise ValueError(f"installed multimedia file differs from wheel RECORD: {item}")
            files.append(dict(package=wheel["package"], file=str(item).replace("\\", "/"),
                              sha256=sha, bytes=path.stat().st_size, wheel_sha256=wheel["sha256"]))
    if not files:
        raise ValueError("wheel RECORD has no multimedia inputs")
    return dict(schema_version=1, **receipt, files=sorted(files, key=lambda x: (x["package"], x["file"])),
                upstream_build_provenance_verified=False, source_complete=False, release_ready=False)


def safe_archive(path: Path, required: list[str]) -> None:
    """Validate tar member shape without extracting or following any link."""
    seen = set()
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            canonical = name.as_posix()
            if (name.is_absolute() or ".." in name.parts or "\\" in member.name
                    or not name.parts or ":" in name.parts[0]
                    or member.name.rstrip("/") != canonical or canonical in seen):
                raise ValueError("unsafe or duplicate source member")
            if not member.isfile() and not member.isdir():
                raise ValueError("source links/devices are not accepted")
            seen.add(canonical)
    if not seen:
        raise ValueError("empty source archive")
    roots = {PurePosixPath(n).parts[0] for n in seen}
    if len(roots) != 1:
        raise ValueError("source archive must have one root")
    root = next(iter(roots))
    if not {root + "/" + n for n in required} <= seen:
        raise ValueError("required source/interfaces/licence/build members absent")


def gate_gaps(lock: dict) -> list[str]:
    # A URL/tag/boolean must not stand in for an independently reconciled
    # upstream build receipt. Current evidence deliberately leaves these null.
    gaps = []
    for key, label in [("upstream_build_receipt", "U1 wheel-build provenance"),
                       ("dependency_source_closure", "U3 source/dependency closure"),
                       ("modification_record", "U1 modifications/build record"),
                       ("replacement_acceptance", "U5 replacement acceptance")]:
        value = lock.get(key)
        if not isinstance(value, dict) or value.get("independently_verified") is not True or not value.get("evidence_sha256"):
            gaps.append(label)
    if lock.get("delivery", {}).get("approved") is not True:
        gaps.append("U4 delivery decision")
    return gaps


def platform_evidence(folder: Path, lock: dict) -> dict:
    receipts = {}
    for platform in lock["platforms"]:
        location = folder / f"qt-package-evidence-{platform}"
        def only(name: str) -> Path:
            matches = list(location.rglob(name)) if location.is_dir() else []
            if len(matches) != 1:
                raise ValueError(f"{platform}: expected one {name}, found {len(matches)}")
            return matches[0]
        inputs_path = only("qt-inputs.json")
        collection_path = only("qt-collection.json")
        inventory_path = only("qt-multimedia.json")
        inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
        collection = json.loads(collection_path.read_text(encoding="utf-8"))
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        if (inputs.get("platform") != platform or collection.get("platform") != platform
                or inventory.get("platform") != platform or inventory.get("failures")
                or inventory.get("release_ready") is not False):
            raise ValueError(f"{platform}: invalid package receipt")
        expected = {w["package"]: w["sha256"] for w in lock["platforms"][platform]["wheels"]}
        if {w["package"]: w["sha256"] for w in inputs["wheels"]} != expected:
            raise ValueError(f"{platform}: wheel input hashes differ from lock")
        observed = inventory.get("selected_files", [])
        if (not collection.get("files") or len(observed) != len(collection["files"])
                or any({k: item.get(k) for k in source} != source
                       or not item.get("final_sha256") or not item.get("final_architecture")
                       for item, source in zip(observed, collection["files"]))):
            raise ValueError(f"{platform}: package collection and inventory differ")
        if platform == "macos":
            pre_path = only("qt-multimedia-pre-sign.json")
            pre = json.loads(pre_path.read_text(encoding="utf-8"))
            if (digest(pre_path) != inventory.get("pre_sign_report_sha256")
                    or pre.get("platform") != platform or pre.get("failures")
                    or {x["path"] for x in pre.get("selected_files", [])}
                    != {x["path"] for x in collection["files"]}):
                raise ValueError("macos: signed package lacks a matching pre-sign receipt")
        receipts[platform] = dict(inputs_sha256=digest(inputs_path),
                                  collection_sha256=digest(collection_path),
                                  inventory_sha256=digest(inventory_path),
                                  selected_files=len(collection["files"]))
        if platform == "macos":
            receipts[platform]["pre_sign_sha256"] = digest(pre_path)
    return receipts


def draft_companion(out: Path, cache: Path, lock: dict, sources: list[dict]) -> dict:
    """Archive verified candidate material only; the manifest remains blocked."""
    if not sources or not all(item["verified"] for item in sources):
        raise ValueError("draft companion requires every declared source")
    version_text = (LOCK.parent.parent / "flightdvr" / "__init__.py").read_text(encoding="utf-8")
    version = re.search(r'^__version__ = "([^"]+)"', version_text, re.M).group(1)
    target = out / f"FlightDVR_Studio-{version}-qt-multimedia-source.tar"
    members = [("sources/" + item["filename"], cache / item["filename"])
               for item in sorted(sources, key=lambda x: x["id"])]
    members += [("LICENSE.LGPL-2.1.txt", LOCK.parent.parent / "LICENSE.LGPL-2.1.txt"),
                ("THIRD-PARTY-NOTICES.md", LOCK.parent.parent / "THIRD-PARTY-NOTICES.md"),
                ("qt-multimedia-sources.json", LOCK)]
    content_manifest = {"sources": [{k: x[k] for k in ("id", "filename", "sha256", "bytes")}
                                    for x in sources],
                        "unresolved": gate_gaps(lock), "source_complete": False,
                        "release_ready": False}
    with tarfile.open(target, "w") as archive:
        for name, path in members:
            info = tarfile.TarInfo(name)
            info.size = path.stat().st_size
            info.mode = 0o644
            with path.open("rb") as stream:
                archive.addfile(info, stream)
        payload = (json.dumps(content_manifest, indent=2, sort_keys=True) + "\n").encode()
        info = tarfile.TarInfo("MANIFEST.json")
        info.size = len(payload)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(payload))
    if target.stat().st_size >= ASSET_LIMIT:
        target.unlink()
        raise ValueError("single source companion reaches 2 GiB; explicit manifest-linked split required")
    return dict(filename=target.name, bytes=target.stat().st_size,
                sha256=digest(target), draft=True)


def prepare(out: Path, cache: Path, lock: dict, fetch: bool = False,
            evidence: Path | None = None) -> dict:
    from urllib.parse import urlparse
    out.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    results = []
    for source in lock["sources"]:
        name = source["filename"]
        if PurePosixPath(name).name != name or ":" in name or "\\" in name:
            raise ValueError("unsafe source filename")
        path = cache / name
        record = dict(id=source["id"], filename=name, url=source["url"],
                      expected_sha256=source["sha256"], verified=False)
        try:
            url = urlparse(source["url"])
            if url.scheme != "https" or url.hostname not in ALLOWED_HOSTS:
                raise ValueError("source origin is not allowlisted HTTPS")
            if not path.exists() and fetch:
                # One attempt only. Never accept partial bytes, never retry a
                # failed origin in a loop, and never run the downloaded code.
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=cache, prefix=name + ".",
                                                     suffix=".partial", delete=False) as sink:
                        temporary = Path(sink.name)
                        size = 0
                        h = hashlib.sha256()
                        with urllib.request.urlopen(source["url"], timeout=45) as response:
                            for block in iter(lambda: response.read(1024 * 1024), b""):
                                size += len(block)
                                if size >= ASSET_LIMIT:
                                    raise ValueError("source download reaches 2 GiB limit")
                                sink.write(block)
                                h.update(block)
                    if h.hexdigest() != source["sha256"]:
                        raise ValueError("source download checksum mismatch")
                    safe_archive(temporary, source["required_members"])
                    os.replace(temporary, path)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            if not path.is_file() or path.is_symlink() or digest(path) != source["sha256"]:
                raise ValueError("source unavailable or checksum differs")
            safe_archive(path, source["required_members"])
            record.update(verified=True, sha256=digest(path), bytes=path.stat().st_size)
        except (ValueError, OSError, tarfile.TarError, EOFError) as exc:
            record["error"] = str(exc)
        except Exception as exc:  # transport failure is unavailable evidence
            record["error"] = f"transport unavailable: {exc}"
        results.append(record)
    gaps = gate_gaps(lock)
    gaps += ["U3 unavailable source: " + x["id"] for x in results if not x["verified"]]
    try:
        payloads = platform_evidence(evidence, lock) if evidence else {}
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        payloads = {}
        gaps.append("U2 package evidence invalid: " + str(exc))
    if not payloads:
        gaps.append("U2 final platform payload manifests not reconciled")
    assets = []
    if all(x["verified"] for x in results):
        try:
            assets = [draft_companion(out, cache, lock, results)]
        except (ValueError, OSError) as exc:
            gaps.append("C1 source companion unavailable: " + str(exc))
    manifest = dict(schema_version=1, purpose="preparatory evidence, not distribution clearance",
                    source_complete=False, release_ready=False, unresolved=sorted(gaps),
                    source_asset_limit_bytes=ASSET_LIMIT, delivery=lock["delivery"],
                    sources=results, platform_inputs=lock["platforms"],
                    platform_payloads=payloads, assets=assets)
    (out / "qt-multimedia-source.manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def release_check(manifest: dict, folder: Path, lock: dict | None = None) -> list[Path]:
    """Exact asset counts/hashes; no permissive fallback or silent splitting."""
    lock = load_lock() if lock is None else lock
    if gate_gaps(lock):
        raise ValueError("Qt multimedia upstream provenance, closure or approval unresolved")
    if (manifest.get("schema_version") != 1 or manifest.get("source_complete") is not True
            or manifest.get("release_ready") is not True or manifest.get("unresolved")
            or manifest.get("delivery") != lock.get("delivery")):
        raise ValueError("Qt multimedia source/relinking release gate is open")
    sources = manifest.get("sources", [])
    if (len(sources) != len(lock["sources"]) or
            {x.get("id") for x in sources} != {x["id"] for x in lock["sources"]}):
        raise ValueError("Qt source set incomplete")
    for source in lock["sources"]:
        found = next(x for x in sources if x["id"] == source["id"])
        if (found.get("verified") is not True or found.get("sha256") != source["sha256"]):
            raise ValueError("Qt source payload not verified against lock")
    if set(manifest.get("platform_payloads", {})) != set(lock["platforms"]):
        raise ValueError("Qt platform package inventories incomplete")
    assets = manifest.get("assets", [])
    delivery = manifest["delivery"]
    expected = delivery.get("split_parts", []) if delivery.get("format") == "manifest-linked-split" else []
    if delivery.get("format") == "single-companion":
        if len(assets) != 1:
            raise ValueError("expected exactly one source companion")
    elif not expected or [a["filename"] for a in assets] != expected:
        raise ValueError("split companion parts/count differ from manifest")
    chosen = []
    seen_members = set()
    member_hashes = {}
    internal_manifest = None
    permitted = {"sources/" + x["filename"] for x in lock["sources"]}
    permitted |= {"MANIFEST.json", "LICENSE.LGPL-2.1.txt",
                  "THIRD-PARTY-NOTICES.md", "qt-multimedia-sources.json"}
    for entry in assets:
        name = entry["filename"]
        if Path(name).name != name or ":" in name or "\\" in name:
            raise ValueError("unsafe asset name")
        path = folder / name
        if (path.is_symlink() or not path.is_file() or path.stat().st_size >= ASSET_LIMIT
                or path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]):
            raise ValueError("source companion size/hash mismatch")
        try:
            with tarfile.open(path, "r:*") as archive:
                for member in archive:
                    name = member.name
                    if (name not in permitted or name in seen_members or not member.isfile()
                            or ".." in PurePosixPath(name).parts):
                        raise ValueError("source companion member missing, duplicate or unsafe")
                    seen_members.add(name)
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError("source companion member unreadable")
                    if name == "MANIFEST.json":
                        internal_manifest = json.load(stream)
                    elif name.startswith("sources/"):
                        h = hashlib.sha256()
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            h.update(block)
                        member_hashes[name] = h.hexdigest()
        except tarfile.TarError as exc:
            raise ValueError("source companion archive invalid") from exc
        chosen.append(path)
    if seen_members != permitted:
        raise ValueError("source companion archive omits required source/notice members")
    if any(member_hashes["sources/" + x["filename"]] != x["sha256"] for x in lock["sources"]):
        raise ValueError("source companion member hash differs from lock")
    if (not isinstance(internal_manifest, dict) or internal_manifest.get("source_complete") is not True
            or internal_manifest.get("release_ready") is not True
            or internal_manifest.get("sources") != manifest["sources"]):
        raise ValueError("source companion internal manifest is incomplete or inconsistent")
    actual = {p.name for p in folder.iterdir() if p.is_file()}
    if actual != {p.name for p in chosen} | {"qt-multimedia-source.manifest.json"}:
        raise ValueError("unexpected or missing source companion artifact")
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inputs = sub.add_parser("inputs")
    inputs.add_argument("pip_report", type=Path)
    inputs.add_argument("output", type=Path)
    prep = sub.add_parser("prepare")
    prep.add_argument("output", type=Path)
    prep.add_argument("--cache", type=Path)
    prep.add_argument("--fetch", action="store_true")
    prep.add_argument("--evidence", type=Path)
    check = sub.add_parser("release-check")
    check.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "inputs":
            report = installed_receipt(json.loads(args.pip_report.read_text()), load_lock())
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"verified locked quartet: {report['platform']}; source completeness remains false")
        elif args.command == "prepare":
            report = prepare(args.output, args.cache or args.output / "cache", load_lock(),
                             args.fetch, args.evidence)
            print(json.dumps({k: report[k] for k in ("source_complete", "release_ready", "unresolved")}))
        else:
            for path in release_check(json.loads(args.manifest.read_text()), args.manifest.parent):
                print(path)
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
