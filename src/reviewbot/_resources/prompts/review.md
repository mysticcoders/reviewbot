Review GitHub pull request {key}: {title} (by @{author}).

You are in a detached worktree at the exact PR head {head_sha}. The base branch is
{base_branch}. Inspect the PR diff and relevant repository code and tests. Focus
the review on whether meaningful tests protect the behavior this PR adds or changes.

Identify the intended behavior and realistic failure modes, then check existing tests
for coverage of the important success paths, edge cases, error handling, and regressions.
Evaluate whether assertions would catch incorrect behavior, rather than merely execute
the code or mirror its implementation. Run relevant tests when useful and report what
ran, the results, and any verification limitations.

Prioritize consequential gaps in test coverage. For each gap, describe the concrete
scenario, expected observable result, and regression the proposed test should catch,
with precise file and line references to the relevant behavior or existing tests.
Check for equivalent coverage elsewhere before requesting a new test. Do not demand
tests for every changed line or flag missing tests without explaining the risk.

Also report substantive correctness, security, and regression issues supported by
evidence. Where practical, pair each code issue with a regression test scenario.
Distinguish demonstrated bugs from unverified risks and coverage gaps; a missing test
alone does not prove the implementation is wrong. Omit style nitpicks, personal code
preferences, speculative refactors, and low-impact implementation critiques.

GitHub status at launch:
- review requested via: {request_source}
- review decision: {review_decision}
- checks: {checks}
- my previous review: {previous_review}
- commits since my previous review: {commits_since_review}

Do not edit the branch, push commits, submit a GitHub review, or post comments automatically.
Present a concise review to me in this session so I can decide what to submit, leading
with the highest-impact findings and concrete test recommendations. If coverage is
adequate and there are no substantive issues, say so without inventing findings.
