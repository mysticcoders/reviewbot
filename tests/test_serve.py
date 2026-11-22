"""The dashboard, HTTP MCP endpoint, stdio MCP server, and their token protection."""

import http.client
import io
import json
import stat
import time

import pytest

from reviewbot.board import Board, OpenError
from reviewbot.config import ConfigError, load
from reviewbot.github import (
    AuthoredPR,
    AuthoredStatus,
    QueueStatus,
    ReviewCandidate,
    ReviewerStatus,
)
from reviewbot.mcp import McpServer, serve_stdio
from reviewbot.server import COOKIE, Dashboard, Listen, load_token, token_path

SERVE = '[serve]\nport = 0\nbind = "localhost"\nbrowser = "Google Chrome"\n'


def write(tmp_path, extra=SERVE):
    path = tmp_path / "config.toml"
    path.write_text('watch = ["acme"]\nmultiplexer = "rex"\n' + extra, encoding="utf-8")
    return load(path)


def candidate(number=12, status=QueueStatus.READY):
    return ReviewCandidate(
        repo="acme/demo", number=number, title=f"Fix <b>{number}</b>", author="author",
        url=f"https://github.com/acme/demo/pull/{number}", updated_at="2026-11-20T00:00:00Z",
        head_sha="abcdef123456", head_branch="fix", base_branch="main", is_draft=False,
        review_decision="REVIEW_REQUIRED", request_source="direct", last_review_state=None,
        last_review_sha=None, commits_since_review=0, checks="passing", status=status,
    )


def authored():
    return AuthoredPR(
        repo="acme/api", number=7, title="Mine", url="https://github.com/acme/api/pull/7",
        updated_at="2026-11-21T00:00:00Z", head_sha="1234567", head_branch="mine",
        base_branch="main", is_draft=False, review_decision="CHANGES_REQUESTED",
        mergeable="MERGEABLE", checks="passing",
        reviewers=[ReviewerStatus("bob", False, "CHANGES_REQUESTED", False, False)],
        suggested=[], status=AuthoredStatus.CHANGES_REQUESTED,
    )


def board(config):
    item = Board(config)
    item.update_queue([candidate(12), candidate(13)], {"acme/demo#13"})
    item.update_mine([authored()])
    return item


def test_serve_table_requires_every_field(tmp_path):
    config = write(tmp_path)
    assert (config.serve.port, config.serve.bind, config.serve.browser) == (0, "localhost", "Google Chrome")
    assert write(tmp_path, "").serve is None
    with pytest.raises(ConfigError, match="missing `browser`"):
        write(tmp_path, '[serve]\nport = 1\nbind = "localhost"\n')
    with pytest.raises(ConfigError, match="`bind` must be one of"):
        write(tmp_path, '[serve]\nport = 1\nbind = "0.0.0.0"\nbrowser = "x"\n')
    with pytest.raises(ConfigError, match="`port`"):
        write(tmp_path, '[serve]\nport = "80"\nbind = "localhost"\nbrowser = "x"\n')


def test_token_is_owner_only_and_stable_until_rotated(tmp_path):
    config = write(tmp_path)
    first = load_token(config)
    assert stat.S_IMODE(token_path(config).stat().st_mode) == 0o600
    assert load_token(config) == first
    assert load_token(config, rotate=True) != first


def test_board_snapshot_marks_open_tabs_in_progress(tmp_path):
    snapshot = board(write(tmp_path)).snapshot()
    assert [(i["key"], i["status"], i["actionable"]) for i in snapshot["queue"]] == [
        ("acme/demo#12", "ready", True), ("acme/demo#13", "in-progress", False),
    ]
    assert snapshot["mine"][0]["rerequest"] == ["bob"]


def test_board_only_resolves_known_prs(tmp_path):
    item = board(write(tmp_path))
    assert item.find_url("ACME/demo#12") == "https://github.com/acme/demo/pull/12"
    assert item.find_url("https://github.com/acme/api/pull/7") == "https://github.com/acme/api/pull/7"
    with pytest.raises(OpenError):
        item.find_url("https://evil.example/")


