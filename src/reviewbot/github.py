"""GitHub CLI access and PR review-status classification."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .config import Scope


class GhError(Exception):
    pass


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

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def actionable(self) -> bool:
        return self.status in {QueueStatus.READY, QueueStatus.UPDATED}


async def _run(*args: str, timeout: float = 60.0) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise GhError(f"timed out after {timeout}s: {' '.join(args)}")
    if proc.returncode != 0:
        message = stderr.decode("utf-8", errors="replace").strip()
        raise GhError(message or f"exit {proc.returncode}: {' '.join(args)}")
    return stdout.decode("utf-8", errors="replace")


async def current_login() -> str:
    return (await _run("gh", "api", "user", "--jq", ".login")).strip()


def _check_status(checks: list[dict[str, Any]]) -> str:
    if not checks:
        return "none"
    if any(
        check.get("status") != "COMPLETED"
        and check.get("state") not in {"SUCCESS", "FAILURE", "ERROR"}
        for check in checks
    ):
        return "pending"
    outcomes = {
        str(check.get("conclusion") or check.get("state") or "").upper()
        for check in checks
    }
    if outcomes & {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}:
        return "failing"
    return "passing"


def _request_source(requests: list[dict[str, Any]], login: str) -> str:
    if any(request.get("login") == login for request in requests):
        return "direct"
    teams = [
        request.get("slug") or request.get("name")
        for request in requests
        if request.get("__typename") == "Team"
    ]
    return f"team:{teams[0]}" if teams and teams[0] else "team"


def _classify(
    *, is_draft: bool, head_sha: str, reviews: list[dict[str, Any]], login: str
) -> tuple[QueueStatus, str | None, str | None]:
    mine = [review for review in reviews if (review.get("author") or {}).get("login") == login]
    mine.sort(key=lambda review: review.get("submittedAt") or "")
    latest = mine[-1] if mine else None
    if is_draft:
        return QueueStatus.DRAFT, None, None
    if latest is None:
        return QueueStatus.READY, None, None
    state = str(latest.get("state") or "").upper()
    review_sha = (latest.get("commit") or {}).get("oid")
    if review_sha != head_sha:
        return QueueStatus.UPDATED, state, review_sha
    status = (
        QueueStatus.CHANGES_REQUESTED
        if state == "CHANGES_REQUESTED"
        else QueueStatus.REVIEWED
    )
    return status, state, review_sha


async def _details(summary: dict[str, Any], login: str) -> ReviewCandidate:
    repo = summary["repository"]["nameWithOwner"]
    number = summary["number"]
    raw = await _run(
        "gh", "pr", "view", str(number), "--repo", repo, "--json",
        "number,title,state,isDraft,headRefOid,headRefName,baseRefName,"
        "reviewDecision,reviewRequests,reviews,statusCheckRollup,commits,"
        "updatedAt,url,author",
    )
    data = json.loads(raw)
    status, review_state, review_sha = _classify(
        is_draft=data.get("isDraft", False), head_sha=data.get("headRefOid", ""),
        reviews=data.get("reviews") or [], login=login,
    )
    commits = [commit.get("oid") for commit in data.get("commits") or []]
    if review_sha and review_sha != data.get("headRefOid"):
        commits_since = (
            len(commits) - commits.index(review_sha) - 1
            if review_sha in commits else len(commits)
        )
    else:
        commits_since = 0
    return ReviewCandidate(
        repo=repo, number=number, title=data.get("title", ""),
        author=(data.get("author") or {}).get("login", "unknown"),
        url=data.get("url", ""), updated_at=data.get("updatedAt", ""),
        head_sha=data.get("headRefOid", ""), head_branch=data.get("headRefName", ""),
        base_branch=data.get("baseRefName", ""), is_draft=data.get("isDraft", False),
        review_decision=data.get("reviewDecision", "") or "",
        request_source=_request_source(data.get("reviewRequests") or [], login),
        last_review_state=review_state, last_review_sha=review_sha,
        commits_since_review=commits_since,
        checks=_check_status(data.get("statusCheckRollup") or []), status=status,
    )


def _search_filters(scope: Scope) -> list[list[str]]:
    """One `gh search` filter per owner plus one for all listed repos; one empty filter for everything."""
    if scope.is_all:
        return [[]]
    filters = [["--owner", owner] for owner in scope.owners]
    if scope.repos:
        filters.append([arg for repo in scope.repos for arg in ("--repo", repo)])
    return filters


async def _requested_summaries(filters: list[str]) -> list[dict[str, Any]]:
    raw = await _run(
        "gh", "search", "prs", *filters,
        "--review-requested", "@me", "--state", "open", "--archived=false",
        "--limit", "200", "--sort", "updated", "--order", "desc",
        "--json", "number,repository",
    )
    return json.loads(raw)


def _dedupe(items: list[dict[str, Any]], key) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for item in items:
        identity = key(item)
        if identity not in seen:
            seen.add(identity)
            result.append(item)
    return result


async def review_queue(scope: Scope) -> list[ReviewCandidate]:
    """Return open PRs in scope currently requesting the authenticated user's review."""
    login, *batches = await asyncio.gather(
        current_login(),
        *(_requested_summaries(filters) for filters in _search_filters(scope)),
    )
    summaries = _dedupe(
        [item for batch in batches for item in batch],
        lambda item: f"{item['repository']['nameWithOwner']}#{item['number']}".lower(),
    )
    candidates = await asyncio.gather(*(_details(item, login) for item in summaries))
    return sorted(candidates, key=lambda item: item.updated_at, reverse=True)


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


