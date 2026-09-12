# Collaboration and publication workflow

This is the current procedure for claiming work, publishing a branch, checking
evidence and handing a result to an independent reviewer. The root
[AGENTS.md](../AGENTS.md) is the short operating agreement and router; this
document carries the commands and identity boundaries that should not be
duplicated in every entry point.

## Establish the floor

Before acting, read the newest direct coordinator message and any explicitly
named current checkpoint. A plan-only, review-only or connection-check floor
does not authorize implementation. A task floor must name all of these:

- one maker and one branch owner;
- one independent verdict owner;
- the exact base and, for a review, the exact head;
- allowed paths and an explicit not-list;
- done criteria, checks and the destination for detailed evidence.

The maker owns the branch and does not review it. If the work must change hands,
the current owner says so and stops pushing. Do not infer ownership from a
historical name, a worktree, a chat mention that is not addressed to you, or a
GitHub login.

Check the repository before choosing a path:

```powershell
gh auth status
gh issue list --label ready
gh pr list --state open
git worktree list
```

An authentication or transport error is not an empty issue/PR list. Check the
files of open PRs before editing; overlapping work on a large module is a stop
condition. Preserve existing worktrees, branches and drafts.

## Claim and publish

For an implementation or documentation change, create a fresh `codex/` branch
from the exact authorized base. Make a small, meaningful first commit, then
open the draft PR before continuing. Put the short plan, allowed paths, not-list
and done criteria in the PR body. Do not create placeholder commits or PRs.

The PR is opened with the `flightdvr-assistant-nkghxst` GitHub App so the human
maintainer can provide the independent review. Commits and ordinary pushes use
the normal `nkghxst` login. The helper emits a short-lived installation token;
keep it process-local and remove it immediately:

```powershell
$env:GH_TOKEN = & .\tools\github_app_token.ps1
try {
    gh pr create --draft --title "..." --body "..."
} finally {
    Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue
}
```

Never copy a private key or token into a checkout, persist a token in `gh`
configuration, weaken a key ACL, or create a second persistent login. A
separate clone may need its own local app settings; a linked worktree shares
them. If token generation fails, report whether normal auth, local settings,
the key path or token generation failed. Do not rotate credentials in response
to an unclassified transport timeout.

For incomplete acceptance, use a neutral issue reference such as
`Related to #96; acceptance remains pending`. Do not use `Closes`, `Fixes` or a
negated closing keyword. Before a merge, inspect `closingIssuesReferences`;
after a merge, inspect the actual issue state. A PR description is not proof
that an issue closed.

## Identity and review

Keep three identities separate in every handoff and review:

1. the agent role, such as Luna or Astra;
2. the authenticated chat sender, taken from the live identity breadcrumb and
   allowed to acquire an instance suffix;
3. the model name, recorded only when it is actually exposed.

At the start of a review body, write for example
`Reviewer: Claude Code (<current model>)`. If the model is not exposed, say
`unknown/not exposed`; never infer it from a role, nickname or product label.

The GitHub identity is separate again. An App-authored PR is reviewed with the
normal `nkghxst` login. A human-authored PR is reviewed with a short-lived App
token. Never review a PR with the identity that authored it. The author names
the verdict owner at handoff; that agent submits the one GitHub review.

Luna's default verdict is advisory only. She is not the sole verdict owner for
output correctness and, unless Nk explicitly assigns it, not for documentation
or mechanical changes. This is a calibration policy, not a price claim; review
independence comes from separate ownership. An explicit assignment may override
the default only for its named scope.

Use the result supported by the evidence: approve, request changes, or comment.
Confirm a finding against the relevant source, execution or measurement before
reporting it. Do not turn maker evidence into an independent verdict.

## Handoffs and evidence

Send one ACK only when taking an assigned review, one handoff when the maker is
finished, and one verdict from the named reviewer. Only directly addressed
agents reply. Put detailed evidence in the job or PR; use `#general` only for a
concise `CLAIM`, `STATUS`, `HANDOFF`, `REVIEW` or `OUTCOME` message with a
`reply_to` where useful. No reminder or ACK loop is progress.

Every handoff names the maker, independent verdict owner, exact commit, checks,
outcome and remaining limits. A useful shape is:

```text
HANDOFF — Maker <name>; verdict owner <name>; exact base <SHA>, head <SHA>.
Paths: <exact list>. Outcome/checks: <observed results>. Remaining: <named
evidence gaps and limits>. Review only <bounded delta>; only Nk merges.
```

For a correction rereview, state the reviewed parent and correction head. The
reviewer proves the named failure, inspects only the correction delta, and does
not restart a broad cycle. No repeat review is requested without a specific
unresolved finding.

Keep claims classified:

- source inspection is not a runtime reproduction;
- a unit or synthetic test is not native UI, device, or real-media acceptance;
- an offscreen render is not a readable screenshot or keyboard/pointer proof;
- a passing assertion is not a clean process if workers remain alive;
- a current-platform test is not evidence for an older ffmpeg packaging floor;
- a queued, absent or cancelled CI job is not a passing or failing run.

Test changed behavior and every named unresolved claim. A behavioral oracle
must make its failure possible; do not use `or True`, silently broaden a
tolerance, or rely on a test that merely describes the implementation. Assert
fixtures have the intended shape. For real media, identify both the filename
and the first 16 lowercase characters of the full-file SHA-256. Report what was
not checked.

Map CI results to the exact head and wait for all applicable jobs. A docs-only
change excluded by the workflow's `paths-ignore` is `NOT APPLICABLE`, not a
pass. A proposal to reduce required checks is not a policy change. Anything
touching ffmpeg arguments waits for the packaging jobs, including the Ubuntu
22.04 job that exercises the older supported ffmpeg.

## Coordinator publication fallback

If a worker cannot reach GitHub, Nk or an explicitly authorized coordinator may
publish an already-created, owner-supplied exact commit and branch and open the
draft with the existing short-lived App token. Before doing so, re-check the
exact head, path ceiling and open-PR ownership; do not force-push. Publication
is not authorship, review or merge authority. Do not create placeholders,
transfer or persist tokens, copy keys, weaken ACLs, restart workers, or rotate
credentials globally. When direct worker access returns, the normal path
resumes. A transport timeout is not evidence of revoked credentials.

## Merge, checkpoint and recovery

Nk owns merge authority. Nk may explicitly delegate a routine coordinator
merge, but the delegation does not cover workers, releases, mission-critical or
product decisions, credentials, lifecycle changes, or bypassing an independent
verdict and applicable CI. Workers never merge.

An interrupted turn with unfinished work is a checkpoint, not completion. Record
the exact SHA, changed paths, completed checks, unresolved finding, blocker and
next action in the job or PR. One bounded continuation may be assigned after
idle is verified. A connection check or a status message is not permission to
resume an old task.

After external recovery, preserve the original identities, sessions, worktrees
and drafts. Do not use Open All, relaunch workers, retire identities, or change
security boundaries without a separate explicit floor. Recovery explains a
reported event only; it does not prove permanent ownership repair or Studio
acceptance.

The existing 20-minute heartbeat is a monitor, not a work grant. Quiet unchanged
checks do not reset it. Pause at completion, no authorized next action, a
notified user-only decision, or three consecutive inactive checks. Only
substantive progress resets the counter.
