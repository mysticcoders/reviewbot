"""Prepare isolated PR checkouts and launch interactive agents in Herdr tabs."""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Agent, Config
from .github import ReviewCandidate


class LaunchError(Exception):
    pass


@dataclass(frozen=True)
class LaunchResult:
    agent: str
    tab_label: str
    pane_id: str
    checkout: Path
    agent_name: str


def _run(*args: str, timeout: float | None = 120) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LaunchError(f"could not run {' '.join(args)}: {error}") from error
    if result.returncode:
        raise LaunchError(result.stderr.strip() or f"{' '.join(args)} exited {result.returncode}")
    return result.stdout.strip()


def _repo_dir(config: Config, candidate: ReviewCandidate) -> Path:
    for checkout in config.checkouts:
        if checkout.slug.lower() == candidate.repo.lower() and (checkout.path / ".git").exists():
            return checkout.path
    return config.work_dir / "repos" / candidate.repo.replace("/", "-")


def prepare_checkout(config: Config, candidate: ReviewCandidate) -> Path:
    """Create or reuse a detached worktree at the exact requested PR SHA."""
    repo_dir = _repo_dir(config, candidate)
    if not repo_dir.exists():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        _run("gh", "repo", "clone", candidate.repo, str(repo_dir), timeout=600)
    if not (repo_dir / ".git").exists():
        raise LaunchError(f"repository cache is not a Git checkout: {repo_dir}")

    checkout = (
        config.work_dir / "worktrees" / candidate.repo.replace("/", "-")
        / f"pr-{candidate.number}-{candidate.head_sha[:7]}"
    )
    if checkout.exists():
        current_sha = _run("git", "-C", str(checkout), "rev-parse", "HEAD")
        if current_sha != candidate.head_sha:
            raise LaunchError(f"existing checkout has the wrong SHA: {checkout}")
        return checkout

    _run(
        "git", "-C", str(repo_dir), "fetch", "--force", "origin",
        f"pull/{candidate.number}/head", timeout=300,
    )
    fetched_sha = _run("git", "-C", str(repo_dir), "rev-parse", "FETCH_HEAD")
    if fetched_sha != candidate.head_sha:
        raise LaunchError(
            f"PR moved while launching ({candidate.head_sha[:7]} -> {fetched_sha[:7]}); refresh and retry"
        )

    checkout.parent.mkdir(parents=True, exist_ok=True)
    _run(
        "git", "-C", str(repo_dir), "worktree", "add", "--detach",
        str(checkout), fetched_sha, timeout=300,
    )
    return checkout


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


def active_review_keys(candidates: list[ReviewCandidate]) -> set[str]:
    """Best-effort mapping of live Herdr review tabs back to queue entries."""
    try:
        raw = _run("herdr", "api", "snapshot")
    except LaunchError:
        return set()
    active: set[str] = set()
    for candidate in candidates:
        marker = f"{candidate.repo.rsplit('/', 1)[-1]} #{candidate.number} "
        if marker in raw:
            active.add(candidate.key)
    return active


PLACEHOLDER = re.compile(r"\{(\w+)\}")


def prompt_fields(candidate: ReviewCandidate, agent: str) -> dict[str, str]:
    """Values available to prompt templates as `{name}` placeholders."""
    previous = "none"
    if candidate.last_review_state:
        previous = f"{candidate.last_review_state} at {(candidate.last_review_sha or '')[:7]}"
    return {
        "key": candidate.key, "repo": candidate.repo, "number": str(candidate.number),
        "title": candidate.title, "author": candidate.author, "url": candidate.url,
        "head_sha": candidate.head_sha, "head_branch": candidate.head_branch,
        "base_branch": candidate.base_branch, "request_source": candidate.request_source,
        "review_decision": candidate.review_decision or "none", "checks": candidate.checks,
        "previous_review": previous,
        "commits_since_review": str(candidate.commits_since_review), "agent": agent,
    }


def render_prompt(
    template: str, candidate: ReviewCandidate, agent: str, extra: dict[str, str] | None = None
) -> str:
    """Fill known `{name}` placeholders, leaving any other braces untouched."""
    fields = {**prompt_fields(candidate, agent), **(extra or {})}
    return PLACEHOLDER.sub(lambda match: fields.get(match.group(1), match.group(0)), template)


def tab_label(candidate: ReviewCandidate, agent: str) -> str:
    return f"{candidate.repo.rsplit('/', 1)[-1]} #{candidate.number} {agent} review"


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
    return LaunchResult(
        agent=agent.name, tab_label=label, pane_id=pane_id, checkout=checkout,
        agent_name=agent_name,
    )


def send_prompt(agent_name: str, text: str) -> None:
    _run("herdr", "agent", "prompt", agent_name, text)


def prompt_and_wait(agent_name: str, text: str) -> None:
    """Send a prompt and block until the agent finishes that turn.

    Herdr reports `blocked` when an agent waits on the user (for example a permission
    prompt), so after the first settled state keep waiting for idle or done.
    """
    _run("herdr", "agent", "prompt", agent_name, text, "--wait", timeout=None)
    _run(
        "herdr", "agent", "wait", agent_name, "--until", "idle", "--until", "done",
        timeout=None,
    )