_AUTHORED_QUERY = """
query($q: String!) {
  search(query: $q, type: ISSUE, first: 100) {
    nodes {
      ... on PullRequest {
        number title url updatedAt isDraft reviewDecision mergeable
        headRefName baseRefName headRefOid
        repository { nameWithOwner }
        commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
        reviewRequests(first: 50) {
          nodes { requestedReviewer { __typename ... on User { login } ... on Team { slug } } }
        }
        latestReviews(first: 50) { nodes { author { login } state submittedAt commit { oid } } }
        suggestedReviewers { reviewer { login } }
      }
    }
  }
}
"""


def _rollup_status(node: dict[str, Any]) -> str:
    commits = (node.get("commits") or {}).get("nodes") or []
    rollup = ((commits[0].get("commit") or {}).get("statusCheckRollup") if commits else None)
    state = str((rollup or {}).get("state") or "").upper()
    if not state:
        return "none"
    if state in {"FAILURE", "ERROR"}:
        return "failing"
    if state == "SUCCESS":
        return "passing"
    return "pending"


def _reviewers(node: dict[str, Any], head_sha: str) -> list[ReviewerStatus]:
    """Merge pending requests and latest reviews into one row per reviewer."""
    result: dict[str, ReviewerStatus] = {}
    for review in (node.get("latestReviews") or {}).get("nodes") or []:
        login = (review.get("author") or {}).get("login")
        if not login:
            continue
        result[login] = ReviewerStatus(
            name=login, is_team=False, state=str(review.get("state") or "").upper(),
            requested=False,
            stale=(review.get("commit") or {}).get("oid") != head_sha,
        )
    for request in (node.get("reviewRequests") or {}).get("nodes") or []:
        reviewer = request.get("requestedReviewer") or {}
        is_team = reviewer.get("__typename") == "Team"
        name = reviewer.get("slug") if is_team else reviewer.get("login")
        if not name:
            continue
        previous = result.get(name)
        result[name] = ReviewerStatus(
            name=name, is_team=is_team,
            state=previous.state if previous else "PENDING",
            requested=True, stale=previous.stale if previous else False,
        )
    return sorted(result.values(), key=lambda item: (not item.requested, item.name.lower()))


