"""The [filters] table and the filtered Open PRs, My PRs, and Notifications views."""

import asyncio
import json
import time
from pathlib import Path

import pytest

from reviewbot import github
from reviewbot.board import Board, OpenError
from reviewbot.config import FILTERS_TABLE, ConfigError, Scope, add_filters, load
from reviewbot.github import AuthoredStatus, stale_cutoff
from reviewbot.mcp import McpServer

BASE = 'watch = ["acme"]\nmultiplexer = "herdr"\n'


def write(tmp_path: Path, filters: str | None = FILTERS_TABLE) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(BASE + (filters or ""), encoding="utf-8")
    return path


def test_a_missing_filters_table_is_written_into_the_config(tmp_path, capsys):
    path = write(tmp_path, None)
    filters = load(path).filters
    assert (filters.open, filters.mine, filters.notifications) == ("requested", "all", "all")
    assert "[filters]" in path.read_text(encoding="utf-8")
    assert (tmp_path / "config.toml.before-0.5").read_text(encoding="utf-8") == BASE
    assert "added a [filters] table" in capsys.readouterr().err
    load(path)
    assert capsys.readouterr().err == ""


def test_a_broken_config_is_not_touched(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('watch = ["acme"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="`multiplexer` is missing"):
        load(path)
    assert path.read_text(encoding="utf-8") == 'watch = ["acme"]\n'
    assert not (tmp_path / "config.toml.before-0.5").exists()


def test_an_unwritable_config_without_filters_fails_to_load(tmp_path):
    path = write(tmp_path, None)
    path.chmod(0o444)
    tmp_path.chmod(0o555)
    try:
        with pytest.raises(ConfigError, match="could not add it"):
            load(path)
    finally:
        tmp_path.chmod(0o755)
        path.chmod(0o644)


def test_filters_table_is_read(tmp_path):
    filters = load(write(tmp_path)).filters
    assert (filters.open, filters.mine, filters.notifications) == ("requested", "all", "all")
    assert filters.notification_kinds == ("pr", "issue")
    assert filters.stale_days == 14


@pytest.mark.parametrize(("line", "message"), [
    ('open = "everything"', "`open` must be one of requested, involves, all, stale"),
    ('mine = "drafts"', "`mine` must be one of"),
    ('notifications = "loud"', "`notifications` must be one of"),
    ('notification_kinds = ["release"]', "`notification_kinds` must list"),
    ("notification_kinds = []", "`notification_kinds` must list"),
    ("stale_days = 0", "`stale_days` must be a whole number"),
    ("stale_days = true", "`stale_days` must be a whole number"),
    ("stale_days = 1000000", "`stale_days` must be a whole number of days from 1 to 3650"),
])
def test_bad_filter_values_are_refused(tmp_path, line, message):
    key = line.split(" = ")[0]
    table = "\n".join(
        line if row.startswith(f"{key} ") else row for row in FILTERS_TABLE.splitlines()
    )
    with pytest.raises(ConfigError, match=message):
        load(write(tmp_path, table + "\n"))


def test_a_missing_filter_key_is_named(tmp_path):
    table = "\n".join(row for row in FILTERS_TABLE.splitlines() if not row.startswith("mine "))
    with pytest.raises(ConfigError, match="`mine` must be one of .*; it is missing"):
        load(write(tmp_path, table + "\n"))


def test_add_filters_makes_an_old_config_load(tmp_path):
    (tmp_path / "p.md").write_text("review {number}", encoding="utf-8")
    old = BASE + 'prompt = "p.md"\n[[agents]]\nname = "a"\nkind = "claude"'
    path = tmp_path / "config.toml"
    path.write_text(add_filters(old), encoding="utf-8")
    config = load(path)
    assert config.filters.open == "requested"
    assert [agent.name for agent in config.agents] == ["a"]


def pr_view(number: int, author: str, requests: list[str]) -> str:
    return json.dumps({
        "number": number, "title": f"PR {number}", "isDraft": False, "headRefOid": "h",
        "headRefName": "b", "baseRefName": "main", "reviewDecision": "",
        "reviewRequests": [{"login": login} for login in requests], "reviews": [],
        "statusCheckRollup": [], "commits": [], "updatedAt": f"2026-11-{number:02d}T00:00:00Z",
        "url": f"https://github.com/acme/api/pull/{number}", "author": {"login": author},
    })


def summary(number: int, author: str) -> dict:
    return {"number": number, "repository": {"nameWithOwner": "acme/api"},
            "author": {"login": author}}


@pytest.fixture
def fake_github(monkeypatch):
    """Answer gh calls from canned data; record each search's person and date filters."""
    searches = []
    found = {
        "--review-requested": [summary(1, "bob")],
        "--involves": [summary(1, "bob"), summary(2, "carol"), summary(3, "me")],
        None: [summary(1, "bob"), summary(2, "carol"), summary(3, "me"), summary(4, "dan")],
    }
    authors = {1: "bob", 2: "carol", 3: "me", 4: "dan"}

    async def run(*args, **kwargs):
        if args[:3] == ("gh", "api", "user"):
            return "me\n"
        if args[:3] == ("gh", "search", "prs"):
            person = next((a for a in args if a in ("--review-requested", "--involves")), None)
            updated = args[args.index("--updated") + 1] if "--updated" in args else None
            searches.append((person, updated))
            return json.dumps(found[person])
        raise AssertionError(args)

    async def details(repo, number):
        return pr_view(number, authors[number], ["me"] if number == 1 else [])

    monkeypatch.setattr(github, "_run", run)
    monkeypatch.setattr(github, "_details_raw", details)
    return searches


def open_keys(view: str, scope: Scope | None = None) -> list[tuple[int, str]]:
    found = asyncio.run(github.open_prs(scope or Scope.parse(["acme"]), view, 14))
    return [(item.number, item.request_source) for item in found]


def test_requested_is_todays_queue(fake_github):
    assert open_keys("requested") == [(1, "direct")]
    assert fake_github == [("--review-requested", None)]


def test_involves_and_all_leave_out_own_prs_and_mark_unrequested_rows(fake_github):
    assert open_keys("involves") == [(2, "none"), (1, "direct")]
    assert open_keys("all") == [(4, "none"), (2, "none"), (1, "direct")]


def test_stale_searches_requested_prs_before_the_cutoff(fake_github):
    open_keys("stale")
    assert fake_github == [("--review-requested", f"<{stale_cutoff(14)}")]


def test_all_is_refused_when_watching_everything(fake_github):
    with pytest.raises(github.GhError, match="needs owners or repositories"):
        open_keys("all", Scope())
    assert fake_github == []


def mine_node(number: int, **overrides) -> dict:
    base = {
        "number": number, "title": f"Mine {number}", "url": f"u{number}",
        "updatedAt": "2026-11-20T00:00:00Z", "isDraft": False, "reviewDecision": "",
        "mergeable": "MERGEABLE", "headRefName": "b", "baseRefName": "main",
        "headRefOid": "h", "repository": {"nameWithOwner": "acme/api"},
        "commits": {"nodes": []}, "suggestedReviewers": [], "latestReviews": {"nodes": []},
        "reviewRequests": {"nodes": [
            {"requestedReviewer": {"__typename": "User", "login": "bob"}},
        ]},
    }
    base.update(overrides)
    return base


@pytest.fixture
def fake_mine(monkeypatch):
    authored = [mine_node(1), mine_node(2, isDraft=True), mine_node(3, mergeable="CONFLICTING")]
    assigned = [mine_node(3, mergeable="CONFLICTING"), mine_node(
        4, updatedAt="2020-01-01T00:00:00Z",
    )]

    async def nodes(query):
        return authored if "author:@me" in query else assigned

    monkeypatch.setattr(github, "_authored_nodes", nodes)


def mine(view: str) -> list[tuple[int, str]]:
    found = asyncio.run(github.my_prs(Scope.parse(["acme"]), view, 14))
    return sorted((pr.number, pr.role_label) for pr in found)


def test_my_prs_include_assigned_prs_with_their_role(fake_mine):
    assert mine("all") == [
        (1, "author"), (2, "author"), (3, "author, assignee"), (4, "assignee"),
    ]


def test_my_prs_filters(fake_mine):
    assert [number for number, _ in mine("ready")] == [1, 3, 4]
    assert [number for number, _ in mine("needs-action")] == [3]
    assert [number for number, _ in mine("stale")] == [4]


def test_needs_action_covers_reviewers_to_ask_again():
    from reviewbot.github import _authored_pr

    pr = _authored_pr(mine_node(9, reviewRequests={"nodes": []}))
    assert pr.status == AuthoredStatus.NEEDS_REVIEWERS and pr.needs_action


@pytest.fixture
def fake_threads(monkeypatch):
    endpoints = []

    def thread(thread_id, kind, reason):
        tail = "pulls/1" if kind == "PullRequest" else "issues/1"
        return json.dumps({
            "id": thread_id, "reason": reason, "updated_at": f"2026-11-2{thread_id}T00:00:00Z",
            "subject": {"type": kind, "title": "t",
                        "url": f"https://api.github.com/repos/acme/api/{tail}"},
            "repository": {"full_name": "acme/api", "html_url": "https://github.com/acme/api"},
        })

    async def run(*args, **kwargs):
        endpoints.append(args[3])
        return "\n".join([
            thread("1", "PullRequest", "review_requested"),
            thread("2", "Issue", "mention"),
            thread("3", "PullRequest", "comment"),
        ])

    monkeypatch.setattr(github, "_run", run)
    return endpoints


def threads(view: str, kinds=("pr", "issue")) -> list[str]:
    found = asyncio.run(github.notifications(Scope.parse(["acme"]), view, kinds))
    return sorted(item.id for item in found)


def test_notification_filters(fake_threads):
    assert threads("all") == ["1", "2", "3"]
    assert threads("reviews") == ["1", "2"]
    assert threads("all", ("pr",)) == ["1", "3"]
    threads("participating")
    assert fake_threads[-1] == "notifications?per_page=50&participating=true"
    assert fake_threads[0] == "notifications?per_page=50"


SERVE = '[serve]\nport = 0\nbind = "localhost"\nbrowser = "Chrome"\n'


def board_with_fetches(tmp_path, monkeypatch):
    item = Board(load(write(tmp_path, SERVE + FILTERS_TABLE)))
    fetched = []

    def fetch(view, value):
        fetched.append((view, value))
        return []

    monkeypatch.setattr(item, "_fetch", fetch)
    monkeypatch.setattr("reviewbot.board.active_review_keys", lambda config, items: set())
    return item, fetched


def test_board_keeps_one_cache_per_filter(tmp_path, monkeypatch):
    item, fetched = board_with_fetches(tmp_path, monkeypatch)
    item.refresh()
    assert sorted(fetched) == [("mine", "all"), ("notifications", "all"), ("queue", "requested")]
    fetched.clear()
    chosen = item.refresh(wanted={"queue": "involves"})
    assert chosen["queue"] == "involves"
    assert fetched == [("queue", "involves")]
    fetched.clear()
    item.refresh(wanted={"queue": "involves"})
    assert fetched == []
    assert item.snapshot({"queue": "involves"})["filters"]["queue"] == "involves"
    with pytest.raises(OpenError, match="not a filter"):
        item.snapshot({"mine": "involves"})


def test_mcp_list_open_prs_takes_a_filter(tmp_path, monkeypatch):
    item, fetched = board_with_fetches(tmp_path, monkeypatch)
    server = McpServer(item, opener=lambda url: None)

    def call(name, arguments):
        return server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })["result"]

    result = call("list_open_prs", {"filter": "all"})["structuredContent"]
    assert result["filter"] == "all" and result["queue"] == []
    assert ("queue", "all") in fetched
    assert call("list_review_queue", {})["structuredContent"]["filter"] == "requested"
    assert call("list_my_prs", {"filter": "bogus"})["isError"] is True


