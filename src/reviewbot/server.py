"""The token-protected dashboard and HTTP MCP endpoint behind `reviewbot serve` and the TUI."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .board import Board, OpenError
from .config import Config, Serve
from .github import GhError
from .mcp import McpServer, handle_payload
from .tailnet import Node, TailnetError

COOKIE = "reviewbot_token"
TAILSCALE_APP = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
MAX_BODY = 1 << 20


class ServeError(Exception):
    pass


def token_path(config: Config) -> Path:
    return config.path.parent / "serve-token"


def load_token(config: Config, *, rotate: bool = False) -> str:
    """Read the serve token, creating (or replacing) it as an owner-only file when needed.

    A new token is written to a private temporary file and moved into place, so a
    running server never reads a half-written token.
    """
    path = token_path(config)
    if not rotate and path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return token


class TokenFile:
    """The serve token as its file holds it now, so `token --rotate` takes effect at once.

    The file is read again whenever its inode, modification time or size changes. If it
    is missing, unreadable or empty, no token is valid: the server refuses every request
    rather than keep trusting a token that may have been rotated away.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._stamp: tuple[int, int, int] | None = None
        self._token: str | None = None

    def current(self) -> str | None:
        try:
            info = os.stat(self.path)
        except OSError:
            return None
        stamp = (info.st_ino, info.st_mtime_ns, info.st_size)
        with self._lock:
            if stamp != self._stamp:
                try:
                    text = self.path.read_text(encoding="utf-8").strip()
                except OSError:
                    return None
                self._stamp, self._token = stamp, text or None
            return self._token

    def accepts(self, offered: str | None) -> bool:
        current = self.current()
        return bool(offered) and current is not None and hmac.compare_digest(
            offered.encode(), current.encode(),
        )


def _tailscale() -> str:
    found = shutil.which("tailscale")
    if found:
        return found
    if TAILSCALE_APP.exists():
        return str(TAILSCALE_APP)
    raise ServeError("bind = \"tailnet\" needs the Tailscale CLI, which was not found")


@dataclass(frozen=True)
class Listen:
    """The address to bind, the hostnames clients may use for it, and the URL to show."""

    address: str
    hosts: tuple[str, ...]
    display: str


LOOPBACK = Listen("127.0.0.1", ("127.0.0.1", "localhost"), "127.0.0.1")


def resolve_listen(bind: str) -> Listen:
    """Turn a bind into a concrete address; never a wildcard.

    With `tailscale-node` the server itself stays on loopback; the helper carries the
    tailnet side.
    """
    if bind in ("localhost", "tailscale-node"):
        return Listen("127.0.0.1", ("127.0.0.1", "localhost"), "127.0.0.1")
    if bind != "tailnet":
        raise ServeError(f"unknown bind `{bind}`")
    cli = _tailscale()
    try:
        address = subprocess.run(
            [cli, "ip", "-4"], check=True, capture_output=True, text=True, timeout=10,
        ).stdout.split()[0]
        status = json.loads(subprocess.run(
            [cli, "status", "--json"], check=True, capture_output=True, text=True, timeout=10,
        ).stdout)
    except (OSError, subprocess.SubprocessError, IndexError, json.JSONDecodeError) as error:
        raise ServeError(f"could not read this machine's Tailscale address: {error}") from None
    dns = str((status.get("Self") or {}).get("DNSName") or "").rstrip(".")
    hosts = [address]
    if dns:
        hosts += [dns, dns.split(".", 1)[0]]
    return Listen(address, tuple(hosts), dns or address)


