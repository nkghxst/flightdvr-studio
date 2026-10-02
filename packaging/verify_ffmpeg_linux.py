"""Verify and unpack exactly the Linux ffmpeg build ffmpeg-build-linux.json pins.

    python3 packaging/verify_ffmpeg_linux.py check-archive ARCHIVE
    python3 packaging/verify_ffmpeg_linux.py unpack ARCHIVE DEST
    python3 packaging/verify_ffmpeg_linux.py check-dir FOLDER

The AppImage bundles ffmpeg and ffprobe, and THIRD-PARTY-NOTICES.md names the
exact build it bundles and points at that build's corresponding source.
Packaging anything else would quietly make that attribution false, so nothing
here trusts a file it has not hashed:

- the archive's size and SHA-256 are checked before it is opened at all;
- every member is listed first, and an archive holding an absolute or
  climbing path, a link, a device, or a second candidate for either program
  is refused outright rather than worked around;
- only the two programs and the licence text are read out, by name, to
  destinations that are checked to stay inside the folder being built;
- each program's size and SHA-256 are checked against the pin as it is
  written, and the folder only appears under its final name once everything
  in it has passed, so an interrupted or refused run leaves nothing that looks
  usable.

check-dir is what build-appimage.sh and the spec run on whatever folder they
are about to bundle, so a folder assembled by hand gets the same checks.

Standard library only: this runs before anything is installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

PIN_PATH = Path(__file__).resolve().parent / "ffmpeg-build-linux.json"
TOOLS = ("ffmpeg", "ffprobe")
PROVENANCE = "ffmpeg-linux-provenance.json"
_CHUNK = 1 << 20


class PinError(Exception):
    """Something does not match the pin. `reason` says which check refused."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def load_pin(path: Path = PIN_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(archive: Path, pin: dict) -> None:
    """Size, then SHA-256, before a single byte is interpreted as a tar."""
    size = Path(archive).stat().st_size
    if size != pin["archive_size"]:
        raise PinError("archive-size",
                       f"{archive} is {size} bytes; the pin says "
                       f"{pin['archive_size']}. Do not package anything else.")
    actual = sha256_file(archive)
    if actual != pin["archive_sha256"]:
        raise PinError("archive-hash",
                       f"{archive} does not match the pin.\n"
                       f"  expected {pin['archive_sha256']}\n"
                       f"  found    {actual}\n"
                       f"THIRD-PARTY-NOTICES.md describes {pin['version']}. "
                       "Do not package anything else.")


def _wanted(pin: dict) -> dict[str, str]:
    """Archive member name -> the file name it is written as."""
    root = pin["archive_root"]
    names = {f"{root}/bin/{tool}": tool for tool in TOOLS}
    names[f"{root}/{pin['licence_member']}"] = pin["licence_member"]
    return names


def select_members(tar: tarfile.TarFile, pin: dict) -> dict[str, tarfile.TarInfo]:
    """Refuse an archive with anything unsafe in it, then pick the wanted members."""
    wanted = _wanted(pin)
    chosen: dict[str, tarfile.TarInfo] = {}
    candidates = {tool: [] for tool in TOOLS}
    for member in tar.getmembers():
        path = PurePosixPath(member.name)
        if (path.is_absolute() or member.name.startswith(("/", "\\"))
                or (path.parts and ":" in path.parts[0])):
            raise PinError("unsafe-path", f"absolute member {member.name!r}")
        if ".." in path.parts or "\\" in member.name:
            raise PinError("unsafe-path", f"member {member.name!r} climbs out of the archive")
        if member.issym() or member.islnk():
            raise PinError("link", f"member {member.name!r} is a link to {member.linkname!r}")
        if not (member.isreg() or member.isdir()):
            raise PinError("special-file", f"member {member.name!r} is not a file or folder")
        if path.name in candidates and member.isreg():
            candidates[path.name].append(member.name)
        if member.name in wanted:
            if member.name in chosen:
                raise PinError("duplicate", f"{member.name!r} appears twice")
            if not member.isreg():
                raise PinError("not-a-file", f"{member.name!r} is not a regular file")
            chosen[member.name] = member
    for tool, found in candidates.items():
        if len(found) > 1:
            raise PinError("duplicate", f"more than one {tool} in the archive: {found}")
    missing = [name for name in wanted if name not in chosen]
    if missing:
        raise PinError("missing-member", "the archive has no " + ", ".join(missing))
    return chosen


def _inside(folder: Path, name: str) -> Path:
    target = (folder / name).resolve()
    if target.parent != folder.resolve():
        raise PinError("unsafe-path", f"{name!r} would be written outside {folder}")
    return target


