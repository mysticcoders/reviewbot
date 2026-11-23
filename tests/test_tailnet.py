"""ReviewBot's own tailnet device: config, the helper process protocol, and HTTPS serving."""

import http.client
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from reviewbot import cli
from reviewbot import config as cfg
from reviewbot.board import Board
from reviewbot.config import ConfigError, load
from reviewbot.server import Dashboard
from reviewbot.tailnet import Node, TailnetError, known_dns, state_dir

NODE = '[serve]\nport = 0\nbind = "tailscale-node"\nhostname = "reviewbot-test"\nbrowser = "Chrome"\n'
DNS = "reviewbot-test.tail1234.ts.net"


def write(tmp_path, serve=NODE):
    path = tmp_path / "config.toml"
    path.write_text('watch = ["acme"]\nmultiplexer = "rex"\n' + serve, encoding="utf-8")
    return load(path)


def fake_helper(tmp_path: Path, script: str) -> Path:
    """A stand-in reviewbot-tsnet that logs its argv and runs `script` with emit() available."""
    helper = tmp_path / "reviewbot-tsnet"
    helper.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"open({str(tmp_path / 'argv.log')!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "def emit(**event):\n"
        "    print(json.dumps(event), flush=True)\n"
        + script,
        encoding="utf-8",
    )
    helper.chmod(0o755)
    return helper


SIGNS_IN = (
    "emit(event='login', url='https://login.tailscale.com/a/abc')\n"
    "emit(event='status', message='certificate not issued yet; retrying in 20s')\n"
    f"emit(event='ready', dns='{DNS}', owner='andrew@example.com')\n"
    "if sys.argv[1] == 'run':\n"
    "    sys.stdin.read()\n"
    f"    open({{log!r}}, 'a').write('stopped\\n')\n"
    "if sys.argv[1] == 'logout':\n"
    "    emit(event='logged-out')\n"
)


def signs_in(tmp_path):
    script = SIGNS_IN.replace("{log!r}", repr(str(tmp_path / "argv.log")))
    script = script.replace("{{", "{").replace("}}", "}")
    return fake_helper(tmp_path, script)


def argv(tmp_path):
    return [json.loads(line) if line.startswith("[") else line.strip()
            for line in (tmp_path / "argv.log").read_text().splitlines()]


def test_tailscale_node_requires_a_valid_hostname(tmp_path):
    assert write(tmp_path).serve.hostname == "reviewbot-test"
    with pytest.raises(ConfigError, match="needs `hostname`"):
        write(tmp_path, '[serve]\nport = 0\nbind = "tailscale-node"\nbrowser = "x"\n')
    with pytest.raises(ConfigError, match="only applies"):
        write(tmp_path, '[serve]\nport = 0\nbind = "localhost"\nhostname = "a"\nbrowser = "x"\n')
    with pytest.raises(ConfigError, match="must be 1-63"):
        write(tmp_path, '[serve]\nport = 0\nbind = "tailscale-node"\nhostname = "Bad_Name"\nbrowser = "x"\n')


def test_node_reports_sign_in_links_and_remembers_its_name(tmp_path):
    config = write(tmp_path)
    links, statuses = [], []
    node = Node(config, "reviewbot-test", on_login=links.append, on_status=statuses.append,
                helper=signs_in(tmp_path))
    assert node.start(4321) == DNS
    assert links == ["https://login.tailscale.com/a/abc"]
    assert statuses == ["certificate not issued yet; retrying in 20s"]
    assert node.owner == "andrew@example.com"
    assert known_dns(config, "reviewbot-test") == DNS
    state = state_dir(config, "reviewbot-test")
    assert oct(state.stat().st_mode & 0o777) == "0o700"
    node.stop()
    assert argv(tmp_path) == [
        ["run", "--hostname", "reviewbot-test", "--state", str(state), "--target", "127.0.0.1:4321"],
        "stopped",
    ]


