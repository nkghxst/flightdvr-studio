"""Corresponding source for the Linux AppImage's FFmpeg, and release file selection.

Run on a Linux CI runner:

    python3 packaging/collect_linux_sources.py collect INPUTS OUT
    python3 packaging/collect_linux_sources.py select-release-files DOWNLOADS OUT

collect               INPUTS is what the AppImage job's source-material step
                      recorded (source-material.json and the FFmpeg and build
                      system archives it downloaded and hashed). From those it
                      resolves the dependency stages the pinned build system
                      enables for linux64 gpl 7.1 by running the build
                      system's own generate.sh, checks that their configure
                      flags all appear in the shipped binary's configuration,
                      fetches each stage's source with the build system's own
                      download recipe and helpers (as its download.sh does),
                      vendors rav1e's locked Rust crates, fetches the Ubuntu
                      source packages the carried libraries were built from
                      (checked against their .dsc and, where still listed, the
                      signed archive index), and writes one tar bundle with a
                      manifest and build instructions. If anything required
                      is missing the bundle is not written and it exits 1.
select-release-files  picks exactly the files a release attaches from the
                      named downloaded artifacts: one AppImage, one .dmg, one
                      installer .exe and one source bundle. Anything else,
                      evidence folders included, is ignored; a missing or
                      ambiguous file is an error.
"""

from __future__ import annotations

import hashlib
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TARGET, VARIANT, ADDINS = "linux64", "gpl", ("7.1",)
STAGE_SECONDS = 1800
RELEASE_ASSET_LIMIT = 2 * 1024 ** 3 - 1          # GitHub's per-file release limit
LAUNCHPAD = "https://launchpad.net/ubuntu/+archive/primary/+files/"
UBUNTU_ARCHIVE = "http://archive.ubuntu.com/ubuntu/dists/"
UBUNTU_KEYRING = Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path: Path) -> dict:
    return {"file": path.name, "size": path.stat().st_size, "sha256": sha256_file(path)}


def safe_extract(archive: Path, dest: Path) -> None:
    """Refuse an archive with an absolute or climbing member name, or a link
    that is absolute or leaves its own folder; then extract with the standard
    library's 'data' filter as a second guard."""
    from pathlib import PurePosixPath
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise tarfile.TarError(f"unsafe member name {member.name!r}")
            if member.issym() or member.islnk():
                target = PurePosixPath(member.linkname)
                base = path.parent if member.issym() else PurePosixPath()
                depth = 0
                for part in (base / target).parts:
                    depth += -1 if part == ".." else (0 if part == "." else 1)
                    if depth < 0:
                        break
                if target.is_absolute() or depth < 0:
                    raise tarfile.TarError(
                        f"link {member.name!r} -> {member.linkname!r} leaves the archive")
        tar.extractall(dest, filter="data")


# -- the build system's own stage list ----------------------------------------------

def enabled_stages(dockerfile_text: str) -> list[dict]:
    """Stages generate.sh enabled, in its order, from the Dockerfile it wrote."""
    return [{"script": m.group(1), "stage": m.group(2)} for m in re.finditer(
        r'^ENV SELF="([^"]+)" STAGENAME="([^"]+)"', dockerfile_text, re.M)]


def ff_configure(dockerfile_text: str) -> list[str]:
    match = re.search(r'FF_CONFIGURE="([^"]*)"', dockerfile_text)
    return match.group(1).split() if match else []


def configure_cross_check(stage_flags: list[str], binary_configuration: str) -> list[str]:
    """Flags the build system adds that are not in the shipped binary."""
    shipped = set(binary_configuration.split())
    return [flag for flag in stage_flags if flag not in shipped]


def _bash(script: str, cwd: Path, timeout: float, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], cwd=cwd, capture_output=True,
                          text=True, timeout=timeout, env=env)


