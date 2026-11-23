import asyncio
import json

import pytest

from reviewbot import github
from reviewbot.config import Scope
from reviewbot.github import (
    QueueStatus,
    _authored_queries,
    _check_status,
    _classify,
    _search_filters,
)


def review(login: str, state: str, sha: str, submitted: str = "2026-01-01") -> dict:
    return {
        "author": {"login": login},
        "state": state,
        "commit": {"oid": sha},
        "submittedAt": submitted,
    }


def test_new_request_is_ready():
    assert _classify(
        is_draft=False, head_sha="new", reviews=[], login="me"
    ) == (QueueStatus.READY, None, None)


def test_review_at_current_head_is_not_actionable():
    assert _classify(
        is_draft=False,
        head_sha="same",
        reviews=[review("me", "APPROVED", "same")],
        login="me",
    )[0] == QueueStatus.REVIEWED


def test_unchanged_changes_requested_is_not_actionable():
    assert _classify(
        is_draft=False,
        head_sha="same",
        reviews=[review("me", "CHANGES_REQUESTED", "same")],
        login="me",
    )[0] == QueueStatus.CHANGES_REQUESTED


def test_commit_after_review_is_updated():
    assert _classify(
        is_draft=False,
        head_sha="new",
        reviews=[review("me", "CHANGES_REQUESTED", "old")],
        login="me",
    )[0] == QueueStatus.UPDATED


def test_other_peoples_reviews_do_not_count():
    assert _classify(
        is_draft=False,
        head_sha="same",
        reviews=[review("someone", "APPROVED", "same")],
        login="me",
    )[0] == QueueStatus.READY


def test_check_rollup():
    assert _check_status([]) == "none"
    assert _check_status([{"status": "IN_PROGRESS"}]) == "pending"
    assert _check_status([{"status": "COMPLETED", "conclusion": "SUCCESS"}]) == "passing"
    assert _check_status([{"status": "COMPLETED", "conclusion": "FAILURE"}]) == "failing"


def test_search_filters_for_each_scope_shape():
    assert _search_filters(Scope()) == [[]]
    assert _search_filters(Scope.parse(["acme", "beta", "x/one", "y/two"])) == [
        ["--owner", "acme"],
        ["--owner", "beta"],
        ["--repo", "x/one", "--repo", "y/two"],
    ]


def test_authored_queries_for_each_scope_shape():
    base = "is:pr is:open archived:false author:@me sort:updated-desc"
    assert _authored_queries(Scope()) == [base]
    assert _authored_queries(Scope.parse(["acme", "x/one", "y/two"])) == [
        f"{base} user:acme",
        f"{base} repo:x/one repo:y/two",
    ]


def _flaky(monkeypatch, errors: list[str]) -> list[int]:
    """Make _run_once fail with each queued error in turn, then succeed; return the call log."""
    calls: list[int] = []

    async def run_once(*args, timeout):
        calls.append(1)
        if errors:
            raise github.GhError(errors.pop(0))
        return "ok"

    async def no_sleep(_):
        return None

    monkeypatch.setattr(github, "_run_once", run_once)
    monkeypatch.setattr(github.asyncio, "sleep", no_sleep)
    return calls


def test_run_retries_github_502(monkeypatch):
    calls = _flaky(monkeypatch, ["gh: HTTP 502", "unexpected end of JSON input"])
    assert asyncio.run(github._run("gh", "api", "graphql")) == "ok"
    assert len(calls) == 3


def test_run_gives_up_after_attempts(monkeypatch):
    calls = _flaky(monkeypatch, ["gh: HTTP 502"] * 5)
    with pytest.raises(github.GhError, match="502"):
        asyncio.run(github._run("gh", "api", "graphql"))
    assert len(calls) == 3


def test_run_does_not_retry_client_errors(monkeypatch):
    calls = _flaky(monkeypatch, ["gh: Not Found (HTTP 404)"])
    with pytest.raises(github.GhError, match="404"):
        asyncio.run(github._run("gh", "api", "repos/x/y"))
    assert len(calls) == 1


def test_notifications_keep_watched_prs_and_issues(monkeypatch):
    def thread(thread_id, repo, kind, url_tail, reason="comment"):
        return json.dumps({
            "id": thread_id, "reason": reason, "updated_at": f"2026-11-2{thread_id}T00:00:00Z",
            "subject": {"type": kind, "title": f"T{thread_id}",
                        "url": f"https://api.github.com/repos/{repo}/{url_tail}"},
            "repository": {"full_name": repo, "html_url": f"https://github.com/{repo}"},
        })

    lines = "\n".join([
        thread("1", "Acme/api", "PullRequest", "pulls/7", "review_requested"),
        thread("2", "acme/web", "Issue", "issues/3"),
        thread("3", "other/x", "PullRequest", "pulls/1"),
        thread("4", "acme/api", "Release", "releases/9"),
    ])

    async def run(*args, **kwargs):
        assert args[:4] == ("gh", "api", "--paginate", "notifications?per_page=50")
        return lines

    monkeypatch.setattr(github, "_run", run)
    found = asyncio.run(github.notifications(Scope.parse(["acme"])))
    assert [(n.key, n.kind, n.url) for n in found] == [
        ("acme/web#3", "issue", "https://github.com/acme/web/issues/3"),
        ("Acme/api#7", "pr", "https://github.com/Acme/api/pull/7"),
    ]
    assert found[1].reason_label == "review requested"
    everything = asyncio.run(github.notifications(Scope()))
    assert len(everything) == 3


def test_mark_read_refuses_a_bad_thread_id():
    with pytest.raises(github.GhError):
        asyncio.run(github.mark_notification_read("../user"))
