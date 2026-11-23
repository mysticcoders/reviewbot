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



async def no_notifications(scope):
    return []


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
    monkeypatch.setattr(tui, "notifications", no_notifications)
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


def test_ctrl_w_serves_the_board_the_tui_already_fetched(monkeypatch, tmp_path):
    import asyncio
    import http.client
    import json
    from types import SimpleNamespace

    from reviewbot.config import Serve
    from reviewbot.tui import app as tui

    first = _candidate(1)

    async def review_queue(scope):
        return [first]

    async def authored_prs(scope):
        return []

    opened = []
    monkeypatch.setattr(tui, "review_queue", review_queue)
    monkeypatch.setattr(tui, "authored_prs", authored_prs)
    monkeypatch.setattr(tui, "notifications", no_notifications)
    monkeypatch.setattr(tui, "active_review_keys", lambda config, candidates: set())
    monkeypatch.setattr(tui, "open_in_browser", lambda serve, url: opened.append(url))
    scope = SimpleNamespace(describe=lambda: "acme", label=lambda repo: repo)
    config = SimpleNamespace(
        scope=scope, launchers=[], agents=[], path=tmp_path / "config.toml",
        multiplexer_name="rex", serve=Serve(port=0, bind="localhost", browser="Chrome"),
    )

    def fetch(port, token):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/api/prs", headers={
            "Host": f"127.0.0.1:{port}", "Authorization": f"Bearer {token}",
        })
        return json.loads(connection.getresponse().read())

    async def scenario():
        app = tui.ReviewQueueApp(config)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+w")
            await app.workers.wait_for_complete()
            dashboard = app._dashboard
            assert dashboard is not None
            assert opened == [dashboard.login_url]
            data = await asyncio.to_thread(fetch, dashboard.port, dashboard.token)
            assert [item["key"] for item in data["queue"]] == [first.key]
            await pilot.press("ctrl+w")
            await pilot.pause()
            assert app._dashboard is None

    asyncio.run(scenario())


def test_notifications_tab_marks_rows_and_marks_read(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace

    from reviewbot.github import Notification
    from reviewbot.tui import app as tui

    first = _candidate(1)
    notes = [
        Notification(id="11", repo="acme/demo", kind="pr", number=1, title="Fix",
                     reason="review_requested", updated_at="2026-11-22T00:00:00Z",
                     url="https://github.com/acme/demo/pull/1"),
        Notification(id="12", repo="acme/demo", kind="issue", number=5, title="Bug",
                     reason="mention", updated_at="2026-11-21T00:00:00Z",
                     url="https://github.com/acme/demo/issues/5"),
    ]
    marked = []

    async def review_queue(scope):
        return [first]

    async def authored_prs(scope):
        return []

    async def fetch_notes(scope):
        return list(notes)

    async def mark(thread_id):
        marked.append(thread_id)

    monkeypatch.setattr(tui, "review_queue", review_queue)
    monkeypatch.setattr(tui, "authored_prs", authored_prs)
    monkeypatch.setattr(tui, "notifications", fetch_notes)
    monkeypatch.setattr(tui, "mark_notification_read", mark)
    monkeypatch.setattr(tui, "active_review_keys", lambda config, candidates: set())
    scope = SimpleNamespace(describe=lambda: "acme", label=lambda repo: repo)
    config = SimpleNamespace(
        scope=scope, launchers=[], agents=[], path=tmp_path, multiplexer_name="rex",
    )

    async def scenario():
        app = tui.ReviewQueueApp(config)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app._table.get_cell_at((0, 2)) == "1 *"
            await pilot.press("3")
            await pilot.pause()
            assert app._notes_table.row_count == 2
            assert app._notes_table.get_cell_at((0, 0)) == "review requested"
            assert str(app.query_one("#tabs").get_tab(tui.NOTES_TAB).label) == "Notifications (2) [3]"
            await pilot.press("m")
            await pilot.pause()
            assert marked == ["11"]
            assert [n.id for n in app._notes] == ["12"]
            assert [n.id for n in app.board.notes] == ["12"]
            assert app._table.get_cell_at((0, 2)) == "1"
            await pilot.press("1")
            await pilot.press("m")
            await pilot.pause()
            assert marked == ["11"]

    asyncio.run(scenario())
