# Glossary

Words that ReviewBot's code and docs use with one specific meaning. Where a word has its own page, the entry links to it.

## Launching reviews

**Agent** (review agent)
: One `[[agents]]` entry in the config: a `name`, a `kind`, and, with Rex, the `command` to run. It may also have `args`, a TUI `key`, and its own `prompt`. An agent is a configuration, not a running program; each launch starts it fresh in a tab. See [Configure agents and groups](../how-to/configure-agents.md).

**Agent kind** (`kind`)
: The kind of agent CLI, such as `claude` or `codex`. Herdr starts an agent by its kind; Rex runs its `command` instead. Saved reviews record it. Two agents can share a kind with different settings.

**Group**
: A `[[groups]]` entry: two or more agents launched together on one PR. Each reviews on its own, in its own tab, at the same time.

**Combat**
: A `[[combats]]` entry: two or more agents that review a PR and then answer each other's reviews, in a `style` of `parallel` or `handoff`, for a number of `rounds`. See [How combat runs work](../explanation/combat-runs.md).

**Opener**
: The agent a combat can run first, before the reviews. It writes a **walkthrough**: what the PR does, what it gets right, where to look. The combatants receive it as context.

**Challenge** (rebuttal)
: A combat step in which an agent answers the other agents' reviews: it confirms, disputes, or adds to them.

**Judge**
: The agent a combat can run last. It writes the **verdict**: one final review with the agreed findings, the resolved disputes, and the findings that did not hold up.

**Round**
: In a combat, one pass of challenges. In `parallel` style, every agent answers the others' latest reviews in each round. In `handoff` style, each round is one agent's turn.

**Launcher**
: What a launch names: an agent, a group, or a combat. `--agent` accepts any of them, and each can have a TUI key.

**Launch**
: Starting a launcher on one PR: check the prompts, check out the PR, open the tabs, and send the prompts or start the runner. See [What a launch does](../explanation/what-a-launch-does.md).

**Plan**
: The phases and steps a launch turns into. A single agent is one phase of one step. A group is one phase with a step per agent. A combat has a phase for the opener, one for the first reviews, one per round, and one for the judge. The opener and judge phases are one step each. In `parallel` style, the first reviews and each round have a step per agent; in `handoff` style, one.

**Phase**
: Steps that run at the same time. The next phase starts when every step of the current one has finished.

**Step**
: One prompt to one agent, in one role (`review`, `walkthrough`, `challenge` or `verdict`), with the earlier steps it is given to read.

**Turn**
: One prompt sent to an agent, and the agent's work until its multiplexer reports that it is done. A step is one turn, or two if the agent must be reminded to write its review.

**Run**
: One launch that the runner drives: its plan, a state file, a log, and the reviews it collected, kept in `<work_dir>/runs/<run>/`. `reviewbot runs` lists them.

**Runner** (background runner)
: The detached `reviewbot run-plan` process that drives a run: it sends each step's prompt, waits for the turn to end, collects the review the agent wrote, and moves on to the next phase. Combats always use it. With `[output]`, every launch does.

**Saved review**
: A finished review kept in `[output]` `dir`, with metadata, plus a line in `index.jsonl`. Without `[output]`, reviews are not saved. See [Save finished reviews](../how-to/save-reviews.md).

## Where agents run

**Multiplexer**
: The terminal workspace that hosts the agents: Herdr or Rex, set by `multiplexer` in the config. ReviewBot never talks to an agent directly; it opens a tab, starts the agent there, and sends prompts through the multiplexer, which also says when a turn is over. See [How ReviewBot drives agents in Herdr and Rex](../explanation/multiplexers.md).

**Tab**
: The multiplexer tab one agent works in, named `<owner>/<repo> #<n> <sha> <agent> review`. Each agent keeps one tab for a whole run, so you can watch or step in. In Rex, a tab is a window and the agent's pane is a block, which ReviewBot addresses by its block ID.

**Clone** (cached clone)
: The repository copy ReviewBot fetches into: a `[[checkouts]]` clone of yours, or its own clone in `<work_dir>/repos/`. ReviewBot never changes its checked-out branch.

**Worktree**
: A detached Git worktree at the exact PR head commit, in `<work_dir>/worktrees/<owner>-<repo>/pr-<n>-<sha>`. Agents review there. ReviewBot reuses one only if it is still at that commit, and never removes them; see [Remove old worktrees](../how-to/remove-old-worktrees.md).

**Review scope**
: The changes an agent reviews: the PR head compared with the base branch's freshly fetched remote-tracking ref (`origin/<base>`), using a three-dot diff. See [How the review scope is pinned](../explanation/review-scope.md).

## The queues

**Watch** (scope)
: Which repositories ReviewBot looks at: the owners and `OWNER/REPO` entries in `watch`, or `"*"` for everything the `gh` account can see. `--watch` and `--all` replace it for one run. See [Choose which repositories to watch](../how-to/choose-what-to-watch.md).

**Queue**
: A list of PRs in the watched repositories: **Review requests**, the open PRs that request your review, or **My PRs**, your own open PRs. The TUI shows each in a tab.

**Queue status**
: The one word that sums up a PR's state in a queue, such as `ready`, `updated` or `in-progress` for a review request, and `re-review` or `waiting` for one of yours. See [Queue statuses](statuses.md).

**Actionable**
: A review request you can launch: status `ready` or `updated`, and no review tab open for it.

## Prompts

**Bundled prompt template**
: A prompt that ships inside ReviewBot, under `src/reviewbot/_resources/prompts/`. Changing it changes only new installations.

**Installed prompt** (prompt directory)
: The copies `reviewbot init` writes into the prompt directory, `~/.config/reviewbot/prompts/` by default. These are the files agents actually get. Upgrading ReviewBot does not change them, so a change to a bundled template reaches an existing installation only when its copy is refreshed. See [Prompts installed by an older version](../how-to/write-prompts.md#prompts-installed-by-an-older-version).

**Placeholder**
: A `{name}` in a prompt, such as `{title}` or `{head_sha}`, that ReviewBot fills in when it sends the prompt. Any other `{...}` text is left as it is. See [Prompt templates and placeholders](prompts.md).

## Serving

**Dashboard**
: The web page `reviewbot serve` (or the TUI's `ctrl+w`) serves: the queues, each PR a link. See [Serve the dashboard and MCP endpoint](../how-to/serve-the-dashboard.md).

**Serve token**
: The secret in `~/.config/reviewbot/serve-token` that every dashboard and HTTP MCP request needs. `reviewbot token` prints it and the login URL. See [The security model of `serve`](../explanation/security-model.md).

**Board**
: The cached copy of the queues that the dashboard and MCP tools read, refreshed from GitHub when it is two minutes old. When the TUI hosts the server, the TUI's own fetches fill it.
