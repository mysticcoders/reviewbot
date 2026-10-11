import re
from pathlib import Path

import pytest

from reviewbot.config import Agent, load
from reviewbot.github import QueueStatus, ReviewCandidate
from reviewbot.herdr import (
    AGENT_NAME_MAX,
    LaunchError,
    _agent_name,
    _pane_ids,
    _start_agent_with_retry,
    render_prompt,
)
from reviewbot.run import launch_review


def candidate() -> ReviewCandidate:
    return ReviewCandidate(
        repo="acme/demo", number=12, title="Fix", author="author",
        url="https://github.com/acme/demo/pull/12", updated_at="now",
        head_sha="abcdef123456", head_branch="fix", base_branch="main",
        is_draft=False, review_decision="REVIEW_REQUIRED", request_source="direct",
        last_review_state="CHANGES_REQUESTED", last_review_sha="123456789",
        commits_since_review=2, checks="passing", status=QueueStatus.UPDATED,
    )


def test_recursive_pane_id_extraction():
    snapshot = {"workspaces": [{"tabs": [{"panes": [{"pane_id": "one"}]}]}]}
    assert _pane_ids(snapshot) == {"one"}


@pytest.mark.parametrize(
    "name", ["review", "walkthrough", "tests", "security", "challenge", "verdict"]
)
def test_bundled_prompts_forbid_mutation_and_fill_placeholders(name):
    path = Path(f"src/reviewbot/_resources/prompts/{name}.md")
    prompt = render_prompt(
        path.read_text(encoding="utf-8"), candidate(), "claude", {"prior_reviews": "R"}
    )
    assert "acme/demo#12" in prompt
    assert "Do not edit the branch" in prompt
    assert re.search(r"\{\w+\}", prompt) is None
    if name == "review":
        assert "commits since my previous review: 2" in prompt


def test_render_prompt_fills_known_fields_and_keeps_other_braces():
    prompt = render_prompt("{agent} on {repo}#{number}: {unknown} {{x}}", candidate(), "gem")
    assert prompt == "gem on acme/demo#12: {unknown} {{x}}"


def test_agent_start_retries_while_new_shell_is_busy(monkeypatch):
    attempts = 0

    def fake_run(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LaunchError('{"error":{"code":"agent_pane_busy"}}')
        return ""

    monkeypatch.setattr("reviewbot.herdr._run", fake_run)
    monkeypatch.setattr("reviewbot.herdr.time.sleep", lambda _: None)
    _start_agent_with_retry("name", Agent(name="c", kind="claude", prompt=Path("p")), "pane")
    assert attempts == 2


def test_launch_review_uses_each_agents_kind_args_and_prompt(tmp_path, monkeypatch):
    (tmp_path / "review.md").write_text("generic {key} for {agent}", encoding="utf-8")
    (tmp_path / "codex.md").write_text("codex-only {number}", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'watch = ["*"]\nprompt = "review.md"\n'
        '[[agents]]\nname = "claude"\nkind = "claude"\nargs = ["--model", "opus"]\n'
        '[[agents]]\nname = "codex"\nkind = "codex"\nprompt = "codex.md"\n'
        '[[groups]]\nname = "pair"\nagents = ["claude", "codex"]\n',
        encoding="utf-8",
    )
    config = load(config_path)
    calls = []
    panes = iter(["pane-1", "pane-2"])

    def fake_run(*args, **kwargs):
        calls.append(args)
        return ""

    monkeypatch.setattr("reviewbot.herdr._run", fake_run)
    monkeypatch.setattr("reviewbot.run.prepare_checkout", lambda c, cand: tmp_path)
    monkeypatch.setattr("reviewbot.herdr._existing_tab_pane", lambda label: next(panes))
    results = launch_review(config, candidate(), agent="pair")

    assert [r.tab_label for r in results] == [
        "demo #12 claude review", "demo #12 codex review",
    ]
    starts = [c for c in calls if c[:3] == ("herdr", "agent", "start")]
    assert starts[0][5:] == ("claude", "--pane", "pane-1", "--timeout", "120000",
                             "--", "--model", "opus")
    assert starts[1][5:] == ("codex", "--pane", "pane-2", "--timeout", "120000")
    prompts = [c[-1] for c in calls if c[:3] == ("herdr", "agent", "prompt")]
    assert prompts == ["generic acme/demo#12 for claude", "codex-only 12"]


def test_launch_review_fails_before_checkout_when_prompt_missing(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'watch = ["*"]\nprompt = "missing.md"\n[[agents]]\nname = "a"\nkind = "claude"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "reviewbot.run.prepare_checkout",
        lambda *a: (_ for _ in ()).throw(AssertionError("checked out")),
    )
    with pytest.raises(LaunchError, match="cannot read prompt"):
        launch_review(load(config_path), candidate(), agent="a")


def test_agent_name_is_valid_for_herdr():
    pattern = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
    cases = [
        ("acme/demo", 12, "claude"),
        ("Org/My.Service_API", 4821, "codex"),
        ("org/a-really-long-repository-name-that-overflows", 123456, "claude"),
        ("org/...", 7, "codex"),
    ]
    for repo, number, agent in cases:
        name = _agent_name(repo, number, agent)
        assert pattern.match(name), name
        assert len(name) <= AGENT_NAME_MAX
        assert name.endswith(f"-{number}-{agent}")
    assert _agent_name("acme/demo", 12, "claude") == "review-demo-12-claude"
    assert _agent_name("Org/My.Service_API", 4821, "codex") == "review-my-service_api-4821-codex"
