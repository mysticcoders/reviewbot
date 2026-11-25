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

[filters]                           # required; see Filters below
open = "requested"
mine = "all"
notifications = "all"
notification_kinds = ["pr", "issue"]
stale_days = 14

[[agents]]
name = "claude"
kind = "claude"
command = "claude"                  # required with Rex
key = "c"

# [[groups]] and [[combats]]: see below

[output]                            # optional; `init` writes it
dir = "~/reviewbot-reviews"

[serve]                             # optional
port = 8765
bind = "localhost"                  # "localhost", "tailnet" or "tailscale-node"
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
| `key` | no | One letter or digit for the TUI. `q`, `r`, `o`, `1`, `2`, `3`, `a`, `e` and `f` belong to the TUI. Keys must not repeat. |
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
| `dir` | yes | The directory for saved reviews. See [Save finished reviews](../how-to/save-reviews.md) and, for the file format, [Saved reviews](saved-reviews.md). |

`reviewbot init` writes `[output]` with `dir = "~/reviewbot-reviews"`, so new installations keep their reviews. A config without the table saves nothing.

## `[serve]`

See [Serve the dashboard and MCP endpoint](../how-to/serve-the-dashboard.md).

| Field | Required | Value |
| --- | --- | --- |
| `port` | yes | The TCP port, 0–65535. With `tailscale-node`, the loopback port behind the device; 0 picks a free port. |
| `bind` | yes | `"localhost"`, `"tailnet"` or `"tailscale-node"` (macOS only). |
| `browser` | yes | On macOS, an app name for `open -a`. On Linux, a command that ReviewBot runs with the URL. |
| `hostname` | with `tailscale-node` | The device name on the tailnet: 1–63 lowercase letters, digits or `-`, not starting or ending with `-`. Not allowed with the other `bind` values. |

## Filters

The `[filters]` table sets what each view shows inside the watched repositories. It is required, and every key in it is required. A config from before 0.5 has no `[filters]`: ReviewBot adds the table to the file the first time it loads it, set to show the views as they were before, and keeps the previous file as `config.toml.before-0.5`.

| Key | Value |
| --- | --- |
| `open` | The **Open PRs** tab: `requested`, `involves`, `all` or `stale`. |
| `mine` | The **My PRs** tab: `all`, `ready`, `needs-action` or `stale`. |
| `notifications` | The **Notifications** tab: `all`, `participating` or `reviews`. |
| `notification_kinds` | Which notifications to show: one or more of `pr` and `issue`. |
| `stale_days` | How many days without activity make a PR `stale`. A whole number, 1 or more. |

**Open PRs**

| `open` | Shows |
| --- | --- |
| `requested` | PRs that request your review, directly or through a team. |
| `involves` | PRs you were requested on, reviewed, commented on, or were mentioned in. Your own PRs are left out. |
| `all` | Every open PR in the watched repositories, except your own. Not available with `watch = ["*"]`, where it would search all of GitHub. |
| `stale` | PRs that request your review, with no activity for `stale_days`. |

With `involves` and `all`, the **Request** column says `none` for PRs that do not ask for your review. You can launch a review of any `ready` or `updated` row.

**My PRs** lists open PRs you authored or are assigned to. The **Role** column says which.

| `mine` | Shows |
| --- | --- |
| `all` | Every one. |
| `ready` | Drafts left out. |
| `needs-action` | Changes requested, failing checks, conflicts, no reviewers, or reviewers to ask again. |
| `stale` | No activity for `stale_days`. |

**Notifications**

| `notifications` | Shows |
| --- | --- |
| `all` | Every unread notification of the kinds in `notification_kinds`. |
| `participating` | Only threads you take part in or were mentioned in. |
| `reviews` | Only review requests and mentions. |

The config is the only lasting source of these filters. In the TUI, `f` changes the current tab's filter until you quit. On the dashboard, each tab's **Filter** menu changes it for that page. Through MCP, each list tool takes a `filter` argument.

## Checkouts

Each `[[checkouts]]` table makes ReviewBot reuse an existing local clone, not clone into `work_dir`.

| Field | Required | Value |
| --- | --- | --- |
| `repo` | yes | `OWNER/REPO`. |
| `path` | yes | The path of the local clone. |

ReviewBot only fetches refs into a reused clone and adds detached worktrees beside it. It never changes the branch that the clone has checked out. ReviewBot clones each repository without a `[[checkouts]]` entry into `<work_dir>/repos/` at its first launch.
