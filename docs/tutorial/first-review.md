# Set up ReviewBot and run your first review

This tutorial takes you from a new install to one agent review of one pull request. It takes about ten minutes.

Before you start, install ReviewBot and the tools in the [README requirements](../../README.md#requirements).

## 1. Sign in to GitHub

ReviewBot uses the account that `gh` is signed in to.

```sh
gh auth login
```

Some organizations enforce SSO. For each of them, authorize the token: run `gh auth refresh` and follow the prompt, or authorize it in the GitHub token settings. Search results silently leave out PRs in organizations that the token cannot see.

## 2. Prepare your multiplexer

ReviewBot opens each review in a tab of a terminal multiplexer: Herdr or Rex. If you do not know which one to use, read [Choose a multiplexer](../how-to/choose-a-multiplexer.md).

- With Herdr, install the Herdr integration for each agent kind that you will use:

  ```sh
  herdr integration install claude
  ```

- With Rex, no setup is necessary. Agents report their state to Rex directly.

## 3. Create a config

Run `reviewbot init`. This example watches the `acme` organization, uses Rex, and adds Claude Code on the `c` key:

```sh
reviewbot init --watch acme --multiplexer rex --agent claude:c
```

Some other forms:

```sh
reviewbot init --watch acme --multiplexer herdr --agent claude:c --agent codex:x
reviewbot init --watch '*' --multiplexer rex   # everything; add agents by hand
```

- If you leave out `--watch` or `--multiplexer`, `init` asks for them.
- Each `--agent KIND[:KEY]` adds an agent of that kind. The agent gets the kind as its name and the optional key as its TUI shortcut. With Rex, its `command` is also set to the kind.

`init` writes `~/.config/reviewbot/config.toml`, and the review templates into `~/.config/reviewbot/prompts/`. It does not replace existing files unless you pass `--force`.

The config saves every finished review to `~/reviewbot-reviews/` (the `[output]` table). To change the folder or stop saving, see [Save finished reviews](../how-to/save-reviews.md).

## 4. Check the setup

```sh
reviewbot doctor
```

At a terminal, `doctor` first offers to add settings that the config is missing. Then it checks these items:

- `gh`, `git` and the configured multiplexer are on your `PATH`.
- For Rex, the server answers and a session is known.
- `gh` is authenticated.
- The config loads.
- The executable of each configured agent is on your `PATH`.
- Each agent and combat prompt file exists.

Fix each failed check before you continue.

## 5. Open the queue

Start or attach to your multiplexer. Then, inside it, run:

```sh
reviewbot tui
```

The **Review requests** tab shows the open PRs that ask for your review. Press `2` to see **My PRs**, and `1` to go back.

## 6. Launch a review

1. Use the arrow keys to select a PR with the status `ready` or `updated`.
2. Press `c` (the key you gave the agent).

ReviewBot checks out the PR in a separate worktree. It opens a new tab and starts the agent there with your review prompt. In the queue, the PR now shows `in-progress`.

## 7. Read the review

Go to the new tab to watch the agent work. Because the config saves reviews, ReviewBot asks the agent to write its review to a file, and the agent then replies briefly in the tab. To read the review, run:

```sh
reviewbot runs
```

It lists the run with each step's status and the path of the saved review, under `~/reviewbot-reviews/`. The review ends with **Recurring patterns**: findings likely to happen again, each with a rule that would catch it. Without `[output]`, the agent presents the review in the tab instead.

ReviewBot does not post anything to GitHub. You decide what to submit, and you submit it yourself.

## Next steps

- Add more agents, or groups of agents: [Configure agents and groups](../how-to/configure-agents.md).
- Let agents argue about a PR: [Run a combat review](../how-to/run-a-combat-review.md).
- Change what the agents look for: [Write review prompts](../how-to/write-prompts.md).
