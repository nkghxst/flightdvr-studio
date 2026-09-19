# Claude entry point

Use the shared [AGENTS.md](AGENTS.md) operating agreement and its document
router. The detailed claiming, identity, publication and review procedure is
in [docs/WORKFLOW.md](docs/WORKFLOW.md); technical rationale and incident
lessons are in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) and
[docs/AGENT_LESSONS.md](docs/AGENT_LESSONS.md).

This file intentionally contains no separate role, GitHub-account or credential
rules. Follow the live authenticated identity and the exact current task floor;
do not infer authority from the fact that this file is named `CLAUDE.md`.

## Public coordination text

Apply the public-text rule in [AGENTS.md](AGENTS.md): never publish chat,
session, share or transcript URLs, session IDs or equivalent private-conversation
references in public pull requests, reviews, comments, issues, commits or
generated attribution footers unless Nk explicitly approves that specific
disclosure. Use repository evidence links and plain model attribution without
URLs or session identifiers, and inspect final generated text before sending.
Do not put real IDs or live session-link examples in committed documentation.

**Check that this copy is current before you rely on it.** These files are read
from the working directory when a session starts, so an old checkout hands you
old governance without saying so. Fetch, compare what you are reading against
refreshed `origin/main`, and follow the refreshed version — saying in your first
status message that you did. If the fetch failed or the ref may be stale, say
that instead of assuming this copy is current. Refresh refs, never the worktree:
a task or review worktree is often detached or pinned to an exact head on
purpose. See *Check that your instructions are current* in
[docs/WORKFLOW.md](docs/WORKFLOW.md).
