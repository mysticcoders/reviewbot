"""GitLab as a second forge: config, keys, status rules, targets, commands and the queue."""
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

DATA = Path(__file__).parent / "data"
TARGETS = json.loads((DATA / "gitlab_targets.json").read_text())
CLASSIFY = json.loads((DATA / "gitlab_classify.json").read_text())
QUEUE = json.loads((DATA / "gitlab" / "queue.json").read_text())
HOST = "gitlab.example.org"

FORGES = f'''
[[forges]]
host = "{HOST}"
kind = "gitlab"
'''


def write(tmp_path, watch, extra=""):
    path = tmp_path / "config.toml"
    path.write_text(f'multiplexer = "herdr"\nwatch = {json.dumps(watch)}\n{extra}', encoding="utf-8")
    return path


def test_watch_splits_github_and_hosted_entries(tmp_path):
    from reviewbot.config import load

    config = load(write(tmp_path, ["acme", f"{HOST}/camp", f"{HOST}/camp/sub/tool"], FORGES))
    assert config.scope.owners == ("acme",)
    assert config.scope.hosted == (f"{HOST}/camp", f"{HOST}/camp/sub/tool")
    assert config.scope.watches_github
    assert config.forges == {HOST: "gitlab"}


def test_hosted_only_watch_skips_github(tmp_path):
    from reviewbot.config import load

    config = load(write(tmp_path, [f"{HOST}/camp"], FORGES))
    assert not config.scope.watches_github
    assert not config.scope.is_all


@pytest.mark.parametrize(("watch", "extra", "message"), [
    ([f"{HOST}/camp"], "", f"no `[[forges]]` entry for `{HOST}`"),
    (["acme"], '[[forges]]\nhost = "x.example.org"\nkind = "gitea"\n', "kind must be one of"),
    (["acme"], '[[forges]]\nhost = "github.com"\nkind = "gitlab"\n', "github.com"),
    (["acme"], FORGES + FORGES, "more than once"),
])
def test_invalid_forges(tmp_path, watch, extra, message):
    from reviewbot.config import ConfigError, load

    with pytest.raises(ConfigError, match=re.escape(message)):
        load(write(tmp_path, watch, extra))


def candidate(**changes):
    from reviewbot.models import QueueStatus, ReviewCandidate

    fields = {
        "repo": f"{HOST}/camp/tool", "number": 7, "title": "t", "author": "noa", "url": "u",
        "updated_at": "", "head_sha": "b" * 40, "head_branch": "h", "base_branch": "develop",
        "is_draft": False, "review_decision": "", "request_source": "direct",
        "last_review_state": None, "last_review_sha": None, "commits_since_review": 0,
        "checks": "none", "status": QueueStatus.READY, "forge": "gitlab",
    }
    return ReviewCandidate(**{**fields, **changes})


def test_gitlab_key_and_tab_marker_use_bang():
    from reviewbot.launch import review_marker

    item = candidate()
    assert item.key == f"{HOST}/camp/tool!7"
    assert review_marker(item) == f"{HOST}/camp/tool !7 "


def test_github_key_is_unchanged():
    from reviewbot.launch import review_marker

    item = candidate(repo="acme/api", number=12, forge="github")
    assert item.key == "acme/api#12"
    assert review_marker(item) == "acme/api #12 "


@pytest.mark.parametrize("case", CLASSIFY, ids=[case["name"] for case in CLASSIFY])
def test_classify(case):
    from reviewbot import gitlab

    status, state, sha = gitlab.classify(
        draft=case["draft"], reviews=case["reviews"], versions=case["versions"],
    )
    assert [status.value, state, sha] == case["expected"]


@pytest.mark.parametrize("case", TARGETS, ids=[case["text"] for case in TARGETS])
def test_gitlab_parses_targets(case):
    from reviewbot import gitlab

    expected = tuple(case["expected"]) if case["expected"] else None
    assert gitlab.parse_target(case["text"]) == expected


def test_gitlab_head_ref():
    from reviewbot import gitlab

    assert gitlab.head_ref(7) == "merge-requests/7/head"


@pytest.mark.parametrize(("installed", "expected"), [
    (True, ["glab", "mr", "view", "7", "-R", f"https://{HOST}/camp/tool", "--web"]),
    (False, None),
])
def test_gitlab_web_command_needs_glab(monkeypatch, installed, expected):
    from reviewbot import gitlab

    monkeypatch.setattr(gitlab.shutil, "which", lambda name: f"/bin/{name}" if installed else None)
    assert gitlab.web_command("pr", f"{HOST}/camp/tool", 7) == expected


