# Saved reviews

With an `[output]` table, ReviewBot keeps every finished review. This page describes what it writes, so other tools, such as a retrospective that reads reviews alongside session transcripts and pull request history, can rely on it. To turn saving on or off, see [Save finished reviews](../how-to/save-reviews.md).

## Layout

```text
<dir>/
  index.jsonl
  <owner>-<repo>/
    pr-<n>/
      <run>/
        <step>-<agent>-<role>.md
```

- `<dir>` is `[output]` `dir`.
- `<run>` names one launch: `<owner>-<repo>-<n>-<UTC time>`, such as `acme-api-412-20261125T101500Z`.
- `<step>` is the two-digit step number within the run. A single agent writes step `01`. A group writes one step per agent. A combat writes one step per turn, in order.
- `<role>` is `review`, `walkthrough` (a combat opener), `challenge` (a combat rebuttal) or `verdict` (a combat judge).

## One review file

Each file is Markdown with front matter between `---` lines. Each front matter line is `key: value`, where the value is JSON: strings are quoted, numbers are not. The agent's review follows, as the agent wrote it.

| Field | Type | Meaning |
| --- | --- | --- |
| `format` | number | The version of this format, now `1`. |
| `repo` | string | `owner/repo`. |
| `pr` | number | The pull request number. |
| `url` | string | The pull request's web address. |
| `title` | string | The pull request title at launch. |
| `author` | string | The pull request author's login. |
| `base_branch` | string | The branch the pull request merges into. |
| `head_sha` | string | The full commit SHA that was reviewed. |
| `run` | string | The run name, as in the folder. |
| `launcher` | string | The agent, group or combat that was launched. |
| `step` | number | The step number within the run, from 1. |
| `agent` | string | The agent's name from the config. |
| `kind` | string | The agent's kind, such as `claude` or `codex`. |
| `role` | string | `review`, `walkthrough`, `challenge` or `verdict`. |
| `round` | number | The combat round, `0` outside the rebuttal rounds. |
| `saved_at` | string | When the review was saved, ISO 8601 in UTC. |

## The index

`index.jsonl` gets one JSON object per saved review, appended as it is saved. Each object has every front matter field, plus `path`, the absolute path of the review file. ReviewBot only appends to it, so it doubles as an audit log of which agents reviewed which commit, and when.

To list the reviews of one pull request, oldest first:

```sh
jq -c 'select(.repo == "acme/api" and .pr == 412) | {saved_at, agent, role, head_sha}' ~/reviewbot-reviews/index.jsonl
```

## Recurring patterns

The bundled `review.md` prompt asks the agent to end each review with a section titled **Recurring patterns**: findings likely to happen again beyond that pull request, each with a proposed deterministic rule (a lint rule, a CI check or a test). A retrospective can collect these sections across reviews to find the rules worth adding. Prompts installed before this section existed don't ask for it; see [Prompts installed by an older version](../how-to/write-prompts.md#prompts-installed-by-an-older-version).

## Stability

Fields are only added, never renamed or removed, without a new `format` number. Files saved before `format` existed have no `format`, `author` or `base_branch` fields; treat a missing `format` as version 0, which otherwise has the same fields. Read fields by name, and ignore fields you don't know.
