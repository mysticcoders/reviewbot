# Save finished reviews

`reviewbot init` turns saving on: the config it writes has this table.

```toml
[output]
dir = "~/reviewbot-reviews"
```

A config written by an older version may not have it. Without an `[output]` table, ReviewBot does not save reviews, so add the table to keep them. To stop saving, remove it.

## What changes

With `[output]`, every launch uses the background runner, not only combats. Single agents and groups use it too. See [How combat runs work](../explanation/combat-runs.md).

## Where the reviews go

ReviewBot writes each finished review to:

```text
<dir>/<owner>-<repo>/pr-<n>/<run>/<step>-<agent>-<role>.md
```

Each file starts with front matter: the format version, repo, PR, URL, title, PR author, base branch, head SHA, run, launcher, step, agent, agent kind, role, round, and save time.

ReviewBot also adds each saved review as one JSON line to `<dir>/index.jsonl`. This file is a simple audit log: which agents reviewed which commit, and when.

For every field, and what other tools can rely on, see [Saved reviews](../reference/saved-reviews.md).
