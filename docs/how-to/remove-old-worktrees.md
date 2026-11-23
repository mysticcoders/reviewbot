# Remove old worktrees

ReviewBot does not remove worktrees. Each launch at a new head commit adds a worktree under:

```text
<work_dir>/worktrees/<owner>-<repo>/pr-<n>-<sha>
```

Here `<work_dir>` is the `work_dir` setting in the config. The default is `~/reviewbot-workspaces`.

## Remove one worktree

Run this against the clone that owns the worktree:

```sh
git -C <clone> worktree remove <path>
```

The clone is the `[[checkouts]]` path for that repository, or `<work_dir>/repos/<owner>-<repo>`.

## Clean up after you delete directories

If you delete worktree directories by hand, let Git forget them:

```sh
git -C <clone> worktree prune
```
