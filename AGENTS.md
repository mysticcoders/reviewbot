# Working in ReviewBot

- Use the words in `docs/reference/glossary.md` with the meanings it gives (launch, plan, phase, step, run, combat, worktree, bundled vs installed prompt, and the rest). Add a term there when you introduce one.

- Review agents use detached PR worktrees. Refresh the target branch's remote-tracking ref on every launch, including reuse; never move the owner's local base branch or worktree HEAD to refresh review scope.
- Before reporting a review, verify the requested head and remote base, then record the merge base and changed-file list. Use the three-dot diff from the remote-tracking base, not stale local `main`. Keep unrelated pre-existing issues separate.
- Prompt templates ship under `src/reviewbot/_resources/prompts/`, but `reviewbot init` installs independent copies under the configured prompt directory. When changing review scope instructions, check the active configured prompt as well; changing bundled templates alone does not update an existing installation.
- Run the relevant pytest suite. The live Rex test requires a reachable Rex server/session; report it as unverified when unavailable rather than treating unit tests as live integration evidence.
- Run `just check` (ruff, then pytest) before finishing any change. In Claude Code, a Stop hook in `.claude/settings.json` runs it when you stop with changes in `src`, `tests`, `pyproject.toml`, `uv.lock` or `justfile`, and hands failures back. If you cannot make it pass, say why and stop again without changing those files; the stop then goes through. CI runs the same checks on every pull request.
- Deliver each change as a branch and a pull request, and merge it after CI passes. Do not commit to `main` directly: ReviewBot installs from `main`, so every push to it is a release.
- Keep reviews read-only unless the user explicitly requests edits or GitHub posting.
