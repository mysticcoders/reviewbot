"""Run external commands for launches and forges, turning failures into LaunchError."""

from __future__ import annotations

import os
import subprocess


class LaunchError(Exception):
    pass


def run_command(
    *args: str, timeout: float | None = 120, input: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Run a command and return its stdout, raising LaunchError with its stderr on failure.

    `env` adds variables to this process's environment for the command.
    """
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False, input=input,
            env={**os.environ, **env} if env else None,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LaunchError(f"could not run {' '.join(args)}: {error}") from error
    if result.returncode:
        raise LaunchError(result.stderr.strip() or f"{' '.join(args)} exited {result.returncode}")
    return result.stdout.strip()