def _stage_recipe(build_system: Path, script: str) -> str:
    """The stage's download recipe (ffbuild_dockerdl), exactly as download.sh
    evaluates it."""
    addins = " ".join(ADDINS)
    result = _bash(
        f"set -e; source util/vars.sh {TARGET} {VARIANT} {addins} >/dev/null; "
        f"source util/dl_functions.sh; source '{script}'; ffbuild_dockerdl",
        build_system, 60)
    if result.returncode != 0:
        raise RuntimeError(f"recipe for {script}: {result.stderr.strip()[-300:]}")
    return result.stdout.rstrip("\n")


def _stage_vars(build_system: Path, script: str) -> dict:
    text = (build_system / script).read_text(encoding="utf-8", errors="replace")
    return dict(re.findall(r'^(SCRIPT_[A-Z0-9_]+)="?([^"\n]*)"?\s*$', text, re.M))


def _helpers(build_system: Path, into: Path) -> Path:
    """The build image's own download helpers, on PATH under their image names."""
    into.mkdir(parents=True, exist_ok=True)
    for name in ("git-mini-clone", "retry-tool", "check-wget"):
        source = build_system / "images" / "base" / f"{name}.sh"
        target = into / name
        shutil.copyfile(source, target)
        target.chmod(0o755)
    return into


def _repo_identities(tree: Path) -> list[dict]:
    """Every fetched repository: where, what kind, its remote and its commit
    or revision. Submodules are listed too, with their own remotes."""
    found = []
    for marker in sorted(tree.rglob(".git")):
        repo = marker.parent
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True)
        url = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                             capture_output=True, text=True)
        found.append({"path": str(repo.relative_to(tree)) or ".", "kind": "git",
                      "url": url.stdout.strip() or None,
                      "commit": head.stdout.strip() or None})
    for marker in sorted(tree.rglob(".svn")):
        repo = marker.parent
        info = subprocess.run(["svn", "info", "--show-item", "revision", str(repo)],
                              capture_output=True, text=True)
        url = subprocess.run(["svn", "info", "--show-item", "url", str(repo)],
                             capture_output=True, text=True)
        found.append({"path": str(repo.relative_to(tree)) or ".", "kind": "svn",
                      "url": url.stdout.strip() or None,
                      "revision": info.stdout.strip() or None})
    return found


def fetch_stage(build_system: Path, stage: dict, helpers: Path, work: Path,
                out_dir: Path) -> dict:
    record = dict(stage)
    record["declared"] = _stage_vars(build_system, stage["script"])
    recipe = _stage_recipe(build_system, stage["script"])
    record["recipe"] = recipe
    if not recipe:
        record["status"] = "no external source (the stage downloads nothing)"
        return record
    # download.sh names its cache file after this hash of the recipe.
    record["dl_hash"] = hashlib.sha256((recipe + "\n").encode()).hexdigest()
    tree = work / stage["stage"]
    tree.mkdir(parents=True)
    # No detached `git gc --auto` / maintenance after a fetch: one repacking
    # in the background changed a tree while it was being packed (libjxl).
    env = dict(os.environ, PATH=f"{helpers}{os.pathsep}{os.environ['PATH']}",
               GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="gc.auto", GIT_CONFIG_VALUE_0="0",
               GIT_CONFIG_KEY_1="maintenance.auto", GIT_CONFIG_VALUE_1="false")
    started = time.monotonic()
    try:
        result = _bash(f'set -xe -o pipefail; shopt -s dotglob; eval "set -e; $STG"',
                       tree, STAGE_SECONDS, env=dict(env, STG=recipe))
    except subprocess.TimeoutExpired:
        record["status"] = f"FAILED: timed out after {STAGE_SECONDS} s"
        shutil.rmtree(tree, ignore_errors=True)
        return record
    record["seconds"] = round(time.monotonic() - started, 1)
    if result.returncode != 0:
        record["status"] = f"FAILED: exit {result.returncode}: {result.stderr.strip()[-400:]}"
        shutil.rmtree(tree, ignore_errors=True)
        return record
    record["identities"] = _repo_identities(tree)
    record["declared_sources"] = bind_declared(stage["stage"], record["declared"],
                                               record["identities"], recipe)
    target = out_dir / f"{stage['stage']}_{record['dl_hash']}.tar.xz"
    packed = subprocess.run(["tar", "-I", "xz -T0", "-cpf", str(target), "-C", str(tree), "."],
                            capture_output=True, text=True)
    if stage["stage"] == "50-rav1e" and packed.returncode == 0:
        record["vendored_crates"] = vendor_crates(tree, out_dir)
    shutil.rmtree(tree, ignore_errors=True)
    if packed.returncode != 0:
        record["status"] = f"FAILED: packing: {packed.stderr.strip()[-300:]}"
        return record
    record["archive"] = _identity(target)
    record["status"] = declared_status(record["declared_sources"])
    return record


