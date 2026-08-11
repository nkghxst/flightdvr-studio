# CLAUDE.md

The working agreement for this repository is in [AGENTS.md](AGENTS.md) — one
file for every assistant that works here (Sol, Claude and Luna), so the three of
us cannot drift apart by reading different rules.

**Read it before starting.** In particular: check `gh pr list` before picking
anything up, claim work with a draft PR, and review what somebody else wrote.
One agent owns a branch at a time, and one named agent owns each review verdict.

## GitHub identity on each computer

The `flightdvr-assistant-nkghxst` GitHub App is installed on this repository
once, but its private keys and local Git settings do not travel with the clone.
The laptop and desktop may use the app at the same time. Give each computer its
own key so one can be revoked without interrupting the other.

On a computer that has not used the app before:

1. Run `gh auth status`. The normal `nkghxst` login is for commits, pushes and
   merges, and for reviewing PRs opened by the app. Authenticate it with
   `gh auth login -h github.com` if needed. This is the computer's only
   persistent GitHub CLI login; **do not log the app into `gh`**.
2. **Maintainer step:** in the app's [General settings](https://github.com/settings/apps/flightdvr-assistant-nkghxst),
   generate a private key for this computer. Do not reinstall the app. Store
   the key in a user-only location outside every checkout and give Claude only
   the path needed for the next step. Claude must not generate, move, display,
   or weaken the ACL on the key, and the other computer's key must not be copied.
3. **Assistant step, after the maintainer supplies the path:** set the
   non-secret IDs and that computer's key path in this clone:

   ```powershell
   git config --local flightdvr.githubAppId 4511822
   git config --local flightdvr.githubAppInstallationId 151828984
   git config --local flightdvr.githubAppKeyPath "D:/secure/location/flightdvr-assistant.private-key.pem"
   ```

   Linked worktrees share these values; a separate clone needs them once.
4. Follow the identity matrix in `AGENTS.md` when opening or reviewing a PR.
   Push normally, run `tools/github_app_token.ps1` with one-time host approval
   if the sandbox cannot read the key, assign the short-lived result to
   `GH_TOKEN` only around the one `gh pr create` or `gh pr review` command, and
   remove `GH_TOKEN` immediately afterwards. Removing it automatically returns
   `gh` to the normal human login; no logout, second login, or account switching
   is needed.

At the start of every review body, state the assistant and current model exactly
as `Reviewer: Claude Code (<current model>)`. Sol and Luna use the equivalent
line for themselves — with three agents sharing two GitHub logins, that line is
the only thing that says who actually reviewed.

If the PR was opened by `nkghxst`, review as the app; if the PR was opened by the
app, review as `nkghxst`. Never use the same GitHub identity for both
maker and reviewer.

If setup fails, report which of `gh auth status`, the three local settings, the
private-key path, or token generation failed. Do not weaken the key's ACL as a
workaround.