class _Handler(BaseHTTPRequestHandler):
    server: _HttpServer
    protocol_version = "HTTP/1.1"
    server_version = "ReviewBot"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        return host in self.server.allowed_hosts

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        parts = urlsplit(origin)
        return parts.scheme in ("http", "https") and parts.netloc.lower() in self.server.allowed_hosts

    def _token_ok(self, token: str | None) -> bool:
        return self.server.tokens.accepts(token)

    def _bearer(self) -> str | None:
        header = self.headers.get("Authorization") or ""
        scheme, _, value = header.partition(" ")
        return value.strip() if scheme.lower() == "bearer" else None

    def _cookie(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie") or "")
        except CookieError:
            return None
        morsel = cookie.get(COOKIE)
        return morsel.value if morsel else None

    def _send(
        self, status: HTTPStatus, body: bytes = b"", content_type: str = "text/plain; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: HTTPStatus, data: Any) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _guard(self) -> bool:
        """Reject requests addressed to an unexpected host or sent from a foreign origin."""
        if not self._host_ok():
            self._send(HTTPStatus.MISDIRECTED_REQUEST, b"unexpected Host\n")
            return False
        if not self._origin_ok():
            self._send(HTTPStatus.FORBIDDEN, b"foreign Origin\n")
            return False
        return True

    def do_GET(self) -> None:
        if not self._guard():
            return
        parts = urlsplit(self.path)
        if parts.path == "/":
            self._page(parts.query)
        elif parts.path == "/api/prs":
            if not (self._token_ok(self._bearer()) or self._token_ok(self._cookie())):
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "token required"})
                return
            refresh = parse_qs(parts.query).get("refresh") == ["1"]
            self.server.board.refresh(force=refresh)
            self._json(HTTPStatus.OK, self.server.board.snapshot())
        elif parts.path == "/mcp":
            self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"POST only\n", headers={"Allow": "POST"})
        else:
            self._send(HTTPStatus.NOT_FOUND, b"not found\n")

    do_HEAD = do_GET

    def _page(self, query: str) -> None:
        """Serve the dashboard, trading a one-time `?token=` for a strict HttpOnly cookie."""
        offered = parse_qs(query).get("token", [None])[0]
        if offered is not None:
            if not self._token_ok(offered):
                self._send(HTTPStatus.UNAUTHORIZED, _DENIED, "text/html; charset=utf-8")
                return
            secure = "; Secure" if self.server.secure else ""
            self._send(HTTPStatus.SEE_OTHER, headers={
                "Location": "/",
                "Set-Cookie": f"{COOKIE}={offered}; HttpOnly; SameSite=Strict; Path=/{secure}",
            })
            return
        if not self._token_ok(self._cookie()):
            self._send(HTTPStatus.UNAUTHORIZED, _DENIED, "text/html; charset=utf-8")
            return
        self._send(HTTPStatus.OK, _page_html(), "text/html; charset=utf-8", headers={
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            ),
        })

    def do_POST(self) -> None:
        """Serve MCP (bearer token) and the dashboard's mark-read call (bearer or cookie).

        Both require a JSON body, which a cross-site form cannot send without a preflight.
        """
        if not self._guard():
            return
        path = urlsplit(self.path).path
        if path == "/mcp":
            allowed = self._token_ok(self._bearer())
        elif path == "/api/notifications/read":
            allowed = self._token_ok(self._bearer()) or self._token_ok(self._cookie())
        else:
            self._send(HTTPStatus.NOT_FOUND, b"not found\n")
            return
        if not allowed:
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "token required"})
            return
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            self._send(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, b"application/json required\n")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            self._send(HTTPStatus.BAD_REQUEST, b"bad Content-Length\n")
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {
                "jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"},
            })
            return
        if path == "/api/notifications/read":
            self._mark_read(payload)
            return
        reply = handle_payload(self.server.mcp, payload)
        if reply is None:
            self._send(HTTPStatus.ACCEPTED)
        else:
            self._json(HTTPStatus.OK, reply)


    def _mark_read(self, payload: Any) -> None:
        thread_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "`id` is required"})
            return
        try:
            item = self.server.board.mark_read(thread_id)
        except OpenError as error:
            self._json(HTTPStatus.NOT_FOUND, {"error": str(error)})
            return
        except GhError as error:
            self._json(HTTPStatus.BAD_GATEWAY, {"error": str(error)})
            return
        self._json(HTTPStatus.OK, {"marked_read": item.id, "key": item.key})


