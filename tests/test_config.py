from pathlib import Path

import pytest

from reviewbot.config import Agent, Checkout, ConfigError, Group, Scope, load

AGENTS = '''
prompt = "review.md"

[[agents]]
name = "claude"
kind = "claude"
key = "c"
args = ["--model", "opus"]

[[agents]]
name = "codex"
kind = "codex"
key = "x"
prompt = "codex.md"

[[agents]]
name = "gem"
kind = "gemini"

[[groups]]
name = "pair"
agents = ["claude", "codex"]
key = "b"
'''


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_config_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="reviewbot init"):
        load(tmp_path / "absent.toml")


def test_watch_is_required(tmp_path):
    with pytest.raises(ConfigError, match="`watch` is required"):
        load(write_config(tmp_path, ""))


def test_no_agents_is_valid_but_launches_nothing(tmp_path):
    config = load(write_config(tmp_path, 'watch = ["*"]\n'))
    assert config.scope.is_all
    assert config.agents == ()
    assert config.target_names == []
    with pytest.raises(ConfigError, match="none configured"):
        config.resolve("claude")


def test_agents_groups_keys_and_prompts(tmp_path):
    config = load(write_config(tmp_path, 'watch = ["acme"]\n' + AGENTS))
    assert config.agents == (
        Agent(
            name="claude", kind="claude", prompt=tmp_path / "review.md", key="c",
            args=("--model", "opus"),
        ),
        Agent(name="codex", kind="codex", prompt=tmp_path / "codex.md", key="x"),
        Agent(name="gem", kind="gemini", prompt=tmp_path / "review.md"),
    )
    assert config.groups == (Group(name="pair", agents=("claude", "codex"), key="b"),)
    assert config.target_names == ["claude", "codex", "gem", "pair"]
    assert [agent.name for agent in config.resolve("pair")] == ["claude", "codex"]
    assert [agent.name for agent in config.resolve("gem")] == ["gem"]


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ('[[agents]]\nname = "a"\nkind = "claude"\n', "no `prompt`"),
        ('prompt = "p.md"\n[[agents]]\nname = "a"\n', "needs a `kind`"),
        ('prompt = "p.md"\n[[agents]]\nname = "Bad Name"\nkind = "x"\n', "lowercase"),
        ('prompt = "p.md"\n[[agents]]\nname = "a"\nkind = "x"\nkey = "q"\n', "reserved"),
        ('prompt = "p.md"\n[[agents]]\nname = "a"\nkind = "x"\nkey = "ab"\n', "single"),
        (
            ('prompt = "p.md"\n[[agents]]\nname = "a"\nkind = "x"\nkey = "k"\n'
            '[[agents]]\nname = "b"\nkind = "y"\nkey = "k"\n'),
            "already used by `a`",
        ),
        (
            ('prompt = "p.md"\n[[agents]]\nname = "a"\nkind = "x"\n'
            '[[agents]]\nname = "a"\nkind = "y"\n'),
            "more than once",
        ),
        (
            ('prompt = "p.md"\n[[agents]]\nname = "a"\nkind = "x"\n'
            '[[groups]]\nname = "g"\nagents = ["a", "z"]\n'),
            "unknown agent",
        ),
    ],
)
def test_invalid_agent_config(tmp_path, snippet, message):
    with pytest.raises(ConfigError, match=message):
        load(write_config(tmp_path, 'watch = ["*"]\n' + snippet))


def test_prompt_template_reads_file(tmp_path):
    (tmp_path / "review.md").write_text("Review {key}", encoding="utf-8")
    config = load(write_config(tmp_path, 'watch = ["*"]\n' + AGENTS))
    assert config.resolve("claude")[0].prompt_template() == "Review {key}"
    with pytest.raises(ConfigError, match="cannot read prompt for agent `codex`"):
        config.resolve("codex")[0].prompt_template()


def test_watch_splits_owners_and_repos(tmp_path):
    config = load(write_config(tmp_path, 'watch = ["acme", "other/tool", "acme"]\n'))
    assert config.scope == Scope(owners=("acme",), repos=("other/tool",))
    assert config.scope.describe() == "acme, other/tool"


def test_legacy_organization_key_becomes_owner_scope(tmp_path):
    config = load(write_config(tmp_path, 'organization = "acme"\n'))
    assert config.scope == Scope(owners=("acme",))


def test_checkouts_and_legacy_repos_tables(tmp_path):
    text = (
        'watch = ["*"]\n'
        '[[checkouts]]\nrepo = "acme/api"\npath = "~/code/api"\n'
        '[[repos]]\nowner = "acme"\nname = "web"\npath = "/src/web"\n'
    )
    config = load(write_config(tmp_path, text))
    assert config.checkouts == (
        Checkout(slug="acme/api", path=Path("~/code/api").expanduser()),
        Checkout(slug="acme/web", path=Path("/src/web")),
    )


def test_scope_parse_accepts_urls_and_wildcard():
    assert Scope.parse(["https://github.com/acme/api/"]) == Scope(repos=("acme/api",))
    assert Scope.parse(["acme", "*"]).is_all
    with pytest.raises(ConfigError):
        Scope.parse(["a/b/c"])


def test_single_owner():
    assert Scope.parse(["acme", "acme/api"]).single_owner == "acme"
    assert Scope.parse(["acme/api", "other/web"]).single_owner is None
    assert Scope().single_owner is None


def test_label_drops_owner_only_for_single_owner_scope():
    assert Scope.parse(["acme"]).label("acme/demo") == "demo"
    assert Scope.parse(["acme", "other/tool"]).label("acme/demo") == "acme/demo"
    assert Scope().label("acme/demo") == "acme/demo"
