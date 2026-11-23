# TUI keys

Start the TUI with `reviewbot tui`.

| Key | Action |
| --- | --- |
| `1` / `2` | Switch between **Review requests** and **My PRs**. |
| arrows | Select a PR. |
| `r` | Refresh the current tab. |
| `q` | Quit. |
| `o` | Open the selected PR on GitHub. |
| your agent keys | Launch that agent, group or combat on the selected review request. The footer shows the keys. |
| `a` | (My PRs) Pick reviewers: earlier reviewers, GitHub's suggestions, or a typed `login` or `org/team`. `ctrl+s` sends the request; `escape` cancels. |
| `e` | (My PRs) Request review again from each reviewer whose review is stale or requested changes. |
| `ctrl+w` | Start the dashboard and HTTP MCP endpoint, and open the dashboard in the `[serve]` browser. Press again to stop. |

The TUI picks from the queue. It is not an agent interface. Agents work in multiplexer tabs, and the queue marks those PRs `in-progress`.

For the statuses in each tab, see [Queue statuses](statuses.md).
