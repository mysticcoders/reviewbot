"""GitLab access through the `glab` CLI: the review queue, MR status, and launch helpers.

A GitLab repository is named with its host first, `host/group/project`, so it cannot be
mistaken for a GitHub `owner/repo`. Every API call names the host with `--hostname`, so
several GitLab servers can be watched at once.

GitLab records no "review at commit X". The status rules therefore compare times: the
user's latest approval or change request counts as a review, and a push after it makes
the MR `updated`. See `classify`.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .models import ForgeError, QueueStatus, ReviewCandidate
from .process import run_command

REVIEW_NOTES = {
    "approved this merge request": "APPROVED",
    "requested changes": "CHANGES_REQUESTED",
}
PASSING = {"success", "skipped"}
FAILING = {"failed", "canceled"}


class GlabError(ForgeError):
    """A `glab` command failed, or GitLab returned data ReviewBot cannot read."""


async def _api(host: str, endpoint: str) -> Any:
    """GET one GitLab API endpoint on `host` through `glab api`, parsed from JSON."""
    try:
        process = await asyncio.create_subprocess_exec(
            "glab", "api", "--hostname", host, endpoint,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise GlabError(f"could not run glab: {error}") from None
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    except TimeoutError:
        process.kill()
        raise GlabError(f"glab api {endpoint} on {host} timed out after 60s") from None
    if process.returncode:
        raise GlabError(stderr.decode().strip() or f"glab api {endpoint} exited {process.returncode}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as error:
        raise GlabError(f"GitLab returned invalid JSON for {endpoint}: {error}") from None


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _utc(text: str) -> str:
    """An API time as UTC with a `Z`, so it sorts as text alongside GitHub's times."""
    if not text:
        return ""
    return _instant(text).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify(
    *, draft: bool, reviews: list[dict[str, str]], versions: list[dict[str, str]]
) -> tuple[QueueStatus, str | None, str | None]:
    """Decide an MR's queue status from the user's reviews and the MR's pushes.

    `reviews` holds `{"state", "at"}` items, with state `APPROVED` or `CHANGES_REQUESTED`.
    `versions` holds `{"at", "sha"}` items, one per push. Times may carry any UTC offset.
    Returns the status, the latest review's state, and the head commit it reviewed (the
    newest push at or before the review), or None for the last two when nothing applies.
    """
    if draft:
        return QueueStatus.DRAFT, None, None
    if not reviews:
        return QueueStatus.READY, None, None
    latest = max(reviews, key=lambda review: _instant(review["at"]))
    reviewed_at = _instant(latest["at"])
    pushes = sorted(versions, key=lambda version: _instant(version["at"]))
    before = [version for version in pushes if _instant(version["at"]) <= reviewed_at]
    sha = before[-1]["sha"] if before else None
    if any(_instant(version["at"]) > reviewed_at for version in pushes):
        return QueueStatus.UPDATED, latest["state"], sha
    if latest["state"] == "CHANGES_REQUESTED":
        return QueueStatus.CHANGES_REQUESTED, latest["state"], sha
    return QueueStatus.REVIEWED, latest["state"], sha


def _checks(pipeline: dict[str, Any] | None) -> str:
    status = (pipeline or {}).get("status")
    if not status:
        return "none"
    if status in PASSING:
        return "passing"
    if status in FAILING:
        return "failing"
    return "pending"


