# ReviewBot

ReviewBot is a terminal UI for working through GitHub pull requests that need
your attention. It shows two queues:

- **Review requests**: open PRs where you (or one of your teams) have been asked
  to review.
- **My PRs**: your own open PRs, with checks, conflicts, and per-reviewer status.

From the review queue, one keypress checks the PR out into an isolated worktree
and starts one of your configured review agents (Claude Code, Codex, Gemini,
or any other agent Herdr supports) in a new Herdr tab, primed with the review
prompt you configure. GitHub remains the source of truth: ReviewBot never
submits reviews or posts comments. You read the agent's findings and decide what
to submit.

## Requirements

| Tool | Why | Install |
| --- | --- | --- |
| Python 3.11+ and [uv](https://docs.astral.sh/uv/) | runs ReviewBot | `brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [GitHub CLI](https://cli.github.com) (`gh`) | all GitHub queries, cloning | `brew install gh` |
| `git` | worktrees per PR | usually preinstalled |
| Herdr 0.7+ | terminal workspace that hosts the agent sessions | see the Herdr project docs |
| One or more agent CLIs Herdr can start | performs the review | per agent, e.g. [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Codex](https://github.com/openai/codex) |

ReviewBot does not assume any particular agent: you list the ones you want in
the config. Browsing the queues needs only `gh`.

## Install

```sh
uv tool install git+https://github.com/mysticcoders/reviewbot
```

Upgrade later with `uv tool upgrade reviewbot`.

To hack on it, clone the repository and install it in editable mode so local
changes take effect without reinstalling:

```sh
git clone git@github.com:mysticcoders/reviewbot.git
cd reviewbot
uv tool install --editable .
```

To run without installing, use `uv run reviewbot ...` from the checkout.

## Setup in a new environment

1. **Authenticate GitHub.** ReviewBot uses whatever account `gh` is logged in as.

   ```sh
   gh auth login
   ```

   For organizations that enforce SSO, authorize the token for each org
   (`gh auth refresh` and follow the prompt, or authorize it in GitHub's token
   settings). PRs in orgs the token cannot see are silently absent from search
   results.

2. **Install the Herdr agent integration** for each agent you will use, so
   Herdr can track its state, e.g.:

   ```sh
   herdr integration install claude
   ```

3. **Create a config.** `init` asks what to watch if you don't pass `--watch`.
   Each `--agent KIND[:KEY]` adds an agent of that Herdr kind, named after the
   kind, bound to an optional TUI key:

   ```sh
   reviewbot init --watch acme --agent claude:c --agent codex:x
   reviewbot init --watch acme/api --watch acme/web --agent gemini:g
   reviewbot init --watch '*'                     # everything, agents added by hand
   ```

   This writes `~/.config/reviewbot/config.toml` and the starter prompt
   `~/.config/reviewbot/review.md` beside it. Existing files are left alone
   unless you pass `--force`. Edit the config afterwards to add agents, groups,
   args, or per-agent prompts (see below).

4. **Check the setup:**

   ```sh
   reviewbot doctor
   ```

   It checks that `gh`, `git`, and `herdr` are on your `PATH`, that `gh` is
   authenticated, that the config loads, and, for every configured agent, that
   its executable is on your `PATH` and its prompt file exists.

5. **Start or attach to Herdr**, then run `reviewbot tui` inside it. Launched
   reviews open as new tabs in the focused Herdr workspace.

## Choosing what to watch

The `watch` list in `config.toml` controls which repositories both queues draw
from. Each entry is an owner (an org or user) or an `OWNER/REPO`:

`watch` is required, so there is no implicit scope:

```toml
watch = ["*"]                       # every repository you can access
watch = ["acme"]                    # everything owned by the acme org
watch = ["acme/api", "acme/web"]    # only these two repositories
watch = ["acme", "someone/tool"]    # an entire org plus one outside repo
```

Entries may be pasted as GitHub URLs.

Every command that reads the queues accepts overrides that replace the
configured list for that run:

```sh
reviewbot tui --watch acme/api            # just one repository
reviewbot tui -w acme -w other-org        # two orgs
reviewbot status --all                    # everything, ignoring the config
```

Searches are limited to open PRs in unarchived repositories. Each owner in the
list is searched separately, and the results are merged, so watching several
orgs costs one GitHub search per org.

When everything you watch belongs to a single owner, the tables show bare
repository names; otherwise they show `owner/repo`.

## Review agents

Agents are defined only in the config; there are no built-in agents and no
default. With none configured, the queues still work but nothing can be
launched.

```toml
prompt = "review.md"          # prompt for every agent without its own

[[agents]]
name = "claude"               # used with --agent and in Herdr tab names
kind = "claude"               # any `herdr agent start --kind` value
key = "c"                     # TUI shortcut, optional
args = ["--model", "opus"]    # extra arguments for the agent CLI, optional

[[agents]]
name = "codex"
kind = "codex"
key = "x"
prompt = "codex-review.md"    # this agent's own prompt

[[agents]]
name = "codex-deep"           # same kind twice with different settings is fine
kind = "codex"
key = "d"
args = ["-c", "model_reasoning_effort=high"]

[[groups]]                    # launch several agents on one PR
name = "pair"
agents = ["claude", "codex"]
key = "b"
```

- `name`: 1-16 characters of lowercase letters, digits, `-`, or `_`; unique
  across agents and groups.
- `kind`: passed to `herdr agent start --kind`, which also names the executable
  `doctor` looks for. Run `herdr agent start --help` for the supported kinds
  (claude, codex, gemini, cursor, opencode, copilot, amp, and more).
- `key`: one letter or digit. `q`, `r`, `o`, `1`, `2`, `a`, and `e` are taken by
  the TUI, and keys must not repeat.
- `args`: appended after `--` to the agent's command line.
- `prompt`: a file path; relative paths resolve against the config file's
  directory. Every agent needs one, either its own or the top-level `prompt`.

`reviewbot agents` lists what is configured.

## Review prompts

A prompt is a plain text or Markdown file sent to the agent once it starts in
the PR worktree. `init` writes a starter `review.md` focused on test coverage
and substantive correctness, security, and regression issues, which also tells
the agent not to edit, push, comment, or submit a review. Change it freely, or
give individual agents their own file.

These placeholders are filled in; any other `{...}` text is left as is:

| Placeholder | Value |
| --- | --- |
| `{key}` | `owner/repo#number` |
| `{repo}`, `{number}`, `{title}`, `{author}`, `{url}` | PR identity |
| `{head_sha}`, `{head_branch}`, `{base_branch}` | the checked-out commit and branches |
| `{request_source}` | `direct` or `team:<slug>` |
| `{review_decision}`, `{checks}` | GitHub's review decision and check rollup |
| `{previous_review}`, `{commits_since_review}` | your last review and commits since |
| `{agent}` | the agent's configured name |

Prompt files are read when you launch, so edits apply to the next review
without restarting the TUI.

## Configuration reference

`~/.config/reviewbot/config.toml`:

```toml
watch = ["acme"]                    # required; ["*"] for everything
work_dir = "~/reviewbot-workspaces" # cached clones and per-PR worktrees
prompt = "review.md"                # shared prompt for agents without their own
# herdr_workspace = "workspace-id"  # force new tabs into one Herdr workspace

[[agents]]                          # see "Review agents"
name = "claude"
kind = "claude"
key = "c"

# Reuse existing local clones instead of cloning into work_dir.
[[checkouts]]
repo = "acme/api"
path = "~/code/api"
```

ReviewBot only fetches PR refs into a reused clone and adds detached worktrees
beside it; it never changes that clone's checked-out branch. Repositories
without a `[[checkouts]]` entry are cloned on first launch into
`<work_dir>/repos/`.

## Using the TUI

```sh
reviewbot tui
```

| Key | Action |
| --- | --- |
| `1` / `2` | switch between **Review requests** and **My PRs** |
| arrows | select a PR |
| `r` | refresh the current tab |
| `q` | quit |
| `o` | open the selected PR on GitHub |
| your agent keys | launch that agent or group on the selected review request (shown in the footer) |
| `a` | (My PRs) pick reviewers: previous reviewers, GitHub's suggestions, or typed `login` / `org/team`; `ctrl+s` requests, `escape` cancels |
| `e` | (My PRs) re-request review from everyone whose review is stale or requested changes |

The TUI is deliberately a queue picker rather than an agent interface; active
work happens in Herdr tabs, which the queue marks as `in-progress`.

### Review request statuses

- `ready`: you have not reviewed it yet.
- `updated`: you reviewed an older commit; there are new commits since.
- `reviewed`: you already reviewed the current head commit.
- `changes-requested`: you requested changes and nothing has been pushed since.
- `draft`: the PR is a draft.
- `in-progress`: a Herdr review tab for it is open.

Only `ready` and `updated` PRs can be launched.

### My PRs statuses

In priority order: `draft`, `conflicts`, `re-review` (changes were requested and
a reviewer can be re-requested), `changes-requested`, `checks-failing`,
`needs-reviewers`, `approved`, `waiting`.

## Command line

The same data is available without the TUI:

```sh
reviewbot status                          # review requests as a table
reviewbot mine                            # your open PRs as a table
reviewbot agents                          # configured agents, groups, and keys
reviewbot next                            # show the most recent actionable request
reviewbot next --agent claude             # ...and offer to launch it
reviewbot next --agent pair --yes         # launch a group without asking
reviewbot launch acme/api#422 --agent claude
reviewbot launch https://github.com/acme/api/pull/422 --agent codex
```

`--agent` takes any agent or group name from the config. `launch` looks the PR up in that repository's review requests, so it works
regardless of the configured `watch` list.

## What a launch does

1. Clones the repository (or reuses a `[[checkouts]]` clone) and fetches
   `pull/<n>/head`.
2. Verifies the fetched commit still matches the PR head, then creates a
   detached worktree at that exact SHA under
   `<work_dir>/worktrees/<owner>-<repo>/pr-<n>-<sha>`.
3. For each agent (one, or every member of a group), opens a Herdr tab named
   `<repo> #<n> <agent> review`, runs `herdr agent start --kind <kind> -- <args>`
   there, and sends it the rendered prompt.

Prompt files are read before anything is cloned or opened, so a missing prompt
fails the launch cleanly.

Worktrees are not cleaned up automatically. Remove old ones with
`git -C <clone> worktree remove <path>` or `git worktree prune` after deleting
the directory.

## Development

```sh
uv sync
uv run pytest
uvx ruff check src tests
```
