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

"""The session-start freshness check, against disposable repositories.

Every repository here is built in a temporary directory and its "remote" is
another directory beside it, so **nothing in this file reaches the network**.
The two cases that cannot be built that way — a fetch that hangs, and a machine
with no git at all — replace `subprocess.run` rather than arranging the real
thing.

The load-bearing assertion is not in any one test: it is that the check reads.
`assert_untouched` records HEAD, the index and the working tree before the
check runs and compares them after, and every test that drives a real
repository uses it. A hook that helpfully refreshed a pinned review worktree
would destroy the one thing that makes a verdict reproducible.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import claude_session_freshness as freshness

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="these drive a real git repository")


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        check=True, shell=False)
    return done.stdout.strip()


def a_repository(root: Path, docs: dict[str, str]) -> Path:
    """One repository with the operating documents committed."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "--quiet", "-b", "main")
    git(root, "config", "user.email", "nobody@example.invalid")
    git(root, "config", "user.name", "Nobody")
    git(root, "config", "commit.gpgsign", "false")
    for name, text in docs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "--quiet", "-m", "the operating documents")
    return root


DOCS = {
    "AGENTS.md": "the agreement\n",
    "CLAUDE.md": "the entry point\n",
    "docs/WORKFLOW.md": "the procedure\n",
}


@pytest.fixture
def paired(tmp_path):
    """A checkout and the `origin` it was cloned from, both on disk.

    A local path as the remote is what keeps this offline. `fetch origin main`
    behaves the same way; it just has nowhere to go.
    """
    upstream = a_repository(tmp_path / "upstream", DOCS)
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--quiet", str(upstream), str(checkout)],
                   check=True, capture_output=True, shell=False)
    git(checkout, "config", "user.email", "nobody@example.invalid")
    git(checkout, "config", "user.name", "Nobody")
    git(checkout, "config", "commit.gpgsign", "false")
    return upstream, checkout


def move_upstream(upstream: Path, name: str, text: str) -> None:
    (upstream / name).write_text(text, encoding="utf-8")
    git(upstream, "add", "-A")
    git(upstream, "commit", "--quiet", "-m", f"change {name}")


def state(root: Path) -> tuple:
    """Everything the check must leave exactly as it found it."""
    head = git(root, "rev-parse", "HEAD")
    status = git(root, "status", "--porcelain")
    tree = sorted(
        (str(p.relative_to(root)), p.read_bytes())
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts)
    return head, status, tree


def run_check(cwd: Path, capsys, payload: dict | None = None) -> list[str]:
    """Drive `main` the way the hook does, and return what it printed."""
    given = {"hook_event_name": "SessionStart", "source": "startup",
             "cwd": str(cwd)} if payload is None else payload
    stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(given) if given is not None else "")
    try:
        code = freshness.main()
    finally:
        sys.stdin = stdin
    assert code == 0, "the check must never block a session"
    return [l for l in capsys.readouterr().out.splitlines() if l.strip()]


def assert_untouched(root: Path, before: tuple) -> None:
    assert state(root) == before, (
        "the check modified the repository it was asked to inspect")


# -- the quiet case ------------------------------------------------------------

def test_a_current_checkout_says_nothing(paired, capsys):
    """Silence is the common case, and noise here is how a warning stops being
    read."""
    _, checkout = paired
    before = state(checkout)

    assert run_check(checkout, capsys) == []

    assert_untouched(checkout, before)


def test_being_behind_with_current_documents_is_not_worth_saying(paired,
                                                                 capsys):
    """A review worktree is behind on purpose. Telling its reviewer so invites
    the one action that would destroy what they were asked to reproduce."""
    upstream, checkout = paired
    (upstream / "product.py").write_text("code\n", encoding="utf-8")
    git(upstream, "add", "-A")
    git(upstream, "commit", "--quiet", "-m", "product change")
    before = state(checkout)

    said = run_check(checkout, capsys)

    assert said == [], said
    assert freshness.commits_behind(str(checkout)) == 1, (
        "the fixture did not actually put the checkout behind")
    assert_untouched(checkout, before)


# -- the case it exists for ----------------------------------------------------

def test_a_changed_operating_document_is_named(paired, capsys):
    upstream, checkout = paired
    move_upstream(upstream, "CLAUDE.md", "the entry point, rewritten\n")
    before = state(checkout)

    said = "\n".join(run_check(checkout, capsys))

    assert "CLAUDE.md" in said
    assert "git show origin/main:" in said
    assert "Do not reset, rebase, pull, switch or delete" in said
    assert_untouched(checkout, before)


