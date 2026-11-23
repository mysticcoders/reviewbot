"""Shared launch plumbing: isolated PR checkouts, prompt rendering, and tab naming."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import forge
from .config import Config
from .models import ReviewCandidate
from .process import LaunchError, run_command

__all__ = ["LaunchError", "LaunchResult", "run_command"]


@dataclass(frozen=True)
class LaunchResult:
    """A started agent; `handle` is what the multiplexer prompts it by (Herdr name, Rex block)."""

    agent: str
    tab_label: str
    checkout: Path
    handle: str


def _repo_dir(config: Config, candidate: ReviewCandidate) -> Path:
    for checkout in config.checkouts:
        if checkout.slug.lower() == candidate.repo.lower() and (checkout.path / ".git").exists():
            return checkout.path
    return config.work_dir / "repos" / candidate.repo.replace("/", "-")


def prepare_checkout(config: Config, candidate: ReviewCandidate) -> Path:
    """Create or reuse a detached worktree at the exact requested PR SHA."""
    host = forge.backend(config, candidate.repo)
    repo_dir = _repo_dir(config, candidate)
    if not repo_dir.exists():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        host.clone(candidate.repo, repo_dir)
    if not (repo_dir / ".git").exists():
        raise LaunchError(f"repository cache is not a Git checkout: {repo_dir}")

    # A cached clone's local base branch may be stale. Refresh the remote-tracking
    # ref on every launch, including when reusing an existing PR worktree.
    run_command(
        "git", "-C", str(repo_dir), "fetch", "--force", "origin",
        f"refs/heads/{candidate.base_branch}:refs/remotes/origin/{candidate.base_branch}",
        timeout=300,
    )

    checkout = (
        config.work_dir / "worktrees" / candidate.repo.replace("/", "-")
        / f"pr-{candidate.number}-{candidate.head_sha[:7]}"
    )
    if checkout.exists():
        current_sha = run_command("git", "-C", str(checkout), "rev-parse", "HEAD")
        if current_sha != candidate.head_sha:
            raise LaunchError(f"existing checkout has the wrong SHA: {checkout}")
        return checkout

    run_command(
        "git", "-C", str(repo_dir), "fetch", "--force", "origin",
        host.head_ref(candidate.number), timeout=300,
    )
    fetched_sha = run_command("git", "-C", str(repo_dir), "rev-parse", "FETCH_HEAD")
    if fetched_sha != candidate.head_sha:
        raise LaunchError(
            f"PR moved while launching ({candidate.head_sha[:7]} -> {fetched_sha[:7]}); refresh and retry"
        )

    checkout.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        "git", "-C", str(repo_dir), "worktree", "add", "--detach",
        str(checkout), fetched_sha, timeout=300,
    )
    return checkout


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
    """Label one agent's tab: the full repo, PR, and head commit, so equal labels share a checkout."""
    return f"{review_marker(candidate)}{candidate.head_sha[:7]} {agent} review"


def review_marker(candidate: ReviewCandidate) -> str:
    """The tab-label prefix shared by every agent's tab for this PR, at any commit."""
    return f"{candidate.repo} #{candidate.number} "
