# Working in ReviewBot

- Review agents use detached PR worktrees. Refresh the target branch's remote-tracking ref on every launch, including reuse; never move the owner's local base branch or worktree HEAD to refresh review scope.
- Before reporting a review, verify the requested head and remote base, then record the merge base and changed-file list. Use the three-dot diff from the remote-tracking base, not stale local `main`. Keep unrelated pre-existing issues separate.
- Prompt templates ship under `src/reviewbot/_resources/prompts/`, but `reviewbot init` installs independent copies under the configured prompt directory. When changing review scope instructions, check the active configured prompt as well; changing bundled templates alone does not update an existing installation.
- Run the relevant pytest suite. The live Rex test requires a reachable Rex server/session; report it as unverified when unavailable rather than treating unit tests as live integration evidence.
- Keep reviews read-only unless the user explicitly requests edits or GitHub posting.
