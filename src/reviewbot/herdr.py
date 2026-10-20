"""Herdr backend: launch interactive agents in Herdr tabs and drive them by agent name."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .config import Agent, Config
from .github import ReviewCandidate
from .launch import LaunchError, LaunchResult, review_marker, run_command, tab_label

_run = run_command


def _pane_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        pane_id = value.get("pane_id")
        if isinstance(pane_id, str):
            found.add(pane_id)
        for child in value.values():
            found.update(_pane_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_pane_ids(child))
    return found


def _snapshot() -> dict[str, Any]:
    raw = _run("herdr", "api", "snapshot")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise LaunchError(f"Herdr returned an invalid session snapshot: {error}") from error


def _snapshot_panes() -> set[str]:
    return _pane_ids(_snapshot())


def _existing_tab_pane(label: str) -> str | None:
    """Return the shell pane from an earlier interrupted launch, if present."""
    snapshot = _snapshot().get("result", {}).get("snapshot", {})
    tab_ids = {
        tab.get("tab_id")
        for tab in snapshot.get("tabs", [])
        if tab.get("label") == label
    }
    matching = [
        pane for pane in snapshot.get("panes", [])
        if pane.get("tab_id") in tab_ids
    ]
    if not matching:
        return None
    if len(matching) != 1:
        raise LaunchError(f"Herdr has multiple panes for existing tab `{label}`")
    pane = matching[0]
    if pane.get("agent"):
        raise LaunchError(f"Herdr tab `{label}` already has an agent running")
    return pane.get("pane_id")


AGENT_NAME_MAX = 32


def _agent_name(repo: str, number: int, agent: str) -> str:
    """Build a Herdr agent name: lowercase letters, digits, '-' or '_', 32 characters max."""
    suffix = f"-{number}-{agent}"
    slug = re.sub(r"[^a-z0-9_-]+", "-", repo.rsplit("/", 1)[-1].lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-_")
    room = AGENT_NAME_MAX - len("review-") - len(suffix)
    slug = slug[:max(room, 0)].rstrip("-_")
    return f"review-{slug}{suffix}" if slug else f"review{suffix}"


def _start_agent_with_retry(agent_name: str, agent: Agent, pane_id: str) -> None:
    """Wait through Herdr's short new-tab shell initialization window."""
    deadline = time.monotonic() + 20
    while True:
        try:
            command = [
                "herdr", "agent", "start", agent_name, "--kind", agent.kind,
                "--pane", pane_id, "--timeout", "120000",
            ]
            if agent.args:
                command.extend(["--", *agent.args])
            _run(*command, timeout=130)
            return
        except LaunchError as error:
            if "agent_pane_busy" not in str(error) or time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def active_review_keys(config: Config, candidates: list[ReviewCandidate]) -> set[str]:
    """Best-effort mapping of live Herdr review tabs back to queue entries."""
    try:
        raw = _run("herdr", "api", "snapshot")
    except LaunchError:
        return set()
    return {candidate.key for candidate in candidates if review_marker(candidate) in raw}


def open_session(
    config: Config, candidate: ReviewCandidate, *, agent: Agent, checkout: Path
) -> LaunchResult:
    """Open (or reuse) the PR's Herdr tab for this agent and start the agent in it."""
    label = tab_label(candidate, agent.name)
    pane_id = _existing_tab_pane(label)
    if pane_id is None:
        before = _snapshot_panes()
        command = ["herdr", "tab", "create"]
        if config.herdr_workspace:
            command.extend(["--workspace", config.herdr_workspace])
        command.extend(["--cwd", str(checkout), "--label", label, "--focus"])
        _run(*command)
        created = _snapshot_panes() - before
        if len(created) != 1:
            raise LaunchError(
                f"expected Herdr to create one pane, but found {len(created)}; "
                f"tab `{label}` was created"
            )
        pane_id = created.pop()
    agent_name = _agent_name(candidate.repo, candidate.number, agent.name)
    _start_agent_with_retry(agent_name, agent, pane_id)
    return LaunchResult(agent=agent.name, tab_label=label, checkout=checkout, handle=agent_name)


def send_prompt(handle: str, text: str) -> None:
    _run("herdr", "agent", "prompt", handle, text)


def prompt_and_wait(handle: str, text: str) -> None:
    """Send a prompt and block until the agent finishes that turn.

    Herdr reports `blocked` when an agent waits on the user (for example a permission
    prompt), so after the first settled state keep waiting for idle or done.
    """
    _run("herdr", "agent", "prompt", handle, text, "--wait", timeout=None)
    _run(
        "herdr", "agent", "wait", handle, "--until", "idle", "--until", "done",
        timeout=None,
    )
