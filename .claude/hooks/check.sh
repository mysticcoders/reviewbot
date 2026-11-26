#!/bin/sh
# Claude Code Stop hook: run `just check` before an agent finishes a turn that left
# changes in the working tree, and hand any failure back to the agent.
#
# It blocks once. When the agent stops again after being blocked (stop_hook_active is
# true), the stop goes through, so a failure the agent cannot fix never traps it in a
# loop; CI still runs the same checks on the pull request.
set -u

input=$(cat)
if printf '%s' "$input" | grep -Eq '"stop_hook_active"[[:space:]]*:[[:space:]]*true'; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
if [ -z "$(git status --porcelain -- src tests pyproject.toml uv.lock justfile 2>/dev/null)" ]; then
  exit 0
fi

if command -v just >/dev/null 2>&1; then
  output=$(just check 2>&1)
else
  output=$( { uv run ruff check src tests && uv run pytest -q; } 2>&1)
fi
status=$?
if [ "$status" -ne 0 ]; then
  {
    echo "just check failed. Fix it before finishing, or explain why it cannot pass:"
    echo
    printf '%s\n' "$output" | tail -n 60
  } >&2
  exit 2
fi
exit 0