class _HttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self, address: tuple[str, int], board: Board, tokens: TokenFile, hosts: tuple[str, ...],
    ):
        super().__init__(address, _Handler)
        self.board = board
        self.tokens = tokens
        self.mcp = McpServer(board)
        self.secure = False
        port = self.server_address[1]
        self.allowed_hosts = frozenset(
            f"{host.lower()}:{port}" for host in hosts
        ) | ({host.lower() for host in hosts} if port == 80 else frozenset())

    def allow_https_host(self, host: str) -> None:
        """Accept a name served over HTTPS on 443 by a proxy in front of this server."""
        host = host.lower()
        self.allowed_hosts = self.allowed_hosts | {host, f"{host}:443"}
        self.secure = True


class Dashboard:
    """Run the dashboard and HTTP MCP endpoint for one config, in the foreground or a thread."""

    def __init__(
        self, config: Config, board: Board, *, serve: Serve | None = None,
        listen: Listen | None = None, on_login: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None, node: Node | None = None,
    ) -> None:
        """Bind the server and, for `tailscale-node`, bring the tailnet device up.

        Joining the tailnet can wait on the owner approving a sign-in link, which is passed
        to `on_login`, so construct this off any UI thread.
        """
        serve = serve or config.serve
        if serve is None:
            raise ServeError(
                f"add a `[serve]` table (port, bind, browser) to {config.path} to serve"
            )
        self.serve = serve
        self.listen = listen or resolve_listen(serve.bind)
        load_token(config)
        self.tokens = TokenFile(token_path(config))
        try:
            self.http = _HttpServer(
                (self.listen.address, serve.port), board, self.tokens, self.listen.hosts,
            )
        except OSError as error:
            raise ServeError(
                f"could not listen on {self.listen.address}:{serve.port}: {error}"
            ) from None
        self._thread: threading.Thread | None = None
        self.node: Node | None = None
        self.dns: str | None = None
        if serve.bind == "tailscale-node":
            self._start_node(config, serve, on_login, on_status, node)

    def _start_node(
        self, config: Config, serve: Serve, on_login: Callable[[str], None] | None,
        on_status: Callable[[str], None] | None, node: Node | None,
    ) -> None:
        try:
            self.node = node or Node(
                config, serve.hostname or "", on_login=on_login or (lambda url: None),
                on_status=on_status,
            )
            self.dns = self.node.start(self.port)
        except TailnetError as error:
            self.http.server_close()
            raise ServeError(str(error)) from None
        self.http.allow_https_host(self.dns)

    @property
    def port(self) -> int:
        return self.http.server_address[1]

    @property
    def url(self) -> str:
        if self.dns:
            return f"https://{self.dns}/"
        return f"http://{self.listen.display}:{self.port}/"

    @property
    def mcp_url(self) -> str:
        return f"{self.url}mcp"

    @property
    def token(self) -> str:
        """The token the server accepts now, after any `token --rotate`."""
        return self.tokens.current() or ""

    @property
    def login_url(self) -> str:
        """The dashboard URL carrying the token once, to set the browser's cookie."""
        return f"{self.url}?token={self.token}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        self.http.serve_forever()

    def stop(self) -> None:
        if self.node is not None:
            self.node.stop()
        if self._thread is not None:
            self.http.shutdown()
            self._thread.join(timeout=5)
        self.http.server_close()


_DENIED = (
    b"<!doctype html><meta charset=utf-8><title>ReviewBot</title>"
    b"<p>This dashboard needs its token. Open the URL printed by <code>reviewbot token</code>.</p>"
)


def _page_html() -> bytes:
    return files("reviewbot").joinpath("_resources/dashboard.html").read_bytes()
