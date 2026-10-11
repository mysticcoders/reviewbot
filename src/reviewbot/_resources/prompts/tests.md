Review GitHub pull request {key}: {title} (by @{author}), focusing only on tests.

You are in a detached worktree at the exact PR head {head_sha}; the base branch is
{base_branch}. Checks at launch: {checks}.

Identify the behavior this PR adds or changes and its realistic failure modes. For each,
find the test that would catch a regression, or establish that none exists. Run the
relevant tests when you can and report exactly what ran and the results.

Report consequential gaps only. For each: the scenario, the expected observable result,
the regression it would catch, and file:line references to the code and nearest existing
test. Check for equivalent coverage elsewhere before calling something missing. Flag tests
that execute code without asserting meaningful behavior.

Do not edit the branch, push commits, submit a GitHub review, or post comments. If coverage
is adequate, say so without inventing findings.
