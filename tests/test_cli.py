from pathlib import Path

from click.testing import CliRunner

from reviewbot import cli
from reviewbot import config as cfg


def run(tmp_path: Path, monkeypatch, *args: str, stdin: str | None = None):
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.toml")
    return CliRunner().invoke(cli.cli, list(args), input=stdin)


def test_init_writes_watch_agents_and_prompt(tmp_path, monkeypatch):
    result = run(
        tmp_path, monkeypatch, "init", "-w", "acme", "-w", "acme/api", "--multiplexer", "herdr",
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
    result = run(tmp_path, monkeypatch, "init", stdin="*\nherdr\n")
    assert result.exit_code == 0, result.output
    config = cfg.load(tmp_path / "config.toml")
    assert config.scope.is_all
    assert config.agents == ()
    assert "Add [[agents]]" in result.output
    assert "exists" in run(tmp_path, monkeypatch, "init", "-w", "*", "--multiplexer", "herdr").output


def test_init_rejects_bad_agent_spec(tmp_path, monkeypatch):
    result = run(tmp_path, monkeypatch, "init", "-w", "*", "--multiplexer", "herdr", "--agent", "claude:cc")
    assert result.exit_code != 0
    assert "KIND:KEY" in result.output


def test_launch_rejects_unknown_agent_before_querying(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch, "init", "-w", "*", "--multiplexer", "herdr", "--agent", "claude:c")
    monkeypatch.setattr(cli, "_queue", lambda config: (_ for _ in ()).throw(AssertionError))
    result = run(tmp_path, monkeypatch, "launch", "acme/api#1", "--agent", "codex")
    assert result.exit_code != 0
    assert "unknown agent `codex` (available: claude)" in result.output


def test_init_explains_an_existing_invalid_config(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text('default_agent = "codex"\n', encoding="utf-8")
    result = run(tmp_path, monkeypatch, "init", "-w", "acme", "--multiplexer", "herdr", "--agent", "claude:c")
    assert result.exit_code != 0
    assert "kept your existing" in result.output
    assert "config.old.toml" in result.output
    assert "--force" in result.output


def test_init_says_flags_were_not_applied_to_an_existing_valid_config(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(
        'watch = ["*"]\nmultiplexer = "herdr"\n', encoding="utf-8"
    )
    result = run(tmp_path, monkeypatch, "init", "-w", "acme", "--multiplexer", "herdr")
    assert result.exit_code == 0
    assert "were not applied" in result.output
    assert cfg.load(tmp_path / "config.toml").scope.is_all


def test_init_for_rex_writes_each_agents_command(tmp_path, monkeypatch):
    result = run(
        tmp_path, monkeypatch, "init", "-w", "*", "--multiplexer", "rex", "--agent", "codex:x",
    )
    assert result.exit_code == 0, result.output
    config = cfg.load(tmp_path / "config.toml")
    assert config.multiplexer == "rex"
    assert [(a.kind, a.command, a.key) for a in config.agents] == [("codex", "codex", "x")]
