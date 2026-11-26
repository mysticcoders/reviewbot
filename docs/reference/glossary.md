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
: A `[[combats]]` entry: two or more agents that review a PR and answer each other's reviews, for a number of `rounds`. In `parallel` style, every agent first reviews on its own, then each round every agent answers the others. In `handoff` style, one agent writes the first review, then agents take turns, each answering every earlier review by the others. See [How combat runs work](../explanation/combat-runs.md).

**Opener**
: The agent a combat runs first, before the reviews, if it sets `opener`. It writes a **walkthrough**: what the PR does, what it gets right, where to look. The combatants receive it as context.

**Challenge** (rebuttal)
: A combat step in which an agent answers the other agents' reviews: it confirms, disputes, or adds to them.

**Judge**
: The agent a combat runs last, if it sets `judge`. It writes the **verdict**: a recommendation (approve, approve with follow-ups, or request changes) with its rationale, then the agreed findings, the resolved disputes, and the findings that did not hold up.

**Round**
: In a combat, one pass of challenges. In `parallel` style, every agent answers the others' latest reviews in each round. In `handoff` style, each round is one agent's challenge step.

**Launcher**
: What a launch names: an agent, a group, or a combat. `--agent` accepts any of them, and each can have a TUI key.

**Launch**
: Starting a launcher on one PR: check the prompts, check out the PR, open the tabs, and send the prompts or start the runner. See [What a launch does](../explanation/what-a-launch-does.md).

**Plan**
: The phases and steps a launch turns into. A single agent is one phase of one step. A group is one phase with a step per agent. A combat has a phase for the first reviews and one per round, plus a phase for the opener before them and one for the judge after them when it sets those. The opener and judge phases are one step each. In `parallel` style, the first reviews and each round have a step per agent; in `handoff` style, one.

**Phase**
: Steps that run at the same time. The next phase starts when every step of the current one has succeeded; if a step fails, for example because the agent wrote no review, the run stops there.

**Step**
: One prompt to one agent, in one role (`review`, `walkthrough`, `challenge` or `verdict`), with the earlier steps it is given to read.

**Turn**
: One prompt sent to an agent, and the agent's work until its multiplexer reports that it is done. A step is one turn, or two if the agent must be reminded to write its review.

**Run**
: One launch that the runner drives: its plan, a state file, a log, and the reviews it collected, kept in `<work_dir>/runs/<run>/`. `reviewbot runs` lists them.

**Runner** (background runner)
: The detached `reviewbot run-plan` process that drives a run: it sends each step's prompt, waits for the turn to end, collects the review the agent wrote, and moves on to the next phase. Combats always use it. With `[output]`, every launch does.

**Saved review**
: A finished review archived in `[output]` `dir`, with metadata, plus a line in `index.jsonl`. Without `[output]` there is no archive or index, though a run still keeps a copy of each review it collected in its run directory. See [Save finished reviews](../how-to/save-reviews.md).

## Where agents run

**Multiplexer**
: The terminal workspace that hosts the agents: Herdr or Rex, set by `multiplexer` in the config. ReviewBot never talks to an agent directly; it opens a tab, starts the agent there, and sends prompts through the multiplexer, which also says when a turn is over. See [How ReviewBot drives agents in Herdr and Rex](../explanation/multiplexers.md).

**Tab**
: The multiplexer tab one agent works in, named `<owner>/<repo> #<n> <sha> <agent> review`, where `<sha>` is the first seven characters of the head commit. Each agent keeps one tab for a whole run, so you can watch or step in. In Rex, a tab is a window and the agent's pane is a block, which ReviewBot addresses by its block ID.

**Clone** (cached clone)
: The repository copy ReviewBot fetches into: a `[[checkouts]]` clone of yours, or its own clone in `<work_dir>/repos/`. ReviewBot never changes its checked-out branch.

