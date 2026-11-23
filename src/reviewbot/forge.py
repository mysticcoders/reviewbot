"""Pick the forge (GitHub or GitLab) that hosts a repository, and merge their queues.

Each forge is a module with the same functions: `clone`, `head_ref`, `parse_target`,
`auth_status`, `web_command` and `review_queue`. This mirrors how `multiplexer.py`
routes to Herdr or Rex.

A repository whose first part contains a dot (`host/group/project`) lives on the forge
that `[[forges]]` names for that host. Any other repository (`owner/repo`) is on GitHub.
"""

from __future__ import annotations

import asyncio
from types import ModuleType

from . import github, gitlab
from .config import Config
from .models import ForgeError, ReviewCandidate

FORGES: dict[str, ModuleType] = {"github": github, "gitlab": gitlab}


def backend(config: Config, repo: str) -> ModuleType:
    """Return the forge module for `owner/repo` (GitHub) or `host/path` (a `[[forges]]` host)."""
    host = repo.split("/", 1)[0].lower()
    if "." not in host:
        return github
    kind = config.forges.get(host)
    if kind is None:
        raise ForgeError(f"no `[[forges]]` entry for `{host}`; add one to the config")
    return FORGES[kind]


async def review_queue(config: Config) -> list[ReviewCandidate]:
    """Every watched forge's review requests, merged and sorted newest first.

    GitHub is queried only when the watch list includes GitHub entries (or `*`). Each
    other host is queried once, with its watch entries.
    """
    scope = config.scope
    queries = [github.review_queue(scope)] if scope.watches_github else []
    for host in scope.hosts:
        entries = tuple(entry for entry in scope.hosted if entry.split("/", 1)[0] == host)
        queries.append(backend(config, host + "/").review_queue(host, entries))
    batches = await asyncio.gather(*queries)
    found = [item for batch in batches for item in batch]
    return sorted(found, key=lambda item: item.updated_at, reverse=True)
