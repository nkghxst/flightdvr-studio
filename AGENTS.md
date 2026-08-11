# Working on FlightDVR Studio

Read this before touching anything. It is written for the three assistants that
work on this repository — **Sol**, **Claude** and **Luna** — and for anyone else
who turns up. None of them remembers the last session, and they cannot see each
other's machines. **The repository is the only durable channel between them.**
Anything not written down here, in an issue, or in a commit message did not
happen: a chat message is how work is coordinated, not how it is recorded.

The maintainer is Isadu Nkemi (`nkghxst`). Only he merges.

---

## What this is

A desktop app that gets HDZero goggle DVR footage off a card and into something
usable: browse, preview, trim, convert. Python, PySide6, ffmpeg under the hood.
Users are FPV pilots, not video engineers.

`README.md` covers using it. `docs/DEVELOPMENT.md` covers building it and the
reasoning behind the parts that look arbitrary — **read it before changing
anything to do with colour, seeking or the preview player.** `docs/ROADMAP.md`
covers where it is going and what it deliberately will not do.

---

## How the three of us work together

Three assistants, one repository, and only two GitHub identities between them.
Most of the rules below exist because of that arithmetic.

| Agent | Chat sender | Suited to |
|---|---|---|
| **Sol** | `sol` | Building features, and the debugging nobody else can finish |
| **Claude** | `claude` | Architecture, specification, adversarial review |
| **Luna** | `luna` | Triage, repository investigation, tests, documentation, CI evidence, first-pass review |

The roles say who is *best* placed for a piece of work, not who is permitted to
do it. Any of the three may take any issue; what does not change is that the
author never reviews their own work.

### Luna is a cost decision

Sol and Claude are expensive per token. Luna is cheap and tireless, and she is
here so that the expensive attention is spent on the work that actually needs
it. **Route bounded, checkable work to her first**: reproducing and narrowing a
reported bug, locating the relevant code and writing an implementation brief,
extending tests after somebody implements, gathering CI evidence, updating
release notes and documentation, checking an ffmpeg command or a signal flow,
preparing issue and PR summaries for the others.

**The saving is end to end or it is not a saving.** A brief that takes longer to
write than the work would take to do has cost more than it saved, and so has a
result nobody can check without redoing it. Before delegating, make sure the
task has: a scope stated in files or directories, an explicit *not* list, the
exact head to work from, a definition of done, somewhere to report, and
something objective the result can be checked against — a test, a measurement,
a diff. Guidance is not overhead on delegation; it is the delegation.

**Her output is scoped input, not assurance.** That is not a comment on the
quality of it — it follows from her being the cheap pass. Anything that becomes
a *claim* — a verdict, a measurement, a merge-blocking finding, "there are no
others" — gets checked by Sol or Claude before it is relied on.

**A negative finding must state the net it used.** "None found" is the easiest
claim to get wrong and the hardest to notice. Luna's first pass on PR #72
searched Markdown outside two files, said exactly that, and was right about what
it covered; a wider search then found four more mentions in code and tests. The
bounding is what made the second pass cheap — it is the model to copy, not a
failure to avoid.

**Verdict ownership.** The largest saving is Luna as the maker, not the judge:
she may own a bounded implementation or documentation branch that Sol or Claude
then reviews. As a reviewer she performs advisory first passes. She is **never**
the sole verdict owner on anything touching output correctness — ffmpeg
arguments, timing, colour, cancellation, queue mutation — and for now not on
docs or mechanical changes either, until there are enough calibrated examples to
revisit it or the maintainer assigns one explicitly. The reason is arithmetic
rather than distrust: if the cheap pass owns the only reciprocal verdict, then
nothing independent has checked it, which is the opposite of what delegating to
it was for. Every blocking finding on PR #70 lived in exactly those
output-correctness paths, and every one of them needed a measurement rather than
a reading.

The saving survives that restriction, because the expensive pass then starts
from evidence and a narrow delta instead of from nothing.

**One agent owns a branch at a time.** This is the rule most likely to be broken
by good intentions — "Luna adds the tests while Sol implements" is genuinely
valuable and is exactly how two agents end up pushing to one branch. Tests or
fixes from a non-owner arrive as a review comment, a patch pasted in chat, or a
follow-up PR. If the branch really should change hands, the owner says so in
chat and stops pushing. Two agents on one branch is worse than the file
collisions the 1.6 milestone was sequenced around, because git will merge it
quietly and neither agent will know whose assumption survived.

### Before starting anything

```bash
gh issue list --label ready          # what is available
gh pr list --state open              # what is already claimed
```

If an open PR touches the file you were about to change, pick something else or
say so on the issue. This matters more than it sounds: `ui.py` is still large
enough that two features in it will conflict.

