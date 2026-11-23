"""A small Model Context Protocol server exposing the review board over stdio or HTTP."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, TextIO

from . import __version__
from .board import Board, OpenError, open_in_browser

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "ReviewBot lists the GitHub pull requests waiting on the user's review and the user's own "
    "open pull requests. Use open_pr to open one in the user's signed-in browser."
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_review_queue",
        "description": (
            "List open pull requests requesting the user's review, with status (ready, updated, "
            "reviewed, changes-requested, draft, or in-progress when a review tab is open), "
            "checks, author, and URL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "refresh": {"type": "boolean", "description": "Fetch fresh data from GitHub."},
                "actionable_only": {
                    "type": "boolean",
                    "description": "Only PRs that are ready or updated and not already under review.",
                },
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "list_my_prs",
        "description": (
            "List the user's own open pull requests with status, checks, mergeability, reviewer "
            "states, and which reviewers need a re-request."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "refresh": {"type": "boolean", "description": "Fetch fresh data from GitHub."},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "open_pr",
        "description": (
            "Open a pull request from the review queue or the user's open PRs in the user's "
            "configured, signed-in browser on their computer. Accepts owner/repo#number or the "
            "PR URL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pr": {"type": "string", "description": "owner/repo#number or the PR URL."},
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
        if name == "list_review_queue":
            self.board.refresh(force=bool(arguments.get("refresh")))
            snapshot = self.board.snapshot()
            queue = snapshot["queue"]
            if arguments.get("actionable_only"):
                queue = [item for item in queue if item["actionable"]]
            return _result(request_id, _tool_result(
                {"scope": snapshot["scope"], "queue": queue,
                 "error": snapshot["errors"].get("queue")},
            ))
        if name == "list_my_prs":
            self.board.refresh(force=bool(arguments.get("refresh")))
            snapshot = self.board.snapshot()
            return _result(request_id, _tool_result(
                {"scope": snapshot["scope"], "mine": snapshot["mine"],
                 "error": snapshot["errors"].get("mine")},
            ))
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
