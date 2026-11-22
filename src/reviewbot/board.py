"""A shared, cached view of the review queue and the user's PRs for the dashboard and MCP."""

from __future__ import annotations

import asyncio
import platform
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Config, Serve
from .github import AuthoredPR, GhError, ReviewCandidate, authored_prs, review_queue
from .launch import LaunchError
from .multiplexer import active_review_keys

STALE_SECONDS = 120


class OpenError(Exception):
    pass


def candidate_json(candidate: ReviewCandidate, active: set[str]) -> dict[str, Any]:
    """Describe one PR requesting the user's review, marking it in progress if a tab is open."""
    return {
        "key": candidate.key,
        "repo": candidate.repo,
        "number": candidate.number,
        "title": candidate.title,
        "author": candidate.author,
        "url": candidate.url,
        "updated_at": candidate.updated_at,
        "status": "in-progress" if candidate.key in active else candidate.status.value,
        "actionable": candidate.actionable and candidate.key not in active,
        "checks": candidate.checks,
        "request_source": candidate.request_source,
        "draft": candidate.is_draft,
        "head_sha": candidate.head_sha,
        "base_branch": candidate.base_branch,
        "head_branch": candidate.head_branch,
        "last_review_state": candidate.last_review_state,
        "commits_since_review": candidate.commits_since_review,
    }


def authored_json(pr: AuthoredPR) -> dict[str, Any]:
    """Describe one of the user's open PRs with its reviewers and who needs a re-request."""
    return {
        "key": pr.key,
        "repo": pr.repo,
        "number": pr.number,
        "title": pr.title,
        "url": pr.url,
        "updated_at": pr.updated_at,
        "status": pr.status.value,
        "checks": pr.checks,
        "mergeable": pr.mergeable,
        "review_decision": pr.review_decision,
        "draft": pr.is_draft,
        "reviewers": [
            {"name": r.name, "team": r.is_team, "label": r.label} for r in pr.reviewers
        ],
        "reviewer_summary": pr.reviewer_summary,
        "rerequest": pr.rerequest_targets,
    }


@dataclass
class Board:
    """The latest queue and authored PRs, fetched on demand once older than STALE_SECONDS.

    The TUI pushes its own fetches in with `update_queue` and `update_mine` so serving from
    the TUI does not query GitHub twice. Safe to use from several threads.
    """

    config: Config
    candidates: list[ReviewCandidate] = field(default_factory=list)
    authored: list[AuthoredPR] = field(default_factory=list)
    active: set[str] = field(default_factory=set)
    queue_at: float = 0.0
    mine_at: float = 0.0
    errors: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _fetch_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update_queue(self, candidates: list[ReviewCandidate], active: set[str]) -> None:
        with self._lock:
            self.candidates, self.active = list(candidates), set(active)
            self.queue_at = time.time()
            self.errors.pop("queue", None)

    def update_active(self, active: set[str]) -> None:
        with self._lock:
            self.active = set(active)

    def update_mine(self, authored: list[AuthoredPR]) -> None:
        with self._lock:
            self.authored = list(authored)
            self.mine_at = time.time()
            self.errors.pop("mine", None)

    def refresh(self, *, force: bool = False) -> None:
        """Fetch from GitHub whatever is stale (or everything when forced), recording failures."""
        with self._fetch_lock:
            now = time.time()
            if force or now - self.queue_at > STALE_SECONDS:
                try:
                    candidates = asyncio.run(review_queue(self.config.scope))
                    active = active_review_keys(self.config, candidates)
                except (GhError, LaunchError, OSError) as error:
                    with self._lock:
                        self.errors["queue"] = str(error)
                else:
                    self.update_queue(candidates, active)
            if force or now - self.mine_at > STALE_SECONDS:
                try:
                    authored = asyncio.run(authored_prs(self.config.scope))
                except (GhError, OSError) as error:
                    with self._lock:
                        self.errors["mine"] = str(error)
                else:
                    self.update_mine(authored)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "scope": self.config.scope.describe(),
                "queue": [candidate_json(c, self.active) for c in self.candidates],
                "mine": [authored_json(pr) for pr in self.authored],
                "queue_at": self.queue_at or None,
                "mine_at": self.mine_at or None,
                "errors": dict(self.errors),
            }

    def find_url(self, target: str) -> str:
        """Resolve a PR key (owner/repo#n) or URL to a URL the board knows about.

        Only PRs on the board can be opened, so a client cannot use `open_pr` to send the
        browser to an arbitrary address.
        """
        wanted = target.strip().lower()
        with self._lock:
            for item in [*self.candidates, *self.authored]:
                if wanted in (item.key.lower(), item.url.lower()):
                    return item.url
        raise OpenError(f"{target} is not in the review queue or your open PRs")


def open_in_browser(serve: Serve | None, url: str) -> None:
    """Open a URL in the configured browser on this machine."""
    if serve is None:
        raise OpenError("add a `[serve]` table with `browser` to the config to open PRs")
    if platform.system() == "Darwin":
        command = ["open", "-a", serve.browser, url]
    else:
        command = [serve.browser, url]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as error:
        raise OpenError(f"could not open {url} in {serve.browser}: {error}") from None
