# Queue statuses

## Review requests

| Status | Meaning |
| --- | --- |
| `ready` | You have not reviewed the PR yet. |
| `updated` | You reviewed an older commit, and there are new commits since. |
| `reviewed` | You already reviewed the current head commit. |
| `changes-requested` | You requested changes, and nobody has pushed since. |
| `draft` | The PR is a draft. |
| `in-progress` | A review tab for the PR is open in Herdr or Rex. |

You can launch only `ready` and `updated` PRs.

## My PRs

A PR shows the first status that applies, in this order:

| Status | Meaning |
| --- | --- |
| `draft` | The PR is a draft. |
| `conflicts` | The PR has merge conflicts. |
| `re-review` | A person (not a team) requested changes, or reviewed an older commit. You can request review from them again. |
| `changes-requested` | Changes are requested, and nobody can be asked again. |
| `checks-failing` | One or more checks fail. |
| `needs-reviewers` | Nobody has reviewed the PR or been asked to. |
| `approved` | GitHub reports the PR as approved. |
| `waiting` | The PR waits for the requested reviews. |

One exception to the order: when nobody requested changes, a stale review gives `re-review` only if no status from `checks-failing` to `approved` applies.

## GitLab merge requests

GitLab does not record which commit a review was for. So ReviewBot compares times:

- Your review is your latest approval or change request on the MR.
- `updated` means that someone pushed to the MR after your review.
- `changes-requested` means that your latest review requested changes, and nobody pushed since.
- `reviewed` means that your latest review was an approval, and nobody pushed since.
- `ready` means that you have not reviewed the MR.

Not included yet:

- Approval rules. An MR asks for your review only when it lists you as a reviewer.
- For MRs, the prompt placeholder `{review_decision}` is `none`, and `{commits_since_review}` is `0`.