@pytest.mark.parametrize(("installed", "expected"), [
    (True, ("glab", "repo", "clone", f"https://{HOST}/camp/tool", "DEST")),
    (False, ("git", "clone", f"https://{HOST}/camp/tool.git", "DEST")),
])
def test_gitlab_clone_prefers_glab(monkeypatch, installed, expected):
    from reviewbot import gitlab

    seen = []
    monkeypatch.setattr(gitlab.shutil, "which", lambda name: f"/bin/{name}" if installed else None)
    monkeypatch.setattr(gitlab, "run_command", lambda *args, **kwargs: seen.append(args))
    gitlab.clone(f"{HOST}/camp/tool", Path("DEST"))
    assert seen == [expected]


@pytest.mark.parametrize(("code", "expected"), [
    (0, (True, "authenticated")),
    (1, (False, f"run `glab auth login --hostname {HOST}`")),
])
def test_gitlab_auth_status(monkeypatch, code, expected):
    from reviewbot import gitlab

    seen = []

    def run(args, **kwargs):
        seen.append(args)
        return SimpleNamespace(returncode=code)

    monkeypatch.setattr(gitlab.subprocess, "run", run)
    assert gitlab.auth_status(HOST) == expected
    assert seen == [["glab", "auth", "status", "--hostname", HOST]]


def fake_api(monkeypatch, data=QUEUE):
    from reviewbot import gitlab

    calls = []

    async def api(host, endpoint):
        calls.append((host, endpoint))
        return data[endpoint]

    monkeypatch.setattr(gitlab, "_api", api)
    return calls


def test_review_queue_reads_watched_merge_requests(monkeypatch):
    from reviewbot import gitlab

    calls = fake_api(monkeypatch)
    [item] = asyncio.run(gitlab.review_queue(HOST, (f"{HOST}/camp",)))
    assert {host for host, _ in calls} == {HOST}
    assert item.key == f"{HOST}/camp/sub/tool!7"
    assert item.forge == "gitlab"
    assert (item.title, item.author, item.head_branch, item.base_branch) == (
        "Add the parser", "noa", "parser", "develop")
    assert item.head_sha == "b" * 40
    assert item.status.value == "updated"
    assert (item.last_review_state, item.last_review_sha) == ("APPROVED", "a" * 40)
    assert item.checks == "passing"
    assert item.request_source == "direct"
    assert item.url == "https://gitlab.example.org/camp/sub/tool/-/merge_requests/7"


def test_review_queue_watching_the_whole_host(monkeypatch):
    from reviewbot import gitlab

    detail = QUEUE["projects/camp%2Fsub%2Ftool/merge_requests/7"]
    fake_api(monkeypatch, {
        **QUEUE,
        "projects/other%2Fthing/merge_requests/3": {
            **detail, "iid": 3, "updated_at": "2026-11-06T09:00:00Z"},
        "projects/other%2Fthing/merge_requests/3/notes?sort=desc&per_page=100": [],
        "projects/other%2Fthing/merge_requests/3/versions": [],
    })
    items = asyncio.run(gitlab.review_queue(HOST, (HOST,)))
    assert [item.key for item in items] == [f"{HOST}/other/thing!3", f"{HOST}/camp/sub/tool!7"]


def test_backend_routes_by_host():
    from reviewbot import forge, github, gitlab

    config = SimpleNamespace(forges={HOST: "gitlab"})
    assert forge.backend(config, f"{HOST}/camp/tool") is gitlab
    assert forge.backend(config, "acme/api") is github


def test_forge_queue_merges_github_and_gitlab(monkeypatch, tmp_path):
    from reviewbot import forge, github, gitlab
    from reviewbot.config import load

    config = load(write(tmp_path, ["acme", f"{HOST}/camp"], FORGES))
    seen = []

    async def github_queue(scope):
        seen.append(("github", scope.owners))
        return [candidate(repo="acme/api", number=1, forge="github", updated_at="2026-11-01")]

    async def gitlab_queue(host, paths):
        seen.append(("gitlab", host, paths))
        return [candidate(updated_at="2026-11-02")]

    monkeypatch.setattr(github, "review_queue", github_queue)
    monkeypatch.setattr(gitlab, "review_queue", gitlab_queue)
    items = asyncio.run(forge.review_queue(config))
    assert [item.key for item in items] == [f"{HOST}/camp/tool!7", "acme/api#1"]
    assert sorted(seen) == [("github", ("acme",)), ("gitlab", HOST, (f"{HOST}/camp",))]


def test_forge_queue_skips_unwatched_github(monkeypatch, tmp_path):
    from reviewbot import forge, github, gitlab
    from reviewbot.config import load

    config = load(write(tmp_path, [f"{HOST}/camp"], FORGES))

    async def github_queue(scope):
        raise AssertionError("GitHub is not watched")

    async def gitlab_queue(host, paths):
        return []

    monkeypatch.setattr(github, "review_queue", github_queue)
    monkeypatch.setattr(gitlab, "review_queue", gitlab_queue)
    assert asyncio.run(forge.review_queue(config)) == []
