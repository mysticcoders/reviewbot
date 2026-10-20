import tomllib
from pathlib import Path

import pytest

from reviewbot.config import (
    Agent,
    Checkout,
    ConfigError,
    Group,
    Scope,
    add_commands,
    add_multiplexer,
    agents_missing_command,
    load,
)

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


def write_config(tmp_path: Path, text: str, multiplexer: str | None = "herdr") -> Path:
    path = tmp_path / "config.toml"
    if multiplexer:
        text = f'multiplexer = "{multiplexer}"\n' + text
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


COMBAT_BASE = (
    'watch = ["*"]\nprompt = "p.md"\n'
    '[[agents]]\nname = "a"\nkind = "x"\n[[agents]]\nname = "b"\nkind = "y"\n'
)


def test_combat_loads_with_everything_explicit(tmp_path):
    config = load(write_config(tmp_path, COMBAT_BASE + (
        '[[combats]]\nname = "duel"\nkey = "d"\nagents = ["a", "b"]\nstyle = "handoff"\n'
        'rounds = 2\nchallenge_prompt = "c.md"\nopener = "a"\nopener_prompt = "w.md"\n'
        '[output]\ndir = "out"\n'
    )))
    duel = config.combat("duel")
    assert (duel.style, duel.rounds, duel.opener, duel.judge) == ("handoff", 2, "a", None)
    assert duel.challenge_prompt == tmp_path / "c.md"
    assert config.target_names == ["a", "b", "duel"]
    assert [agent.name for agent in config.resolve("duel")] == ["a", "b"]
    assert config.output.dir == tmp_path / "out"


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ('agents = ["a"]\nstyle = "handoff"\nrounds = 1\nchallenge_prompt = "c"\n', "two distinct"),
        ('agents = ["a", "a"]\nstyle = "handoff"\nrounds = 1\nchallenge_prompt = "c"\n',
         "two distinct"),
        ('agents = ["a", "z"]\nstyle = "handoff"\nrounds = 1\nchallenge_prompt = "c"\n',
         "unknown agent"),
        ('agents = ["a", "b"]\nrounds = 1\nchallenge_prompt = "c"\n', "`style` must be"),
        ('agents = ["a", "b"]\nstyle = "handoff"\nchallenge_prompt = "c"\n', "`rounds`"),
        ('agents = ["a", "b"]\nstyle = "handoff"\nrounds = 0\nchallenge_prompt = "c"\n',
         "`rounds`"),
        ('agents = ["a", "b"]\nstyle = "handoff"\nrounds = 1\n', "`challenge_prompt`"),
        (('agents = ["a", "b"]\nstyle = "handoff"\nrounds = 1\nchallenge_prompt = "c"\n'
         'judge = "b"\n'), "`judge_prompt`"),
        (('agents = ["a", "b"]\nstyle = "handoff"\nrounds = 1\nchallenge_prompt = "c"\n'
         'opener = "q"\nopener_prompt = "w"\n'), "unknown agent"),
    ],
)
def test_invalid_combat(tmp_path, snippet, message):
    with pytest.raises(ConfigError, match=message):
        load(write_config(tmp_path, COMBAT_BASE + '[[combats]]\nname = "duel"\n' + snippet))


def test_output_needs_dir(tmp_path):
    with pytest.raises(ConfigError, match="needs `dir`"):
        load(write_config(tmp_path, 'watch = ["*"]\n[output]\n'))


def test_multiplexer_is_required_and_validated(tmp_path):
    with pytest.raises(ConfigError, match="`multiplexer` is missing"):
        load(write_config(tmp_path, 'watch = ["*"]\n', multiplexer=None))
    with pytest.raises(ConfigError, match="`multiplexer` must be"):
        load(write_config(tmp_path, 'watch = ["*"]\n', multiplexer="tmux"))


def test_rex_agents_need_a_command(tmp_path):
    agent = '[[agents]]\nname = "c"\nkind = "claude"\nprompt = "p.md"\n'
    with pytest.raises(ConfigError, match="agent `c` needs a `command`"):
        load(write_config(tmp_path, 'watch = ["*"]\n' + agent, multiplexer="rex"))
    config = load(write_config(
        tmp_path, 'watch = ["*"]\nrex_session = "reviews"\n' + agent + 'command = "claude"\n',
        multiplexer="rex",
    ))
    assert (config.multiplexer, config.multiplexer_name, config.rex_session) == (
        "rex", "Rex", "reviews",
    )
    assert config.agents[0].command == "claude"


def test_add_multiplexer_without_tables_appends_a_loadable_line(tmp_path):
    text = add_multiplexer('watch = ["*"]', "rex")
    assert load(write_config(tmp_path, text, multiplexer=None)).multiplexer == "rex"


def test_add_commands_only_touches_the_named_agents(tmp_path):
    text = (
        'watch = ["*"]\nmultiplexer = "rex"\nprompt = "p.md"\n'
        '[[agents]]\nname = "a"\nkind = "claude"\ncommand = "claude"\n'
        '[[agents]]\nkind = "codex"\nname = "b"\n'
        '[output]\ndir = "out"\n'
    )
    raw = tomllib.loads(text)
    assert agents_missing_command(raw) == [("b", "codex")]
    config = load(write_config(tmp_path, add_commands(text, {"b": "codex"}), multiplexer=None))
    assert [a.command for a in config.agents] == ["claude", "codex"]
    assert config.output is not None