def test_node_errors_and_early_exits_are_reported(tmp_path):
    config = write(tmp_path)
    failing = fake_helper(tmp_path, "emit(event='error', message='you must enable HTTPS')\n")
    with pytest.raises(TailnetError, match="enable HTTPS"):
        Node(config, "reviewbot-test", on_login=print, helper=failing).start(1)
    quitting = fake_helper(tmp_path, "sys.exit(3)\n")
    with pytest.raises(TailnetError, match=r"exited \(3\)"):
        Node(config, "reviewbot-test", on_login=print, helper=quitting).start(1)


def test_logout_signs_out_and_deletes_state(tmp_path):
    config = write(tmp_path)
    node = Node(config, "reviewbot-test", on_login=print, helper=signs_in(tmp_path))
    assert node.login() == (DNS, "andrew@example.com")
    node.logout()
    assert not state_dir(config, "reviewbot-test").exists()
    assert [entry[0] for entry in argv(tmp_path)] == ["login", "logout"]


def test_dashboard_behind_the_node_accepts_its_https_name(tmp_path):
    config = write(tmp_path)
    node = Node(config, "reviewbot-test", on_login=print, helper=signs_in(tmp_path))
    dashboard = Dashboard(config, Board(config), node=node)
    dashboard.start()
    try:
        assert dashboard.url == f"https://{DNS}/"
        assert dashboard.mcp_url == f"https://{DNS}/mcp"
        connection = http.client.HTTPConnection("127.0.0.1", dashboard.port, timeout=5)
        connection.request("GET", f"/?token={dashboard.token}",
                           headers={"Host": DNS, "Origin": f"https://{DNS}"})
        response = connection.getresponse()
        response.read()
        assert response.status == 303
        assert "; Secure" in response.getheader("Set-Cookie")
        connection.request("GET", "/", headers={"Host": f"{DNS}:8443"})
        response = connection.getresponse()
        response.read()
        assert response.status == 421
    finally:
        dashboard.stop()
    assert argv(tmp_path)[-1] == "stopped"


def run_cli(tmp_path, monkeypatch, *args):
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.toml")
    return CliRunner().invoke(cli.cli, list(args))


def test_token_uses_the_nodes_https_name_once_signed_in(tmp_path, monkeypatch):
    config = write(tmp_path)
    result = run_cli(tmp_path, monkeypatch, "token")
    assert result.exit_code == 1
    assert "reviewbot tailnet login" in result.output
    Node(config, "reviewbot-test", on_login=print, helper=signs_in(tmp_path)).login()
    result = run_cli(tmp_path, monkeypatch, "token")
    assert result.exit_code == 0, result.output
    assert f"https://{DNS}/?token=" in result.output
    assert f"https://{DNS}/mcp" in result.output


def test_hostname_override_is_validated(tmp_path, monkeypatch):
    write(tmp_path, '[serve]\nport = 0\nbind = "localhost"\nbrowser = "x"\n')
    result = run_cli(tmp_path, monkeypatch, "token", "--hostname", "a")
    assert "only applies with --bind tailscale-node" in result.output
    result = run_cli(tmp_path, monkeypatch, "token", "--bind", "tailscale-node")
    assert "needs a device name" in result.output
    result = run_cli(tmp_path, monkeypatch, "token", "--bind", "tailscale-node", "--hostname", "Bad_")
    assert "not a valid device name" in result.output


def test_helper_log_is_rotated_past_the_limit(tmp_path, monkeypatch):
    from reviewbot import tailnet

    config = write(tmp_path)
    monkeypatch.setattr(tailnet, "LOG_LIMIT", 10)
    state = state_dir(config, "reviewbot-test")
    state.mkdir(parents=True)
    (state / "helper.log").write_text("x" * 20)
    Node(config, "reviewbot-test", on_login=print, helper=signs_in(tmp_path)).login()
    assert (state / "helper.log.1").read_text() == "x" * 20
    assert (state / "helper.log").stat().st_size == 0
