from pathlib import Path

from click.testing import CliRunner

from reviewbot import cli
from reviewbot import config as cfg


def run(tmp_path: Path, monkeypatch, *args: str, stdin: str | None = None):
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.toml")
    return CliRunner().invoke(cli.cli, list(args), input=stdin)


def test_init_writes_watch_agents_and_prompt(tmp_path, monkeypatch):
    result = run(
        tmp_path, monkeypatch, "init", "-w", "acme", "-w", "acme/api",
        "--agent", "claude:c", "--agent", "gemini",
    )
    assert result.exit_code == 0, result.output
    config = cfg.load(tmp_path / "config.toml")
    assert config.scope == cfg.Scope(owners=("acme",), repos=("acme/api",))
    assert [(a.name, a.kind, a.key) for a in config.agents] == [
        ("claude", "claude", "c"), ("gemini", "gemini", None),
    ]
    assert "{key}" in config.agents[0].prompt_template()


def test_init_prompts_for_watch_and_writes_no_agents(tmp_path, monkeypatch):
    result = run(tmp_path, monkeypatch, "init", stdin="*\n")
    assert result.exit_code == 0, result.output
    config = cfg.load(tmp_path / "config.toml")
    assert config.scope.is_all
    assert config.agents == ()
    assert "Add [[agents]]" in result.output
    assert "exists" in run(tmp_path, monkeypatch, "init", "-w", "*").output


def test_init_rejects_bad_agent_spec(tmp_path, monkeypatch):
    result = run(tmp_path, monkeypatch, "init", "-w", "*", "--agent", "claude:cc")
    assert result.exit_code != 0
    assert "KIND:KEY" in result.output


def test_launch_rejects_unknown_agent_before_querying(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch, "init", "-w", "*", "--agent", "claude:c")
    monkeypatch.setattr(cli, "_queue", lambda config: (_ for _ in ()).throw(AssertionError))
    result = run(tmp_path, monkeypatch, "launch", "acme/api#1", "--agent", "codex")
    assert result.exit_code != 0
    assert "unknown agent `codex` (available: claude)" in result.output