# Declared sources whose recipe itself deletes the VCS metadata, so the
# fetched tree cannot be re-read: (stage, declaration number) -> the part of
# the recipe that does it. Checked against the actual recipe, never assumed.
RECIPE_ONLY = {
    ("50-amf", 1): "rm -rf .git",
    ("20-libiconv", 2): "rm -rf gnulib/.git",
}


def _normal_url(url) -> str:
    url = (url or "").strip().rstrip("/")
    return url[:-4] if url.endswith(".git") else url


def _declared_numbers(declared: dict) -> list[int]:
    numbers = set()
    for key in declared:
        match = re.fullmatch(r"SCRIPT_(?:COMMIT|REV)(\d*)", key)
        if match:
            numbers.add(int(match.group(1) or 1))
    return sorted(numbers)


def _resolve_tag(remote: str, tag: str) -> set:
    listed = subprocess.run(["git", "ls-remote", remote, f"refs/tags/{tag}",
                             f"refs/tags/{tag}^{{}}"],
                            capture_output=True, text=True, timeout=120)
    return {line.split()[0] for line in listed.stdout.splitlines() if line.strip()}


def bind_declared(stage: str, declared: dict, identities: list[dict], recipe: str,
                  resolve=_resolve_tag) -> list[dict]:
    """Bind every declared source (SCRIPT_COMMIT/REV, ...2, ...3) to the
    fetched repository that came from its own remote (SCRIPT_REPO or
    SCRIPT_MIRROR of the same number) and check that one is at the declared
    commit, resolved tag or revision. A match in some other repository or
    submodule never counts. Each fetched repository is used once."""
    used: set = set()
    bound = []
    for number in _declared_numbers(declared):
        suffix = "" if number == 1 else str(number)
        commit = declared.get(f"SCRIPT_COMMIT{suffix}")
        revision = declared.get(f"SCRIPT_REV{suffix}")
        remotes = [declared[k] for k in (f"SCRIPT_REPO{suffix}", f"SCRIPT_MIRROR{suffix}")
                   if declared.get(k)]
        wanted_urls = {_normal_url(r) for r in remotes}
        entry = {"number": number, "remotes": remotes, "declared": revision or commit}
        candidates = [i for i in identities if i["path"] not in used
                      and _normal_url(i.get("url")) in wanted_urls]
        if revision:
            accepted, key = {revision}, "revision"
        elif commit and re.fullmatch(r"[0-9a-f]{40}", commit):
            accepted, key = {commit}, "commit"
        elif commit:
            accepted, key = set(), "commit"
            for remote in remotes:
                accepted |= resolve(remote, commit)
            entry["resolved"] = sorted(accepted)
        else:
            accepted, key = set(), "commit"
        match = next((i for i in candidates if i.get(key) in accepted), None)
        allowed = RECIPE_ONLY.get((stage, number))
        if match is not None:
            used.add(match["path"])
            entry.update(status="matched", path=match["path"], fetched=match.get(key))
        elif not candidates and allowed and allowed in recipe:
            entry.update(status="recipe-only",
                         reason=f"the recipe runs `{allowed}`, so the fetched tree's "
                                "commit cannot be re-read; it is pinned by the recipe")
        elif not candidates:
            entry["status"] = "missing: no fetched repository came from its remote"
        else:
            entry.update(status="wrong: fetched from its remote but not at the "
                                "declared identity",
                         fetched=[(i["path"], i.get(key)) for i in candidates])
        bound.append(entry)
    return bound


