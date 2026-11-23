# Choose a multiplexer

ReviewBot opens each review in a tab of a terminal multiplexer. It supports Herdr and Rex.

## Compare them

The difference that affects your agents:

- Herdr starts an agent from its `kind`.
- Rex runs the program named in the agent's `command`.

Claude Code and Codex work with both. For how ReviewBot drives each one, read [How ReviewBot drives agents in Herdr and Rex](../explanation/multiplexers.md).

## Set the multiplexer

Set `multiplexer` in `config.toml` to `"herdr"` or `"rex"`. The setting is required.

Put it before the first `[[agents]]` line, or before any other `[...]` line. Otherwise TOML reads it as part of that table.

## Example: Herdr

```toml
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

1. Install the Herdr integration for each agent kind, for example `herdr integration install claude`.
2. Optional: set `herdr_workspace` to send new tabs to one workspace. Without it, tabs open in the focused workspace.

## Example: Rex

This is the same config, plus a `command` for each agent:

```toml
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

- `command` is the program to run. ReviewBot looks it up on the `PATH` that your login shell sets. You can also give a full path.
- Tabs open in `rex_session` (an ID or label) if you set it. Otherwise they open in the session that ReviewBot runs in (`$REX_SESSION`).
- Rex needs no integration.

## Switch between them

`args` work the same with both. They come after the program, for example `args = ["--model", "opus"]`.

A config can keep its `command` lines while it uses Herdr, because Herdr ignores them. To switch, change only the `multiplexer` line.
