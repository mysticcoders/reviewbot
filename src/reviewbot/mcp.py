"""A small Model Context Protocol server exposing the review board over stdio or HTTP."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, TextIO

from . import __version__
from .board import CHOICES, Board, OpenError, open_in_browser
from .github import GhError

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "ReviewBot lists open pull requests in the watched repositories (by default those waiting "
    "on the user's review), the pull requests the user authored or is assigned to, and the "
    "user's unread GitHub notifications about pull requests and issues. Each list takes a "
    "`filter`; without one, it uses the filter the user configured. Use open_pr to open one in the user's signed-in browser, "
    "and mark_notification_read once the user has dealt with a notification."
)

def _filter_property(view: str, meanings: str) -> dict[str, Any]:
    return {
        "type": "string", "enum": list(CHOICES[view]),
        "description": f"Which ones to list: {meanings}. Omit for the user's configured filter.",
    }


OPEN_PRS_TOOL: dict[str, Any] = {
    "name": "list_open_prs",
    "description": (
        "List open pull requests in the watched repositories, with status (ready, updated, "
        "reviewed, changes-requested, draft, or in-progress when a review tab is open), "
        "checks, author, how the user's review was requested (direct, team:<slug>, or none), "
        "and URL."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "filter": _filter_property(
                "queue",
                "requested (asking for the user's review), involves (any PR the user is part "
                "of), all (every open PR except the user's own), stale (requested with no "
                "recent activity)",
            ),
            "refresh": {"type": "boolean", "description": "Fetch fresh data from GitHub."},
            "actionable_only": {
                "type": "boolean",
                "description": "Only PRs that are ready or updated and not already under review.",
            },
        },
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "openWorldHint": True},
}

TOOLS: list[dict[str, Any]] = [
    OPEN_PRS_TOOL,
    {
        **OPEN_PRS_TOOL,
        "name": "list_review_queue",
        "description": "Same as list_open_prs; kept for clients that use the older name.",
    },
    {
        "name": "list_my_prs",
        "description": (
            "List open pull requests the user authored or is assigned to (`roles`), with "
            "status, checks, mergeability, reviewer states, and which reviewers need a "
            "re-request."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": _filter_property(
                    "mine",
                    "all, ready (no drafts), needs-action (changes requested, failing checks, "
                    "conflicts, no reviewers, or reviewers to ask again), stale (no recent "
                    "activity)",
                ),
                "refresh": {"type": "boolean", "description": "Fetch fresh data from GitHub."},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "list_notifications",
        "description": (
            "List the user's unread GitHub notifications about pull requests and issues in "
            "watched repositories: thread id, owner/repo#number, kind (pr or issue), title, "
            "reason (such as comment, mention, review_requested, author, assign, state_change), "
            "updated time, and URL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": _filter_property(
                    "notifications",
                    "all, participating (threads the user takes part in or was mentioned "
                    "in), reviews (review requests and mentions)",
                ),
                "refresh": {"type": "boolean", "description": "Fetch fresh data from GitHub."},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "mark_notification_read",
        "description": (
            "Mark one unread notification read on GitHub, by the thread id from "
            "list_notifications. Only the user sees read state; nothing is posted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Thread id from list_notifications."},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
            "openWorldHint": True,
        },
    },
    {
        "name": "open_pr",
        "description": (
            "Open a pull request from the review queue, the user's open PRs, or a notification "
            "in the user's configured, signed-in browser on their computer. Accepts "
            "owner/repo#number or the URL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pr": {"type": "string", "description": "owner/repo#number or the URL."},
            },
            "required": ["pr"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
    },
]


class McpServer:
    """Dispatch MCP JSON-RPC messages to the board's tools."""

    def __init__(self, board: Board, opener: Callable[[str], None] | None = None) -> None:
        self.board = board
        self.opener = opener or (lambda url: open_in_browser(board.config.serve, url))

    def handle(self, message: Any) -> dict[str, Any] | None:
        """Answer one JSON-RPC message; notifications and responses get no reply."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, -32600, "Invalid Request")
        method = message.get("method")
        if "id" not in message:
            return None
        request_id = message["id"]
        if not isinstance(method, str):
            return _error(request_id, -32600, "Invalid Request")
        params = message.get("params") or {}
        if method == "initialize":
            return _result(request_id, self._initialize(params))
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": TOOLS})
        if method == "tools/call":
            return self._call(request_id, params)
        return _error(request_id, -32601, f"Method not found: {method}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "reviewbot", "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    def _call(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "arguments must be an object")
        lists = {
            "list_open_prs": "queue", "list_review_queue": "queue",
            "list_my_prs": "mine", "list_notifications": "notifications",
        }
        if name in lists:
            view = lists[name]
            try:
                wanted = {view: arguments.get("filter")}
                self.board.refresh(force=bool(arguments.get("refresh")), wanted=wanted)
                snapshot = self.board.snapshot(wanted)
            except OpenError as error:
                return _result(request_id, _tool_error(str(error)))
            items = snapshot[view]
            if view == "queue" and arguments.get("actionable_only"):
                items = [item for item in items if item["actionable"]]
            return _result(request_id, _tool_result({
                "scope": snapshot["scope"], "filter": snapshot["filters"][view],
                view: items, "error": snapshot["errors"].get(view),
            }))
        if name == "mark_notification_read":
            thread_id = arguments.get("id")
            if not isinstance(thread_id, str) or not thread_id.strip():
                return _result(request_id, _tool_error("`id` is required"))
            try:
                item = self.board.mark_read(thread_id.strip())
            except (OpenError, GhError) as error:
                return _result(request_id, _tool_error(str(error)))
            return _result(request_id, _tool_result({"marked_read": item.id, "key": item.key}))
        if name == "open_pr":
            target = arguments.get("pr")
            if not isinstance(target, str) or not target.strip():
                return _result(request_id, _tool_error("`pr` is required"))
            try:
                try:
                    url = self.board.find_url(target)
                except OpenError:
                    self.board.refresh()
                    url = self.board.find_url(target)
                self.opener(url)
            except OpenError as error:
                return _result(request_id, _tool_error(str(error)))
            return _result(request_id, _tool_result({"opened": url}))
        return _error(request_id, -32602, f"Unknown tool: {name}")


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_result(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data, indent=2)}],
        "structuredContent": data,
    }


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def handle_payload(server: McpServer, payload: Any) -> Any:
    """Answer a single message or a batch, returning None when nothing needs a reply."""
    if isinstance(payload, list):
        replies = [reply for item in payload if (reply := server.handle(item)) is not None]
        return replies or None
    return server.handle(payload)


def serve_stdio(server: McpServer, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
    """Run the newline-delimited JSON-RPC stdio transport until stdin closes."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            reply: Any = _error(None, -32700, "Parse error")
        else:
            reply = handle_payload(server, payload)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
