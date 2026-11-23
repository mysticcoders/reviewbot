"""Forge-neutral records of pull requests, reviewers and notifications.

Each forge module (GitHub today) builds these records from its own API. The rest of
ReviewBot reads only these records, so it does not depend on one forge. Reviewer
states use GitHub's words (`APPROVED`, `CHANGES_REQUESTED`, `COMMENTED`, `PENDING`);
another forge maps its own states onto them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ForgeError(Exception):
    """A forge command or API call failed; the message is shown to the user."""


class QueueStatus(str, Enum):
    READY = "ready"
    UPDATED = "updated"
    REVIEWED = "reviewed"
    CHANGES_REQUESTED = "changes-requested"
    DRAFT = "draft"


@dataclass(frozen=True)
class ReviewCandidate:
    repo: str
    number: int
    title: str
    author: str
    url: str
    updated_at: str
    head_sha: str
    head_branch: str
    base_branch: str
    is_draft: bool
    review_decision: str
    request_source: str
    last_review_state: str | None
    last_review_sha: str | None
    commits_since_review: int
    checks: str
    status: QueueStatus
    forge: str = "github"

    @property
    def mark(self) -> str:
        """The sign before the number: `#` for a GitHub PR, `!` for a GitLab MR."""
        return "!" if self.forge == "gitlab" else "#"

    @property
    def key(self) -> str:
        """`owner/repo#12` on GitHub; `host/group/project!12` on GitLab, where `repo` holds the host."""
        return f"{self.repo}{self.mark}{self.number}"

    @property
    def actionable(self) -> bool:
        return self.status in {QueueStatus.READY, QueueStatus.UPDATED}


class AuthoredStatus(str, Enum):
    DRAFT = "draft"
    CONFLICTS = "conflicts"
    CHANGES_REQUESTED = "changes-requested"
    CHECKS_FAILING = "checks-failing"
    NEEDS_REVIEWERS = "needs-reviewers"
    RE_REVIEW = "re-review"
    APPROVED = "approved"
    WAITING = "waiting"


@dataclass(frozen=True)
class ReviewerStatus:
    name: str
    is_team: bool
    state: str
    requested: bool
    stale: bool

    @property
    def label(self) -> str:
        if self.requested:
            return "requested" if self.state == "PENDING" else "re-requested"
        state = self.state.lower().replace("_", "-")
        return f"{state} (stale)" if self.stale else state

    @property
    def needs_rerequest(self) -> bool:
        return not self.requested and not self.is_team and (
            self.stale or self.state == "CHANGES_REQUESTED"
        )


@dataclass(frozen=True)
class AuthoredPR:
    repo: str
    number: int
    title: str
    url: str
    updated_at: str
    head_sha: str
    head_branch: str
    base_branch: str
    is_draft: bool
    review_decision: str
    mergeable: str
    checks: str
    reviewers: list[ReviewerStatus]
    suggested: list[str]
    status: AuthoredStatus

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def rerequest_targets(self) -> list[str]:
        return [reviewer.name for reviewer in self.reviewers if reviewer.needs_rerequest]

    @property
    def reviewer_summary(self) -> str:
        counts: dict[str, int] = {}
        for reviewer in self.reviewers:
            if reviewer.requested:
                bucket = "waiting"
            elif reviewer.state == "APPROVED":
                bucket = "approved"
            elif reviewer.state == "CHANGES_REQUESTED":
                bucket = "changes"
            else:
                bucket = "commented"
            counts[bucket] = counts.get(bucket, 0) + 1
        order = ("approved", "changes", "waiting", "commented")
        return ", ".join(f"{counts[k]} {k}" for k in order if k in counts) or "none"


@dataclass(frozen=True)
class Notification:
    """One unread GitHub notification thread about a pull request or issue."""

    id: str
    repo: str
    kind: str
    number: int
    title: str
    reason: str
    updated_at: str
    url: str

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def reason_label(self) -> str:
        return self.reason.replace("_", " ")
