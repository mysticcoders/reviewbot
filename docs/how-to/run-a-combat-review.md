# Run a combat review

A combat makes agents argue about one PR. They review it, then answer each other's reviews. For how a run works, read [How combat runs work](../explanation/combat-runs.md).

## 1. Define the combat

A combat needs at least two agents. Every setting is required, except `key`, `opener` and `judge`.

```toml
[[combats]]
name = "duel"
key = "m"
agents = ["claude", "codex"]
style = "handoff"                         # or "parallel"
rounds = 2
challenge_prompt = "prompts/challenge.md"
opener = "claude"                         # optional walkthrough first
opener_prompt = "prompts/walkthrough.md"
judge = "codex"                           # optional final verdict
judge_prompt = "prompts/verdict.md"
```

## 2. Choose a style

- `style = "parallel"`: each agent reviews independently, at the same time. Then, in each of the `rounds`, each agent gets the latest reviews of the other agents. It confirms, disputes or adds to them.
- `style = "handoff"`: the first agent reviews. Then agents take turns for `rounds` turns. Each one gets every earlier review by the others. With `rounds = 2`, claude reviews, codex answers claude, and claude answers codex. An agent's first turn combines its own review prompt with the challenge prompt.

## 3. Optional: add an opener and a judge

- `opener`: the opener writes a walkthrough first: what the PR does, and what it gets right. The combatants get it as context.
- `judge`: the judge gets every review and every answer. It writes one final review with the agreed findings, the resolved disputes, and the findings that did not hold.

## 4. Launch it

In the TUI, select a PR and press the combat's key. From the command line:

```sh
reviewbot launch acme/api#422 --agent duel
```

## 5. Follow the run

Each agent keeps one tab for the whole combat. You can watch it, or step in at any time. Closing the TUI does not stop a run.

```sh
reviewbot runs
```

This shows recent runs, the status of each step, and where ReviewBot saved each review.

ReviewBot posts nothing to GitHub. The combat gives you the material, and you submit the review.
