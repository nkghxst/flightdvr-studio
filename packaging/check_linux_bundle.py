"""Evidence that the Linux build bundles, and actually uses, the pinned ffmpeg.

Run on a Linux CI runner, never on a workstation: it executes the pinned
programs and the built AppImage.

    python3 packaging/check_linux_bundle.py inspect-pair FOLDER OUT
    python3 packaging/check_linux_bundle.py export-check FOLDER OUT
    python3 packaging/check_linux_bundle.py appimage-check APPIMAGE OUT

inspect-pair   identity, version, configuration, encoders and filters, and the
               ELF dynamic requirements read by readelf and objdump (independent
               of the stdlib reading recorded when the archive was verified),
               plus whether this machine can satisfy them.
export-check   generated media encoded through every codec and filter family
               the presets use, with the resulting streams read back by the
               pinned ffprobe.
appimage-check the built AppImage itself, on a machine where no system ffmpeg
               can be found: its --check must exit 0 and resolve both programs
               inside the bundle, whose bytes must match the pin, and its
               --check-export must run the app's own probe and exports inside
               the packaged process with that pair and pass. Both are repeated
               with a conflicting ffmpeg/ffprobe pair first on PATH, which must
               be neither selected nor ever run. It also records which of the
               pair's declared libraries the bundle itself carries.

Each writes <OUT>/<command>.json plus raw logs, prints a summary, and exits 1
if any requirement fails. Hardware encoders are recorded as telemetry only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from verify_ffmpeg_linux import PinError, check_dir, load_pin, sha256_file  # noqa: E402

# What the presets and the export paths ask ffmpeg for (flightdvr/presets.py,
# jobs.py, audio_export.py, stills.py, thumbs.py, trim.py at ebe18dc).
REQUIRED_ENCODERS = ("libx264", "aac", "pcm_s16le", "prores_ks", "dnxhd", "png")
REQUIRED_FILTERS = ("zscale", "scale", "fps", "format", "pad", "crop", "setsar",
                    "setpts", "trim", "select", "thumbnail", "split", "concat",
                    "atrim", "asetpts", "aresample", "aformat", "apad", "afade",
                    "volume", "amix")
HARDWARE_ENCODERS = ("h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv",
                     "h264_vaapi", "hevc_vaapi", "h264_amf", "hevc_amf",
                     "h264_vulkan")
# Measured from the archive with a stdlib ELF reader (Sol-verified bytes,
# LINUX_ARCHIVE_VERIFICATION.md); readelf/objdump must agree independently.
EXPECTED_NEEDED = {"libm.so.6", "libdl.so.2", "librt.so.1", "libpthread.so.0",
                   "libc.so.6", "ld-linux-x86-64.so.2", "libmvec.so.1",
                   "libgcc_s.so.1"}
GLIBC_FLOOR = (2, 35)          # the AppImage is built on, and supports, 22.04


class Evidence:
    def __init__(self, out: Path, name: str):
        self.out, self.name = out, name
        out.mkdir(parents=True, exist_ok=True)
        self.record: dict = {"command": name, "checks": {}, "failures": []}

    def run(self, label: str, argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=kwargs.pop("timeout", 300), **kwargs)
        (self.out / f"{label}.log").write_text(
            f"$ {' '.join(argv)}\nexit {result.returncode}\n--- stdout\n{result.stdout}"
            f"--- stderr\n{result.stderr}", encoding="utf-8")
        return result

    def require(self, label: str, ok: bool, detail="") -> None:
        self.record["checks"][label] = {"ok": bool(ok), "detail": detail}
        if not ok:
            self.record["failures"].append(label)
        print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f": {detail}" if detail and not ok else ""))

    def finish(self) -> int:
        self.record["result"] = "FAIL" if self.record["failures"] else "PASS"
        (self.out / f"{self.name}.json").write_text(json.dumps(self.record, indent=1) + "\n",
                                                    encoding="utf-8")
        print(f"{self.name}: {self.record['result']}"
              + (f" ({', '.join(self.record['failures'])})" if self.record["failures"] else ""))
        return 1 if self.record["failures"] else 0


def _machine(ev: Evidence) -> None:
    release = Path("/etc/os-release")
    pretty = ""
    if release.exists():
        match = re.search(r'^PRETTY_NAME="?([^"\n]*)', release.read_text(), re.M)
        pretty = match.group(1) if match else ""
    ldd = ev.run("ldd-version", ["ldd", "--version"])
    ev.record["machine"] = {"os": pretty, "glibc": ldd.stdout.splitlines()[0] if ldd.stdout else "",
                            "kernel": os.uname().release}


def _names(listing: str, arrow: bool) -> set[str]:
    names = set()
    for line in listing.splitlines():
        tokens = line.split()
        if arrow and len(tokens) >= 3 and "->" in tokens[2]:
            names.add(tokens[1])
        elif not arrow and len(tokens) >= 2 and re.fullmatch(r"[VAS][F.][S.][X.][B.][D.]", tokens[0]):
            names.add(tokens[1])
    return names


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def inspect_pair(folder: Path, out: Path) -> int:
    ev = Evidence(out, "inspect-pair")
    _machine(ev)
    pin = load_pin()
    try:
        ev.record["hashes"] = check_dir(folder, pin)
        ev.require("pair matches the pin", True)
    except PinError as exc:
        ev.require("pair matches the pin", False, f"{exc.reason}: {exc}")
        return ev.finish()
    ffmpeg, ffprobe = str(folder / "ffmpeg"), str(folder / "ffprobe")

    version = ev.run("ffmpeg-version", [ffmpeg, "-hide_banner", "-version"])
    ev.require("ffmpeg -version exits 0", version.returncode == 0, version.stderr[-300:])
    recorded = (HERE / "ffmpeg-configuration-linux.txt").read_text(encoding="utf-8").splitlines()
    ev.require("configuration matches ffmpeg-configuration-linux.txt",
               version.stdout.splitlines()[:3] == recorded)
    ev.require("version is the pinned build", pin["version"] in version.stdout)
    probe_version = ev.run("ffprobe-version", [ffprobe, "-hide_banner", "-version"])
    ev.require("ffprobe -version exits 0 and is the pinned build",
               probe_version.returncode == 0 and pin["version"] in probe_version.stdout)
    ev.run("ffmpeg-buildconf", [ffmpeg, "-hide_banner", "-buildconf"])

    encoders = _names(ev.run("ffmpeg-encoders", [ffmpeg, "-hide_banner", "-encoders"]).stdout, False)
    filters = _names(ev.run("ffmpeg-filters", [ffmpeg, "-hide_banner", "-filters"]).stdout, True)
    for name in REQUIRED_ENCODERS:
        ev.require(f"encoder {name}", name in encoders)
    for name in REQUIRED_FILTERS:
        ev.require(f"filter {name}", name in filters)
    ev.record["hardware_encoders_listed"] = sorted(n for n in HARDWARE_ENCODERS if n in encoders)

    for tool, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        dynamic = ev.run(f"{tool}-readelf-d", ["readelf", "-d", "-W", path])
        versions = ev.run(f"{tool}-readelf-V", ["readelf", "-V", "-W", path])
        objdump = ev.run(f"{tool}-objdump-p", ["objdump", "-p", path])
        needed_readelf = set(re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", dynamic.stdout))
        needed_objdump = set(re.findall(r"^\s+NEEDED\s+(\S+)", objdump.stdout, re.M))
        runpath = re.findall(r"\((RUNPATH|RPATH)\)", dynamic.stdout)
        glibc = sorted({v for v in re.findall(r"Name: (GLIBC_[0-9.]+)", versions.stdout)},
                       key=lambda v: _version_tuple(v[6:]))
        gcc = sorted({v for v in re.findall(r"Name: (GCC_[0-9.]+)", versions.stdout)},
                     key=lambda v: _version_tuple(v[4:]))
        ev.record[f"{tool}_elf"] = {"needed": sorted(needed_readelf), "runpath": runpath,
                                    "max_glibc": glibc[-1] if glibc else None,
                                    "max_gcc": gcc[-1] if gcc else None}
        ev.require(f"{tool}: readelf and objdump agree on NEEDED",
                   bool(needed_readelf) and needed_readelf == needed_objdump,
                   f"{sorted(needed_readelf)} vs {sorted(needed_objdump)}")
        ev.require(f"{tool}: NEEDED matches the stdlib reading", needed_readelf == EXPECTED_NEEDED,
                   f"{sorted(needed_readelf ^ EXPECTED_NEEDED)}")
        ev.require(f"{tool}: no RUNPATH or RPATH", not runpath, str(runpath))
        ev.require(f"{tool}: highest GLIBC version within the 2.35 floor",
                   bool(glibc) and _version_tuple(glibc[-1][6:]) <= GLIBC_FLOOR,
                   glibc[-1] if glibc else "none found")
        ldd = ev.run(f"{tool}-ldd", ["ldd", path])
        resolved = dict(re.findall(r"^\s*(\S+) => (\S+)", ldd.stdout, re.M))
        ev.record[f"{tool}_ldd"] = resolved
        ev.require(f"{tool}: every dependency resolves on this machine",
                   ldd.returncode == 0 and "not found" not in ldd.stdout, ldd.stdout[-400:])
        ev.require(f"{tool}: libgcc_s.so.1 resolves", resolved.get("libgcc_s.so.1", "").startswith("/"),
                   resolved.get("libgcc_s.so.1", "missing"))
        ev.require(f"{tool}: no glibc carried in the folder",
                   not any(Path(p).parent == folder.resolve() for p in resolved.values()))
    return ev.finish()


def _probe(ev: Evidence, ffprobe: str, path: Path, label: str) -> dict:
    result = ev.run(f"probe-{label}", [ffprobe, "-v", "error", "-print_format", "json",
                                       "-show_streams", "-show_format", str(path)])
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def export_check(folder: Path, out: Path) -> int:
    ev = Evidence(out, "export-check")
    _machine(ev)
    try:
        check_dir(folder)
        ev.require("pair matches the pin", True)
    except PinError as exc:
        ev.require("pair matches the pin", False, f"{exc.reason}: {exc}")
        return ev.finish()
    ffmpeg, ffprobe = str(folder / "ffmpeg"), str(folder / "ffprobe")
    work = out / "media"
    work.mkdir(parents=True, exist_ok=True)
    base = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]

    # Shaped like the goggles' recordings: MPEG-TS, 60 fps, full-range YUV.
    source = work / "source.ts"
    made = ev.run("make-source", base + [
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=60:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuvj420p", "-color_range", "pc",
        "-g", "60", "-c:a", "aac", "-f", "mpegts", str(source)])
    ev.require("generated source written", made.returncode == 0 and source.exists(), made.stderr[-300:])
    if made.returncode != 0:
        return ev.finish()

    rec709 = ("zscale=rangein=full:range=limited:matrixin=470bg:matrix=709"
              ":primariesin=bt470bg:primaries=bt709:transferin=smpte170m"
              ":transfer=bt709:dither=error_diffusion")
    cases = {
        "h264-aac.mp4": (["-vf", f"{rec709},format=yuv420p", "-c:v", "libx264", "-crf", "18",
                          "-c:a", "aac"], {"video": "h264", "audio": "aac"}),
        "dnxhr.mov": (["-vf", f"{rec709},format=yuv422p", "-c:v", "dnxhd", "-profile:v",
                       "dnxhr_sq", "-c:a", "pcm_s16le"], {"video": "dnxhd", "audio": "pcm_s16le"}),
        "prores.mov": (["-vf", f"{rec709},format=yuv422p10le", "-c:v", "prores_ks",
                        "-profile:v", "2", "-c:a", "pcm_s16le"], {"video": "prores", "audio": "pcm_s16le"}),
        "trimmed.mp4": (["-filter_complex",
                         "[0:v]trim=start=0.5:end=1.5,setpts=PTS-STARTPTS,fps=60,scale=640:-2,"
                         "pad=640:360:(ow-iw)/2:(oh-ih)/2,crop=640:352,setsar=1,format=yuv420p[v];"
                         "[0:a]atrim=start=0.5:end=1.5,asetpts=PTS-STARTPTS,aresample=48000,"
                         "aformat=channel_layouts=stereo,afade=t=in:d=0.1,volume=0.8,"
                         "apad=whole_dur=1[a]",
                         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-c:a", "aac"],
                        {"video": "h264", "audio": "aac"}),
        "still.png": (["-ss", "1", "-frames:v", "1", "-vf", "select=gte(n\\,10),thumbnail=10",
                       "-c:v", "png", "-an"], {"video": "png"}),
        "audio.wav": (["-vn", "-c:a", "pcm_s16le"], {"audio": "pcm_s16le"}),
    }
    ev.record["outputs"] = {}
    for name, (args, expect) in cases.items():
        target = work / name
        result = ev.run(f"export-{name}", base + ["-i", str(source)] + args + [str(target)])
        info = _probe(ev, ffprobe, target, name) if target.exists() else {}
        streams = {s.get("codec_type"): s.get("codec_name") for s in info.get("streams", [])}
        ev.record["outputs"][name] = {"exit": result.returncode, "streams": streams,
                                      "duration": info.get("format", {}).get("duration"),
                                      "bytes": target.stat().st_size if target.exists() else 0}
        ok = result.returncode == 0 and all(streams.get(k) == v for k, v in expect.items())
        ev.require(f"export {name}", ok, f"exit {result.returncode}, streams {streams}, "
                                         f"{result.stderr[-200:]}")

    mixed = work / "mixed.wav"
    result = ev.run("export-amix", base + ["-i", str(source), "-i", str(source), "-filter_complex",
                                           "[0:a][1:a]amix=inputs=2:normalize=0[a]", "-map", "[a]",
                                           "-c:a", "pcm_s16le", str(mixed)])
    joined = work / "joined.mp4"
    result2 = ev.run("export-concat", base + ["-i", str(source), "-i", str(source), "-filter_complex",
                                              "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a];"
                                              "[v]split=2[v1][v2];[v2]nullsink",
                                              "-map", "[v1]", "-map", "[a]", "-c:v", "libx264",
                                              "-c:a", "aac", str(joined)])
    ev.require("export amix", result.returncode == 0 and mixed.exists(), result.stderr[-200:])
    ev.require("export concat/split", result2.returncode == 0 and joined.exists(), result2.stderr[-200:])
    return ev.finish()


def _absent_system_tools() -> dict[str, list[str]]:
    """Every place the app would look for ffmpeg outside its bundle."""
    sys.path.insert(0, str(ROOT))
    from flightdvr.media import _EXTRA_DIRS
    folders = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    folders += list(_EXTRA_DIRS) + [Path("/usr/bin"), Path("/bin"), Path("/usr/local/bin"),
                                    Path("/snap/bin")]
    found: dict[str, list[str]] = {}
    for tool in ("ffmpeg", "ffprobe"):
        hits = sorted({str(f / tool) for f in folders if (f / tool).exists()})
        if hits:
            found[tool] = hits
    return found


def _check_report(text: str) -> dict[str, str]:
    paths = {}
    for line in text.splitlines():
        match = re.match(r"^(ffmpeg|ffprobe)\s+(\S.*?)(?:\s+\((bundled|from this system)\))?$", line)
        if match:
            paths[match.group(1)] = match.group(2).strip()
            if match.group(3):
                paths[f"{match.group(1)}_origin"] = match.group(3)
    return paths


def appimage_check(appimage: Path, out: Path) -> int:
    ev = Evidence(out, "appimage-check")
    _machine(ev)
    pin = load_pin()
    appimage = appimage.resolve()
    ev.record["appimage"] = {"name": appimage.name, "size": appimage.stat().st_size,
                             "sha256": sha256_file(appimage)}

    system = _absent_system_tools()
    ev.record["system_tools_found"] = system
    ev.require("no system ffmpeg or ffprobe is discoverable", not system, json.dumps(system))

    extract = out / "extract"
    extract.mkdir(parents=True, exist_ok=True)
    unpacked = ev.run("appimage-extract", [str(appimage), "--appimage-extract"], cwd=extract)
    inner = extract / "squashfs-root" / "usr" / "bin" / "_internal" / "ffmpeg"
    ev.require("AppImage extracts", unpacked.returncode == 0 and inner.is_dir(), unpacked.stderr[-300:])
    try:
        ev.record["bundled_hashes"] = check_dir(inner, pin)
        ev.require("bundled pair inside the AppImage matches the pin", True)
    except PinError as exc:
        ev.require("bundled pair inside the AppImage matches the pin", False, f"{exc.reason}: {exc}")

    # Which of the pair's declared dependencies the bundle itself carries.
    # PyInstaller collects some for Python and Qt (libgcc_s.so.1 among them),
    # and a child started by the frozen app may load those rather than the
    # host's. Recorded, not judged: which copy ffmpeg actually loads at run
    # time is not measured here.
    internal = inner.parent
    ev.record["bundle_carries_declared_dependencies"] = {
        name: {"size": (internal / name).stat().st_size, "sha256": sha256_file(internal / name)}
        for name in sorted(EXPECTED_NEEDED) if (internal / name).is_file()}

    home = out / "home"
    for sub in ("config", "data", "cache", "tmp"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / "config"),
               XDG_DATA_HOME=str(home / "data"), XDG_CACHE_HOME=str(home / "cache"),
               TMPDIR=str(home / "tmp"), QT_QPA_PLATFORM="offscreen",
               APPIMAGE_EXTRACT_AND_RUN="1")

    def check(label: str, run_env: dict) -> dict[str, str]:
        result = ev.run(label, [str(appimage), "--check"], env=run_env, timeout=180)
        report = _check_report(result.stdout)
        ev.record[label] = {"exit": result.returncode, "resolved": report}
        ev.require(f"{label}: --check exits 0", result.returncode == 0, f"exit {result.returncode}")
        for tool in ("ffmpeg", "ffprobe"):
            path = report.get(tool, "")
            ev.require(f"{label}: {tool} resolved inside the bundle",
                       path.endswith(f"/usr/bin/_internal/ffmpeg/{tool}"), path or "not reported")
        ev.require(f"{label}: reported as bundled", report.get("ffmpeg_origin") == "bundled",
                   report.get("ffmpeg_origin", "no origin"))
        return report

    def check_export(label: str, run_env: dict) -> None:
        """The app's own probe and exports, inside the packaged process."""
        parent = out / label
        parent.mkdir(parents=True, exist_ok=False)
        result = ev.run(label, [str(appimage), "--check-export", str(parent)],
                        env=run_env, timeout=900)
        receipts = sorted(parent.glob("flightdvr-check-export-*/receipt.json"))
        receipt = json.loads(receipts[0].read_text(encoding="utf-8")) if len(receipts) == 1 else {}
        ev.record[label] = {"exit": result.returncode, "receipts": [str(r) for r in receipts],
                            "receipt": receipt}
        ev.require(f"{label}: --check-export exits 0", result.returncode == 0,
                   f"exit {result.returncode}: {result.stdout[-400:]}")
        ev.require(f"{label}: one receipt, reporting PASS",
                   len(receipts) == 1 and receipt.get("result") == "PASS",
                   f"{len(receipts)} receipts; failures {receipt.get('failures')}")
        ev.require(f"{label}: ran frozen", receipt.get("app", {}).get("frozen") is True)
        for tool in ("ffmpeg", "ffprobe"):
            used = receipt.get("tools", {}).get(tool, {})
            ev.require(f"{label}: {tool} used was the bundled, pinned copy",
                       used.get("bundled") is True
                       and used.get("path", "").endswith(f"/usr/bin/_internal/ffmpeg/{tool}")
                       and used.get("sha256") == pin["binaries"][tool],
                       json.dumps(used))
        for name in ("master", "edit", "remux"):
            done = receipt.get("exports", {}).get(name, {})
            ev.require(f"{label}: {name} export done and inspected",
                       done.get("status") == "Done" and "probe" in done, json.dumps(done)[:300])

    check("check-isolated", env)
    check_export("check-export-isolated", env)

    # A conflicting pair first on PATH, which would win if bundled-first broke.
    # Each one leaves a marker if it is ever run, and that fails the check.
    decoy = out / "decoy"
    decoy.mkdir(exist_ok=True)
    marker = out / "decoy-was-run"
    for tool in ("ffmpeg", "ffprobe"):
        script = decoy / tool
        script.write_text(f"#!/bin/sh\necho decoy >> '{marker}'\necho 'decoy {tool}'\n")
        script.chmod(0o755)
    conflicting = dict(env, PATH=f"{decoy}{os.pathsep}{env.get('PATH', '')}")
    ev.require("decoy pair is what PATH would find",
               shutil.which("ffmpeg", path=conflicting["PATH"]) == str(decoy / "ffmpeg")
               and shutil.which("ffprobe", path=conflicting["PATH"]) == str(decoy / "ffprobe"))
    report = check("check-conflicting-path", conflicting)
    ev.require("check-conflicting-path: decoy not selected",
               all(str(decoy) not in report.get(t, "") for t in ("ffmpeg", "ffprobe")))
    check_export("check-export-conflicting-path", conflicting)
    ev.record["decoy_was_run"] = marker.exists()
    ev.require("conflicting PATH: decoy never run", not marker.exists(),
               "the conflicting PATH ffmpeg/ffprobe was executed")
    return ev.finish()


def main(argv: list[str]) -> int:
    commands = {"inspect-pair": inspect_pair, "export-check": export_check,
                "appimage-check": appimage_check}
    if len(argv) == 3 and argv[0] in commands:
        return commands[argv[0]](Path(argv[1]).resolve(), Path(argv[2]).resolve())
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
