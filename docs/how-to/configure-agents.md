# Configure agents and groups

You define agents only in the config. There are no built-in agents and no default agent. With no agents, the queues still work, but you cannot launch a review.

## Add an agent

Add an `[[agents]]` table for each agent:

```toml
prompt = "prompts/review.md"  # prompt for every agent without its own

[[agents]]
name = "claude"
kind = "claude"
command = "claude"            # Rex only
key = "c"
args = ["--model", "opus"]
```

## Give an agent its own prompt

```toml
[[agents]]
name = "codex"
kind = "codex"
key = "x"
prompt = "prompts/tests.md"
```

## Use the same kind twice

You can define the same kind more than once with different settings:

```toml
[[agents]]
name = "codex-deep"
kind = "codex"
key = "d"
args = ["-c", "model_reasoning_effort=high"]
```

## Launch several agents on one PR

A group launches each of its agents on the same PR:

```toml
[[groups]]
name = "pair"
agents = ["claude", "codex"]
key = "b"
```

## Check the result

```sh
reviewbot agents
```

This lists the configured agents, groups, combats and keys.

For the rules of each field, see [Agents in the configuration reference](../reference/configuration.md#agents).
