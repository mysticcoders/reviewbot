"""PR scope must survive stale caches and reused worktrees."""
from pathlib import Path
from types import SimpleNamespace
import subprocess

from reviewbot.launch import prepare_checkout


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def commit(path: Path, name: str) -> str:
    (path / name).write_text(name, encoding="utf-8")
    git(path, "add", name)
    git(path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-m", name)
    return git(path, "rev-parse", "HEAD")


def history(tmp_path: Path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)],
                   check=True, capture_output=True)
    writer = tmp_path / "writer"
    subprocess.run(["git", "clone", str(remote), str(writer)], check=True, capture_output=True)
    old_base = commit(writer, "old.txt")
    git(writer, "push", "origin", "main")
    work = tmp_path / "work"
    cache = work / "repos" / "acme-demo"
    cache.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", str(remote), str(cache)], check=True, capture_output=True)
    base = commit(writer, "already-merged.txt")
    git(writer, "push", "origin", "main")
    git(writer, "checkout", "-b", "feature")
    head = commit(writer, "feature.txt")
    git(writer, "push", "origin", "HEAD:refs/pull/12/head")
    config = SimpleNamespace(work_dir=work, checkouts=())
    candidate = SimpleNamespace(repo="acme/demo", number=12, head_sha=head, base_branch="main")
    return writer, cache, config, candidate, old_base, base


def test_fresh_remote_base_excludes_previously_merged_changes(tmp_path):
    _, cache, config, candidate, old_base, base = history(tmp_path)
    checkout = prepare_checkout(config, candidate)

    assert git(cache, "rev-parse", "main") == old_base
    assert git(checkout, "rev-parse", "origin/main") == base
    assert git(checkout, "rev-parse", "HEAD") == candidate.head_sha
    assert git(checkout, "merge-base", "origin/main", "HEAD") == base
    assert git(checkout, "diff", "--name-only", "origin/main...HEAD") == "feature.txt"


def test_reused_worktree_refreshes_base_without_moving_pr_head(tmp_path):
    writer, _, config, candidate, _, _ = history(tmp_path)
    checkout = prepare_checkout(config, candidate)
    git(writer, "checkout", "main")
    newer_base = commit(writer, "newer-base.txt")
    git(writer, "push", "origin", "main")

    assert prepare_checkout(config, candidate) == checkout
    assert git(checkout, "rev-parse", "origin/main") == newer_base
    assert git(checkout, "rev-parse", "HEAD") == candidate.head_sha
    assert git(checkout, "diff", "--name-only", "origin/main...HEAD") == "feature.txt"