def declared_status(bound: list) -> str:
    bad = [b for b in bound if b["status"] not in ("matched", "recipe-only")]
    if bad:
        return "FAILED: " + "; ".join(f"declared source {b['number']}: {b['status']}"
                                       for b in bad)
    if not bound:
        return "FAILED: no declared source to check"
    recipe_only = [str(b["number"]) for b in bound if b["status"] == "recipe-only"]
    if recipe_only:
        return ("collected (declared source " + ", ".join(recipe_only)
                + " pinned by a recipe that removes its VCS metadata)")
    return "collected"


def vendor_crates(tree: Path, out_dir: Path) -> dict:
    """rav1e's locked crate dependencies, vendored from its Cargo.lock."""
    if not (tree / "Cargo.lock").is_file():
        return {"status": "FAILED: no Cargo.lock"}
    vendored = tree.parent / "rav1e-vendor"
    result = subprocess.run(["cargo", "vendor", "--locked", "--versioned-dirs", str(vendored)],
                            cwd=tree, capture_output=True, text=True, timeout=STAGE_SECONDS)
    if result.returncode != 0:
        return {"status": f"FAILED: cargo vendor exit {result.returncode}: "
                          f"{result.stderr.strip()[-300:]}"}
    target = out_dir / "50-rav1e_vendored-crates.tar.xz"
    subprocess.run(["tar", "-I", "xz -T0", "-cpf", str(target), "-C", str(vendored.parent),
                    vendored.name], check=True)
    shutil.rmtree(vendored, ignore_errors=True)
    return {"status": "collected", "lockfile_sha256": sha256_file(tree / "Cargo.lock"),
            "archive": _identity(target),
            "not_recoverable": ("the build runs `cargo update cc` first, so the cc "
                                "build-tool crate it used is whatever was newest then; "
                                "cc builds the C/assembly parts and is not linked into "
                                "the binary")}


# -- Ubuntu source packages ------------------------------------------------------------

def parse_dsc_files(dsc_text: str) -> list[dict]:
    """The Checksums-Sha256 entries of a .dsc (signature lines ignored)."""
    files, inside = [], False
    for line in dsc_text.splitlines():
        if line.startswith("Checksums-Sha256:"):
            inside = True
            continue
        if inside:
            if not line.startswith(" "):
                break
            sha, size, name = line.split()
            files.append({"name": name, "size": int(size), "sha256": sha})
    return files


def _get(url: str, target: Path, timeout: float = 900) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as response, open(target, "xb") as out:
        declared = response.headers.get("Content-Length")
        shutil.copyfileobj(response, out)
    if declared is not None and target.stat().st_size != int(declared):
        raise OSError(f"short read for {url}: {target.stat().st_size} of {declared}")


def _archive_listing(package: str, version: str, dsc_sha256: str, scratch: Path) -> dict:
    """Is this exact .dsc listed in the signed Ubuntu archive index?"""
    for pocket in ("jammy-updates", "jammy-security", "jammy"):
        try:
            release = scratch / f"{pocket}.InRelease"
            _get(f"{UBUNTU_ARCHIVE}{pocket}/InRelease", release, 120)
            verified = subprocess.run(["gpgv", "--keyring", str(UBUNTU_KEYRING), str(release)],
                                      capture_output=True, text=True)
            if verified.returncode != 0:
                continue
            text = release.read_text(encoding="utf-8", errors="replace")
            sha256_section = text.split("SHA256:", 1)[1]
            match = re.search(r"^ ([0-9a-f]{64})\s+\d+ main/source/Sources\.xz$",
                              sha256_section, re.M)
            if not match:
                continue
            sources = scratch / f"{pocket}.Sources.xz"
            _get(f"{UBUNTU_ARCHIVE}{pocket}/main/source/Sources.xz", sources, 300)
            if sha256_file(sources) != match.group(1):
                continue
            index = lzma.decompress(sources.read_bytes()).decode("utf-8", "replace")
            for stanza in index.split("\n\n"):
                if (f"\nPackage: {package}\n" in "\n" + stanza
                        and f"\nVersion: {version}\n" in stanza + "\n"
                        and dsc_sha256 in stanza):
                    return {"listed": True, "pocket": pocket,
                            "rationale": "InRelease verified with the Ubuntu archive keyring; "
                                         "Sources.xz matches it; the .dsc's SHA-256 is listed"}
        except (OSError, IndexError):
            continue
    return {"listed": False,
            "rationale": "not listed in jammy, jammy-updates or jammy-security now; "
                         "files are checked against the .dsc's own checksums only"}


