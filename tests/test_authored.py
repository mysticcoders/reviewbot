from reviewbot.github import AuthoredStatus, _authored_pr


def node(**overrides) -> dict:
    base = {
        "number": 7, "title": "Add thing", "url": "u", "updatedAt": "now",
        "isDraft": False, "reviewDecision": "REVIEW_REQUIRED", "mergeable": "MERGEABLE",
        "headRefName": "feat", "baseRefName": "main", "headRefOid": "head",
        "repository": {"nameWithOwner": "org/demo"},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
        "reviewRequests": {"nodes": []},
        "latestReviews": {"nodes": []},
        "suggestedReviewers": [],
    }
    base.update(overrides)
    return base


def requested(login: str, team: bool = False) -> dict:
    if team:
        return {"requestedReviewer": {"__typename": "Team", "slug": login}}
    return {"requestedReviewer": {"__typename": "User", "login": login}}


def reviewed(login: str, state: str, sha: str) -> dict:
    return {"author": {"login": login}, "state": state, "commit": {"oid": sha}}


def test_no_reviewers_needs_reviewers():
    assert _authored_pr(node()).status == AuthoredStatus.NEEDS_REVIEWERS


def test_pending_request_is_waiting():
    pr = _authored_pr(node(reviewRequests={"nodes": [requested("alice")]}))
    assert pr.status == AuthoredStatus.WAITING
    assert pr.reviewers[0].label == "requested"
    assert pr.rerequest_targets == []


def test_changes_requested_not_yet_rerequested_is_re_review():
    pr = _authored_pr(node(
        reviewDecision="CHANGES_REQUESTED",
        latestReviews={"nodes": [reviewed("bob", "CHANGES_REQUESTED", "old")]},
    ))
    assert pr.status == AuthoredStatus.RE_REVIEW
    assert pr.rerequest_targets == ["bob"]


def test_rerequested_reviewer_keeps_prior_state():
    pr = _authored_pr(node(
        reviewDecision="CHANGES_REQUESTED",
        reviewRequests={"nodes": [requested("bob")]},
        latestReviews={"nodes": [reviewed("bob", "CHANGES_REQUESTED", "old")]},
    ))
    assert pr.status == AuthoredStatus.CHANGES_REQUESTED
    assert pr.reviewers[0].label == "re-requested"
    assert pr.rerequest_targets == []


def test_stale_approval_is_rerequest_target_but_still_approved():
    pr = _authored_pr(node(
        reviewDecision="APPROVED",
        latestReviews={"nodes": [reviewed("carol", "APPROVED", "old")]},
        suggestedReviewers=[{"reviewer": {"login": "carol"}}, {"reviewer": {"login": "dan"}}],
    ))
    assert pr.status == AuthoredStatus.APPROVED
    assert pr.reviewers[0].label == "approved (stale)"
    assert pr.rerequest_targets == ["carol"]
    assert pr.suggested == ["dan"]


def test_teams_are_never_rerequest_targets():
    pr = _authored_pr(node(reviewRequests={"nodes": [requested("platform", team=True)]}))
    assert pr.reviewers[0].is_team
    assert pr.rerequest_targets == []


def test_draft_and_conflicts_take_priority():
    assert _authored_pr(node(isDraft=True, mergeable="CONFLICTING")).status == AuthoredStatus.DRAFT
    assert _authored_pr(node(mergeable="CONFLICTING")).status == AuthoredStatus.CONFLICTS


def test_failing_checks():
    commits = {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]}
    pr = _authored_pr(node(commits=commits, reviewRequests={"nodes": [requested("a")]}))
    assert pr.checks == "failing"
    assert pr.status == AuthoredStatus.CHECKS_FAILING


def test_reviewer_summary():
    pr = _authored_pr(node(
        reviewRequests={"nodes": [requested("a"), requested("b")]},
        latestReviews={"nodes": [reviewed("c", "APPROVED", "head")]},
    ))
    assert pr.reviewer_summary == "1 approved, 2 waiting"
