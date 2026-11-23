"""Herdr backend: launch interactive agents in Herdr tabs and drive them by agent name."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from .config import Agent, Config
from .launch import LaunchError, LaunchResult, review_marker, run_command, tab_label
from .models import ReviewCandidate

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


def _tabs() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    snapshot = _snapshot().get("result", {}).get("snapshot", {})
    return snapshot.get("tabs", []), snapshot.get("panes", [])


def _existing_tab_pane(label: str, checkout: Path) -> str | None:
    """Return the shell pane from an earlier interrupted launch, if present.

    The label names the head commit, so a matching tab was opened for this checkout; the
    pane's directory is still checked, since its shell may have moved elsewhere.
    """
    tabs, panes = _tabs()
    tab_ids = {tab.get("tab_id") for tab in tabs if tab.get("label") == label}
    matching = [pane for pane in panes if pane.get("tab_id") in tab_ids]
    if not matching:
        return None
    if len(matching) != 1:
        raise LaunchError(f"Herdr has multiple panes for existing tab `{label}`")
    pane = matching[0]
    if pane.get("agent"):
        raise LaunchError(f"Herdr tab `{label}` already has an agent running")
    cwd = pane.get("cwd")
    if cwd and Path(cwd).resolve() != checkout.resolve():
        raise LaunchError(
            f"Herdr tab `{label}` is in {cwd}, not the PR checkout {checkout}; close it and retry"
        )
    return pane.get("pane_id")


AGENT_NAME_MAX = 32


def _agent_name(candidate: ReviewCandidate, agent: str) -> str:
    """Build a Herdr agent name unique to the repository, PR, head commit, and agent.

    Herdr allows lowercase letters, digits, '-' or '_', 32 characters max, so the readable
    part is trimmed to fit and a short hash of the full identity keeps names distinct
    across owners and commits.
    """
    identity = f"{candidate.repo}#{candidate.number}@{candidate.head_sha}/{agent}"
    digest = hashlib.sha1(identity.encode()).hexdigest()[:6]
    slug = re.sub(r"[^a-z0-9_-]+", "-", candidate.repo.rsplit("/", 1)[-1].lower())
    readable = re.sub(r"-{2,}", "-", f"review-{slug}-{candidate.number}-{agent}")
    readable = readable[:AGENT_NAME_MAX - len(digest) - 1].rstrip("-_")
    return f"{readable}-{digest}"


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
        tabs, _ = _tabs()
    except LaunchError:
        return set()
    labels = [tab.get("label") or "" for tab in tabs]
    return {
        candidate.key for candidate in candidates
        if any(label.startswith(review_marker(candidate)) for label in labels)
    }


def open_session(
    config: Config, candidate: ReviewCandidate, *, agent: Agent, checkout: Path
) -> LaunchResult:
    """Open (or reuse) the PR's Herdr tab for this agent and start the agent in it."""
    label = tab_label(candidate, agent.name)
    pane_id = _existing_tab_pane(label, checkout)
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
    agent_name = _agent_name(candidate, agent.name)
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