def test_it_never_tells_a_session_to_move_its_base(paired, capsys):
    """The warning has to be actionable without being dangerous."""
    upstream, checkout = paired
    move_upstream(upstream, "docs/WORKFLOW.md", "the procedure, rewritten\n")
    said = "\n".join(run_check(checkout, capsys)).lower()

    for forbidden in ("git pull", "git reset", "git rebase", "git checkout",
                      "update your base", "move your base"):
        assert forbidden not in said, f"it suggested {forbidden!r}"
    assert "only the coordinator moves an assigned base" in said


def test_several_changed_documents_are_all_named(paired, capsys):
    upstream, checkout = paired
    move_upstream(upstream, "AGENTS.md", "the agreement, rewritten\n")
    move_upstream(upstream, "CLAUDE.md", "the entry point, rewritten\n")

    said = "\n".join(run_check(checkout, capsys))

    assert "AGENTS.md" in said and "CLAUDE.md" in said
    assert "docs/WORKFLOW.md" not in said, "it named a document that matches"


def test_the_commit_distance_is_mentioned_only_alongside_a_difference(
        paired, capsys):
    upstream, checkout = paired
    move_upstream(upstream, "CLAUDE.md", "the entry point, rewritten\n")
    (upstream / "product.py").write_text("code\n", encoding="utf-8")
    git(upstream, "add", "-A")
    git(upstream, "commit", "--quiet", "-m", "product change")

    said = "\n".join(run_check(checkout, capsys))

    assert "2 commits behind" in said, said


# -- an uncommitted edit is what the session is actually reading ---------------

def test_an_uncommitted_edit_to_an_operating_document_is_caught(paired,
                                                               capsys):
    """The working file is what a session reads, so the working file is what is
    compared. Reading the committed version would miss exactly this."""
    _, checkout = paired
    (checkout / "CLAUDE.md").write_text("locally edited\n", encoding="utf-8")
    before = state(checkout)

    said = "\n".join(run_check(checkout, capsys))

    assert "CLAUDE.md" in said, said
    assert git(checkout, "status", "--porcelain"), (
        "the fixture did not leave an uncommitted edit")
    assert_untouched(checkout, before)


def test_a_crlf_working_file_against_an_lf_blob_is_not_a_difference(tmp_path,
                                                                    capsys):
    """Found by this suite failing on Windows, and it is the difference
    between a useful hook and one nobody reads.

    `core.autocrlf` is on for most of this project's checkouts, so every
    working file is CRLF while the blob is LF. Comparing raw bytes made all
    three documents differ on every session start — a permanent warning that
    says nothing about what anybody edited, because the difference is git's
    own.
    """
    upstream = a_repository(tmp_path / "upstream", DOCS)
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--quiet", str(upstream), str(checkout)],
                   check=True, capture_output=True, shell=False)
    for name, text in DOCS.items():
        # Written from the known text, not by transforming what is on disk:
        # a checkout with `core.autocrlf` already has CRLF, and converting it
        # again would produce `\r\r\n` and test a difference nobody has.
        (checkout / name).write_bytes(
            text.replace("\n", "\r\n").encode("utf-8"))
    written = (checkout / "CLAUDE.md").read_bytes()
    assert b"\r\n" in written and b"\r\r" not in written, (
        "the fixture did not produce a plain CRLF working file")

    assert run_check(checkout, capsys) == []


def test_a_real_edit_is_still_caught_when_the_endings_also_differ(tmp_path,
                                                                  capsys):
    """Normalising endings must not normalise away the content."""
    upstream = a_repository(tmp_path / "upstream", DOCS)
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--quiet", str(upstream), str(checkout)],
                   check=True, capture_output=True, shell=False)
    (checkout / "CLAUDE.md").write_bytes(b"the entry point, changed\r\n")

    said = "\n".join(run_check(checkout, capsys))

    assert "CLAUDE.md" in said, said


# -- a pinned, detached checkout ----------------------------------------------

def test_a_detached_old_head_with_current_documents_stays_quiet(paired,
                                                                capsys):
    """The shape of every review worktree here."""
    upstream, checkout = paired
    first = git(checkout, "rev-parse", "HEAD")
    (upstream / "product.py").write_text("code\n", encoding="utf-8")
    git(upstream, "add", "-A")
    git(upstream, "commit", "--quiet", "-m", "product change")
    git(checkout, "checkout", "--quiet", "--detach", first)
    before = state(checkout)

    assert run_check(checkout, capsys) == []

    assert git(checkout, "rev-parse", "HEAD") == first
    assert_untouched(checkout, before)


