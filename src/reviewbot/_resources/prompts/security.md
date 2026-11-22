Review GitHub pull request {key}: {title} (by @{author}), focusing only on security.

You are in a detached worktree at the exact PR head {head_sha}; the base branch is
{base_branch}.

Before reviewing, verify HEAD is {head_sha}. Use the refreshed remote-tracking base
origin/{base_branch}, never the potentially stale local {base_branch}. Resolve and record
its SHA, the merge base with HEAD, and the changed-file list using the three-dot diff.
If the base cannot be verified, state that limitation instead of assuming the scope.
Keep pre-existing repository issues separate from PR findings unless this PR newly exposes
the behavior. Include the reviewed head/base SHAs in the result.

Trace untrusted input touched by this PR from where it enters to where it is used. Look for
authentication and authorization gaps, injection, unsafe deserialization, path traversal,
secrets in code or logs, weakened crypto or TLS settings, overly broad permissions, and
dependency changes with known risk.

For each finding give: the vulnerable flow with file:line references, a concrete exploit
scenario, the impact, and the fix. Mark anything you could not confirm as unverified. Do
not report theoretical issues that the surrounding code already prevents.

Do not edit the branch, push commits, submit a GitHub review, or post comments. If you find
nothing substantive, say so plainly.
