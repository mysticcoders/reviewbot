#!/bin/sh
# Claude Code Stop hook: run `just check` before an agent finishes a turn that left
# changes in the working tree, and hand any failure back to the agent.
#
# When it blocks, it records a fingerprint of the checked files for the session. If
# the agent stops again without changing them, the stop goes through: the agent has
# seen the failure and decided it cannot fix it, and CI still runs the same checks on
# the pull request. If the agent changed them, they are checked again. At most
# MAX_BLOCKS stops per session are blocked, so a failure that keeps changing shape
# cannot trap the agent either.
set -u

MAX_BLOCKS=3
PATHS="src tests pyproject.toml uv.lock justfile"

input=$(cat)
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

session=$(printf '%s' "$input" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | tr -cd 'A-Za-z0-9_-')
state_dir="$(git rev-parse --git-dir)/reviewbot-stop-hook"
state="$state_dir/${session:-unknown}"

# shellcheck disable=SC2086
if [ -z "$(git status --porcelain -- $PATHS 2>/dev/null)" ]; then
  rm -f "$state"
  exit 0
fi

# The fingerprint covers tracked changes and the contents of untracked files.
# shellcheck disable=SC2086
fingerprint=$(
  {
    git diff HEAD -- $PATHS
    git ls-files --others --exclude-standard -z -- $PATHS | xargs -0 cat 2>/dev/null
  } | git hash-object --stdin
)

blocks=0
if [ -f "$state" ]; then
  read -r last_fingerprint blocks < "$state" || true
  if [ "${last_fingerprint:-}" = "$fingerprint" ]; then
    rm -f "$state"
    exit 0
  fi
  case "${blocks:-}" in ''|*[!0-9]*) blocks=0 ;; esac
  if [ "$blocks" -ge "$MAX_BLOCKS" ]; then
    rm -f "$state"
    exit 0
  fi
fi

if command -v just >/dev/null 2>&1; then
  output=$(just check 2>&1)
else
  output=$( { uv run ruff check src tests && uv run pytest -q; } 2>&1)
fi
status=$?
if [ "$status" -eq 0 ]; then
  rm -f "$state"
  exit 0
fi

mkdir -p "$state_dir"
printf '%s %s\n' "$fingerprint" "$((blocks + 1))" > "$state"
{
  echo "just check failed. Fix it before finishing. If it cannot pass, explain why and stop"
  echo "again without changing src, tests, pyproject.toml, uv.lock or justfile."
  echo
  printf '%s\n' "$output" | tail -n 60
} >&2
exit 2
