# How combat runs work

A plain launch sends each agent its prompt, and then ReviewBot is done. A combat, and any launch while `[output]` is set, needs more: ReviewBot must wait for each turn and read the review before it starts the next turn. A background process does this work.

## The background runner

The launch writes a plan and starts a detached `reviewbot run-plan` process. The process runs separately from the TUI, so closing the TUI does not stop a run.

The plan is a list of phases. The steps in one phase run at the same time. For each step, the runner does these things:

1. It opens the agent's tab, if the agent does not have one yet. Each agent keeps one tab and one session for the whole combat.
2. It sends the prompt, with the earlier reviews that the step answers.
3. It waits until the agent's turn is over. This includes the time that the agent waits for you, for example at a permission prompt.
4. It reads the review that the agent wrote.

## Where reviews are written

Each prompt tells the agent to write its review to `.reviewbot/<run>/` in the worktree. That directory contains a `.gitignore` that ignores everything in it, so Git ignores it.

If an agent ends its turn without writing the review, the runner reminds it once. If the review is still missing or empty, the runner stops the run and marks it failed.

## Run state

Each run has a directory under `<work_dir>/runs/`. It holds the plan, a state file, a log, and a copy of each review. `reviewbot runs` reads the state files.

With `[output]`, the runner also saves each review there. See [Save finished reviews](../how-to/save-reviews.md).

## Known limits

- The runner waits for a turn without a time limit. A stuck agent leaves the run in `running`.
- The runner does not record its process ID. If the runner dies, the run stays `running`, and there is no command to cancel or resume it.