**An error from `gh` is not an empty list.** A fresh machine or a fresh agent may
have no GitHub login at all, and `gh` then answers with `HTTP 401` rather than
nothing — which reads exactly like "no open PRs" to anyone skimming. Run
`gh auth status` first; if it fails, say so in chat and ask, rather than
concluding that nothing is claimed and starting work on top of somebody.

### Claiming work

**Open a draft PR on your first commit, before doing the work.** Title it for
the issue it closes. That is the claim — it costs nothing extra, because the PR
has to exist anyway, and unlike a status file it cannot drift out of date.

Commits and branch pushes use the maintainer's normal Git identity, but the PR
itself must be opened with the `flightdvr-assistant-nkghxst` GitHub App. GitHub
then records the app as the PR author, so `nkghxst` can submit a real approval
or request changes. The app is installed only on this repository and has only
contents read and pull-request write access.

```powershell
git checkout -b short-branch-name
git commit                           # something small and real
$env:GH_TOKEN = & .\tools\github_app_token.ps1
try {
    gh pr create --draft --title "..." --body "Closes #14"
} finally {
    Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue
}
```

The helper reads the app ID, installation ID and private-key path from this
checkout's local git configuration. It prints a short-lived installation token
but never stores one. The private key lives outside the repository on the
development machine. Never commit it, copy it into the worktree, or persist an
installation token in `gh` configuration. Its ACL deliberately denies the
assistant sandbox; request a one-time host-side approval to mint a token rather
than weakening that boundary.

The app never needs its own persistent `gh auth login`. Assigning the token to
`GH_TOKEN` temporarily selects the app identity for exactly that command; removing
the variable restores the normal `nkghxst` login. The laptop and desktop may mint
separate short-lived tokens at the same time from their own private keys.

Mark it ready for review when it is finished.

### Reviewing

**Whoever did not write it reviews it.** Then the maintainer merges. This has
already earned its keep in every direction it has been tried: a review of PR #9
found three real defects, a review of a roadmap found four of its six stated
prerequisites had already shipped, and the review of PR #70 found a preset that
silently dropped a third of the frames in a recording. (Sol was called Codex
before the team grew; older reviews and issues still say so.)

### One verdict, named at handoff

With three agents it is no longer obvious who "the reviewer" is, and two
half-reviews add up to nobody having checked. So:

- The author names the reviewer when they hand the PR over. That agent submits
  the GitHub review, and it is the only review that counts as the reciprocal
  check the maintainer waits for.
- A **first-pass review** — missing tests, stale documentation, obvious
  regressions, CI evidence — is advisory, posted in chat, and does not approve
  or request changes on GitHub. It is genuinely useful before the real review,
  and it is not a substitute for one.
- Two agents may both look. Only one owns the verdict, and the PR says which.

A handoff that leaves any of these unstated will cost somebody an hour:

| | |
|---|---|
| **Branch owner** | who may push to it — exactly one agent |
| **Verdict owner** | who submits the GitHub review |
| **Exact head** | the full SHA reviewed, because it moves under you |
| **Done criteria** | what the reviewer should check, and what "finished" means |
| **Findings destination** | the PR for the verdict, the channel for advisory notes |

The exact head matters more than it looks. On PR #70 a review began on a head
that had already been superseded twice, and each time the reviewer had to stop
and re-verify. Say the SHA, and confirm it has not moved immediately before
submitting.

```bash
gh pr diff 15
gh pr view 15 --json author --jq .author.login
```

**Every review starts by naming its actual reviewer.** There are three agents
and two GitHub identities, so the login on a review does not identify who wrote
it — two of the three are always sharing one. That first line is the audit
trail, not a courtesy:

```
Reviewer: Sol (<current model>)
Reviewer: Claude Code (<current model>)
Reviewer: Luna (<current model>)
```

State the model you are actually running as, not the one the role table implies.
The same goes for a commit trailer or a chat handoff: "reviewed" without a name
is unverifiable the moment more than two of us are working.

The review must be submitted by the identity that did **not** open the PR:

- If the PR author is `app/flightdvr-assistant-nkghxst`, review with the normal
  `nkghxst` login. Make sure `GH_TOKEN` is unset first.
- If the PR author is `nkghxst`, review with a short-lived app token. This is the
  fallback for a PR accidentally opened under the human identity; it preserves a
  distinct maker and reviewer without adding another persistent login.
- Never review an app-authored PR with the app, or a human-authored PR with the
  human login. GitHub does not allow an author to approve its own PR.

Use the review result that matches the evidence: `--approve` when it is ready,
`--request-changes` for confirmed blocking findings, or `--comment` when there is
no binary verdict. For a human-authored PR:

