"""A shared, cached view of the review queue, the user's PRs, and notifications for the dashboard and MCP."""

from __future__ import annotations

import asyncio
import platform
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .config import MINE_FILTERS, NOTIFICATION_FILTERS, OPEN_FILTERS, Config, Serve
from .github import (
    AuthoredPR,
    GhError,
    Notification,
    ReviewCandidate,
    mark_notification_read,
    my_prs,
    notifications,
    open_prs,
)
from .launch import LaunchError
from .multiplexer import active_review_keys

STALE_SECONDS = 120


class OpenError(Exception):
    pass


def candidate_json(candidate: ReviewCandidate, active: set[str]) -> dict[str, Any]:
    """Describe one open PR, marking it in progress if a review tab is open for it."""
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
        "roles": list(pr.roles),
    }


def notification_json(item: Notification) -> dict[str, Any]:
    """Describe one unread notification about a PR or issue."""
    return {
        "id": item.id,
        "key": item.key,
        "repo": item.repo,
        "number": item.number,
        "kind": item.kind,
        "title": item.title,
        "reason": item.reason,
        "updated_at": item.updated_at,
        "url": item.url,
    }


VIEWS = ("queue", "mine", "notifications")
CHOICES = {
    "queue": OPEN_FILTERS, "mine": MINE_FILTERS, "notifications": NOTIFICATION_FILTERS,
}


@dataclass
class _Slot:
    """One view's items for one filter, when they were fetched, and the last fetch error."""

    items: list[Any] = field(default_factory=list)
    at: float = 0.0
    error: str | None = None


@dataclass
class Board:
    """The latest Open PRs, My PRs, and notifications, per filter, refetched after STALE_SECONDS.

    Each view (`queue`, `mine`, `notifications`) keeps one cache per filter, so the TUI,
    the dashboard, and MCP clients can each look at a different filter. Without a filter,
    a view uses the one `[filters]` sets. The TUI pushes its own fetches in with
    `update_queue`, `update_mine`, and `update_notifications`, so serving from the TUI
    does not query GitHub twice. Safe to use from several threads.
    """

    config: Config
    active: set[str] = field(default_factory=set)
    _slots: dict[tuple[str, str], _Slot] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _fetch_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def filters(self, wanted: dict[str, str | None] | None = None) -> dict[str, str]:
        """The filter for each view: the one asked for if valid, else the configured one."""
        chosen = {
            "queue": self.config.filters.open,
            "mine": self.config.filters.mine,
            "notifications": self.config.filters.notifications,
        }
        for view, value in (wanted or {}).items():
            if value is None:
                continue
            if view not in CHOICES or value not in CHOICES[view]:
                raise OpenError(f"`{value}` is not a filter for {view}")
            chosen[view] = value
        return chosen

    def _slot(self, view: str, value: str) -> _Slot:
        return self._slots.setdefault((view, value), _Slot())

    def _store(self, view: str, value: str | None, items: list[Any]) -> None:
        value = value or self.filters()[view]
        with self._lock:
            self._slots[(view, value)] = _Slot(list(items), time.time())

    def update_queue(
        self, candidates: list[ReviewCandidate], active: set[str], view: str | None = None,
    ) -> None:
        with self._lock:
            self.active = set(active)
        self._store("queue", view, candidates)

    def update_active(self, active: set[str]) -> None:
        with self._lock:
            self.active = set(active)

    def update_mine(self, authored: list[AuthoredPR], view: str | None = None) -> None:
        self._store("mine", view, authored)

    def update_notifications(self, notes: list[Notification], view: str | None = None) -> None:
        self._store("notifications", view, notes)

    def items(self, view: str, value: str | None = None) -> list[Any]:
        """The cached items of one view for one filter (the configured one by default)."""
        value = value or self.filters()[view]
        with self._lock:
            return list(self._slot(view, value).items)

    def mark_read(self, thread_id: str) -> Notification:
        """Mark a notification on the board read on GitHub and drop it from every filter."""
        with self._lock:
            found = next((
                item for (view, _), slot in self._slots.items() if view == "notifications"
                for item in slot.items if item.id == thread_id
            ), None)
        if found is None:
            raise OpenError(f"{thread_id} is not an unread notification")
        asyncio.run(mark_notification_read(thread_id))
        with self._lock:
            for (view, _), slot in self._slots.items():
                if view == "notifications":
                    slot.items = [item for item in slot.items if item.id != thread_id]
        return found

    def _fetch(self, view: str, value: str) -> list[Any]:
        filters = self.config.filters
        if view == "queue":
            return asyncio.run(open_prs(self.config.scope, value, filters.stale_days))
        if view == "mine":
            return asyncio.run(my_prs(self.config.scope, value, filters.stale_days))
        return asyncio.run(
            notifications(self.config.scope, value, filters.notification_kinds)
        )

    def refresh(
        self, *, force: bool = False, wanted: dict[str, str | None] | None = None,
    ) -> dict[str, str]:
        """Fetch whatever is stale for the chosen filters (all when forced); return the filters.

        A failed fetch keeps the earlier items and records the error for that filter.
        """
        chosen = self.filters(wanted)
        with self._fetch_lock:
            now = time.time()
            for view in VIEWS:
                value = chosen[view]
                with self._lock:
                    fetched = self._slot(view, value).at
                if not force and now - fetched <= STALE_SECONDS:
                    continue
                try:
                    items = self._fetch(view, value)
                    active = active_review_keys(self.config, items) if view == "queue" else None
                except (GhError, LaunchError, OSError) as error:
                    with self._lock:
                        self._slot(view, value).error = str(error)
                    continue
                if active is not None:
                    self.update_queue(items, active, value)
                else:
                    self._store(view, value, items)
        return chosen

    def snapshot(self, wanted: dict[str, str | None] | None = None) -> dict[str, Any]:
        chosen = self.filters(wanted)
        with self._lock:
            slots = {view: self._slot(view, chosen[view]) for view in VIEWS}
            return {
                "scope": self.config.scope.describe(),
                "filters": chosen,
                "choices": {view: list(values) for view, values in CHOICES.items()},
                "queue": [candidate_json(c, self.active) for c in slots["queue"].items],
                "mine": [authored_json(pr) for pr in slots["mine"].items],
                "notifications": [
                    notification_json(item) for item in slots["notifications"].items
                ],
                "queue_at": slots["queue"].at or None,
                "mine_at": slots["mine"].at or None,
                "notifications_at": slots["notifications"].at or None,
                "errors": {
                    view: slot.error for view, slot in slots.items() if slot.error
                },
            }

    def find_url(self, target: str) -> str:
        """Resolve a PR key (owner/repo#n) or URL to a URL the board knows about.

        Only PRs and notifications on the board, under any filter, can be opened, so a
        client cannot use `open_pr` to send the browser to an arbitrary address.
        """
        wanted = target.strip().lower()
        with self._lock:
            for slot in self._slots.values():
                for item in slot.items:
                    if wanted in (item.key.lower(), item.url.lower()):
                        return item.url
        raise OpenError(f"{target} is not in Open PRs, My PRs, or notifications")


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
