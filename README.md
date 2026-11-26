# ReviewBot

ReviewBot is a terminal UI for working through GitHub pull requests that need
your attention. It shows two queues:

- **Open PRs**: open PRs where you (or one of your teams) have been asked to
  review, or, with a filter, every PR you are involved in or every open PR in the
  repositories you watch.
- **My PRs**: open PRs you authored or are assigned to, with checks, conflicts,
  and per-reviewer status.

A third tab lists your unread GitHub notifications about PRs and issues in the
repositories you watch, and marks the PRs in the two queues that have unread
activity.

From the review queue, one keypress checks the PR out into an isolated worktree
and starts one of your configured review agents (Claude Code, Codex, Gemini,
or any other agent CLI) in a new tab of your terminal multiplexer, primed with
the review prompt you configure. Two multiplexers are supported:
Herdr and Rex. GitHub remains the source of truth: ReviewBot never
submits reviews or posts comments. You read the agent's findings and decide what
to submit.

## Requirements

| Tool | Why | Install |
| --- | --- | --- |
| Python 3.11+ and [uv](https://docs.astral.sh/uv/) | runs ReviewBot | `brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [GitHub CLI](https://cli.github.com) (`gh`) | all GitHub queries, cloning | `brew install gh` |
| `git` | worktrees per PR | usually preinstalled |
| Herdr 0.7+ or [Rex](https://www.superlogical.com/rex/docs) | terminal multiplexer that hosts the agent sessions | see each project's docs |
| One or more agent CLIs | performs the review | per agent, e.g. [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Codex](https://github.com/openai/codex) |

ReviewBot does not assume any particular agent: you list the ones you want in
the config. Browsing the queues needs only `gh`.

## Install

```sh
uv tool install git+https://github.com/mysticcoders/reviewbot
```

That installs the latest release from `main`. To install a specific release
instead, add its tag, e.g. `...reviewbot@v0.2.1`. See [Upgrade ReviewBot](docs/how-to/upgrade.md)
for keeping it current.

To hack on it, clone the repository and install it in editable mode so local
changes take effect without reinstalling:

```sh
git clone git@github.com:mysticcoders/reviewbot.git
cd reviewbot
uv tool install --editable .
```

To run without installing, use `uv run reviewbot ...` from the checkout.

## Documentation

New to ReviewBot? Follow the tutorial: [Set up ReviewBot and run your first review](docs/tutorial/first-review.md).

The full documentation is in [`docs/`](docs/README.md):

- **How-to guides:** upgrade, choose a multiplexer, choose what to watch, configure agents, run a combat review, save reviews, write prompts, serve the dashboard, remove old worktrees.
- **Reference:** the configuration file, the command line, the TUI keys, the queue statuses, the prompt placeholders, the MCP tools.
- **Explanation:** what a launch does, how the review scope is pinned, how agents are driven, how combat runs work, the security model of `serve`.

## Development

The development commands are in the `justfile`. Install [just](https://just.systems), then run `just` to list them:

```sh
just sync        # install the dependencies
just check       # lint, then run the unit tests
just test-live   # the live Rex test; run it inside a Rex session
```

CI runs the same lint and tests (`just check`) on every pull request, on Python 3.11 and 3.15. Changes go in through pull requests: `main` is what `uv tool install` installs, so every push to it is a release.