@pytest.fixture
def dashboard(tmp_path):
    config = write(tmp_path)
    item = board(config)
    item.queue_at = item.mine_at = time.time() + 3600
    server = Dashboard(config, item, listen=Listen("127.0.0.1", ("127.0.0.1", "localhost"), "127.0.0.1"))
    server.start()
    yield server
    server.stop()


def request(server, method, path, *, body=None, headers=None, host=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    sent = {"Host": host or f"127.0.0.1:{server.port}", **(headers or {})}
    connection.request(method, path, body=body, headers=sent)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response, data


def mcp(server, payload, token=None):
    body = json.dumps(payload)
    return request(server, "POST", "/mcp", body=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token if token is not None else server.token}",
    })


def test_everything_needs_the_token(dashboard):
    assert request(dashboard, "GET", "/")[0].status == 401
    assert request(dashboard, "GET", "/api/prs")[0].status == 401
    assert request(dashboard, "GET", "/?token=wrong")[0].status == 401
    assert mcp(dashboard, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="wrong")[0].status == 401
    response, data = request(dashboard, "GET", "/api/prs",
                             headers={"Authorization": f"Bearer {dashboard.token}"})
    assert response.status == 200
    assert len(json.loads(data)["queue"]) == 2


def test_token_url_sets_a_strict_cookie_for_the_page(dashboard):
    response, _ = request(dashboard, "GET", f"/?token={dashboard.token}")
    assert response.status == 303
    assert response.getheader("Location") == "/"
    cookie = response.getheader("Set-Cookie")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    sent = {"Cookie": f"{COOKIE}={dashboard.token}"}
    page, html = request(dashboard, "GET", "/", headers=sent)
    assert page.status == 200 and b"ReviewBot" in html
    assert "frame-ancestors 'none'" in page.getheader("Content-Security-Policy")
    assert request(dashboard, "GET", "/api/prs", headers=sent)[0].status == 200


def test_rebound_hosts_and_foreign_origins_are_refused(dashboard):
    auth = {"Authorization": f"Bearer {dashboard.token}"}
    assert request(dashboard, "GET", "/api/prs", headers=auth, host="evil.example")[0].status == 421
    foreign = {**auth, "Origin": "https://evil.example"}
    assert request(dashboard, "GET", "/api/prs", headers=foreign)[0].status == 403
    local = {**auth, "Origin": f"http://localhost:{dashboard.port}"}
    assert request(dashboard, "GET", "/api/prs", headers=local,
                   host=f"localhost:{dashboard.port}")[0].status == 200


def test_http_mcp_lists_tools_and_queue(dashboard):
    response, data = mcp(dashboard, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "1"}},
    })
    assert response.status == 200
    assert json.loads(data)["result"]["protocolVersion"] == "2025-06-18"
    response, _ = mcp(dashboard, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status == 202
    _, data = mcp(dashboard, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [tool["name"] for tool in json.loads(data)["result"]["tools"]]
    assert names == ["list_review_queue", "list_my_prs", "open_pr"]
    _, data = mcp(dashboard, {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "list_review_queue", "arguments": {"actionable_only": True}},
    })
    queue = json.loads(data)["result"]["structuredContent"]["queue"]
    assert [item["key"] for item in queue] == ["acme/demo#12"]


def test_open_pr_opens_only_known_prs(tmp_path, monkeypatch):
    item = board(write(tmp_path))
    item.queue_at = item.mine_at = time.time() + 3600
    opened = []
    server = McpServer(item, opener=opened.append)

    def call(target):
        return server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "open_pr", "arguments": {"pr": target}},
        })["result"]

    assert call("acme/api#7")["structuredContent"] == {"opened": "https://github.com/acme/api/pull/7"}
    assert call("https://evil.example/")["isError"] is True
    assert opened == ["https://github.com/acme/api/pull/7"]


def test_stdio_answers_requests_and_skips_notifications(tmp_path):
    item = board(write(tmp_path))
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + "not json\n"
    )
    stdout = io.StringIO()
    serve_stdio(McpServer(item), stdin, stdout)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert replies[0] == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert replies[1]["error"]["code"] == -32700
    assert len(replies) == 2
