from reviewbot.github import QueueStatus, ReviewCandidate
from reviewbot.tui.app import _display_status


def test_display_status_uses_live_herdr_state():
    item = ReviewCandidate(
        repo="acme/demo", number=12, title="Fix", author="author",
        url="url", updated_at="now", head_sha="abc", head_branch="fix",
        base_branch="main", is_draft=False, review_decision="REVIEW_REQUIRED",
        request_source="direct", last_review_state=None, last_review_sha=None,
        commits_since_review=0, checks="passing", status=QueueStatus.UPDATED,
    )
    assert _display_status(item, set()) == "updated"
    assert _display_status(item, {item.key}) == "in-progress"

