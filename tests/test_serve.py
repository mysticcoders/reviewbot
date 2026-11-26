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
    Notification,
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


def note(thread_id="101", number=12, kind="pr"):
    path = "pull" if kind == "pr" else "issues"
    return Notification(
        id=thread_id, repo="acme/demo", kind=kind, number=number, title=f"Note {number}",
        reason="comment", updated_at="2026-11-22T00:00:00Z",
        url=f"https://github.com/acme/demo/{path}/{number}",
    )


def board(config):
    item = Board(config)
    item.update_queue([candidate(12), candidate(13)], {"acme/demo#13"})
    item.update_mine([authored()])
    item.update_notifications([note("101", 12), note("102", 40, "issue")])
    return item


@pytest.fixture
def marked(monkeypatch):
    """Record mark-read calls instead of sending them to GitHub."""
    from reviewbot import board as board_module

    calls = []

    async def mark(thread_id):
        calls.append(thread_id)

    monkeypatch.setattr(board_module, "mark_notification_read", mark)
    return calls


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
    rotated = load_token(config, rotate=True)
    assert rotated != first
    assert stat.S_IMODE(token_path(config).stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_a_token_file_the_server_would_refuse_is_replaced(tmp_path):
    config = write(tmp_path)
    path = token_path(config)
    for broken in ("half-a-token\n", "\n"):
        path.write_text(broken, encoding="utf-8")
        token = load_token(config)
        assert token != broken.strip() and len(token) == 43
        assert path.read_text(encoding="utf-8").strip() == token
    path.write_bytes(b"\xff\xfe")
    assert len(load_token(config)) == 43


def test_recovering_a_broken_token_never_overwrites_a_rotation(tmp_path, monkeypatch):
    import secrets
    import threading

    from reviewbot import server as server_module

    config = write(tmp_path)
    token_path(config).write_text("broken\n", encoding="utf-8")
    rotated = []
    real_token = secrets.token_urlsafe
    started = threading.Event()

    def rotation_meanwhile(size):
        if not started.is_set():
            started.set()
            rotator = threading.Thread(
                target=lambda: rotated.append(load_token(config, rotate=True)),
            )
            rotator.start()
            rotator.join(0.3)
            assert rotator.is_alive(), "rotation ran while recovery held the lock"
            rotation_meanwhile.rotator = rotator
        return real_token(size)

    monkeypatch.setattr(server_module.secrets, "token_urlsafe", rotation_meanwhile)
    recovered = load_token(config)
    rotation_meanwhile.rotator.join(5)
    assert rotated and rotated[0] != recovered
    assert token_path(config).read_text(encoding="utf-8").strip() == rotated[0]


def test_a_rotated_token_takes_effect_on_a_running_server(dashboard):
    def get(token):
        return request(dashboard, "GET", "/api/prs", headers={
            "Authorization": f"Bearer {token}",
        })[0].status

    old = dashboard.token
    assert get(old) == 200
    new = load_token(dashboard.http.board.config, rotate=True)
    assert (get(old), get(new)) == (401, 200)
    assert dashboard.token == new
    assert dashboard.login_url.endswith(f"?token={new}")
    cookie = {"Cookie": f"{COOKIE}={old}"}
    assert request(dashboard, "GET", "/", headers=cookie)[0].status == 401


def test_a_missing_or_empty_token_file_refuses_everything(dashboard):
    def get(token):
        return request(dashboard, "GET", "/api/prs", headers={
            "Authorization": f"Bearer {token}",
        })[0].status

    token = dashboard.token
    path = dashboard.tokens.path
    path.unlink()
    assert get(token) == 401
    path.write_text("\n", encoding="utf-8")
    assert get(token) == 401
    assert get("") == 401
    path.write_text(token + "\n", encoding="utf-8")
    assert get(token) == 200


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
    item.queue_at = item.mine_at = item.notes_at = time.time() + 3600
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


def test_malformed_cookie_is_refused(dashboard):
    sent = {"Cookie": f"a@b=c; {COOKIE}={dashboard.token}"}
    assert request(dashboard, "GET", "/", headers=sent)[0].status == 401


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
    assert names == [
        "list_review_queue", "list_my_prs", "list_notifications", "mark_notification_read",
        "open_pr",
    ]
    _, data = mcp(dashboard, {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "list_review_queue", "arguments": {"actionable_only": True}},
    })
    queue = json.loads(data)["result"]["structuredContent"]["queue"]
    assert [item["key"] for item in queue] == ["acme/demo#12"]


