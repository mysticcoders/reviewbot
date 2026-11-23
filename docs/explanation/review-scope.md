# How the review scope is pinned

An agent reviews the changes of a PR. It finds them by comparing the PR head with the base branch. If ReviewBot gives it the wrong base, the agent reviews the wrong changes.

## The problem: a stale local base

ReviewBot often works in a clone that already exists: a `[[checkouts]]` clone, or its own cached clone. The local base branch in that clone, for example `main`, can be days old. A diff against it includes commits that are already merged, and that are not part of the PR.

## What ReviewBot does

- On each launch, ReviewBot fetches the base branch into its remote-tracking ref, `refs/remotes/origin/<base>`. It also does this when it reuses an existing worktree.
- It does not move your local base branch. It does not change the branch or HEAD of your clone or worktree.
- The worktree is detached at the exact PR head commit that the queue showed.

So `origin/<base>` is current when the agent starts, and nothing of yours moves.

## What the prompts tell the agent

The bundled review prompts tell the agent to compare against the remote-tracking base, with a three-dot diff:

```sh
git diff origin/<base>...HEAD
```

A three-dot diff shows only the changes since the merge base. So commits that landed on the base after the PR branched do not appear as PR changes.

## Prompts from an older version

`reviewbot init` copies the prompts. An upgrade does not change the copies. If your copies come from an older version, they may tell the agent to diff against the local base. See [Write review prompts](../how-to/write-prompts.md#prompts-installed-by-an-older-version).
