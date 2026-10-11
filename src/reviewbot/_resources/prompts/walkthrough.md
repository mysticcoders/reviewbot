Walk through GitHub pull request {key}: {title} (by @{author}).

You are in a detached worktree at the exact PR head {head_sha}; the base branch is
{base_branch}. This is the opening pass before other reviewers weigh in, so your job is to
understand the change, not to hunt for defects.

Read the PR diff and enough surrounding code to explain it. Then write:

## What this PR does
The intent of the change and how it achieves it, in a few short paragraphs. Name the key
files and functions with file:line references.

## What it gets right
Specific choices that are sound and why: design, naming, test coverage, error handling.
Only real strengths, with evidence; no generic praise.

## Where to look closely
The two to five areas where a defect would most likely hide or hurt most, with file:line
references and the reason each deserves scrutiny. Do not assert bugs you have not verified.

Do not edit the branch, push commits, submit a GitHub review, or post comments.
