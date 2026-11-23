# ReviewBot documentation

The documentation follows the [Diátaxis](https://diataxis.fr/) framework. Each page has one purpose.

## Tutorial

Start here if you have not used ReviewBot before.

- [Set up ReviewBot and run your first review](tutorial/first-review.md)

## How-to guides

Each guide does one task. They assume that ReviewBot is installed and configured.

- [Upgrade ReviewBot](how-to/upgrade.md)
- [Choose a multiplexer](how-to/choose-a-multiplexer.md)
- [Choose which repositories to watch](how-to/choose-what-to-watch.md)
- [Configure agents and groups](how-to/configure-agents.md)
- [Run a combat review](how-to/run-a-combat-review.md)
- [Save finished reviews](how-to/save-reviews.md)
- [Write review prompts](how-to/write-prompts.md)
- [Serve the dashboard and MCP endpoint](how-to/serve-the-dashboard.md)
- [Remove old worktrees](how-to/remove-old-worktrees.md)

## Reference

Exact facts: settings, commands, keys, statuses.

- [Configuration file](reference/configuration.md)
- [Command line](reference/cli.md)
- [TUI keys](reference/tui.md)
- [Queue statuses](reference/statuses.md)
- [Prompt templates and placeholders](reference/prompts.md)
- [MCP tools](reference/mcp-tools.md)

## Explanation

Background: how ReviewBot works and why.

- [What a launch does](explanation/what-a-launch-does.md)
- [How the review scope is pinned](explanation/review-scope.md)
- [How ReviewBot drives agents in Herdr and Rex](explanation/multiplexers.md)
- [How combat runs work](explanation/combat-runs.md)
- [The security model of `serve`](explanation/security-model.md)
