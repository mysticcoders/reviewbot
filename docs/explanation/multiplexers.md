# How ReviewBot drives agents in Herdr and Rex

ReviewBot does not talk to agents directly. It opens a tab in the multiplexer, starts the agent there, and sends prompts through the multiplexer. The multiplexer also tells ReviewBot when a turn is over.

## Herdr

Herdr has an agent registry. ReviewBot uses it:

1. It opens a tab in the worktree.
2. It runs `herdr agent start --kind <kind>` in that tab. The agent gets a name that is unique to the repository, PR, head commit and agent.
3. It sends prompts with `herdr agent prompt`, and waits with `herdr agent wait`.

Herdr reports `blocked` when an agent waits for you, for example at a permission prompt. So after a turn first settles, ReviewBot keeps waiting until the agent is `idle` or `done`.

Herdr knows an agent kind only after you install its integration, for example `herdr integration install claude`.

If an interrupted launch left a tab with the same name, ReviewBot reuses its shell pane. It refuses if an agent already runs in the pane, or if the shell is no longer in the worktree.

## Rex

Rex has no agent registry. Instead, agents report their own state with the OSC 7501 program status protocol. Claude Code and Codex both do this. An agent that never reports a status cannot be driven this way.

1. ReviewBot opens a tab that runs the agent's `command` and `args` in the worktree.
2. It addresses the agent by its block ID.
3. It sends each prompt as one bracketed paste followed by Return. So prompts with several lines arrive intact.

The state rules:

- An agent is ready when it reports `idle`.
- A turn is over when the status goes to `working` (or to `blocked`, which waits for you) and then settles on `idle` or `done`.
- `error`, or the agent exiting, fails the turn.

Two bundled Lua scripts do the waiting inside Rex. ReviewBot runs them with `rex do`:

- `launch.lua` opens the tab and waits until the agent is ready.
- `prompt.lua` pastes the prompt and waits until the turn is over.

Each script registers its status handlers before it acts. So it does not miss a fast turn.

Startup dialogs come before the agent reports `idle`. Examples are folder trust, and Codex's "hooks need review". Answer them in the new tab. ReviewBot waits up to two minutes for an agent to become ready.

If an earlier launch left a tab with the same name, ReviewBot closes it when its agent has exited. It refuses if the agent still runs.
