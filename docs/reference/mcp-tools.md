# MCP tools

`reviewbot serve` offers these tools at `/mcp`. `reviewbot mcp` offers the same tools over stdio. See [Serve the dashboard and MCP endpoint](../how-to/serve-the-dashboard.md).

| Tool | Arguments | What it does |
| --- | --- | --- |
| `list_review_queue` | `refresh` (boolean), `actionable_only` (boolean) | Lists the open PRs that request your review, with status, checks, author and URL. `actionable_only` keeps only `ready` and `updated` PRs that are not under review. |
| `list_my_prs` | `refresh` (boolean) | Lists your open PRs with status, checks, mergeability, reviewer states, and the reviewers to ask again. |
| `open_pr` | `pr` (string, required) | Opens a PR in the `[serve]` browser on your computer. Accepts `owner/repo#number` or the PR URL. |

## Caching

ReviewBot fetches GitHub data on demand and keeps it for two minutes. `refresh: true` fetches fresh data. When the TUI hosts the server, the server uses the TUI's own fetches.

## Limits

`open_pr` opens only a PR that is on one of the two lists. It never opens any other address.
