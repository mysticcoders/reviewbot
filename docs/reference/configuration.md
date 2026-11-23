# Configuration file

ReviewBot reads `~/.config/reviewbot/config.toml`. `reviewbot init` creates it.

## Complete example

```toml
watch = ["acme"]                    # required; ["*"] for everything
multiplexer = "rex"                 # required; "herdr" or "rex"
work_dir = "~/reviewbot-workspaces" # cached clones and per-PR worktrees
prompt = "prompts/review.md"        # shared prompt for agents without their own
# herdr_workspace = "workspace-id"  # Herdr: force new tabs into one workspace
# rex_session = "reviews"           # Rex: open tabs in this session

[[agents]]
name = "claude"
kind = "claude"
command = "claude"                  # required with Rex
key = "c"

# [[groups]] and [[combats]]: see below

[output]                            # optional
dir = "~/reviewbot-reviews"

[serve]                             # optional
port = 8765
bind = "localhost"                  # "localhost" or "tailnet"
browser = "Google Chrome"           # opens PRs for the MCP open_pr tool

[[checkouts]]                       # optional; reuse an existing local clone
repo = "acme/api"
path = "~/code/api"
```

Put the top-level settings before the first `[...]` line. Otherwise TOML reads them as part of that table.

## Top-level settings

| Setting | Required | Value |
| --- | --- | --- |
| `watch` | yes | A list of owners or `OWNER/REPO` entries, or `["*"]`. GitHub URLs are accepted. See [Choose which repositories to watch](../how-to/choose-what-to-watch.md). |
| `multiplexer` | yes | `"herdr"` or `"rex"`. |
| `work_dir` | no | The directory for cached clones and PR worktrees. The default is `~/reviewbot-workspaces`. |
| `prompt` | no | The prompt file for each agent that has no `prompt` of its own. |
| `herdr_workspace` | no | Herdr only: the workspace for new tabs. Without it, tabs open in the focused workspace. |
| `rex_session` | no | Rex only: the session ID or label for new tabs. Without it, tabs open in `$REX_SESSION`. |

ReviewBot resolves relative paths against the directory of the config file.

## Agents

Each `[[agents]]` table defines one agent.

| Field | Required | Value |
| --- | --- | --- |
| `name` | yes | 1–16 characters: lowercase letters, digits, `-` or `_`. Unique across agents, groups and combats. Used with `--agent` and in tab names. |
| `kind` | yes | The kind of agent. ReviewBot records it with saved reviews. With Herdr, it goes to `herdr agent start --kind`, and `doctor` looks for an executable of that name. Run `herdr agent start --help` for the supported kinds. |
| `command` | with Rex | The program that Rex runs in the tab, and the executable that `doctor` looks for. Herdr ignores it. |
| `key` | no | One letter or digit for the TUI. `q`, `r`, `o`, `1`, `2`, `a` and `e` belong to the TUI. Keys must not repeat. |
| `args` | no | Extra arguments for the agent's command line. |
| `prompt` | no | This agent's prompt file. Each agent needs a prompt: its own, or the top-level `prompt`. |

## Groups

Each `[[groups]]` table launches several agents on one PR.

| Field | Required | Value |
| --- | --- | --- |
| `name` | yes | The same rules as an agent name. |
| `agents` | yes | The names of two or more agents to launch. |
| `key` | no | The same rules as an agent key. |

## Combats

Each `[[combats]]` table defines one combat. See [Run a combat review](../how-to/run-a-combat-review.md).

| Field | Required | Value |
| --- | --- | --- |
| `name` | yes | The same rules as an agent name. |
| `agents` | yes | Two or more distinct agent names. |
| `style` | yes | `"parallel"` or `"handoff"`. |
| `rounds` | yes | A whole number, 1 or more: the answer rounds (parallel) or turns (handoff). |
| `challenge_prompt` | yes | The prompt for answering other reviews. |
| `key` | no | The same rules as an agent key. |
| `opener` | no | The agent that writes a walkthrough first. |
| `opener_prompt` | with `opener` | The prompt for the walkthrough. |
| `judge` | no | The agent that writes the final verdict. |
| `judge_prompt` | with `judge` | The prompt for the verdict. |

## `[output]`

| Field | Required | Value |
| --- | --- | --- |
| `dir` | yes | The directory for saved reviews. See [Save finished reviews](../how-to/save-reviews.md). |

## `[serve]`

All fields are required. See [Serve the dashboard and MCP endpoint](../how-to/serve-the-dashboard.md).

| Field | Value |
| --- | --- |
| `port` | The TCP port. |
| `bind` | `"localhost"` or `"tailnet"`. |
| `browser` | On macOS, an app name for `open -a`. On Linux, a command that ReviewBot runs with the URL. |

## Checkouts

Each `[[checkouts]]` table makes ReviewBot reuse an existing local clone, not clone into `work_dir`.

| Field | Required | Value |
| --- | --- | --- |
| `repo` | yes | `OWNER/REPO`. |
| `path` | yes | The path of the local clone. |

ReviewBot only fetches refs into a reused clone and adds detached worktrees beside it. It never changes the branch that the clone has checked out. ReviewBot clones each repository without a `[[checkouts]]` entry into `<work_dir>/repos/` at its first launch.
