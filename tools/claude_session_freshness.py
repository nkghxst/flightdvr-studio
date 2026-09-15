# FlightDVR Studio - browse, trim and convert HDZero goggle DVR footage.
# Copyright (C) 2026 Isadu Nkemi
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.

"""Say at session start when the operating documents being read are not current.

Claude Code reads `CLAUDE.md` and its router from the working directory when a
session starts. An old checkout therefore hands a session old governance and
says nothing about it: one opened 51 commits behind and worked from a
superseded identity matrix for most of its length, discovering the current one
only when an edit found a different document. Nothing in the session could have
noticed.

Two rules shape everything here.

**It only ever reads.** `git fetch` updates refs and changes no working tree;
every other command is a query. It never resets, rebases, pulls, switches or
deletes, because a review worktree is detached or pinned to an exact head *on
purpose* and that is what makes a verdict reproducible.

**It never blocks a session.** Every failure — no git, no repository, no
remote, no network, no ref, unreadable input — is reported in one line and
exits 0. The point is to remove a silent failure, not to add a loud one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

# The documents that govern how a session works. A stale copy of any of these
# is the defect; a stale copy of the product code is not.
OPERATING_DOCS = ("AGENTS.md", "CLAUDE.md", "docs/WORKFLOW.md")

REMOTE = "origin"
BRANCH = "main"
REMOTE_REF = f"refs/remotes/{REMOTE}/{BRANCH}"

# Long enough for an ordinary fetch on a slow link, short enough that nobody
# waits on it. A session start is not the place to discover a hung network.
FETCH_TIMEOUT_SECONDS = 20.0
QUERY_TIMEOUT_SECONDS = 10.0


def _quiet_git_env() -> dict:
    """Git's environment with every interactive prompt refused.

    A credential prompt at session start would hang until the hook timed out,
    which is the one failure mode worse than a stale document. These make git
    fail immediately instead of asking.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    env["SSH_ASKPASS"] = ""
    env["GCM_INTERACTIVE"] = "Never"
    # Keep the output parseable whatever the user's locale is.
    env["LC_ALL"] = "C"
    return env


def _git(args: list[str], cwd: str, timeout: float) -> tuple[int, str, str]:
    """Run one git command. Never raises, whatever goes wrong.

    The argument list is passed through without a shell, so a repository path
    containing spaces — or anything else — is an argument rather than
    something to quote.
    """
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=_quiet_git_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout:g}s"
    except FileNotFoundError:
        return 127, "", "git was not found"
    except OSError as exc:                       # pragma: no cover - rare
        return 126, "", str(exc)
    return done.returncode, done.stdout.strip(), done.stderr.strip()


def working_directory(payload) -> str:
    """Where the session is, from the hook's own input when it is usable.

    The documented `cwd` field is preferred because the hook may be run from
    somewhere else. Anything unusable falls back to the process directory
    rather than failing: a wrong answer here would check the wrong checkout,
    and refusing to check at all is worse than checking where we stand.
    """
    if isinstance(payload, dict):
        given = payload.get("cwd")
        if isinstance(given, str) and given and os.path.isdir(given):
            return given
    return os.getcwd()


def read_payload(stream) -> object:
    """The hook's JSON input, or None when there is none to be had.

    Claude Code supplies it on stdin. A session run by hand has no stdin at
    all, and a future version could send something this does not expect; both
    are ordinary here rather than errors.
    """
    try:
        raw = stream.read()
    except (OSError, ValueError):
        return None
    if not raw or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def repository_root(cwd: str) -> str | None:
    code, out, _ = _git(["rev-parse", "--show-toplevel"], cwd,
                        QUERY_TIMEOUT_SECONDS)
    return out or None if code == 0 else None


def refresh(root: str) -> str:
    """Fetch the one branch this cares about. Returns a reason, or empty.

    Only `origin main`, so a repository with fifty branches pays for one ref.
    A failure is returned rather than raised: the comparison below can still
    run against whatever `origin/main` is already known, and saying the refs
    may be stale is more use than saying nothing.
    """
    code, _, err = _git(["fetch", "--quiet", REMOTE, BRANCH], root,
                        FETCH_TIMEOUT_SECONDS)
    if code == 0:
        return ""
    return err.splitlines()[-1] if err else f"git fetch exited {code}"


def commits_behind(root: str) -> int | None:
    code, out, _ = _git(["rev-list", "--count", f"HEAD..{REMOTE_REF}"], root,
                        QUERY_TIMEOUT_SECONDS)
    if code != 0:
        return None
    try:
        return int(out)
    except ValueError:
        return None


