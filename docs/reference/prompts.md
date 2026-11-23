# Prompt templates and placeholders

## Templates

`reviewbot init` writes these templates into `~/.config/reviewbot/prompts/`:

| Template | Use |
| --- | --- |
| `review.md` | A general review: test coverage, correctness, security and regressions. |
| `tests.md` | Test coverage only. |
| `security.md` | Security only. |
| `walkthrough.md` | A combat `opener_prompt`: what the PR does, what it gets right, where to look. |
| `challenge.md` | A combat `challenge_prompt`: confirm, dispute or add to other reviews. |
| `verdict.md` | A combat `judge_prompt`: merge everything into one final review. |

Each template tells the agent not to edit, push, comment or submit a review.

## Placeholders

ReviewBot fills in these placeholders. It leaves any other `{...}` text as it is.

| Placeholder | Value |
| --- | --- |
| `{key}` | `owner/repo#number` on GitHub; `host/group/project!number` on GitLab. |
| `{forge}` | `GitHub` or `GitLab`. |
| `{change}`, `{change_abbr}` | `pull request` and `PR` on GitHub; `merge request` and `MR` on GitLab. |
| `{repo}`, `{number}`, `{title}`, `{author}`, `{url}` | The identity of the PR. |
| `{head_sha}`, `{head_branch}`, `{base_branch}` | The checked-out commit and the branches. |
| `{request_source}` | `direct`, or `team:<slug>`. |
| `{review_decision}`, `{checks}` | GitHub's review decision and check rollup. |
| `{previous_review}`, `{commits_since_review}` | Your last review, and the number of commits since. |
| `{agent}` | The configured name of the agent. |
| `{prior_reviews}` | In combats, the earlier reviews that this step answers. Otherwise `none`. |

## Text that ReviewBot adds

- When ReviewBot collects a review, it adds one instruction at the end. The instruction names the file for the review.
- When a step answers earlier reviews and the template has no `{prior_reviews}`, ReviewBot adds them under "Earlier reviews of this PR".