def test_a_detached_old_head_with_stale_documents_still_warns(paired, capsys):
    upstream, checkout = paired
    first = git(checkout, "rev-parse", "HEAD")
    move_upstream(upstream, "AGENTS.md", "the agreement, rewritten\n")
    git(checkout, "checkout", "--quiet", "--detach", first)
    before = state(checkout)

    said = "\n".join(run_check(checkout, capsys))

    assert "AGENTS.md" in said
    assert git(checkout, "rev-parse", "HEAD") == first, (
        "the check moved a deliberately pinned head")
    assert_untouched(checkout, before)


# -- failing open --------------------------------------------------------------

def test_somewhere_that_is_not_a_repository_says_so_and_exits_zero(tmp_path,
                                                                   capsys):
    plain = tmp_path / "not a repo"
    plain.mkdir()

    said = "\n".join(run_check(plain, capsys))

    assert "not a git repository" in said


def test_a_missing_remote_does_not_block_or_raise(tmp_path, capsys):
    """No `origin` at all: the fetch fails and there is no ref to compare."""
    lonely = a_repository(tmp_path / "lonely", DOCS)
    before = state(lonely)

    said = "\n".join(run_check(lonely, capsys))

    assert "could not" in said.lower(), said
    assert_untouched(lonely, before)