def _remote_bytes(root: str, path: str) -> bytes | None:
    """One file as `origin/main` has it, or None when it is not there."""
    try:
        done = subprocess.run(
            ["git", "show", f"{REMOTE_REF}:{path}"],
            cwd=root, env=_quiet_git_env(), capture_output=True,
            timeout=QUERY_TIMEOUT_SECONDS, shell=False,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return done.stdout if done.returncode == 0 else None


def _working_bytes(root: str, path: str) -> bytes | None:
    """One file as it actually is on disk.

    Read from the working tree, not from the index or from HEAD. An
    uncommitted edit to `CLAUDE.md` is what the session is really reading, and
    comparing the committed version would miss exactly that.
    """
    try:
        with open(os.path.join(root, path), "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _normalised(data: bytes) -> bytes:
    """Line endings flattened, because git itself moves them.

    `core.autocrlf` is on for most of this project's checkouts, so a working
    file is CRLF while the blob it came from is LF. Comparing raw bytes made
    every document on Windows differ from `origin/main` — a warning on every
    session start, which is precisely how a warning stops being read. The
    difference is git's, carries no information about what anybody edited, and
    is removed here rather than reported.
    """
    return data.replace(b"\r\n", b"\n")


def differing_docs(root: str) -> tuple[list[str], list[str]]:
    """Which operating documents differ from `origin/main`, and which could
    not be compared at all.

    Content only: see `_normalised` for why line endings are flattened first.
    """
    differ: list[str] = []
    unknown: list[str] = []
    for path in OPERATING_DOCS:
        theirs = _remote_bytes(root, path)
        ours = _working_bytes(root, path)
        if theirs is None or ours is None:
            unknown.append(path)
        elif _normalised(theirs) != _normalised(ours):
            differ.append(path)
    return differ, unknown


def report(root: str | None, *, fetch_error: str = "", behind: int | None,
           differ: list[str], unknown: list[str]) -> list[str]:
    """The lines to print, which is usually none of them.

    Being behind is **not** on its own worth saying. A review worktree is
    pinned to an exact head deliberately, and telling its reviewer they are
    behind invites the one action that would destroy what they were asked to
    reproduce. What matters is whether the documents being *read* are current.
    """
    if root is None:
        return ["Session freshness: not a git repository, so the operating "
                "documents were not checked."]

    lines: list[str] = []
    if differ:
        named = ", ".join(differ)
        lines.append(
            f"Session freshness: {named} differ" if len(differ) > 1
            else f"Session freshness: {named} differs")
        lines[-1] += f" from {REMOTE}/{BRANCH} in this working tree."
        if behind:
            lines.append(
                f"This checkout is {behind} commit"
                f"{'' if behind == 1 else 's'} behind {REMOTE}/{BRANCH}.")
        lines.append(
            f"Follow the refreshed version — read it with "
            f"`git show {REMOTE}/{BRANCH}:<path>` — and say in your first "
            "status message that you did.")
        lines.append(
            "Do not reset, rebase, pull, switch or delete anything to fix "
            "this. This checkout may be pinned to an exact base on purpose, "
            "and moving it would destroy what a reviewer was asked to "
            "reproduce. Only the coordinator moves an assigned base.")

    if unknown:
        lines.append(
            "Session freshness: could not compare "
            f"{', '.join(unknown)} against {REMOTE}/{BRANCH}.")

    if fetch_error and (differ or unknown):
        lines.append(
            f"The refresh of {REMOTE}/{BRANCH} failed ({fetch_error}), so "
            "this comparison used refs that may themselves be stale.")
    elif fetch_error and not lines:
        lines.append(
            f"Session freshness: could not refresh {REMOTE}/{BRANCH} "
            f"({fetch_error}). The operating documents match the refs "
            "already here, which may be stale.")
    return lines


def main(argv: list[str] | None = None) -> int:
    """Always zero. A freshness check that blocked a session would be worse
    than the staleness it reports."""
    payload = read_payload(sys.stdin)
    cwd = working_directory(payload)
    root = repository_root(cwd)

    if root is None:
        for line in report(None, behind=None, differ=[], unknown=[]):
            print(line)
        return 0

    fetch_error = refresh(root)
    differ, unknown = differing_docs(root)
    behind = commits_behind(root)
    for line in report(root, fetch_error=fetch_error, behind=behind,
                       differ=differ, unknown=unknown):
        print(line)
    return 0


if __name__ == "__main__":                        # pragma: no cover
    try:
        sys.exit(main())
    except Exception as exc:                      # noqa: BLE001
        # Nothing above is expected to raise, and a session start is the worst
        # possible place to be wrong about that.
        print(f"Session freshness: the check itself failed ({exc}).")
        sys.exit(0)
