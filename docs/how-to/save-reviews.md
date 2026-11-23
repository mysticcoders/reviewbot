# Save finished reviews

Without an `[output]` table, ReviewBot does not save reviews. To keep each finished review, add one:

```toml
[output]
dir = "~/reviewbot-reviews"
```

## What changes

With `[output]`, every launch uses the background runner, not only combats. Single agents and groups use it too. See [How combat runs work](../explanation/combat-runs.md).

## Where the reviews go

ReviewBot writes each finished review to:

```text
<dir>/<owner>-<repo>/pr-<n>/<run>/<step>-<agent>-<role>.md
```

Each file starts with front matter: repo, PR, URL, title, head SHA, run, launcher, step, agent, agent kind, role, round, and save time.

ReviewBot also adds each saved review as one JSON line to `<dir>/index.jsonl`. This file is a simple audit log: which agents reviewed which commit, and when.
