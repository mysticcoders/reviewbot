# Command line

Run `reviewbot --help`, or `reviewbot <command> --help`, for every option.

## Commands

| Command | What it does |
| --- | --- |
| `reviewbot init` | Writes the config and the prompt templates. See the [tutorial](../tutorial/first-review.md). |
| `reviewbot doctor` | Checks the tools, the config and the prompts. At a terminal, it offers to add missing settings. |
| `reviewbot tui` | Opens the TUI. See [TUI keys](tui.md). |
| `reviewbot status` | Prints the review requests as a table. |
| `reviewbot mine` | Prints your open PRs as a table. |
| `reviewbot agents` | Lists the configured agents, groups, combats and keys. |
| `reviewbot runs` | Lists background runs and their progress. |
| `reviewbot next` | Shows the most recent actionable review request. |
| `reviewbot launch <pr> --agent <name>` | Launches a review of one PR. |
| `reviewbot serve` | Serves the dashboard and the HTTP MCP endpoint. |
| `reviewbot token` | Prints the token, the dashboard login URL and the MCP setup. |
| `reviewbot mcp` | Runs the MCP server over stdio. |
| `reviewbot tailnet login` | Joins the tailnet as ReviewBot's own device, and prints a sign-in link if the device is not approved yet. macOS only. |
| `reviewbot tailnet logout` | Signs the device out of the tailnet, and deletes its local state. |

## Examples

```sh
reviewbot next --agent claude             # show the request and offer to launch it
reviewbot next --agent pair --yes         # launch a group without asking
reviewbot launch acme/api#422 --agent claude
reviewbot launch https://github.com/acme/api/pull/422 --agent duel
reviewbot serve --bind tailnet --port 9000
reviewbot token --bind tailnet --port 9000
reviewbot token --rotate                  # replace the token; clients must update
```

## Common options

- `--agent` takes any agent, group or combat name from the config.
- `--watch` (`-w`) and `--all` replace the configured `watch` list for one run. They work on each command that reads the queues: `tui`, `status`, `mine`, `next`, `serve`, `mcp`.
- `serve` and `token` take `--bind`, `--port` and `--hostname`, which replace those `[serve]` settings for one run. `--hostname` applies only with `tailscale-node`.
- `tailnet login` and `tailnet logout` take `--hostname`.
- `tui --serve` starts the dashboard with the TUI, and does not open the browser.

`launch` looks the PR up in the review requests of that repository. So it works whatever the configured `watch` list is.