def test_a_fetch_that_hangs_is_bounded_and_still_compares(paired, capsys,
                                                          monkeypatch):
    """A credential prompt or a dead link must not hold a session open."""
    upstream, checkout = paired
    move_upstream(upstream, "CLAUDE.md", "the entry point, rewritten\n")
    git(checkout, "fetch", "--quiet", "origin", "main")
    real = subprocess.run
    seen: list[float] = []

    def hang(args, **kwargs):
        if isinstance(args, list) and len(args) > 1 and args[1] == "fetch":
            seen.append(kwargs.get("timeout"))
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout") or 0)
        return real(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", hang)
    before = state(checkout)

    said = "\n".join(run_check(checkout, capsys))

    assert seen and seen[0] == freshness.FETCH_TIMEOUT_SECONDS, (
        "the fetch was not given an explicit bounded timeout")
    assert "CLAUDE.md" in said, "a failed refresh stopped it comparing at all"
    assert "may themselves be stale" in said
    assert_untouched(checkout, before)


def test_no_git_at_all_reports_rather_than_raises(tmp_path, capsys,
                                                  monkeypatch):
    plain = tmp_path / "anywhere"
    plain.mkdir()

    def absent(args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(subprocess, "run", absent)

    said = "\n".join(run_check(plain, capsys))

    assert "not a git repository" in said, said


def test_unreadable_hook_input_falls_back_instead_of_failing(paired, capsys,
                                                             monkeypatch):
    """Run by hand, or sent something unexpected: check where we stand."""
    _, checkout = paired
    (checkout / "CLAUDE.md").write_text("locally edited\n", encoding="utf-8")
    monkeypatch.chdir(checkout)

    for payload in (None, {"cwd": 17}, {"cwd": "/no/such/place"}):
        said = "\n".join(run_check(checkout, capsys, payload=payload))
        assert "CLAUDE.md" in said, (payload, said)


def test_bad_json_on_stdin_is_not_an_error(paired, capsys, monkeypatch):
    _, checkout = paired
    monkeypatch.chdir(checkout)
    stdin = sys.stdin
    sys.stdin = io.StringIO("{not json at all")
    try:
        assert freshness.main() == 0
    finally:
        sys.stdin = stdin
    assert capsys.readouterr().out.strip() == ""


# -- the awkward path ----------------------------------------------------------

def test_a_path_with_spaces_is_handled(tmp_path, capsys):
    """Windows development directories have spaces in them, and every git call
    here passes arguments rather than a command line."""
    upstream = a_repository(tmp_path / "up stream repo", DOCS)
    checkout = tmp_path / "my check out"
    subprocess.run(["git", "clone", "--quiet", str(upstream), str(checkout)],
                   check=True, capture_output=True, shell=False)
    move_upstream(upstream, "CLAUDE.md", "the entry point, rewritten\n")
    before = state(checkout)

    said = "\n".join(run_check(checkout, capsys))

    assert "CLAUDE.md" in said, said
    assert_untouched(checkout, before)


# -- it asks git for nothing that could change anything ------------------------

def test_every_git_command_it_runs_is_a_read(paired, capsys, monkeypatch):
    """Fetch updates refs and no working tree; everything else is a query.

    Asserted on the verbs rather than on the outcome, because a command that
    happened not to change anything on this fixture could still change
    something on a real checkout.
    """
    upstream, checkout = paired
    move_upstream(upstream, "CLAUDE.md", "the entry point, rewritten\n")
    real = subprocess.run
    verbs: list[str] = []

    def record(args, **kwargs):
        if isinstance(args, list) and args and args[0] == "git":
            verbs.append(args[1])
        return real(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", record)
    run_check(checkout, capsys)

    assert verbs, "it ran no git at all"
    assert set(verbs) <= {"rev-parse", "fetch", "rev-list", "show"}, verbs


def test_the_fetch_refuses_to_prompt_for_credentials(paired, capsys,
                                                     monkeypatch):
    """A prompt at session start would hang until the hook timed out."""
    upstream, checkout = paired
    move_upstream(upstream, "CLAUDE.md", "the entry point, rewritten\n")
    real = subprocess.run
    envs: list[dict] = []

    def record(args, **kwargs):
        if isinstance(args, list) and len(args) > 1 and args[1] == "fetch":
            envs.append(kwargs.get("env") or {})
        return real(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", record)
    run_check(checkout, capsys)

    assert envs, "no fetch was attempted"
    assert envs[0].get("GIT_TERMINAL_PROMPT") == "0"
    assert envs[0].get("GCM_INTERACTIVE") == "Never"


def test_it_fetches_one_branch_and_not_everything(paired, capsys, monkeypatch):
    upstream, checkout = paired
    real = subprocess.run
    calls: list[list[str]] = []

    def record(args, **kwargs):
        if isinstance(args, list) and len(args) > 1 and args[1] == "fetch":
            calls.append(list(args))
        return real(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", record)
    run_check(checkout, capsys)

    assert calls == [["git", "fetch", "--quiet", "origin", "main"]], calls


def test_no_git_call_uses_a_shell(paired, capsys, monkeypatch):
    """A repository path is an argument, never something to quote."""
    _, checkout = paired
    real = subprocess.run
    shells: list[object] = []

    def record(args, **kwargs):
        shells.append(kwargs.get("shell"))
        return real(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", record)
    run_check(checkout, capsys)

    assert shells and not any(shells), shells


def test_every_line_it_can_print_is_plain_ascii():
    """A Windows console on a legacy code page cannot encode an em dash, and
    `print` raising there would turn this into the hook error it exists to
    avoid. Asserted on every branch of `report`, not on one sample of output.
    """
    cases = [
        freshness.report(None, behind=None, differ=[], unknown=[]),
        freshness.report("/repo", behind=1, differ=["CLAUDE.md"], unknown=[]),
        freshness.report("/repo", behind=None,
                         differ=["AGENTS.md", "docs/WORKFLOW.md"],
                         unknown=["CLAUDE.md"]),
        freshness.report("/repo", fetch_error="no route to host", behind=0,
                         differ=[], unknown=[]),
        freshness.report("/repo", fetch_error="timed out after 20s",
                         behind=3, differ=["CLAUDE.md"], unknown=[]),
    ]
    assert any(lines for lines in cases), "no branch produced any output"
    for lines in cases:
        for line in lines:
            line.encode("ascii")           # raises if it ever stops being so


# -- what the settings file has to say -----------------------------------------

def test_the_hook_is_wired_to_this_helper():
    """The settings file and the helper are two halves of one change; a rename
    that broke the wiring would otherwise be invisible until a session start.
    """
    root = Path(__file__).resolve().parents[1]
    settings = root / ".claude" / "settings.json"
    if not settings.exists():
        pytest.skip("the repository-local settings file is not present")
    config = json.loads(settings.read_text(encoding="utf-8"))

    entries = config["hooks"]["SessionStart"]
    matchers = {entry.get("matcher") for entry in entries}
    assert matchers == {"startup", "resume"}, matchers

    for entry in entries:
        for hook in entry["hooks"]:
            assert hook["type"] == "command"
            assert "tools/claude_session_freshness.py" in hook["command"]
            # Quoted, because the checkout path may contain spaces.
            assert '"${CLAUDE_PROJECT_DIR}/tools/' in hook["command"]
            assert isinstance(hook.get("timeout"), int)
            assert hook["timeout"] > freshness.FETCH_TIMEOUT_SECONDS


# -- what a failure is allowed to say ------------------------------------------
#
# A fetch error is the remote's own text. It quotes the URL, so a credential in
# a path or query would be repeated into session context, and it arrives in
# whatever encoding the remote chose, which is the other way the ASCII-only
# promise above gets broken. These pin the reason to a fixed set instead.

DUMMY_TOKEN = "NOT_A_REAL_TOKEN_0000"
DUMMY_REMOTE = f"http://user:{DUMMY_TOKEN}@127.0.0.1:1/repo?token={DUMMY_TOKEN}"


def failing_git(monkeypatch, *, code: int, stderr: str):
    """Make every git call fail the same way, without running git.

    The point is the value `refresh` returns, not git's behaviour, and a real
    remote is not needed to settle it — nor wanted, since the whole subject is
    text arriving from outside this machine.
    """
    calls = []

    def fake(args, cwd, timeout):
        calls.append(list(args))
        return code, ""
    monkeypatch.setattr(freshness, "_git", fake)
    # Kept so a reader can see what git would have printed on this path. It is
    # deliberately not returned: `_git` has no stderr channel any more, which
    # is the point of `test_git_hands_back_no_channel_for_what_git_printed`.
    assert stderr
    return calls


def test_a_fetch_failure_never_repeats_what_git_printed(monkeypatch):
    """The defect: the last stderr line was returned verbatim, and a fetch
    error quotes the remote URL — credentials and all."""
    failing_git(monkeypatch, code=128,
                stderr=f"fatal: unable to access '{DUMMY_REMOTE}': refused")

    reason = freshness.refresh("/repo")

    assert DUMMY_TOKEN not in reason, reason
    assert "127.0.0.1" not in reason, reason
    assert "http" not in reason, reason
    assert reason == "git fetch exited 128"


def test_the_warning_a_session_sees_carries_no_secret(monkeypatch):
    """End of the same path: whatever `refresh` returns is interpolated into
    the line a session reads, so the assertion belongs there too."""
    failing_git(monkeypatch, code=128,
                stderr=f"fatal: unable to access '{DUMMY_REMOTE}': refused")
    reason = freshness.refresh("/repo")

    for lines in (
        freshness.report("/repo", fetch_error=reason, behind=0,
                         differ=[], unknown=[]),
        freshness.report("/repo", fetch_error=reason, behind=2,
                         differ=["CLAUDE.md"], unknown=[]),
        freshness.report("/repo", fetch_error=reason, behind=None,
                         differ=[], unknown=["AGENTS.md"]),
    ):
        said = "\n".join(lines)
        assert said, "this branch printed nothing to check"
        assert DUMMY_TOKEN not in said, said
        assert "127.0.0.1" not in said, said


def test_a_non_ascii_fetch_error_cannot_reach_the_output(monkeypatch):
    """Git's stderr is not ASCII by any promise of git's. The committed ASCII
    test only ever supplied ASCII fixtures, so it could not catch this."""
    failing_git(monkeypatch, code=128,
                stderr="fatal: " + chr(0x4E2D) + chr(0x6587))

    reason = freshness.refresh("/repo")

    reason.encode("ascii")                 # raises if stderr got through
    lines = freshness.report("/repo", fetch_error=reason, behind=1,
                             differ=["CLAUDE.md"], unknown=[])
    assert lines
    for line in lines:
        line.encode("ascii")


def test_each_way_git_fails_to_run_has_its_own_named_reason(monkeypatch):
    """A code is something a reader can act on. It is also an integer, which
    is what makes it safe to print."""
    seen = {}
    for code in (freshness.TIMED_OUT, freshness.CANNOT_RUN,
                 freshness.NOT_FOUND, 128, 1):
        failing_git(monkeypatch, code=code, stderr=DUMMY_REMOTE)
        seen[code] = freshness.refresh("/repo")

    assert seen[freshness.TIMED_OUT] == "the fetch timed out"
    assert seen[freshness.CANNOT_RUN] == "git could not be run"
    assert seen[freshness.NOT_FOUND] == "git was not found"
    assert seen[128] == "git fetch exited 128"
    assert seen[1] == "git fetch exited 1"
    assert len(set(seen.values())) == len(seen), (
        "two different failures gave the same reason")


def test_a_refusal_to_run_git_at_all_says_nothing_about_the_path(monkeypatch):
    """`_git`'s own OSError branch fed the same warning with `str(exc)`, and
    an OS error names the path it failed on."""
    def explode(*args, **kwargs):
        raise OSError(f"cannot execute {DUMMY_REMOTE}")
    monkeypatch.setattr(freshness.subprocess, "run", explode)

    returned = freshness._git(["fetch"], os.getcwd(), 1.0)

    assert returned[0] == freshness.CANNOT_RUN
    assert not any(DUMMY_TOKEN in str(value) for value in returned), returned
    assert freshness.refresh(os.getcwd()) == "git could not be run"


def test_git_hands_back_no_channel_for_what_git_printed(monkeypatch):
    """Structural, not a promise anybody has to keep. Stderr is captured so it
    stays off the console and then dropped, so no caller can forward it even
    by mistake — which is how the last one got in."""
    class Done:
        returncode = 128
        stdout = "out"
        stderr = f"fatal: unable to access '{DUMMY_REMOTE}'"

    monkeypatch.setattr(freshness.subprocess, "run",
                        lambda *a, **k: Done())

    returned = freshness._git(["fetch"], os.getcwd(), 1.0)

    assert len(returned) == 2, (
        f"_git still offers a channel for git's own text: {returned!r}")
    assert not any(DUMMY_TOKEN in str(value) for value in returned), returned


def test_the_last_resort_handler_reports_the_type_not_the_message(capsys):
    """The entrypoint's own catch interpolated `{exc}`, the one place left
    that could print anything it was handed.

    Driving `report_failure` rather than restating it. Written the other way
    round first, this test rebuilt the line itself and would have passed
    against the very code it was meant to pin.
    """
    class Failure(Exception):
        pass

    code = freshness.report_failure(Failure(f"leaked {DUMMY_REMOTE}"))

    said = capsys.readouterr().out
    assert code == 0, "the last-resort handler must still exit zero"
    assert DUMMY_TOKEN not in said, said
    assert "127.0.0.1" not in said, said
    assert "Failure" in said, said
    said.encode("ascii")


def test_the_entrypoint_is_wired_to_that_handler(monkeypatch, capsys):
    """`main` raising must reach `report_failure`, not a bare traceback."""
    def explode(*args, **kwargs):
        raise RuntimeError(f"leaked {DUMMY_REMOTE}")
    monkeypatch.setattr(freshness, "main", explode)

    try:
        freshness.main()
    except Exception as exc:                      # noqa: BLE001
        assert freshness.report_failure(exc) == 0
    said = capsys.readouterr().out
    assert DUMMY_TOKEN not in said, said
    assert "RuntimeError" in said, said


def test_plain_ascii_survives_a_name_that_is_not_ascii():
    """A class name is allowed to be non-ASCII, and the handler prints one."""
    rendered = freshness.plain_ascii("Fehler" + chr(0x4E2D))

    rendered.encode("ascii")
    assert "Fehler" in rendered


def test_it_exits_zero_on_a_failed_fetch_under_a_strict_ascii_console(
        tmp_path):
    """The executable, as the hook runs it, on a console that cannot encode
    anything but ASCII — with a fetch that fails and nothing reachable.

    The remote shape is load-bearing and was got wrong first. A nonexistent
    local path also fails, but git puts the path on an early stderr line and
    ends with "and the repository exists."; the old code took the *last* line,
    so that fixture passed against the very defect it was written for. A URL
    fails on one line, with the URL on it.

    `127.0.0.1:1` is refused by the local stack — nothing leaves the machine,
    no endpoint is contacted, and the proxy and credential helper are disabled
    for this repository so neither is consulted.
    `PYTHONIOENCODING=ascii:strict` makes `print` raise on the first non-ASCII
    character, so a leaked diagnostic is a non-zero exit rather than a quiet
    pass.
    """
    checkout = a_repository(tmp_path / "checkout", DOCS)
    git(checkout, "remote", "add", "origin",
        f"http://user:{DUMMY_TOKEN}@127.0.0.1:1/repo?token={DUMMY_TOKEN}")
    git(checkout, "config", "http.proxy", "")
    git(checkout, "config", "credential.helper", "")

    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "ascii:strict"
    environment["PYTHONPATH"] = str(Path(freshness.__file__).parents[1])
    done = subprocess.run(
        [sys.executable, freshness.__file__],
        input=json.dumps({"cwd": str(checkout)}),
        capture_output=True, text=True, env=environment,
        cwd=str(checkout), timeout=180, shell=False)

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip(), "a failed refresh said nothing at all"
    assert DUMMY_TOKEN not in done.stdout, done.stdout
    assert "127.0.0.1" not in done.stdout, done.stdout
    assert "Traceback" not in done.stderr, done.stderr
    done.stdout.encode("ascii")