async def _details(host: str, path: str, iid: int, login: str) -> ReviewCandidate:
    base = f"projects/{quote(path, safe='')}/merge_requests/{iid}"
    mr, notes, versions = await asyncio.gather(
        _api(host, base),
        _api(host, f"{base}/notes?sort=desc&per_page=100"),
        _api(host, f"{base}/versions"),
    )
    reviews = [
        {"state": REVIEW_NOTES[note["body"]], "at": note["created_at"]}
        for note in notes
        if note.get("system") and (note.get("author") or {}).get("username") == login
        and note.get("body") in REVIEW_NOTES
    ]
    pushes = [{"at": item["created_at"], "sha": item["head_commit_sha"]} for item in versions]
    draft = bool(mr.get("draft"))
    status, review_state, review_sha = classify(draft=draft, reviews=reviews, versions=pushes)
    return ReviewCandidate(
        repo=f"{host}/{path}", number=iid, title=mr.get("title", ""),
        author=(mr.get("author") or {}).get("username", "unknown"),
        url=mr.get("web_url", ""), updated_at=_utc(mr.get("updated_at", "")),
        head_sha=mr.get("sha", ""), head_branch=mr.get("source_branch", ""),
        base_branch=mr.get("target_branch", ""), is_draft=draft,
        # shortcut: GitLab approvals are not mapped to a review decision, and commits since
        # the review are not counted; add both when a view needs them.
        review_decision="", request_source="direct",
        last_review_state=review_state, last_review_sha=review_sha, commits_since_review=0,
        checks=_checks(mr.get("head_pipeline")), status=status, forge="gitlab",
    )


def _watched(repo: str, entries: tuple[str, ...]) -> bool:
    wanted = repo.lower()
    return any(
        wanted == entry.lower() or wanted.startswith(entry.lower() + "/") for entry in entries
    )


async def review_queue(host: str, entries: tuple[str, ...]) -> list[ReviewCandidate]:
    """Open MRs on `host` that list the signed-in user as a reviewer, newest first.

    `entries` are watch entries on this host (`host`, `host/group` or `host/group/project`);
    an MR is kept when its project is one of them or lies below one.
    """
    login = (await _api(host, "user"))["username"]
    # shortcut: one page of 100 MRs; paginate when a reviewer has more open requests.
    listed = await _api(
        host, f"merge_requests?scope=all&state=opened&reviewer_username={login}&per_page=100",
    )
    wanted = []
    for item in listed:
        path = item["references"]["full"].rsplit("!", 1)[0]
        if _watched(f"{host}/{path}", entries):
            wanted.append((path, item["iid"]))
    candidates = await asyncio.gather(
        *(_details(host, path, iid, login) for path, iid in wanted)
    )
    return sorted(candidates, key=lambda item: item.updated_at, reverse=True)


TARGET = re.compile(
    r"(?:https://)?([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)/((?:[^/\s!]+/)+[^/\s!]+)"
    r"(?:!|/-/merge_requests/)(\d+)/?"
)


def parse_target(text: str) -> tuple[str, int] | None:
    """Read `host/group/project!iid` or a GitLab MR URL into `host/path` and the MR number.

    Surrounding spaces are ignored. Returns None when the text is neither form.
    """
    match = TARGET.fullmatch(text.strip())
    if not match:
        return None
    return f"{match.group(1).lower()}/{match.group(2)}", int(match.group(3))


def head_ref(number: int) -> str:
    """The ref that holds an MR's head commit on GitLab."""
    return f"merge-requests/{number}/head"


def clone(repo: str, destination: Path) -> None:
    """Clone `host/path` with `glab` when it is installed, else with plain `git` over HTTPS.

    Raises LaunchError when the clone fails or takes longer than ten minutes.
    """
    if shutil.which("glab"):
        run_command("glab", "repo", "clone", f"https://{repo}", str(destination), timeout=600)
    else:
        run_command("git", "clone", f"https://{repo}.git", str(destination), timeout=600)


def auth_status(host: str) -> tuple[bool, str]:
    """Whether `glab` is signed in to `host`, with a short detail for `reviewbot doctor`."""
    result = subprocess.run(
        ["glab", "auth", "status", "--hostname", host], capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        return True, "authenticated"
    return False, f"run `glab auth login --hostname {host}`"


def web_command(kind: str, repo: str, number: int) -> list[str] | None:
    """The `glab` command that opens an MR (`kind` "pr") or issue ("issue") in the browser.

    Returns None when `glab` is not installed; the caller then opens the URL itself.
    """
    if shutil.which("glab") is None:
        return None
    command = "mr" if kind == "pr" else "issue"
    return ["glab", command, "view", str(number), "-R", f"https://{repo}", "--web"]