def _authored_status(
    *, is_draft: bool, mergeable: str, review_decision: str, checks: str,
    reviewers: list[ReviewerStatus],
) -> AuthoredStatus:
    if is_draft:
        return AuthoredStatus.DRAFT
    if mergeable == "CONFLICTING":
        return AuthoredStatus.CONFLICTS
    if review_decision == "CHANGES_REQUESTED":
        if any(reviewer.needs_rerequest for reviewer in reviewers):
            return AuthoredStatus.RE_REVIEW
        return AuthoredStatus.CHANGES_REQUESTED
    if checks == "failing":
        return AuthoredStatus.CHECKS_FAILING
    if not reviewers:
        return AuthoredStatus.NEEDS_REVIEWERS
    if review_decision == "APPROVED":
        return AuthoredStatus.APPROVED
    if any(reviewer.needs_rerequest for reviewer in reviewers):
        return AuthoredStatus.RE_REVIEW
    return AuthoredStatus.WAITING


def _authored_pr(node: dict[str, Any]) -> AuthoredPR:
    head_sha = node.get("headRefOid", "")
    reviewers = _reviewers(node, head_sha)
    checks = _rollup_status(node)
    mergeable = str(node.get("mergeable") or "")
    review_decision = str(node.get("reviewDecision") or "")
    is_draft = bool(node.get("isDraft"))
    taken = {reviewer.name for reviewer in reviewers}
    suggested = [
        login for item in node.get("suggestedReviewers") or []
        if (login := (item.get("reviewer") or {}).get("login")) and login not in taken
    ]
    return AuthoredPR(
        repo=node["repository"]["nameWithOwner"], number=node["number"],
        title=node.get("title", ""), url=node.get("url", ""),
        updated_at=node.get("updatedAt", ""), head_sha=head_sha,
        head_branch=node.get("headRefName", ""), base_branch=node.get("baseRefName", ""),
        is_draft=is_draft, review_decision=review_decision, mergeable=mergeable,
        checks=checks, reviewers=reviewers, suggested=suggested,
        status=_authored_status(
            is_draft=is_draft, mergeable=mergeable, review_decision=review_decision,
            checks=checks, reviewers=reviewers,
        ),
    )


def _authored_queries(scope: Scope) -> list[str]:
    """GitHub search strings for the user's open PRs, one per owner plus one for listed repos."""
    base = "is:pr is:open archived:false author:@me sort:updated-desc"
    if scope.is_all:
        return [base]
    queries = [f"{base} user:{owner}" for owner in scope.owners]
    if scope.repos:
        queries.append(f"{base} " + " ".join(f"repo:{repo}" for repo in scope.repos))
    return queries


async def _authored_nodes(query: str) -> list[dict[str, Any]]:
    raw = await _run(
        "gh", "api", "graphql", "-f", f"query={_AUTHORED_QUERY}", "-F", f"q={query}",
    )
    return json.loads(raw).get("data", {}).get("search", {}).get("nodes") or []


async def authored_prs(scope: Scope) -> list[AuthoredPR]:
    """Return open PRs in scope authored by the authenticated user, newest activity first."""
    batches = await asyncio.gather(*(_authored_nodes(q) for q in _authored_queries(scope)))
    nodes = _dedupe(
        [node for batch in batches for node in batch if node.get("number")],
        lambda node: f"{node['repository']['nameWithOwner']}#{node['number']}".lower(),
    )
    prs = [_authored_pr(node) for node in nodes]
    return sorted(prs, key=lambda pr: pr.updated_at, reverse=True)


async def request_reviewers(repo: str, number: int, reviewers: list[str]) -> None:
    """Request (or re-request) review; entries containing '/' are treated as teams."""
    args = ["gh", "api", "-X", "POST", f"repos/{repo}/pulls/{number}/requested_reviewers"]
    for reviewer in reviewers:
        if "/" in reviewer:
            args += ["-f", f"team_reviewers[]={reviewer.rsplit('/', 1)[-1]}"]
        else:
            args += ["-f", f"reviewers[]={reviewer.removeprefix('@')}"]
    await _run(*args)
