# CLAUDE.md

The working agreement for this repository is in [AGENTS.md](AGENTS.md) — one
file for every assistant that works here, so the two of us cannot drift apart
by reading different rules.

**Read it before starting.** In particular: check `gh pr list` before picking
anything up, claim work with a draft PR, and review what the other one wrote.

## GitHub identity on each computer

The `flightdvr-assistant-nkghxst` GitHub App is installed on this repository
once, but its private keys and local Git settings do not travel with the clone.
The laptop and desktop may use the app at the same time. Give each computer its
own key so one can be revoked without interrupting the other.

On a computer that has not used the app before:

1. Run `gh auth status`. The normal `nkghxst` login is for commits, pushes,
   reviews and merges; authenticate it with `gh auth login -h github.com` if
   needed.
2. In the app's [General settings](https://github.com/settings/apps/flightdvr-assistant-nkghxst),
   generate a new private key for this computer. Do not reinstall the app. Put
   the key in a user-only location outside every checkout, and do not copy the
   other computer's key or grant an assistant sandbox permanent access to it.
3. Set the non-secret IDs and that computer's key path in this clone:

   ```powershell
   git config --local flightdvr.githubAppId 4511822
   git config --local flightdvr.githubAppInstallationId 151828984
   git config --local flightdvr.githubAppKeyPath "D:/secure/location/flightdvr-assistant.private-key.pem"
   ```

   Linked worktrees share these values; a separate clone needs them once.
4. Follow `AGENTS.md` when opening a PR: push the branch normally, run
   `tools/github_app_token.ps1` with one-time host approval if the sandbox
   cannot read the key, use its token only around `gh pr create`, and remove
   `GH_TOKEN` immediately afterwards. Never put the app token into
   `gh auth login`; reviews must remain under the normal human login.

If setup fails, report which of `gh auth status`, the three local settings, the
private-key path, or token generation failed. Do not weaken the key's ACL as a
workaround.
