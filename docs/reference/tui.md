# TUI keys

Start the TUI with `reviewbot tui`.

| Key | Action |
| --- | --- |
| `1` / `2` / `3` | Switch between **Review requests**, **My PRs** and **Notifications**. |
| arrows | Select a PR. |
| `r` | Refresh the current tab. |
| `q` | Quit. |
| `o` | Open the selected PR on GitHub. On **Notifications**, open the PR or issue and mark the notification read. |
| your agent keys | Launch that agent, group or combat on the selected review request. The footer shows the keys. |
| `a` | (My PRs) Pick reviewers: earlier reviewers, GitHub's suggestions, or a typed `login` or `org/team`. `ctrl+s` sends the request; `escape` cancels. |
| `e` | (My PRs) Request review again from each reviewer whose review is stale or requested changes. |
| `m` | (Notifications) Mark the selected notification read on GitHub. |
| `ctrl+w` | Start the dashboard and HTTP MCP endpoint, and open the dashboard in the `[serve]` browser. Press again to stop. |

The TUI picks from the queue. It is not an agent interface. Agents work in multiplexer tabs, and the queue marks those PRs `in-progress`.

For the statuses in each tab, see [Queue statuses](statuses.md).

## Notifications

The **Notifications** tab lists your unread GitHub notifications about PRs and issues in the repositories you watch, newest first. Its title shows how many there are. Notifications about releases, CI, security alerts and other kinds are left out. The TUI fetches them when it starts, every two minutes after that, and when you press `r` on the tab.

In **Review requests** and **My PRs**, a `*` after the PR number means that PR has an unread notification, and the detail pane lists why, such as `comment` or `review requested`.

Marking a notification read changes only your own read state on GitHub. Nothing is posted.
