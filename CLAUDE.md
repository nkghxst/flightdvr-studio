# CLAUDE.md

The working agreement for this repository is in [AGENTS.md](AGENTS.md) — one
file for every assistant that works here, so the two of us cannot drift apart
by reading different rules.

**Read it before starting.** In particular: check `gh pr list` before picking
anything up, claim work with a draft PR, and review what the other one wrote.

## Current handoff — 7 August 2026

The separate PR identity is now the `flightdvr-assistant-nkghxst` GitHub App.
Use the short-lived-token workflow in `AGENTS.md` to open future PRs. Commits
and pushes still use the normal maintainer identity; reviews and merges must
also use the normal identity with `GH_TOKEN` unset.

Current workstation note: `git push` works through Windows Credential Manager,
but `gh auth status` reports the saved `nkghxst` CLI token as invalid. Run
`gh auth login -h github.com` before the next review command. Do not work around
that by reviewing with the app token; the app is only the PR author.

Codex has completed these reviews:

- [PR #24](https://github.com/nkghxst/flightdvr-studio/pull/24) needs changes;
  four confirmed findings were left inline.
- [PR #26](https://github.com/nkghxst/flightdvr-studio/pull/26) is ready from
  review. This identity branch is based on it because it already changes
  `AGENTS.md`.
- [PR #27](https://github.com/nkghxst/flightdvr-studio/pull/27) is ready from
  review; unit, UI, integration and full-suite checks passed locally.
- [PR #28](https://github.com/nkghxst/flightdvr-studio/pull/28) is Codex's
  implementation and is ready for Claude's review.

GitHub Actions was recovering from an outage during these reviews, so a queued
or absent remote job is not by itself evidence of a code failure. Check the
current run before drawing a conclusion.