def test_open_pr_opens_only_known_prs(tmp_path, monkeypatch):
    item = board(write(tmp_path))
    item.queue_at = item.mine_at = item.notes_at = time.time() + 3600
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


def test_snapshot_lists_notifications_and_opens_them(tmp_path):
    item = board(write(tmp_path))
    data = item.snapshot()
    assert [n["key"] for n in data["notifications"]] == ["acme/demo#12", "acme/demo#40"]
    assert data["notifications"][1]["url"] == "https://github.com/acme/demo/issues/40"
    assert item.find_url("acme/demo#40") == "https://github.com/acme/demo/issues/40"


def test_mcp_marks_only_known_notifications_read(tmp_path, marked):
    item = board(write(tmp_path))
    item.queue_at = item.mine_at = item.notes_at = time.time() + 3600
    server = McpServer(item, opener=lambda url: None)

    def call(name, arguments):
        return server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })["result"]

    listed = call("list_notifications", {})["structuredContent"]["notifications"]
    assert [n["id"] for n in listed] == ["101", "102"]
    assert call("mark_notification_read", {"id": "999"})["isError"] is True
    assert call("mark_notification_read", {"id": "101"})["structuredContent"] == {
        "marked_read": "101", "key": "acme/demo#12",
    }
    assert marked == ["101"]
    listed = call("list_notifications", {})["structuredContent"]["notifications"]
    assert [n["id"] for n in listed] == ["102"]


def test_dashboard_marks_read_with_the_cookie_but_not_cross_site(dashboard, marked):
    body = json.dumps({"id": "102"})
    json_type = {"Content-Type": "application/json"}
    cookie = {**json_type, "Cookie": f"{COOKIE}={dashboard.token}"}
    path = "/api/notifications/read"
    assert request(dashboard, "POST", path, body=body, headers=json_type)[0].status == 401
    foreign = {**cookie, "Origin": "https://evil.example"}
    assert request(dashboard, "POST", path, body=body, headers=foreign)[0].status == 403
    form = {**cookie, "Content-Type": "text/plain"}
    assert request(dashboard, "POST", path, body=body, headers=form)[0].status == 415
    assert marked == []
    response, data = request(dashboard, "POST", path, body=body, headers=cookie)
    assert response.status == 200 and json.loads(data)["key"] == "acme/demo#40"
    assert marked == ["102"]
    unknown = json.dumps({"id": "102"})
    assert request(dashboard, "POST", path, body=unknown, headers=cookie)[0].status == 404


def bearer_status(server, token):
    return request(server, "GET", "/api/prs", headers={
        "Authorization": f"Bearer {token}",
    })[0].status


def test_a_token_file_the_server_cannot_read_refuses_everything(dashboard):
    import os

    if os.geteuid() == 0:
        pytest.skip("root reads files regardless of their mode")
    token = dashboard.token
    path = dashboard.tokens.path
    assert bearer_status(dashboard, token) == 200
    path.chmod(0o000)
    try:
        assert bearer_status(dashboard, token) == 401
    finally:
        path.chmod(0o600)
    assert bearer_status(dashboard, token) == 200


def test_half_a_token_or_bytes_that_are_not_utf8_refuse_everything(dashboard):
    token = dashboard.token
    path = dashboard.tokens.path
    path.write_text(token[:20] + "\n", encoding="utf-8")
    assert bearer_status(dashboard, token[:20]) == 401
    assert bearer_status(dashboard, token) == 401
    path.write_bytes(b"\xff\xfe" + token.encode())
    assert bearer_status(dashboard, token) == 401
    path.write_text(token + "\n", encoding="utf-8")
    assert bearer_status(dashboard, token) == 200


def test_a_post_whose_body_arrives_after_rotation_is_refused(dashboard, monkeypatch):
    import socket
    import threading

    from reviewbot import server as server_module

    checked = threading.Event()
    real_allowed = server_module._Handler._post_allowed

    def recording(self, path):
        allowed = real_allowed(self, path)
        if allowed:
            checked.set()
        return allowed

    monkeypatch.setattr(server_module._Handler, "_post_allowed", recording)
    old = dashboard.token
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
    connection = socket.create_connection(("127.0.0.1", dashboard.port), timeout=5)
    try:
        connection.sendall(
            f"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{dashboard.port}\r\n"
            f"Authorization: Bearer {old}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n\r\n".encode()
        )
        assert checked.wait(5), "the server never checked the token before the body"
        load_token(dashboard.http.board.config, rotate=True)
        connection.sendall(body)
        reply = connection.recv(4096).decode()
    finally:
        connection.close()
    assert reply.startswith("HTTP/1.1 401")