def fetch_ubuntu_source(package: str, version: str, out_dir: Path, scratch: Path) -> dict:
    upstream = version.split(":", 1)[-1]
    record = {"source_package": package, "version": version, "files": []}
    dsc_name = f"{package}_{upstream}.dsc"
    try:
        dsc = out_dir / dsc_name
        _get(LAUNCHPAD + dsc_name, dsc)
        record["dsc"] = _identity(dsc)
        for entry in parse_dsc_files(dsc.read_text(encoding="utf-8", errors="replace")):
            target = out_dir / entry["name"]
            _get(LAUNCHPAD + entry["name"], target)
            got = _identity(target)
            got["matches_dsc"] = (got["size"] == entry["size"]
                                  and got["sha256"] == entry["sha256"])
            record["files"].append(got)
        record["archive_index"] = _archive_listing(package, version,
                                                   record["dsc"]["sha256"], scratch)
    except OSError as exc:
        record["status"] = f"FAILED: {type(exc).__name__}: {exc}"
        return record
    if not (record["files"] and all(f["matches_dsc"] for f in record["files"])):
        record["status"] = "FAILED: a file does not match the .dsc"
    elif not record["archive_index"].get("listed"):
        record["status"] = ("FAILED: not authenticated by the signed Ubuntu archive index: "
                            + record["archive_index"].get("rationale", "unavailable"))
    else:
        record["status"] = "collected"
    return record


# -- collect -------------------------------------------------------------------------------

BUILD_INSTRUCTIONS = """# FlightDVR Studio {version}: Linux FFmpeg corresponding source

This bundle holds the source for the FFmpeg and FFprobe programs in the
FlightDVR Studio {version} Linux AppImage, and for the two system libraries
the AppImage carries alongside them. `MANIFEST.json` lists every file with
its SHA-256, where it came from and why it is the matching version.

## What is here

- `ffmpeg/`: FFmpeg at commit {ffmpeg_commit}.
- `build-system/`: BtbN/FFmpeg-Builds at commit {build_commit}, which built
  the binaries (variant linux64-gpl, FFmpeg 7.1 add-in).
- `dependencies/`: the source of every dependency stage that build system
  enables for linux64-gpl-7.1, fetched with its own recipes. Each file is
  named `<stage>_<hash>.tar.xz`, where the hash is the one the build system's
  `download.sh` uses for its download cache.
- `dependencies/50-rav1e_vendored-crates.tar.xz`: rav1e's Rust crates,
  vendored from its Cargo.lock.
- `ubuntu/`: the Ubuntu source packages (with their Debian patches) for the
  carried `libgcc_s.so.1` and `libmvec.so.1`.

## Rebuilding the binaries

1. Unpack `build-system/` and change into it.
2. Create `.cache/downloads/` and copy every `dependencies/<stage>_<hash>.tar.xz`
   into it. Those are the names `download.sh` would have produced, so it
   reuses them instead of downloading.
3. Build with the build system's own instructions: `./build.sh linux64 gpl 7.1`.
   It uses Docker and builds its toolchain image from `images/`.

The toolchain image clones crosstool-ng at its newest commit when it is
built (see `images/base-linux64/Dockerfile`), so a rebuild today may use a
different crosstool-ng. The toolchain's component versions are fixed by
`images/base-linux64/ct-ng-config` and are listed in the manifest.
"""