```powershell
$env:GH_TOKEN = & .\tools\github_app_token.ps1
try {
    gh pr review 15 --approve --body-file review.md
} finally {
    Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue
}
```

Reviews and merges are different operations: only the maintainer merges, always
under the normal `nkghxst` login with `GH_TOKEN` unset.

Say what you checked and what you could not check. A review that only lists
findings hides how much of the change was actually looked at.

Confirm a finding against the code before reporting it, and say whether you
did. "This looks wrong" and "I ran it and it is wrong" are different claims.

**A cancelled run is not a passing run, and "nothing is pending" is not "the run
finished".** Both mistakes were made on PR #70 within an hour. Pushing a new
commit cancels the run for the previous head, and for a few seconds afterwards
no job is pending because the next run has not registered yet — so a check for
"any pending jobs" answers "no" and a status check answers `CLEAN`, both about a
head that was never tested. Map runs to commits before reporting anything:

```bash
gh run list --branch <branch> --limit 5 \
  --json databaseId,headSha,status,conclusion
```

Report the head SHA next to the result, wait for every job rather than the test
jobs alone, and treat a `cancelled` conclusion as no evidence at all.

**Ubuntu-latest passing is not evidence about the oldest ffmpeg we support.** The
test runners carry a current build; the 22.04 image behind the AppImage job is
the only place ffmpeg 4.4 is exercised, and it is where `-fps_mode` does not
exist. A current Windows build has meanwhile *removed* `-vsync`. Anything
touching ffmpeg arguments waits for the packaging jobs too, and asks
`frame_rate_mode()` rather than naming an option — including in tests.

A queued, absent or cancelled GitHub Actions job is not evidence that the code
failed. GitHub itself has outages and capacity problems. Check the current run
and its logs before calling a CI failure a code failure, and say when remote
verification was unavailable.

**The two machines hold different footage**, so a measurement on real media
usually cannot be repeated by the other reviewer — the laptop has `D:\movies`,
the desktop has `F:\FPV clips` and whatever card is in the reader. Identify the
source with **both its filename and a content-derived review ID** — for example
`hdz_022.ts (clip ecc754e09f6eed20)`.

The filename is for the person reading the review, who knows his own card and
cannot map a bare hash back to a recording without hashing all of them. The ID
is for the claim: DVR filenames repeat after a card wipe, so a name alone stops
meaning anything the moment the card is reused, and the content is what stays
the same if a file ever moves between machines. Compute the full-file SHA-256
and report its first 16 hexadecimal characters, normalised to lowercase:

```powershell
(Get-FileHash -Algorithm SHA256 -LiteralPath $clip).Hash.Substring(0, 16).ToLowerInvariant()
```

It costs about a second on a 600 MB clip, so there is no reason to skip it.

Do not use `ClipInfo.fingerprint` instead: it deliberately incorporates the
local path and modification time for cache invalidation, so it changes across
copies — the opposite of what an identifier that travels needs.

Folder names like `F:\FPV clips` or `G:\movies` are fine to write down; they
say nothing worth withholding. A review that says "I could not repeat this and
here is why" is doing its job; one that quietly skips the check is not.

Offscreen Qt is not a substitute for either. It has no usable fonts, reports
text widths roughly double the real ones, and no key mapper — so a layout
"verified" there says nothing about the real window, and a shortcut that does
not fire there may well fire on a keyboard. Both mistakes have been made.

### Handing over

Nothing special is required if the rules above are followed, because the state
lives in git and on the issue tracker. If you stop mid-task, push what you have
to the draft PR and write what is left in its description. An unpushed branch
on someone's disk is invisible to everyone.

---

## How to work here

These are not style preferences. Each one is here because ignoring it cost real
time, and the history is in `docs/DEVELOPMENT.md`.

### Measure. Do not assert.

The single most important habit in this codebase.

The colour handling is what it is because four candidate chains were measured
by rendering to RGB and comparing, and the intuitive answer lost. A trim was
documented as landing "exactly" for a whole release while it was in fact
producing half a second of garbage on every mid-GOP cut. Both were found by
measuring; neither was visible in the arguments.

If you are about to write "this should be identical" or "this is faster",
measure it and put the number in the commit message. If you cannot measure it,
say that instead of implying you did.

**This applies to test data too, and that is the easiest place to forget it.**
A detector for the still period before take-off was written with fourteen tests
against synthetic curves. Every one passed. It then failed on every real clip,
because there is no still period before take-off in this footage — the DVR
records from arming. The tests had confirmed the author's idea of a recording,
not a recording. Nothing about them was wrong except where the numbers came
from.

Synthetic data is fine, and often the only way to test an awkward shape. But
**take the shapes from the world**: run the thing over a real card, look at what
comes back, and build the fixtures from those numbers. If the tests and the
footage disagree, the footage is right.