**Worktree**
: A detached Git worktree at the exact PR head commit, in `<work_dir>/worktrees/<owner>-<repo>/pr-<n>-<sha>`, where `<sha>` is the first seven characters of that commit. Agents review there. ReviewBot reuses one only if its full head commit still matches, and never removes them; see [Remove old worktrees](../how-to/remove-old-worktrees.md).

**Review scope**
: The changes an agent reviews: the PR head compared with the base branch's freshly fetched remote-tracking ref (`origin/<base>`), using a three-dot diff. See [How the review scope is pinned](../explanation/review-scope.md).

## The queues

**Watch** (scope)
: Which repositories ReviewBot looks at: the owners and `OWNER/REPO` entries in `watch`, or `"*"` for everything the `gh` account can see. `--watch` and `--all` replace it for one run. See [Choose which repositories to watch](../how-to/choose-what-to-watch.md).

**Queue**
: A list of PRs in the watched repositories: **Open PRs**, which by default are the open PRs that request your review, or **My PRs**, the open PRs you authored or are assigned to. The TUI shows each in a tab, beside a third tab of notifications.

**Filter**
: What a view shows inside the watched repositories, set in `[filters]`: `open` for Open PRs (`requested`, `involves`, `all` or `stale`), `mine` for My PRs, and `notifications` for the Notifications tab. The config is the only lasting source; `f` in the TUI, the dashboard's Filter menus and the MCP `filter` argument change one for a session. See [Filters](configuration.md#filters).

**Notification**
: An unread GitHub notification about a PR or issue in the watched repositories, shown in the TUI's Notifications tab, on the dashboard, and through MCP. In the TUI and on the dashboard, opening one also marks it read; through MCP, `open_pr` only opens it and `mark_notification_read` marks it. Marking read changes only your read state on GitHub.

**Queue status**
: The one word that sums up a PR's state in a queue, such as `ready`, `updated` or `in-progress` in Open PRs, and `re-review` or `waiting` in My PRs. See [Queue statuses](statuses.md).

**Actionable**
: An Open PRs row whose status is `ready` or `updated`, so it can be launched, whether or not it requests your review. A PR that already has a review tab open still shows in the TUI and on the dashboard, as `in-progress`. The TUI won't launch it again, and `reviewbot next` skips it. `reviewbot launch` doesn't check for an open tab on the PR, though the multiplexer still refuses a second tab for the same agent and commit.

## Prompts

**Bundled prompt template**
: A prompt that ships inside ReviewBot, under `src/reviewbot/_resources/prompts/`. It is the source `reviewbot init` copies from; agents never read it directly.

**Installed prompt** (prompt directory)
: The copies `reviewbot init` writes into the prompt directory, `~/.config/reviewbot/prompts/` by default. Agents get whatever file the config's `prompt` settings name, rendered with placeholders filled in; with the config `init` writes, that is these copies. Upgrading ReviewBot does not change them, so a change to a bundled template reaches an existing installation only when its installed copy is refreshed. See [Prompts installed by an older version](../how-to/write-prompts.md#prompts-installed-by-an-older-version).

**Placeholder**
: A `{name}` in a prompt, such as `{title}` or `{head_sha}`, that ReviewBot fills in when it sends the prompt. Any other `{...}` text is left as it is. See [Prompt templates and placeholders](prompts.md).

## Serving

**Dashboard**
: The web page `reviewbot serve` (or the TUI's `ctrl+w`) serves: the queues and your unread notifications, each a link. See [Serve the dashboard and MCP endpoint](../how-to/serve-the-dashboard.md).

**Serve token**
: The secret in `~/.config/reviewbot/serve-token` that every dashboard and HTTP MCP request needs. `reviewbot token` prints it and the login URL. See [The security model of `serve`](../explanation/security-model.md).

**Board**
: The cached copy of the queues and notifications that the dashboard and MCP tools read. Each list is fetched again when a refresh is asked for and it is more than two minutes old, or at once when the refresh is forced. When the TUI hosts the server, the TUI's own fetches fill it.
