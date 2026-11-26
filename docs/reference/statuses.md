# Queue statuses

## Open PRs

| Status | Meaning |
| --- | --- |
| `ready` | You have not reviewed the PR yet. |
| `updated` | You reviewed an older commit, and there are new commits since. |
| `reviewed` | You already reviewed the current head commit. |
| `changes-requested` | You requested changes, and nobody has pushed since. |
| `draft` | The PR is a draft. |
| `in-progress` | A review tab for the PR is open in Herdr or Rex. |

You can launch only `ready` and `updated` PRs. With the `involves` and `all` filters, that includes PRs that do not ask for your review.

The **Request** column says how your review was asked for: `direct`, `team:<slug>`, or `none` when the PR does not ask for it.

## My PRs

My PRs lists the open PRs you authored or are assigned to. The **Role** column says `author`, `assignee`, or both.

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
