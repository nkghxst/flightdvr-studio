# Working on FlightDVR Studio

FlightDVR is a desktop app for browsing, previewing, trimming and converting
HDZero goggle DVR footage. `README.md` covers use; `docs/DEVELOPMENT.md` covers
technical design and build reasoning; `docs/ROADMAP.md` covers planned and
deliberately omitted work.

This root file is the concise operating agreement and document router. Use the
smallest relevant document for the assigned floor:

- [docs/WORKFLOW.md](docs/WORKFLOW.md) — claiming, identity, publication,
  review, evidence, checkpoints and merge authority.
- [docs/AGENT_LESSONS.md](docs/AGENT_LESSONS.md) — technical incident lessons
  and the evidence that can settle recurring traps.
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — source layout, measured design
  rationale, platform/build details and longer technical explanations.
- [CLAUDE.md](CLAUDE.md) — thin entry point for agents that discover this file.

Anything not written in the repository, an issue, or the job/PR evidence did
not happen. Chat coordinates work; Git and the issue tracker preserve it.

## Non-negotiable boundaries

- Nk owns merge authority. Nk may explicitly delegate a routine coordinator
  merge; workers never merge, and no delegation bypasses the independent
  verdict or applicable CI.
- One agent owns a branch at a time. The maker never owns the reciprocal review.
  Every floor names one maker, one independent verdict owner, exact Git objects,
  paths, checks, done criteria and remaining limits.
- Do not mutate user footage, settings, accounts, credentials, security
  boundaries, devices or worker lifecycle without an explicit floor. Preserve
  existing worktrees, branches, drafts and user exploration.
- Do not copy keys or tokens, persist short-lived credentials, weaken ACLs,
  relaunch recovered workers, use Open All, or infer permanent ownership repair
  from a reconnect.
- Do not import `QtMultimedia`, remove the preview's bounded frame queue, or
  call `stop_process` from the UI thread. Use the existing worker-owned stop
  boundary described in [AGENT_LESSONS.md](docs/AGENT_LESSONS.md). The one
  exception is `flightdvr/audio_device.py`, which may take `QAudioFormat`,
  `QAudioSink` and `QMediaDevices` to hand already-decoded PCM to an output
  device. Players, decoders and capture stay forbidden everywhere, FFmpeg
  keeps all decoding, and `tests/test_player.py` holds that line.
- Do not change default colour handling or ffmpeg arguments without the
  measurements and applicable platform/package evidence described in the
  development notes.
- No release publication, broad configuration migration, new credentials,
  global workflow relaxation or reserved product decision is implied by a
  coding, review, connection or documentation floor.

## Team and identity

Role, authenticated sender and model are separate facts. Use the live identity
breadcrumb for the sender; instance suffixes are possible. Record the actual
model only when it is exposed, otherwise write `unknown/not exposed`. Never
infer a model from a nickname, role or GitHub account.

| Role | Sender base | Default contribution |
|---|---|---|
| Sol | `sol` | Feature implementation and difficult debugging |
| Claude | `fdvr-claude` | Architecture, specification and adversarial review |
| Luna | `luna` | Triage, documentation, tests and CI evidence |
| Astra | `astra` | Independent adversarial verdicts when assigned |

Luna's default verdict is advisory. She is not the sole verdict owner for
output-correctness work and, unless Nk explicitly assigns it, not for docs or
mechanical changes. This is a calibration/routing policy, not a price claim;
independence comes from separate ownership. An explicit assignment overrides
the default only for its named scope.

## Task floors and evidence

Read the newest direct coordinator message and any explicitly named current
checkpoint. A plan-only, review-only or connection-check floor does not
authorize implementation. Stop for material scope expansion, another owner's
files, new external permissions or a reserved user decision.

Keep evidence types distinct: source inspection is not runtime reproduction;
unit or synthetic tests are not native, device or real-media acceptance; an
offscreen render is not a readable screenshot; a passing assertion is not a
clean process if workers remain alive; and current-platform CI is not evidence
for an older ffmpeg floor. Test changed behavior and named unresolved claims.
Use real measurements where a claim is about output, timing, colour, audio,
frames or performance. Report what was not checked.

Behavioral oracles must make their failure possible. Do not use `or True`, hide
an absent assertion, silently broaden a tolerance, or call implementation
parroting an independent test. Assert fixture shapes. A cancelled or absent CI
job is not a pass, and docs-only `paths-ignore` is `NOT APPLICABLE`, not a
passing run.

## Starting and completing a floor

Before choosing a path, confirm current authentication, open PR ownership and
the exact base. An error from `gh` is not an empty result. Check the current
worktree and keep a fresh branch isolated from existing drafts. A meaningful
first commit claims an implementation or documentation floor; the App-authored
draft PR must record the short plan before work continues.

Within an implementation floor, continue through the leased change, applicable
checks, correction of failures caused by that change, publication and the
named handoff. Stop when the scope expands, another owner's file is required,
new external permission is needed, or a reserved user decision is reached.
Plan-only, review-only and connection-check floors stop at their stated
deliverable.

Do not call a result complete merely because assertions passed, a source tree
is clean, a worker reconnected, or a draft exists. Completion needs the stated
checks, a clean relevant process exit, the applicable acceptance gates and an
exact-head handoff. Keep a known gap visible rather than filling it with an
optional improvement or a broader unasked-for check.

For output claims, decoded-media evidence settles the file, native acceptance
settles interaction, and an independent correction-delta review settles a
named finding. These gates are separate. Preserve them when a docs-only or
coordination change makes a check inapplicable.

## Communication and routing

Only directly addressed agents reply. Use one ACK, one maker handoff and one
independent verdict; no reminder or idle ACK loop is progress. Put detail in
the job/PR. Keep `#general` to concise `CLAIM`, `STATUS`, `HANDOFF`, `REVIEW`
and `OUTCOME` messages, naming the maker, verdict owner, exact SHA, checks and
limits. Use [docs/WORKFLOW.md](docs/WORKFLOW.md) for the exact templates and
GitHub identity procedure.

An interrupted turn with unfinished work is a checkpoint, not completion.
Record exact SHA, paths, checks, unresolved finding, blocker and next action.
One bounded continuation may follow verified idle. Recovery preserves original
identities and sessions; it does not authorize Open All, relaunch, security
changes or old-task resumption.

For technical traps, use [docs/AGENT_LESSONS.md](docs/AGENT_LESSONS.md). For
source and build reasoning, use [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
For current ownership, use the live coordinator checkpoint rather than adding
task-specific PR state to this permanent agreement.
