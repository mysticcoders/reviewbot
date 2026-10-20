"""Rex backend: launch interactive agents in Rex tabs and drive them by block ID.

Agents report their state to Rex with the OSC 7501 program status protocol (Claude Code
and Codex both do). The waiting is done by two bundled Lua scripts run with `rex do`,
which register their status handlers before acting, so no status change is missed:
`launch.lua` opens the tab and waits until the agent reports ready, and `prompt.lua`
pastes a prompt and waits until the status goes `working` or `blocked` and settles on
`idle` or `done`.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Iterator
from importlib import resources
from pathlib import Path
from typing import Any

from .config import Agent, Config
from .github import ReviewCandidate
from .launch import LaunchError, LaunchResult, review_marker, run_command, tab_label

_run = run_command

READY_TIMEOUT = 120
EXITED, AGENT_ERROR, NOT_READY = 3, 2, 4


def _json(*args: str) -> Any:
    raw = _run(*args)
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError as error:
        raise LaunchError(f"Rex returned invalid JSON from {' '.join(args[:3])}: {error}") from error


def _session(config: Config) -> str:
    """The configured Rex session, or the one ReviewBot is running in."""
    session = config.rex_session or os.environ.get("REX_SESSION")
    if not session:
        raise LaunchError(
            "no Rex session to open review tabs in: set `rex_session` in the config "
            "or run ReviewBot inside Rex"
        )
    return session


def _view(session: str) -> dict[str, Any]:
    return _json("rex", "api", "call", "-s", session, "session.view")


def _windows(view: dict[str, Any]) -> Iterator[tuple[dict[str, Any], list[str]]]:
    """Each window in a session view with the IDs of the blocks it holds."""
    for window in view.get("windows", []):
        blocks = [
            block["block_id"]
            for layer in window.get("layers", [])
            for block in layer.get("blocks", [])
        ]
        yield window, blocks


def _exited(block: str) -> Any:
    """The block program's exit details once it has ended, else None."""
    return _json("rex", "block", "call", "-b", block, "process").get("last_exit")


def _clear_finished_tab(session: str, label: str) -> None:
    """Close a tab left by an earlier launch whose agent has exited; refuse a live one."""
    for window, blocks in _windows(_view(session)):
        if window.get("label") != label:
            continue
        if any(_exited(block) is None for block in blocks):
            raise LaunchError(f"Rex tab `{label}` already has an agent running")
        _run("rex", "window", "close", "-s", session, window["window_id"])


def _script(
    name: str, target: tuple[str, str], args: dict[str, Any], timeout: float | None
) -> tuple[int, str]:
    """Run a bundled Lua script with `rex do` against a session or block.

    Arguments go through a JSON file so long prompts stay off the command line. Returns
    the exit status and stdout; a status of 1 (the script itself failed) raises.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(args, handle)
    script = resources.files("reviewbot").joinpath("_resources", "rex", name)
    try:
        with resources.as_file(script) as path:
            result = subprocess.run(
                ["rex", "do", *target, str(path), "--args", f"@{handle.name}"],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LaunchError(f"could not run Rex script {name}: {error}") from error
    finally:
        Path(handle.name).unlink(missing_ok=True)
    if result.returncode == 1:
        raise LaunchError(f"Rex script {name} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.returncode, result.stdout


def active_review_keys(config: Config, candidates: list[ReviewCandidate]) -> set[str]:
    """Best-effort mapping of live Rex review tabs, in any session, back to queue entries."""
    try:
        sessions = _json("rex", "ls", "--json").get("sessions", [])
        labels = [
            window.get("label") or ""
            for session in sessions
            for window, _ in _windows(_view(session["session_id"]))
        ]
    except (LaunchError, KeyError):
        return set()
    return {
        candidate.key for candidate in candidates
        if any(label.startswith(review_marker(candidate)) for label in labels)
    }


def open_session(
    config: Config, candidate: ReviewCandidate, *, agent: Agent, checkout: Path
) -> LaunchResult:
    """Open the PR's Rex tab for this agent running its command, and wait until it is ready."""
    if not agent.command:
        raise LaunchError(f"agent `{agent.name}` has no `command` for Rex to run")
    session = _session(config)
    label = tab_label(candidate, agent.name)
    _clear_finished_tab(session, label)
    code, output = _script(
        "launch.lua", ("-s", session),
        {
            "label": label, "cwd": str(checkout), "command": [agent.command, *agent.args],
            "timeout": READY_TIMEOUT,
        },
        timeout=READY_TIMEOUT + 30,
    )
    block = next(
        (line.split(" ", 1)[1] for line in output.splitlines() if line.startswith("block ")), None,
    )
    if block is None:
        raise LaunchError(f"Rex did not report a pane for new tab `{label}`")
    if code == EXITED:
        raise LaunchError(f"the agent in Rex tab `{label}` exited before it was ready")
    if code == NOT_READY:
        raise LaunchError(
            f"the agent in Rex tab `{label}` did not report ready within {READY_TIMEOUT}s; "
            "answer any startup prompt in that tab (folder trust, hooks) and retry"
        )
    if code:
        raise LaunchError(f"Rex script launch.lua exited {code} for tab `{label}`")
    return LaunchResult(agent=agent.name, tab_label=label, checkout=checkout, handle=block)


def send_prompt(handle: str, text: str) -> None:
    """Paste a prompt into the agent's block and press Return without waiting."""
    code, _ = _script("prompt.lua", ("-b", handle), {"text": text, "wait": False}, timeout=60)
    if code:
        raise LaunchError(f"Rex script prompt.lua exited {code} for block {handle}")


def prompt_and_wait(handle: str, text: str) -> None:
    """Send a prompt and block until the agent's turn ends.

    The turn is over when the status goes `working` or `blocked` (a permission prompt or
    question, which waits on the user) and then settles on `idle` or `done`. A settled
    status before that, such as one re-sent while a startup dialog has focus, is ignored.
    `error`, or the agent exiting, fails the turn.
    """
    code, output = _script("prompt.lua", ("-b", handle), {"text": text, "wait": True}, None)
    if code == AGENT_ERROR:
        message = next(
            (line[6:] for line in output.splitlines() if line.startswith("error ")), "",
        )
        raise LaunchError(f"the agent in Rex block {handle} reported an error: {message}")
    if code == EXITED:
        raise LaunchError(f"the agent in Rex block {handle} exited mid-turn")
    if code:
        raise LaunchError(f"Rex script prompt.lua exited {code} for block {handle}")