def test_dashboard_refuses_an_unknown_filter(tmp_path, monkeypatch):
    import http.client

    from reviewbot.server import Dashboard, Listen

    item, _ = board_with_fetches(tmp_path, monkeypatch)
    item.update_queue([], set())
    server = Dashboard(item.config, item, listen=Listen("127.0.0.1", ("127.0.0.1",), "127.0.0.1"))
    server.start()
    try:
        def get(path):
            connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            connection.request("GET", path, headers={
                "Host": f"127.0.0.1:{server.port}", "Authorization": f"Bearer {server.token}",
            })
            response = connection.getresponse()
            return response.status, json.loads(response.read())

        status, data = get("/api/prs?open=bogus")
        assert status == 400 and "not a filter" in data["error"]
        status, data = get("/api/prs?open=all&notifications=reviews")
        assert status == 200
        assert data["filters"] == {"queue": "all", "mine": "all", "notifications": "reviews"}
        assert data["choices"]["queue"] == ["requested", "involves", "all", "stale"]
    finally:
        server.stop()


def test_tui_f_cycles_the_current_tabs_filter(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from reviewbot.config import Filters
    from reviewbot.tui import app as tui

    asked = []

    async def open_prs(scope, view, stale_days):
        asked.append(view)
        return []

    async def nothing(*args):
        return []

    monkeypatch.setattr(tui, "open_prs", open_prs)
    monkeypatch.setattr(tui, "my_prs", nothing)
    monkeypatch.setattr(tui, "notifications", nothing)
    monkeypatch.setattr(tui, "active_review_keys", lambda config, candidates: set())
    filters = Filters("requested", "all", "all", ("pr", "issue"), 14)

    def app_for(scope):
        config = SimpleNamespace(
            scope=scope, filters=filters, launchers=[], agents=[], path=tmp_path,
            multiplexer_name="rex",
        )
        return tui.ReviewQueueApp(config)

    async def scenario(scope, presses):
        asked.clear()
        app = app_for(scope)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            for _ in range(presses):
                await pilot.press("f")
                await pilot.pause()
            return list(asked), str(app.query_one("#tabs").get_tab(tui.QUEUE_TAB).label)

    acme = Scope.parse(["acme"])
    assert asyncio.run(scenario(acme, 2)) == (
        ["requested", "involves", "all"], "Open PRs: all (0) [1]",
    )
    everything = Scope()
    asked_all, _ = asyncio.run(scenario(everything, 3))
    assert asked_all == ["requested", "involves", "stale", "requested"]


def test_stale_cutoff_is_a_date_in_the_past():
    cutoff = stale_cutoff(14)
    assert len(cutoff) == 10 and cutoff < time.strftime("%Y-%m-%d")


def test_automatic_filters_keep_permissions_and_follow_a_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    target = write(real, None)
    target.chmod(0o600)
    link = tmp_path / "config.toml"
    link.symlink_to(target)
    load(link)
    assert link.is_symlink()
    assert "[filters]" in target.read_text(encoding="utf-8")
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    backup = real / "config.toml.before-0.5"
    assert oct(backup.stat().st_mode & 0o777) == "0o600"
    assert [p.name for p in real.iterdir() if p.name.startswith(".")] == []


def test_automatic_filters_use_a_config_changed_meanwhile(tmp_path, monkeypatch):
    from reviewbot import config as cfg

    path = write(tmp_path, None)
    real_flock = cfg.fcntl.flock

    def other_process_first(handle, operation):
        path.write_text(
            'watch = ["other"]\nmultiplexer = "herdr"\n'
            + FILTERS_TABLE.replace('"requested"', '"involves"'),
            encoding="utf-8",
        )
        real_flock(handle, operation)

    monkeypatch.setattr(cfg.fcntl, "flock", other_process_first)
    config = load(path)
    assert (config.filters.open, config.scope.describe()) == ("involves", "other")
    assert path.read_text(encoding="utf-8").count("[filters]") == 1
    assert not (tmp_path / "config.toml.before-0.5").exists()


def test_a_bad_work_dir_is_refused_before_the_config_is_touched(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(BASE + "work_dir = 123\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="`work_dir` must be a directory path"):
        load(path)
    assert "[filters]" not in path.read_text(encoding="utf-8")


def candidate_for(number):
    from reviewbot.github import QueueStatus, ReviewCandidate

    return ReviewCandidate(
        repo="acme/api", number=number, title="t", author="bob", url=f"u{number}",
        updated_at="2026-11-20T00:00:00Z", head_sha="h", head_branch="b", base_branch="main",
        is_draft=False, review_decision="", request_source="none", last_review_state=None,
        last_review_sha=None, commits_since_review=0, checks="none", status=QueueStatus.READY,
    )


def test_board_keeps_open_tabs_of_prs_another_filter_shows(tmp_path, monkeypatch):
    item, _ = board_with_fetches(tmp_path, monkeypatch)
    wide, narrow = candidate_for(1), candidate_for(2)
    item.update_queue([wide, narrow], {wide.key}, "all")
    item.update_queue([narrow], set(), "requested")
    assert wide.key in item.active
    item.update_active(set(), [wide.key])
    assert wide.key not in item.active
    rows = item.snapshot({"queue": "all"})["queue"]
    assert [(row["key"], row["actionable"]) for row in rows] == [(wide.key, True), (narrow.key, True)]


def note_at(thread_id, updated_at):
    from reviewbot.github import Notification

    return Notification(
        id=thread_id, repo="acme/api", kind="pr", number=1, title="t", reason="comment",
        updated_at=updated_at, url="https://github.com/acme/api/pull/1",
    )


def test_a_read_thread_leaves_every_filter_and_only_a_later_fetch_restores_it(
    tmp_path, monkeypatch,
):
    item, _ = board_with_fetches(tmp_path, monkeypatch)
    note = note_at("7", "2026-01-01T00:00:00Z")
    item.update_notifications([note], "all")
    item.update_notifications([note], "reviews")
    before_read = time.monotonic()
    item.forget("7")
    assert item.items("notifications", "all") == item.items("notifications", "reviews") == []
    item.update_notifications([note], "reviews", before_read)
    assert item.items("notifications", "reviews") == []
    item.update_notifications([note], "all", time.monotonic() - 3600)
    assert item.items("notifications", "all") == []
    item.update_notifications([note], "all")
    assert item.items("notifications", "all") == [note]


def test_an_older_fetch_never_replaces_a_newer_one(tmp_path, monkeypatch):
    item, _ = board_with_fetches(tmp_path, monkeypatch)
    older, newer = time.monotonic(), time.monotonic() + 1
    assert item.update_mine([], "ready", newer) is True
    assert item.update_mine(["stale result"], "ready", older) is False
    assert item.items("mine", "ready") == []


def test_launch_looks_the_pr_up_directly(monkeypatch):
    views = {
        5: pr_view(5, "bob", ["me"]), 6: pr_view(6, "me", []),
        7: json.dumps({**json.loads(pr_view(7, "bob", [])), "state": "MERGED"}),
    }

    async def run(*args, **kwargs):
        if args[:3] == ("gh", "api", "user"):
            return "me\n"
        if args[:3] == ("gh", "search", "prs"):
            return json.dumps([summary(5, "bob")])
        raise AssertionError(args)

    async def details(repo, number):
        data = json.loads(views[number])
        data.setdefault("state", "OPEN")
        return json.dumps(data)

    monkeypatch.setattr(github, "_run", run)
    monkeypatch.setattr(github, "_details_raw", details)
    assert asyncio.run(github.one_pr("acme/api", 5)).request_source == "direct"

    async def search_down(*args, **kwargs):
        if args[:3] == ("gh", "search", "prs"):
            raise github.GhError("gh: HTTP 502")
        return await run(*args, **kwargs)

    monkeypatch.setattr(github, "_run", search_down)
    assert asyncio.run(github.one_pr("acme/api", 5)).request_source == "direct"
    with pytest.raises(github.GhError, match="your own PR"):
        asyncio.run(github.one_pr("acme/api", 6))
    with pytest.raises(github.GhError, match="is merged, not open"):
        asyncio.run(github.one_pr("acme/api", 7))


def tui_app(tmp_path, scope=None):
    from types import SimpleNamespace

    from reviewbot.config import Filters
    from reviewbot.tui import app as tui

    config = SimpleNamespace(
        scope=scope or Scope.parse(["acme"]),
        filters=Filters("requested", "all", "all", ("pr", "issue"), 14),
        launchers=[], agents=[], path=tmp_path, multiplexer_name="rex",
    )
    return tui, tui.ReviewQueueApp(config)


def test_tui_ignores_a_late_fetch_for_an_earlier_filter(monkeypatch, tmp_path):
    tui, app = tui_app(tmp_path)
    release = asyncio.Event()

    async def fetch_notes(scope, view, kinds):
        if view == "all":
            await release.wait()
            return [note_at("1", "2026-11-01T00:00:00Z")]
        return [note_at("2", "2026-11-02T00:00:00Z")]

    async def nothing(*args):
        return []

    monkeypatch.setattr(tui, "open_prs", nothing)
    monkeypatch.setattr(tui, "my_prs", nothing)
    monkeypatch.setattr(tui, "notifications", fetch_notes)
    monkeypatch.setattr(tui, "active_review_keys", lambda config, candidates: set())

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.press("f")
            await pilot.pause()
            assert [note.id for note in app._notes] == ["2"]
            release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert [note.id for note in app._notes] == ["2"]
            assert app._views["notifications"] == "participating"
            assert [note.id for note in app.board.items("notifications", "all")] == ["1"]

    asyncio.run(scenario())


def test_tui_keeps_the_old_filter_when_the_new_one_fails(monkeypatch, tmp_path):
    tui, app = tui_app(tmp_path)

    async def open_prs(scope, view, stale_days):
        if view == "involves":
            raise github.GhError("search failed")
        return [candidate_for(1)]

    async def nothing(*args):
        return []

    monkeypatch.setattr(tui, "open_prs", open_prs)
    monkeypatch.setattr(tui, "my_prs", nothing)
    monkeypatch.setattr(tui, "notifications", nothing)
    monkeypatch.setattr(tui, "active_review_keys", lambda config, candidates: set())

    async def scenario():
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("f")
            await pilot.pause()
            assert app._views["queue"] == "requested"
            assert str(app.query_one("#tabs").get_tab(tui.QUEUE_TAB).label) == (
                "Open PRs: requested (1) [1]"
            )
            assert "could not load involves: search failed" in str(app._status_line.render())

    asyncio.run(scenario())


def test_a_poll_overtaking_a_filter_change_keeps_the_new_filter(monkeypatch, tmp_path):
    tui, app = tui_app(tmp_path)
    release = asyncio.Event()

    async def fetch_notes(scope, view, kinds):
        if view == "participating":
            await release.wait()
        return [note_at("3", "2026-11-03T00:00:00Z")]

    async def nothing(*args):
        return []

    monkeypatch.setattr(tui, "open_prs", nothing)
    monkeypatch.setattr(tui, "my_prs", nothing)
    monkeypatch.setattr(tui, "notifications", fetch_notes)
    monkeypatch.setattr(tui, "active_review_keys", lambda config, candidates: set())

    async def scenario():
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("3")
            change = asyncio.ensure_future(app.action_cycle_filter())
            await pilot.pause()
            poll = asyncio.ensure_future(app._poll_notes())
            await pilot.pause()
            release.set()
            await asyncio.gather(change, poll)
            await pilot.pause()
            assert app._views["notifications"] == "participating"
            label = str(app.query_one("#tabs").get_tab(tui.NOTES_TAB).label)
            assert label == "Notifications: participating (1) [3]"

    asyncio.run(scenario())


def test_my_prs_skip_the_stale_date_unless_asked(monkeypatch):
    async def nodes(query):
        return []

    def boom(days):
        raise OverflowError("date value out of range")

    monkeypatch.setattr(github, "_authored_nodes", nodes)
    monkeypatch.setattr(github, "stale_cutoff", boom)
    assert asyncio.run(github.my_prs(Scope.parse(["acme"]), "all", 14)) == []


def test_an_edit_during_the_automatic_write_is_kept(tmp_path, monkeypatch):
    from reviewbot import config as cfg

    path = write(tmp_path, None)
    real_stage = cfg._stage
    edited = BASE + "# edited while ReviewBot was writing\n"

    def editor_saves(target, text, mode, staged):
        result = real_stage(target, text, mode, staged)
        if target == path.resolve():
            path.write_text(edited, encoding="utf-8")
        return result

    monkeypatch.setattr(cfg, "_stage", editor_saves)
    monkeypatch.setattr(cfg, "add_filters", lambda text: text + FILTERS_TABLE)
    with pytest.raises(ConfigError, match="could not add it"):
        load(path, _attempts=1)
    assert path.read_text(encoding="utf-8") == edited
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_a_failed_poll_that_overtook_a_filter_change_puts_the_old_filter_back(
    monkeypatch, tmp_path,
):
    tui, app = tui_app(tmp_path)
    release = asyncio.Event()
    calls = []

    async def fetch_notes(scope, view, kinds):
        calls.append(view)
        if view == "participating":
            if len(calls) == 2:
                await release.wait()
                return [note_at("9", "2026-11-09T00:00:00Z")]
            raise github.GhError("rate limited")
        return [note_at("1", "2026-11-01T00:00:00Z")]

    async def nothing(*args):
        return []

    monkeypatch.setattr(tui, "open_prs", nothing)
    monkeypatch.setattr(tui, "my_prs", nothing)
    monkeypatch.setattr(tui, "notifications", fetch_notes)
    monkeypatch.setattr(tui, "active_review_keys", lambda config, candidates: set())

    async def scenario():
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("3")
            change = asyncio.ensure_future(app.action_cycle_filter())
            await pilot.pause()
            await app._poll_notes()
            release.set()
            await change
            await pilot.pause()
            assert app._views["notifications"] == "all"
            assert [note.id for note in app._notes] == ["1"]
            label = str(app.query_one("#tabs").get_tab(tui.NOTES_TAB).label)
            assert label == "Notifications: all (1) [3]"

    asyncio.run(scenario())


def test_launch_skips_the_search_for_a_direct_request_and_bounds_it_otherwise(monkeypatch):
    searched = []
    team = json.loads(pr_view(8, "bob", []))
    team["reviewRequests"] = [{"__typename": "Team", "slug": "core"}]

    async def run(*args, **kwargs):
        if args[:3] == ("gh", "api", "user"):
            return "me\n"
        searched.append(args)
        await asyncio.sleep(60)
        raise AssertionError("the search should have been cut off")

    async def details(repo, number):
        data = team if number == 8 else json.loads(pr_view(number, "bob", ["me"]))
        return json.dumps({**data, "state": "OPEN"})

    monkeypatch.setattr(github, "_run", run)
    monkeypatch.setattr(github, "_details_raw", details)
    monkeypatch.setattr(github, "TEAM_SEARCH_SECONDS", 0.05)
    assert asyncio.run(github.one_pr("acme/api", 5)).request_source == "direct"
    assert searched == []
    assert asyncio.run(github.one_pr("acme/api", 8)).request_source == "none"
    assert len(searched) == 1


def test_an_older_queue_result_or_error_changes_nothing(tmp_path, monkeypatch):
    item, _ = board_with_fetches(tmp_path, monkeypatch)
    pr = candidate_for(1)
    newer, older = time.monotonic(), time.monotonic() - 10
    assert item.update_queue([pr], {pr.key}, "requested", newer) is True
    assert item.update_queue([pr], set(), "requested", older) is False
    assert pr.key in item.active
    item.update_active(set(), [pr.key], older)
    assert pr.key in item.active

    def failing(view, value):
        raise github.GhError("late failure")

    monkeypatch.setattr(item, "_fetch", failing)
    monkeypatch.setattr("reviewbot.board.time.monotonic", lambda: older)
    item.refresh(force=True)
    assert "queue" not in item.snapshot()["errors"]


def test_a_permission_change_during_the_automatic_write_is_kept(tmp_path, monkeypatch):
    from reviewbot import config as cfg

    path = write(tmp_path, None)
    path.chmod(0o644)
    real_stage = cfg._stage

    def user_restricts(target, text, mode, staged):
        result = real_stage(target, text, mode, staged)
        if target == path.resolve():
            path.chmod(0o600)
        return result

    monkeypatch.setattr(cfg, "_stage", user_restricts)
    with pytest.raises(ConfigError, match="could not add it"):
        load(path, _attempts=1)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert "[filters]" not in path.read_text(encoding="utf-8")


def test_a_cancelled_gh_call_kills_its_process():
    started = []
    real_exec = asyncio.create_subprocess_exec

    async def recording(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)
        started.append(proc)
        return proc

    async def scenario():
        asyncio.create_subprocess_exec = recording
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(github._run_once("sleep", "30", timeout=60), 0.2)
        finally:
            asyncio.create_subprocess_exec = real_exec
        return started[0].returncode

    assert asyncio.run(scenario()) is not None
