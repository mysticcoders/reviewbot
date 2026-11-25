# MCP tools

`reviewbot serve` offers these tools at `/mcp`. `reviewbot mcp` offers the same tools over stdio. See [Serve the dashboard and MCP endpoint](../how-to/serve-the-dashboard.md).

| Tool | Arguments | What it does |
| --- | --- | --- |
| `list_open_prs` | `filter` (string), `refresh` (boolean), `actionable_only` (boolean) | Lists open PRs in the watched repositories, with status, checks, author, how your review was requested (`direct`, `team:<slug>` or `none`) and URL. `actionable_only` keeps only `ready` and `updated` PRs that are not under review. |
| `list_review_queue` | the same | The same as `list_open_prs`, kept for clients that use the older name. |
| `list_my_prs` | `filter` (string), `refresh` (boolean) | Lists open PRs you authored or are assigned to (`roles`), with status, checks, mergeability, reviewer states, and the reviewers to ask again. |
| `list_notifications` | `filter` (string), `refresh` (boolean) | Lists your unread GitHub notifications about PRs and issues in the watched repositories: thread `id`, `key`, `kind` (`pr` or `issue`), title, reason, update time and URL. |
| `mark_notification_read` | `id` (string, required) | Marks one notification read on GitHub, by the `id` from `list_notifications`. Only your read state changes; nothing is posted. |
| `open_pr` | `pr` (string, required) | Opens a PR in the `[serve]` browser on your computer. Accepts `owner/repo#number` or the URL. It also opens the PR or issue of a notification. |

## Filters

`filter` takes the same values as the matching key in `[filters]`: `open` for `list_open_prs`, `mine` for `list_my_prs`, and `notifications` for `list_notifications`. Without it, a tool uses the configured filter. Each result says which filter it used. See [Filters](configuration.md#filters).

## Caching

ReviewBot fetches GitHub data on demand and keeps it for two minutes, separately for each filter. `refresh: true` fetches fresh data. When the TUI hosts the server, the server uses the TUI's own fetches.

## Limits

`open_pr` opens only a PR on one of the two lists, or the PR or issue of an unread notification. It never opens any other address.

`mark_notification_read` accepts only the `id` of a notification ReviewBot has listed.
