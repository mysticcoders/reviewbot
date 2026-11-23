Walk through {forge} {change} {key}: {title} (by @{author}).

You are in a detached worktree at the exact {change_abbr} head {head_sha}; the base branch is
{base_branch}. This is the opening pass before other reviewers weigh in, so your job is to
understand the change, not to hunt for defects.

Before reviewing, verify HEAD is {head_sha}. Use the refreshed remote-tracking base
origin/{base_branch}, never the potentially stale local {base_branch}. Resolve and record
its SHA, the merge base with HEAD, and the changed-file list using the three-dot diff.
If the base cannot be verified, state that limitation instead of assuming the scope.
Keep pre-existing repository issues separate from {change_abbr} findings unless this {change_abbr} newly exposes
the behavior. Include the reviewed head/base SHAs in the result.

Read the {change_abbr} diff and enough surrounding code to explain it. Then write:

## What this {change_abbr} does
The intent of the change and how it achieves it, in a few short paragraphs. Name the key
files and functions with file:line references.

## What it gets right
Specific choices that are sound and why: design, naming, test coverage, error handling.
Only real strengths, with evidence; no generic praise.

## Where to look closely
The two to five areas where a defect would most likely hide or hurt most, with file:line
references and the reason each deserves scrutiny. Do not assert bugs you have not verified.

Do not edit the branch, push commits, submit a {forge} review, or post comments.
