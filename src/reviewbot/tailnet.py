"""Run the optional reviewbot-tsnet helper that gives ReviewBot its own tailnet device."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO, Callable

from .config import Config

HELPER = "reviewbot-tsnet"
INSTALL_HINT = (
    'install it with: uv tool install --force "reviewbot[tailnet] @ '
    'git+https://github.com/mysticcoders/reviewbot"'
)


class TailnetError(Exception):
    pass


def helper_path() -> Path:
    """Find the helper beside this Python (the `[tailnet]` extra) or on PATH."""
    if platform.system() != "Darwin":
        raise TailnetError('bind = "tailscale-node" is only supported on macOS')
    beside = Path(sys.executable).parent / HELPER
    if beside.exists():
        return beside
    found = shutil.which(HELPER)
    if found:
        return Path(found)
    raise TailnetError(f"{HELPER} is not installed; {INSTALL_HINT}")


def state_dir(config: Config, hostname: str) -> Path:
    return config.path.parent / "tsnet" / hostname


def _node_file(config: Config, hostname: str) -> Path:
    return state_dir(config, hostname) / "reviewbot-node.json"


def known_dns(config: Config, hostname: str) -> str | None:
    """The node's tailnet DNS name as of its last sign-in, if it has signed in."""
    try:
        return json.loads(_node_file(config, hostname).read_text(encoding="utf-8")).get("dns")
    except (OSError, ValueError):
        return None


def _remember(config: Config, hostname: str, dns: str, owner: str) -> None:
    _node_file(config, hostname).write_text(
        json.dumps({"dns": dns, "owner": owner}) + "\n", encoding="utf-8",
    )


class Node:
    """One reviewbot-tsnet process, from launch through sign-in to shutdown.

    `on_login` receives each sign-in URL while the node waits for its owner to approve it.
    """

    def __init__(
        self, config: Config, hostname: str, *, on_login: Callable[[str], None],
        helper: Path | None = None,
    ) -> None:
        self.config = config
        self.hostname = hostname
        self.on_login = on_login
        self.helper = helper or helper_path()
        self.dns: str | None = None
        self.owner: str | None = None
        self._process: subprocess.Popen[str] | None = None
        self._log: IO[str] | None = None

    def _spawn(self, command: str, *extra: str) -> subprocess.Popen[str]:
        state = state_dir(self.config, self.hostname)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        state.chmod(0o700)
        self._log = (state / "helper.log").open("a", encoding="utf-8")
        return subprocess.Popen(
            [str(self.helper), command, "--hostname", self.hostname, "--state", str(state), *extra],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log, text=True, bufsize=1,
        )

    def _await(self, process: subprocess.Popen[str], until: str) -> dict:
        """Read events until `until` arrives, passing sign-in links on; fail on error or exit."""
        assert process.stdout is not None
        for line in process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("event")
            if kind == "login":
                self.on_login(event.get("url", ""))
            elif kind == "error":
                self._close(process)
                raise TailnetError(event.get("message") or "reviewbot-tsnet failed")
            elif kind == until:
                return event
        code = process.wait()
        self._close(process)
        raise TailnetError(f"reviewbot-tsnet exited ({code}) before it was {until}")

    def start(self, target_port: int) -> str:
        """Join the tailnet and proxy HTTPS to the loopback port; return the node's DNS name."""
        self._process = self._spawn("run", "--target", f"127.0.0.1:{target_port}")
        ready = self._await(self._process, "ready")
        self.dns, self.owner = ready.get("dns") or "", ready.get("owner") or ""
        if not self.dns:
            self.stop()
            raise TailnetError("reviewbot-tsnet did not report a DNS name")
        _remember(self.config, self.hostname, self.dns, self.owner)
        threading.Thread(target=self._drain, daemon=True).start()
        return self.dns

    def _drain(self) -> None:
        process = self._process
        if process is not None and process.stdout is not None:
            for _ in process.stdout:
                pass

    def login(self) -> tuple[str, str]:
        """Sign the node in (or confirm it is signed in) and return its DNS name and owner."""
        process = self._spawn("login")
        ready = self._await(process, "ready")
        self._close(process)
        dns, owner = ready.get("dns") or "", ready.get("owner") or ""
        if dns:
            _remember(self.config, self.hostname, dns, owner)
        return dns, owner

    def logout(self) -> None:
        """Sign the node out of the tailnet and delete its state."""
        state = state_dir(self.config, self.hostname)
        if state.exists():
            process = self._spawn("logout")
            try:
                self._await(process, "logged-out")
            finally:
                self._close(process)
            shutil.rmtree(state)

    def _close(self, process: subprocess.Popen[str]) -> None:
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if self._log is not None:
            self._log.close()
            self._log = None

    def stop(self) -> None:
        """Close the helper's stdin, which shuts it down, and wait for it to exit."""
        if self._process is not None:
            process, self._process = self._process, None
            self._close(process)
