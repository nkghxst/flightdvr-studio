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
