# Write review prompts

A prompt is a plain text or Markdown file. ReviewBot sends it to the agent after the agent starts in the PR worktree.

## Start from a template

`reviewbot init` writes templates into `~/.config/reviewbot/prompts/`. Change them freely, or add your own. For the list of templates, see [Prompt templates and placeholders](../reference/prompts.md).

The bundled templates tell the agent not to edit, push, comment or submit a review. Keep that instruction in your own prompts.

## Use placeholders

Write `{name}` to insert a value, for example `{title}` or `{head_sha}`. To make one prompt work for GitHub and GitLab, write `{forge}`, `{change}` and `{change_abbr}` in place of "GitHub", "pull request" and "PR". ReviewBot leaves any other `{...}` text as it is. For the full list, see [Placeholders](../reference/prompts.md#placeholders).

## Point an agent at a prompt

- Set the top-level `prompt` for every agent that has no prompt of its own.
- Set `prompt` in an `[[agents]]` table for that agent only.

ReviewBot resolves relative paths against the directory of the config file.

## What you do not need to write

- **Where to write the review.** When ReviewBot collects a review, it adds one instruction that names the output file.
- **The earlier reviews.** If a combat step answers earlier reviews and the template has no `{prior_reviews}`, ReviewBot adds them under "Earlier reviews of this PR".

## Try a change

ReviewBot reads prompt files at each launch. Your edits apply to the next review. You do not need to restart the TUI.

## Prompts installed by an older version

`reviewbot init` copies the templates. An upgrade does not change the copies in your prompt directory.

To get the new bundled version of a template:

1. Move your copy aside, for example `mv review.md review.mine.md` in the prompt directory.
2. Run `reviewbot init`. It writes only the files that are missing, and keeps your config. If it asks what to watch, the answer has no effect when the config already exists.
3. Merge your changes from the old copy into the new file.

Do not use `reviewbot init --force` for this. It also replaces your config.