def collect(inputs: Path, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    pin = json.loads((HERE / "ffmpeg-build-linux.json").read_text(encoding="utf-8"))
    version = re.search(r'__version__ = "([^"]+)"',
                        (ROOT / "flightdvr" / "__init__.py").read_text(encoding="utf-8")).group(1)
    name = f"FlightDVR_Studio-{version}-linux-ffmpeg-source"
    material = json.loads((inputs / "source-material.json").read_text(encoding="utf-8"))
    manifest: dict = {"bundle": name, "pin": {k: pin[k] for k in (
        "version", "archive", "archive_sha256", "ffmpeg_git_commit_full",
        "build_system_commit", "binaries")},
        "required_failures": [], "not_collected": []}

    def require(label: str, ok: bool, detail: str = "") -> None:
        if not ok:
            manifest["required_failures"].append(f"{label}: {detail}" if detail else label)

    staging = out / name
    staging.mkdir()
    for key, folder in (("ffmpeg_source", "ffmpeg"), ("build_system_source", "build-system")):
        entry = material.get(key, {})
        source = inputs / entry.get("file", "missing")
        (staging / folder).mkdir()
        if source.is_file() and sha256_file(source) == entry.get("sha256"):
            shutil.copyfile(source, staging / folder / source.name)
            manifest[key] = dict(entry, rationale="GitHub archive of the pinned commit; "
                                                  "SHA-256 as recorded when downloaded")
        else:
            require(key, False, "missing or not the recorded bytes")

    work = out / "work"
    build_system = work / "build-system"
    build_system.mkdir(parents=True)
    archive = staging / "build-system" / material["build_system_source"]["file"]
    safe_extract(archive, build_system)
    build_system = next(p for p in build_system.iterdir() if p.is_dir())
    generated = _bash(f"bash generate.sh {TARGET} {VARIANT} {' '.join(ADDINS)}",
                      build_system, 600)
    require("generate.sh", generated.returncode == 0, generated.stderr.strip()[-300:])
    dockerfile = (build_system / "Dockerfile").read_text(encoding="utf-8") \
        if (build_system / "Dockerfile").is_file() else ""
    stages = enabled_stages(dockerfile)
    flags = ff_configure(dockerfile)
    binary_configuration = (HERE / "ffmpeg-configuration-linux.txt").read_text(
        encoding="utf-8").splitlines()[2]
    missing_flags = configure_cross_check(flags, binary_configuration)
    manifest["stage_resolution"] = {
        "method": f"the build system's own generate.sh {TARGET} {VARIANT} {' '.join(ADDINS)}",
        "stages": len(stages), "ff_configure": flags,
        "flags_not_in_shipped_binary": missing_flags}
    require("enabled stages resolved", bool(stages))
    require("every stage's configure flag is in the shipped binary", not missing_flags,
            " ".join(missing_flags))
    toolchain = build_system / "images" / "base-linux64" / "ct-ng-config"
    manifest["toolchain"] = {
        "config_sha256": sha256_file(toolchain) if toolchain.is_file() else None,
        "versions": dict(re.findall(r'^(CT_[A-Z0-9_]+_VERSION)="([^"]+)"',
                                    toolchain.read_text(encoding="utf-8"), re.M))
        if toolchain.is_file() else {}}
    manifest["not_collected"].append(
        "Toolchain sources (crosstool-ng, GCC, binutils, the glibc 2.28 sysroot): the "
        "binaries link glibc dynamically and take GCC's runtime libraries under the GCC "
        "Runtime Library Exception; versions are recorded under toolchain, sources not "
        "collected. crosstool-ng itself is cloned at its newest commit by the image.")

    helpers = _helpers(build_system, work / "helpers")
    deps = staging / "dependencies"
    deps.mkdir()
    manifest["dependencies"] = []
    for stage in stages:
        try:
            record = fetch_stage(build_system, stage, helpers, work / "stages", deps)
        except Exception as exc:  # noqa: BLE001 — recorded as a required failure
            record = dict(stage, status=f"FAILED: {type(exc).__name__}: {exc}")
        manifest["dependencies"].append(record)
        print(f"  {stage['stage']}: {record['status']}", flush=True)
        require(f"dependency {stage['stage']}", not record["status"].startswith("FAILED"),
                record["status"])
        crates = record.get("vendored_crates")
        if crates is not None:
            require("rav1e vendored crates", crates["status"] == "collected", crates["status"])
            manifest["not_collected"].append(crates.get("not_recoverable", ""))

    ubuntu = staging / "ubuntu"
    ubuntu.mkdir()
    manifest["ubuntu"] = []
    for library, carried in sorted(material.get("carried_libraries", {}).items()):
        owner = carried.get("host_package") or {}
        if not carried.get("in_bundle"):
            continue
        record = fetch_ubuntu_source(owner.get("source_package"), owner.get("source_version"),
                                     ubuntu, work)
        record["library"] = library
        record["binary_package"] = owner.get("package")
        manifest["ubuntu"].append(record)
        require(f"Ubuntu source for {library}", record["status"] == "collected",
                record["status"])

    licence = ROOT / "LICENSE"
    shutil.copyfile(licence, staging / "LICENSE.GPL-3.0.txt")
    (staging / "README.md").write_text(BUILD_INSTRUCTIONS.format(
        version=version, ffmpeg_commit=pin["ffmpeg_git_commit_full"],
        build_commit=pin["build_system_commit"]), encoding="utf-8")
    manifest["complete"] = not manifest["required_failures"]
    (staging / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n",
                                           encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)

    report = out / f"{name}.manifest.json"
    if not manifest["complete"]:
        report.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
        shutil.rmtree(staging, ignore_errors=True)
        print("INCOMPLETE; no bundle written:\n  " + "\n  ".join(manifest["required_failures"]))
        return 1
    bundle = out / f"{name}.tar"
    subprocess.run(["tar", "-cf", str(bundle), "-C", str(out), name], check=True)
    shutil.rmtree(staging, ignore_errors=True)
    manifest["bundle_file"] = _identity(bundle)
    report.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    if bundle.stat().st_size > RELEASE_ASSET_LIMIT:
        print(f"bundle is {bundle.stat().st_size} bytes, over the 2 GiB release-asset limit")
        bundle.unlink()
        return 1
    print(f"complete: {bundle.name} {bundle.stat().st_size} bytes {manifest['bundle_file']['sha256']}")
    return 0


# -- release file selection ---------------------------------------------------------------------

RELEASE_FILES = {
    "linux-appimage": "*.AppImage",
    "macos-dmg": "*.dmg",
    "windows-installer": "*.exe",
    "linux-ffmpeg-source": "*-linux-ffmpeg-source.tar",
}


def select_release_files(downloads: Path, out: Path) -> list[Path]:
    """Exactly one file from each named artifact folder; nothing else."""
    out.mkdir(parents=True, exist_ok=True)
    chosen = []
    for artifact, pattern in RELEASE_FILES.items():
        folder = downloads / artifact
        if not folder.is_dir():
            raise SystemExit(f"release artifact {artifact} was not downloaded")
        matches = [p for p in folder.glob(pattern) if p.is_file()]
        if len(matches) != 1:
            raise SystemExit(f"release artifact {artifact}: expected one {pattern}, "
                             f"found {[p.name for p in matches]}")
        target = out / matches[0].name
        shutil.copyfile(matches[0], target)
        chosen.append(target)
    return chosen


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "collect":
        return collect(Path(argv[1]).resolve(), Path(argv[2]).resolve())
    if len(argv) == 3 and argv[0] == "select-release-files":
        for path in select_release_files(Path(argv[1]), Path(argv[2])):
            print(path)
        return 0
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
