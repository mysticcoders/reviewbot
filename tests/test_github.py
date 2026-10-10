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
