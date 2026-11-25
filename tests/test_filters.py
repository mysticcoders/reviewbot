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
