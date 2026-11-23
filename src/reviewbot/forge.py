"""Pick the forge (GitHub today) that hosts a repository.

Each forge is a module with the same functions: `clone`, `head_ref`, `parse_target`,
`auth_status` and `web_command`, plus the queue queries. This mirrors how
`multiplexer.py` routes to Herdr or Rex.
"""

from __future__ import annotations

from types import ModuleType

from . import github
from .config import Config

FORGES: dict[str, ModuleType] = {"github": github}


def backend(config: Config, repo: str) -> ModuleType:
    """Return the forge module for a repository, given as `owner/repo`.

    GitHub is the only forge until the config can name others, so every repository
    maps to it and `config` is not read yet.
    """
    return github
