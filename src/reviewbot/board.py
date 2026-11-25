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
    """One view's items for one filter, when they were fetched, and the last fetch error.

    `at` is the wall-clock time the items arrived, shown to people; `started` is when
    their fetch began on the monotonic clock, used to order fetches of the same slot.
    """

    items: list[Any] = field(default_factory=list)
    at: float = 0.0
    started: float = 0.0
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
    _read: dict[str, float] = field(default_factory=dict, repr=False)
    _active_at: dict[str, float] = field(default_factory=dict, repr=False)
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

    def _store(
        self, view: str, value: str | None, items: list[Any], started: float | None = None,
    ) -> bool:
        """Keep a fetch's items for its filter, unless a fetch that began later is already
        kept. `started` is when the fetch began (`time.monotonic()`); None means now."""
        value = value or self.filters()[view]
        started = time.monotonic() if started is None else started
        with self._lock:
            return self._store_locked(view, value, items, started)

    def _store_locked(self, view: str, value: str, items: list[Any], started: float) -> bool:
        if started < self._slot(view, value).started:
            return False
        if view == "notifications":
            items = [item for item in items if not self._read_after(item, started)]
        self._slots[(view, value)] = _Slot(list(items), time.time(), started)
        return True

    def _read_after(self, item: Notification, started: float) -> bool:
        """Whether the user marked this thread read after the fetch that found it began.

        A fetch that began after the read is GitHub's word on whether the thread is
        unread again, so only earlier fetches are filtered.
        """
        read_at = self._read.get(item.id)
        return read_at is not None and read_at > started

    def update_queue(
        self, candidates: list[ReviewCandidate], active: set[str], view: str | None = None,
        started: float | None = None,
    ) -> bool:
        """Keep a queue fetch and its open tabs together, unless a newer fetch is kept.

        Rows and their in-progress state change under one lock, so a snapshot never
        shows a new row as actionable before its open review tab is recorded.
        """
        value = view or self.filters()["queue"]
        started = time.monotonic() if started is None else started
        with self._lock:
            if not self._store_locked("queue", value, candidates, started):
                return False
            self._observe_active(active, [candidate.key for candidate in candidates], started)
        return True

    def update_active(
        self, active: set[str], checked: list[str], started: float | None = None,
    ) -> None:
        """Record which of the `checked` PRs have an open review tab, keeping the others.

        Tabs are looked up only for the PRs a caller has, so a refresh of one filter must
        not clear the in-progress state of PRs that only another filter shows. Each PR
        keeps its newest observation: a look that began before an earlier recorded one
        (`started`, on the monotonic clock; None means now) is ignored for that PR.
        """
        started = time.monotonic() if started is None else started
        with self._lock:
            self._observe_active(active, checked, started)

    def _observe_active(self, active: set[str], checked: list[str], started: float) -> None:
        for key in checked:
            if started < self._active_at.get(key, float("-inf")):
                continue
            self._active_at[key] = started
            if key in active:
                self.active.add(key)
            else:
                self.active.discard(key)

    def update_mine(
        self, authored: list[AuthoredPR], view: str | None = None, started: float | None = None,
    ) -> bool:
        return self._store("mine", view, authored, started)

    def update_notifications(
        self, notes: list[Notification], view: str | None = None, started: float | None = None,
    ) -> bool:
        return self._store("notifications", view, notes, started)

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
        self.forget(thread_id)
        return found

    def forget(self, thread_id: str) -> None:
        """Drop a thread marked read from every filter, and keep a fetch that was already
        under way from bringing it back."""
        with self._lock:
            self._read[thread_id] = time.monotonic()
            for (view, _), slot in self._slots.items():
                if view == "notifications":
                    slot.items = [item for item in slot.items if item.id != thread_id]

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
                started = time.monotonic()
                try:
                    items = self._fetch(view, value)
                    active = active_review_keys(self.config, items) if view == "queue" else None
                except (GhError, LaunchError, OSError) as error:
                    with self._lock:
                        slot = self._slot(view, value)
                        if started >= slot.started:
                            slot.error = str(error)
                    continue
                if active is not None:
                    self.update_queue(items, active, value, started)
                else:
                    self._store(view, value, items, started)
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
