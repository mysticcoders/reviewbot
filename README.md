# ReviewBot

ReviewBot is a terminal UI for working through GitHub pull requests that need
your attention. It shows two queues:

- **Review requests**: open PRs where you (or one of your teams) have been asked
  to review.
- **My PRs**: your own open PRs, with checks, conflicts, and per-reviewer status.

From the review queue, one keypress checks the PR out into an isolated worktree
and starts one of your configured review agents (Claude Code, Codex, Gemini,
or any other agent CLI) in a new tab of your terminal multiplexer, primed with
the review prompt you configure. Two multiplexers are supported:
[Herdr](#herdr) and [Rex](#rex). GitHub remains the source of truth: ReviewBot never
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

Upgrade later with `uv tool upgrade reviewbot`.

To hack on it, clone the repository and install it in editable mode so local
changes take effect without reinstalling:

```sh
git clone git@github.com:mysticcoders/reviewbot.git
cd reviewbot
uv tool install --editable .
```

To run without installing, use `uv run reviewbot ...` from the checkout.

### Upgrading to 0.2

0.2 adds Rex support and makes `multiplexer` a required config setting, so a
config written by 0.1 no longer loads until you add it. The quickest fix is to
run `reviewbot doctor` at a terminal: it asks which multiplexer you use, adds
the line for you, and keeps the old file as `config.toml.bak`.

To make the change by hand, add one line near the top of
`~/.config/reviewbot/config.toml`, above the first `[[agents]]`:

```toml
multiplexer = "herdr"     # keep using Herdr; nothing else changes
```

To move to Rex instead, use `multiplexer = "rex"` and give every agent a
`command` (see [Choosing a multiplexer](#choosing-a-multiplexer)). `doctor`
offers to add those too.

## Setup in a new environment

1. **Authenticate GitHub.** ReviewBot uses whatever account `gh` is logged in as.

   ```sh
   gh auth login
   ```

   For organizations that enforce SSO, authorize the token for each org
   (`gh auth refresh` and follow the prompt, or authorize it in GitHub's token
   settings). PRs in orgs the token cannot see are silently absent from search
   results.

2. **With Herdr, install the Herdr agent integration** for each agent you will
   use, so Herdr can track its state, e.g.:

   ```sh
   herdr integration install claude
   ```

   Rex needs no setup: agents report their state to it directly.

3. **Create a config.** `init` asks what to watch if you don't pass `--watch`,
   and which multiplexer to use if you don't pass `--multiplexer`. Each
   `--agent KIND[:KEY]` adds an agent of that kind, named after the kind, bound
   to an optional TUI key (with Rex, its `command` is set to the kind too):

   ```sh
   reviewbot init --watch acme --multiplexer herdr --agent claude:c --agent codex:x
   reviewbot init --watch acme/api --multiplexer rex --agent claude:c
   reviewbot init --watch '*' --multiplexer rex   # everything, agents added by hand
   ```

   This writes `~/.config/reviewbot/config.toml` and the review templates into
   `~/.config/reviewbot/prompts/`. Existing files are left alone unless you pass
   `--force`. Edit the config afterwards to add agents, groups, combats, args,
   per-agent prompts, or saved output (see below).

4. **Check the setup:**

   ```sh
   reviewbot doctor
   ```

   At a terminal, it first offers to add any setting the config is missing
   (such as `multiplexer`). Then it checks that `gh`, `git`, and the configured
   multiplexer are on your
   `PATH` (and, for Rex, that its server answers and a session is known), that
   `gh` is authenticated, that the config loads, that every configured agent's
   executable is on your `PATH`, and that every agent and combat prompt file
   exists.

5. **Start or attach to your multiplexer**, then run `reviewbot tui` inside it.
   Launched reviews open as new tabs: in the focused Herdr workspace, or in
   the Rex session ReviewBot runs in.

## Choosing a multiplexer

`multiplexer` in `config.toml` is required and is either `"herdr"` or `"rex"`.
It is a top-level setting, so it must come before the first `[[agents]]` (or
any other `[...]`) line; TOML would otherwise read it as part of that table.

The two differ in one way that affects your agents: Herdr starts an agent from
its `kind`, while Rex runs the program named in `command`. Claude Code and Codex
work with both. These are complete configs for each:

```toml
# Herdr
watch = ["acme"]
multiplexer = "herdr"
prompt = "prompts/review.md"

[[agents]]
name = "claude"
kind = "claude"
key = "c"

[[agents]]
name = "codex"
kind = "codex"
key = "x"
```

```toml
# Rex: the same, plus a command for each agent
watch = ["acme"]
multiplexer = "rex"
prompt = "prompts/review.md"

[[agents]]
name = "claude"
kind = "claude"
command = "claude"
key = "c"

[[agents]]
name = "codex"
kind = "codex"
command = "codex"
key = "x"
```

`command` is the program to run, looked up on the `PATH` your login shell sets
(or give a full path). `args` work the same with both: they are added after the
program, e.g. `args = ["--model", "opus"]`. A config can carry `command` lines
while using Herdr; Herdr ignores them, so switching back and forth is one line.

### Herdr

ReviewBot opens a tab, runs `herdr agent start --kind <kind>` in it, and drives
the agent by name with `herdr agent prompt` and `herdr agent wait`. Install the
Herdr integration for each agent kind first (`herdr integration install
claude`). Set `herdr_workspace` to send tabs to a specific workspace instead of
the focused one.

### Rex

ReviewBot opens a tab running the agent's `command` and `args` in the
worktree, and drives it by its block ID. Prompts are sent as one bracketed
paste followed by Return, so multi-line prompts arrive intact. No integration
needs installing.

Rex has no agent registry. Instead, agents report their own state with the
OSC 7501 program status protocol, which Claude Code and Codex both do. An agent
is ready when it reports `idle`. A turn is over when its status goes `working`
(or `blocked`, waiting on you) and then settles on `idle` or `done`. The
waiting is done inside Rex by two bundled Lua scripts that ReviewBot runs with
`rex do`: `launch.lua` opens the tab and waits for ready, and `prompt.lua`
pastes the prompt and waits for the turn. Each registers its status handlers
before acting, so a fast turn is never missed. An agent that never reports a
status cannot be driven this way.

Startup dialogs, such as folder trust or Codex's "hooks need review", come
before the agent reports `idle`. Answer them in the new tab; ReviewBot waits up
to two minutes for an agent to become ready.

Tabs open in `rex_session` (an ID or label) if set, else in the session
ReviewBot runs in (`$REX_SESSION`).

## Choosing what to watch

The `watch` list in `config.toml` controls which repositories both queues draw
from. Each entry is an owner (an org or user) or an `OWNER/REPO`. It is
required, so there is no implicit scope:

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
prompt = "prompts/review.md"  # prompt for every agent without its own

[[agents]]
name = "claude"               # used with --agent and in tab names
kind = "claude"               # the agent kind
command = "claude"            # Rex only: the program to run in the tab
key = "c"                     # TUI shortcut, optional
args = ["--model", "opus"]    # extra arguments for the agent CLI, optional

[[agents]]
name = "codex"
kind = "codex"
key = "x"
prompt = "prompts/tests.md"   # this agent's own prompt

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
  across agents, groups, and combats.
- `kind`: the kind of agent, recorded with saved reviews. With Herdr it is
  passed to `herdr agent start --kind` and names the executable `doctor` looks
  for. Run `herdr agent start --help` for the supported kinds (claude, codex,
  gemini, cursor, opencode, copilot, amp, and more).
- `command`: required with Rex, and ignored by Herdr. The program Rex runs in
  the tab, and the executable `doctor` looks for.
- `key`: one letter or digit. `q`, `r`, `o`, `1`, `2`, `a`, and `e` are taken by
  the TUI, and keys must not repeat.
- `args`: extra arguments for the agent's command line.
- `prompt`: a file path; relative paths resolve against the config file's
  directory. Every agent needs one, either its own or the top-level `prompt`.

`reviewbot agents` lists what is configured.

## Combat review

A combat pits agents against each other on one PR: they review it, then answer
each other's reviews. It needs at least two agents, and every setting is
explicit except `key`, `opener`, and `judge`.

```toml
[[combats]]
name = "duel"
key = "m"
agents = ["claude", "codex"]
style = "handoff"                         # or "parallel"
rounds = 2
challenge_prompt = "prompts/challenge.md"
opener = "claude"                         # optional walkthrough before the combat
opener_prompt = "prompts/walkthrough.md"
judge = "codex"                           # optional final verdict
judge_prompt = "prompts/verdict.md"
```

- `style = "parallel"`: every agent reviews independently at the same time.
  Then, for each of `rounds`, every agent receives the other agents' latest
  reviews and confirms, disputes, or adds to them.
- `style = "handoff"`: the first agent reviews, then agents take turns for
  `rounds` turns. Each one receives every earlier review by the others. With
  `rounds = 2`, claude reviews, codex answers claude, and claude rebuts codex.
  An agent's first turn combines its own review prompt with the challenge
  prompt.
- `opener`: runs a walkthrough first ("here is what this PR does and what it
  gets right"); its output is given to the combatants as context.
- `judge`: receives every review and rebuttal and writes one final review with
  agreed findings, resolved disputes, and findings that did not hold up.

Each agent keeps one tab and session for the whole combat, so you can
watch it or step in at any point. Turns are driven by a background
`reviewbot` process: it sends each prompt, waits for the agent to finish its
turn (including any time it spends waiting on you for permission), and reads
the review the agent was told to write to `.reviewbot/<run>/` in the worktree.
That directory ignores itself in Git. An agent that finishes without writing
its review is reminded once; if it still has not, the run stops and is marked
failed. Closing the TUI does not stop a run.

```sh
reviewbot runs        # recent runs, each step's status, and where reviews were saved
```

Nothing is posted to GitHub. The combat gives you the material; you submit
the review.

## Saving reviews

Add an `[output]` table to keep every finished review:

```toml
[output]
dir = "~/reviewbot-reviews"
```

With it, every launch (single agents and groups too, not only combats) uses
the background runner, and each finished review is written to
`<dir>/<owner>-<repo>/pr-<n>/<run>/<step>-<agent>-<role>.md`. Each file starts
with front matter: repo, PR, URL, title, head SHA, run, launcher, step, agent,
agent kind, role, round, and save time. Every saved review is also appended as
one JSON line to `<dir>/index.jsonl`, which makes a simple audit log of which
agents reviewed which commit, and when. Without `[output]`, reviews are not
saved.

## Review prompts

A prompt is a plain text or Markdown file sent to the agent once it starts in
the PR worktree. `init` writes these templates into `prompts/`; change them
freely or add your own:

| Template | Use |
| --- | --- |
| `review.md` | general review focused on test coverage and substantive correctness, security, and regression issues |
| `tests.md` | test coverage only |
| `security.md` | security only |
| `walkthrough.md` | a combat `opener_prompt`: what the PR does, what it gets right, where to look |
| `challenge.md` | a combat `challenge_prompt`: confirm, dispute, or add to other reviews |
| `verdict.md` | a combat `judge_prompt`: merge everything into one final review |

All of them tell the agent not to edit, push, comment, or submit a review.
When a review is being collected, ReviewBot appends one instruction naming the
file to write it to, so custom prompts do not need to mention it.

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
| `{prior_reviews}` | in combats, the earlier reviews this step responds to (`none` otherwise) |

If a step responds to earlier reviews and its template has no
`{prior_reviews}`, they are appended under "Earlier reviews of this PR".

Prompt files are read when you launch, so edits apply to the next review
without restarting the TUI.

## Configuration reference

`~/.config/reviewbot/config.toml`:

```toml
watch = ["acme"]                    # required; ["*"] for everything
multiplexer = "rex"                 # required; "herdr" or "rex"
work_dir = "~/reviewbot-workspaces" # cached clones and per-PR worktrees
prompt = "prompts/review.md"        # shared prompt for agents without their own
# herdr_workspace = "workspace-id"  # Herdr: force new tabs into one workspace
# rex_session = "reviews"           # Rex: open tabs in this session

[[agents]]                          # see "Review agents"
name = "claude"
kind = "claude"
command = "claude"                  # required with Rex
key = "c"

# [[groups]] and [[combats]]        # see "Review agents" and "Combat review"

[output]                            # optional; see "Saving reviews"
dir = "~/reviewbot-reviews"

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
| your agent keys | launch that agent, group, or combat on the selected review request (shown in the footer) |
| `a` | (My PRs) pick reviewers: previous reviewers, GitHub's suggestions, or typed `login` / `org/team`; `ctrl+s` requests, `escape` cancels |
| `e` | (My PRs) re-request review from everyone whose review is stale or requested changes |

The TUI is deliberately a queue picker rather than an agent interface; active
work happens in multiplexer tabs, which the queue marks as `in-progress`.

### Review request statuses

- `ready`: you have not reviewed it yet.
- `updated`: you reviewed an older commit; there are new commits since.
- `reviewed`: you already reviewed the current head commit.
- `changes-requested`: you requested changes and nothing has been pushed since.
- `draft`: the PR is a draft.
- `in-progress`: a review tab for it is open in Herdr or Rex.

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
reviewbot agents                          # configured agents, groups, combats, and keys
reviewbot runs                            # background runs and their progress
reviewbot next                            # show the most recent actionable request
reviewbot next --agent claude             # ...and offer to launch it
reviewbot next --agent pair --yes         # launch a group without asking
reviewbot launch acme/api#422 --agent claude
reviewbot launch https://github.com/acme/api/pull/422 --agent duel
```

`--agent` takes any agent, group, or combat name from the config. `launch`
looks the PR up in that repository's review requests, so it works regardless of
the configured `watch` list.

## What a launch does

1. Clones the repository (or reuses a `[[checkouts]]` clone) and fetches
   `pull/<n>/head`.
2. Verifies the fetched commit still matches the PR head, then creates a
   detached worktree at that exact SHA under
   `<work_dir>/worktrees/<owner>-<repo>/pr-<n>-<sha>`.
3. For each agent in the first phase (one agent, every member of a group, or
   a combat's first speakers), opens a tab named `<repo> #<n> <agent> review`
   and starts the agent there: `herdr agent start --kind <kind> -- <args>`
   with Herdr, or `<command> <args>` with Rex, waiting until it reports ready.
4. Without combats or `[output]`, sends each agent its rendered prompt and is
   done. Otherwise, starts the background runner described under "Combat
   review", which opens later agents' tabs as their turns come up.

Prompt files are checked before anything is cloned or opened, so a missing
prompt fails the launch cleanly.

Worktrees are not cleaned up automatically. Remove old ones with
`git -C <clone> worktree remove <path>` or `git worktree prune` after deleting
the directory.

## Development

```sh
uv sync
uv run pytest
uvx ruff check src tests
```