**A test that agrees with the implementation proves nothing, and this is the
trap that comes with dividing the work up.** Two examples from one afternoon.
An equivalence matrix for naming templates appended the file extension by hand
instead of building the real path, so it passed while every non-Remux export was
renamed. A still-capture test built its reference with the same colour chain the
code under test used, so a perfect score proved the frame and said nothing about
the colour. Both were written by the author of the code.

Independent tests are stronger — the person who wrote the code cannot help
knowing what it does — but only if the assertion comes from the issue and from
measurement, never from reading the implementation and describing it back. If a
test claims to catch something, show that it does: **revert the fix and watch it
fail**, or measure the output and put the number in the commit message. "It
passes" is not evidence that it would have caught the defect.

### Write down the trap, next to the code

Comments here explain **why**, not what. Prefer the comment that saves the next
person from re-deriving something painful:

> Terminating ffmpeg makes `communicate()` return normally, so a cancelled
> extraction reached the publishing code with whatever frames it had.

That is worth ten comments saying what the line does.

### Tests are sentences about what went wrong

`test_a_mid_gop_trim_produces_no_corrupt_frames`, not `test_trim_2`. The
docstring says what broke and what it looked like when it did. A test whose
name does not describe a failure mode is usually testing the implementation
rather than the behaviour.

- `xfail(strict=True)` is the known-defects list. `xfail_strict` is on, so a
  defect that gets fixed turns the test into an error telling you to delete the
  marker. Never let it rot.
- **Assert your fixtures.** Three attempts at an odd-dimensions fixture tested
  nothing and passed while doing it, because the encoder quietly rounded the
  size. If a fixture is meant to have an awkward property, check that it has it
  and skip loudly if not.
- **Compare paths as `Path`, never as strings.** To POSIX,
  `C:\Program Files\...` is one long filename, so a string comparison silently
  never matches and the test passes for the wrong reason.
- Unit tests need no ffmpeg and no display. Integration tests
  (`-m integration`) run real ffmpeg and inspect the file that comes out. They
  exist because eighteen defects were once found in code where **every**
  argument was correct.

### Do not break these

- **Do not reach for `QtMultimedia`.** Its Windows backend cannot decode HEVC
  inside MPEG-TS, which is the only format this app exists for. It would pass
  every test on synthetic footage and fail on every real recording. A test
  asserts nothing imports it.
- **Do not change the default colour mode** without re-running
  `tools/compare_colour.py`. The numbers are in `docs/DEVELOPMENT.md`.
- **Do not remove the preview's bounded frame queue.** The bound *is* the
  back-pressure.
- **Do not call `stop_process` from the UI thread.** It waits twice. Use
  `request_stop`, which only asks; the worker that owns the process runs the
  escalation in its own cleanup.

---

## Review checklist

Things that have actually gone wrong in this repository. Check for them.

- [ ] **Does the test fail without the fix?** Several defects were guarded by
      tests that passed.
- [ ] **Where did the test data come from — the world, or the author's idea of
      it?** Fourteen tests once passed against invented curves for a feature
      that worked on none of the real footage.
- [ ] **Does a "measured" claim have a number behind it?**
- [ ] **Mechanical moves:** does anything moved have test coverage on the paths
      that moved? A missing import in a rarely-run path surfaced as a
      segmentation fault in Qt, nowhere near its cause.
- [ ] **Deferred measurement:** widget geometry read in the same handler that
      changed it is stale. Qt has not laid out yet.
- [ ] **Cancellation:** can it be stopped, and does stopping it leave nothing
      half-written? A cancelled job that publishes partial output is worse than
      one that cannot be cancelled.
- [ ] **Silent wrongness:** would this fail loudly, or produce a plausible file
      that is quietly wrong? This footage makes the second easy.
- [ ] **Both themes**, if it draws anything. `AlternateBase` is eight values
      from `Window` in the light theme.
- [ ] **Offscreen rendering is not a screenshot.** That platform has no usable
      fonts and draws every label as empty boxes. Fine for geometry, useless
      for anything anyone reads.

---

## Practical

```bash
pip install -r requirements.txt pytest
python -m flightdvr                     # run it
python -m pytest -m "not integration"   # the fast loop, about a second
python -m pytest                        # everything, about 30 seconds
```

Commit messages: what changed and **why**, in prose. Include measurements.
End with:

```
Co-Authored-By: <your model name> <noreply@anthropic.com>
```

Licence is GPL v3. Every source file carries the header — copy it into new
ones. Third-party notices are in `THIRD-PARTY-NOTICES.md` and must stay
accurate; ffmpeg is bundled on Windows and its corresponding source is pinned
in `packaging/ffmpeg-build.json`.