def _expected(pin: dict, file_name: str) -> tuple[int, str]:
    if file_name in pin["binaries"]:
        return pin["binary_sizes"][file_name], pin["binaries"][file_name]
    return pin["licence_size"], pin["licence_sha256"]


def unpack(archive: Path, dest: Path, pin: dict | None = None) -> Path:
    """Verify `archive`, then publish the pair and licence as `dest`.

    `dest` must not exist yet. Everything is written to a sibling folder first
    and renamed into place only after every check has passed.
    """
    pin = pin or load_pin()
    archive, dest = Path(archive), Path(dest)
    if dest.exists():
        raise PinError("destination-exists", f"{dest} already exists; not overwriting it")
    verify_archive(archive, pin)
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{dest.name}.partial-", dir=dest.parent))
    try:
        record = {"archive": pin["archive"], "archive_sha256": pin["archive_sha256"],
                  "archive_size": pin["archive_size"], "url": pin["url"],
                  "release_tag": pin["release_tag"],
                  "build_system_commit": pin["build_system_commit"],
                  "ffmpeg_git_commit": pin["ffmpeg_git_commit_full"], "files": {}}
        with tarfile.open(archive, "r:xz") as tar:
            for member_name, member in select_members(tar, pin).items():
                file_name = _wanted(pin)[member_name]
                size, sha = _expected(pin, file_name)
                target = _inside(staging, file_name)
                digest, written = hashlib.sha256(), 0
                source = tar.extractfile(member)
                with open(target, "xb") as out:
                    while chunk := source.read(_CHUNK):
                        digest.update(chunk)
                        written += len(chunk)
                        out.write(chunk)
                if written != size:
                    raise PinError("binary-size",
                                   f"{file_name} is {written} bytes; the pin says {size}")
                if digest.hexdigest() != sha:
                    raise PinError("binary-hash",
                                   f"{file_name} does not match the pin.\n"
                                   f"  expected {sha}\n  found    {digest.hexdigest()}")
                if file_name in TOOLS:
                    target.chmod(0o755)
                record["files"][file_name] = {"member": member_name, "size": written,
                                              "sha256": sha}
        (staging / PROVENANCE).write_text(json.dumps(record, indent=1) + "\n",
                                          encoding="utf-8")
        os.replace(staging, dest)
    except BaseException:
        _discard(staging)
        raise
    return dest


def _discard(staging: Path) -> None:
    """Remove the staging folder this run created, by exact name only."""
    if not staging.exists():
        return
    for child in staging.iterdir():
        child.unlink()
    staging.rmdir()


def check_dir(folder: Path, pin: dict | None = None) -> dict[str, str]:
    """The folder about to be bundled holds exactly the pinned pair."""
    pin = pin or load_pin()
    folder = Path(folder)
    found = {}
    for tool in TOOLS:
        path = folder / tool
        if path.is_symlink():
            raise PinError("link", f"{path} is a link; bundle the file itself")
        if not path.is_file():
            raise PinError("missing-member",
                           f"{tool} not found in {folder}. Set FFMPEG_DIR to the "
                           "folder packaging/fetch-ffmpeg.sh prints.")
        size = path.stat().st_size
        if size != pin["binary_sizes"][tool]:
            raise PinError("binary-size",
                           f"{path} is {size} bytes; the pin says {pin['binary_sizes'][tool]}")
        actual = sha256_file(path)
        if actual != pin["binaries"][tool]:
            raise PinError("binary-hash",
                           f"{path} does not match the pinned build.\n"
                           f"  expected {pin['binaries'][tool]}\n"
                           f"  found    {actual}\n"
                           f"The notices describe {pin['version']}. Use that build.")
        if os.name != "nt" and not os.access(path, os.X_OK):
            raise PinError("not-executable", f"{path} is not executable")
        found[tool] = actual
    return found


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 3 and argv[0] == "unpack":
            folder = unpack(Path(argv[1]), Path(argv[2]))
            for tool, sha in check_dir(folder).items():
                print(f"  {tool} matches the pin ({sha})", file=sys.stderr)
            print(folder)
            return 0
        if len(argv) == 2 and argv[0] == "check-archive":
            verify_archive(Path(argv[1]), load_pin())
            print("  archive matches the pin")
            return 0
        if len(argv) == 2 and argv[0] == "check-dir":
            for tool, sha in check_dir(Path(argv[1])).items():
                print(f"  {tool} matches the pin ({sha})")
            return 0
    except PinError as exc:
        print(f"refused ({exc.reason}): {exc}", file=sys.stderr)
        return 1
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
