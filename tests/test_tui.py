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



def _candidate(number):
    return ReviewCandidate(
        repo="acme/demo", number=number, title="Fix", author="author",
        url="url", updated_at="now", head_sha="abc1234", head_branch="fix",
        base_branch="main", is_draft=False, review_decision="REVIEW_REQUIRED",
        request_source="direct", last_review_state=None, last_review_sha=None,
        commits_since_review=0, checks="passing", status=QueueStatus.UPDATED,
    )


def test_closed_review_tab_frees_the_row_and_refetches(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from reviewbot.tui import app as tui

    first, second = _candidate(1), _candidate(2)
    queue = [[first, second], [second]]
    fetches = []
    tabs = {first.key}

    async def review_queue(scope):
        fetches.append(1)
        return queue[min(len(fetches), len(queue)) - 1]

    async def authored_prs(scope):
        return []

    monkeypatch.setattr(tui, "review_queue", review_queue)
    monkeypatch.setattr(tui, "authored_prs", authored_prs)
    monkeypatch.setattr(tui, "active_review_keys", lambda config, candidates: set(tabs))
    scope = SimpleNamespace(describe=lambda: "acme", label=lambda repo: repo)
    config = SimpleNamespace(
        scope=scope, launchers=[], agents=[], path=tmp_path, multiplexer_name="rex",
    )

    async def scenario():
        app = tui.ReviewQueueApp(config)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._table.move_cursor(row=1)
            assert app._table.get_cell_at((0, 0)) == "in-progress"
            await app._sync_active()
            assert len(fetches) == 1
            tabs.clear()
            await app._sync_active()
            await pilot.pause()
            assert len(fetches) == 2
            assert [c.key for c in app._candidates] == [second.key]
            assert app._table.get_cell_at((0, 0)) == "updated"
            assert app._selected().key == second.key

    import asyncio
    asyncio.run(scenario())
