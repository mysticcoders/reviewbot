"""The forge seam: PR models outside github.py, and GitHub's launch operations behind it."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

TARGETS = json.loads((Path(__file__).parent / "data" / "github_targets.json").read_text())


def test_models_live_outside_github():
    from reviewbot import github, models

    for name in ("ReviewCandidate", "QueueStatus", "AuthoredPR", "AuthoredStatus",
                 "ReviewerStatus", "Notification"):
        assert getattr(github, name) is getattr(models, name)
    assert issubclass(github.GhError, models.ForgeError)


@pytest.mark.parametrize("case", TARGETS, ids=[case["text"] for case in TARGETS])
def test_github_parses_targets(case):
    from reviewbot import github

    expected = tuple(case["expected"]) if case["expected"] else None
    assert github.parse_target(case["text"]) == expected


def test_github_head_ref():
    from reviewbot import github

    assert github.head_ref(12) == "pull/12/head"


def test_backend_is_github():
    from reviewbot import forge, github

    assert forge.backend(SimpleNamespace(), "acme/api") is github


@pytest.mark.parametrize(("code", "expected"), [
    (0, (True, "authenticated")),
    (1, (False, "run `gh auth login`")),
])
def test_github_auth_status(monkeypatch, code, expected):
    from reviewbot import github

    monkeypatch.setattr(github.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=code))
    assert github.auth_status() == expected


def test_github_clone_uses_gh(monkeypatch, tmp_path):
    from reviewbot import github

    seen = []
    monkeypatch.setattr(github, "run_command", lambda *args, **kwargs: seen.append(args))
    github.clone("acme/api", tmp_path / "api")
    assert seen == [("gh", "repo", "clone", "acme/api", str(tmp_path / "api"))]


@pytest.mark.parametrize("installed", [True, False])
def test_github_web_command_needs_gh(monkeypatch, installed):
    from reviewbot import github

    monkeypatch.setattr(github.shutil, "which", lambda name: f"/bin/{name}" if installed else None)
    expected = ["gh", "pr", "view", "12", "--repo", "acme/api", "--web"] if installed else None
    assert github.web_command("pr", "acme/api", 12) == expected
