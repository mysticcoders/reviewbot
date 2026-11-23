# What a launch does

When you launch an agent, a group or a combat on a PR, ReviewBot does these steps.

## 1. Check the prompts

ReviewBot reads each prompt file that the launch needs before it clones or opens anything. So a missing prompt stops the launch before it changes anything.

## 2. Get the repository

ReviewBot reuses the `[[checkouts]]` clone for the repository, if there is one. Otherwise it clones the repository into `<work_dir>/repos/` at the first launch.

Then it refreshes the remote-tracking ref of the PR's base branch. For why, read [How the review scope is pinned](review-scope.md).

## 3. Create the worktree

ReviewBot fetches `pull/<n>/head`. It checks that the fetched commit is still the PR head that the queue showed. If the PR moved, the launch stops, and you refresh and try again.

Then it creates a detached worktree at that exact commit:

```text
<work_dir>/worktrees/<owner>-<repo>/pr-<n>-<sha>
```

If the worktree already exists, ReviewBot reuses it, but only if it is still at that commit. ReviewBot never removes worktrees; see [Remove old worktrees](../how-to/remove-old-worktrees.md).

## 4. Open the tabs

The first phase is one agent, each member of a group, or the first speakers of a combat. For each agent in it, ReviewBot opens a tab with this name:

```text
<owner>/<repo> #<n> <sha> <agent> review
```

It starts the agent in the tab and waits until the agent reports that it is ready:

- With Herdr, it runs `herdr agent start --kind <kind> -- <args>`.
- With Rex, it runs `<command> <args>`.

The tab name holds the head commit. So ReviewBot reuses a tab left by an interrupted launch only for the same commit. After the PR moves, a new tab opens for the new commit. For the details per multiplexer, read [How ReviewBot drives agents in Herdr and Rex](multiplexers.md).

## 5. Send the prompts

- Without combats or `[output]`, ReviewBot sends each agent its rendered prompt. Then the launch is done.
- Otherwise, ReviewBot starts the background runner. The runner opens the tabs of later agents when their turns come. Read [How combat runs work](combat-runs.md).
