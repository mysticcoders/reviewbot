import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from reviewbot import rex
from reviewbot.config import load
from reviewbot.github import QueueStatus, ReviewCandidate
from reviewbot.launch import LaunchError

LABEL = "demo #12 claude review"


def candidate(number: int = 12) -> ReviewCandidate:
    return ReviewCandidate(
        repo="acme/demo", number=number, title="Fix", author="author",
        url="https://github.com/acme/demo/pull/12", updated_at="now",
        head_sha="abcdef123456", head_branch="fix", base_branch="main",
        is_draft=False, review_decision="REVIEW_REQUIRED", request_source="direct",
        last_review_state=None, last_review_sha=None,
        commits_since_review=0, checks="passing", status=QueueStatus.READY,
    )


def window(label: str, block: str) -> dict:
    return {
        "window_id": f"window:{block}", "label": label,
        "layers": [{"blocks": [{"block_id": block}]}],
    }


class FakeRex:
    """Stands in for the rex CLI and the bundled scripts: scripted windows, exits, results."""

    def __init__(self, windows=(), exited=None, script=(0, "block block:new\n")):
        self.calls = []
        self.scripts = []
        self.windows = list(windows)
        self.exited = exited
        self.result = script

    def run(self, *args, timeout=120, input=None):
        self.calls.append(args)
        if args[:2] == ("rex", "ls"):
            return json.dumps({"sessions": [{"session_id": "session:s"}]})
        if args[-1] == "session.view":
            return json.dumps({"windows": self.windows})
        if args[-1] == "process":
            return json.dumps({"last_exit": self.exited})
        return ""

    def script(self, name, target, args, timeout):
        self.scripts.append((name, target, args))
        return self.result


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("REX_SESSION", "session:s")

    def install(**kwargs):
        runner = FakeRex(**kwargs)
        monkeypatch.setattr(rex, "_run", runner.run)
        monkeypatch.setattr(rex, "_script", runner.script)
        return runner

    return install


def rex_config(tmp_path: Path, extra: str = ""):
    path = tmp_path / "config.toml"
    path.write_text(
        'watch = ["*"]\nmultiplexer = "rex"\nprompt = "p.md"\n' + extra
        + '[[agents]]\nname = "claude"\nkind = "claude"\ncommand = "claude"\n'
        'args = ["--model", "opus"]\n',
        encoding="utf-8",
    )
    return load(path)


def launch(tmp_path, extra: str = ""):
    config = rex_config(tmp_path, extra)
    return rex.open_session(config, candidate(), agent=config.agents[0], checkout=tmp_path)


def test_open_session_runs_launch_script_with_the_agent_command(tmp_path, fake):
    runner = fake()
    result = launch(tmp_path, 'rex_session = "reviews"\n')
    assert (result.handle, result.tab_label) == ("block:new", LABEL)
    assert runner.scripts == [("launch.lua", ("-s", "reviews"), {
        "label": LABEL, "cwd": str(tmp_path), "command": ["claude", "--model", "opus"],
        "timeout": rex.READY_TIMEOUT,
    })]


@pytest.mark.parametrize(
    ("code", "message"),
    [(3, "exited before it was ready"), (4, "answer any startup prompt"), (9, "exited 9")],
)
def test_open_session_reports_why_the_agent_is_not_ready(tmp_path, fake, code, message):
    fake(script=(code, "block block:new\n"))
    with pytest.raises(LaunchError, match=message):
        launch(tmp_path)


def test_open_session_refuses_a_tab_whose_agent_is_still_running(tmp_path, fake):
    runner = fake(windows=[window(LABEL, "block:old")])
    with pytest.raises(LaunchError, match="already has an agent running"):
        launch(tmp_path)
    assert runner.scripts == []


def test_open_session_replaces_a_tab_whose_agent_exited(tmp_path, fake):
    runner = fake(windows=[window(LABEL, "block:old")], exited={"exit_code": 0})
    launch(tmp_path)
    assert ("rex", "window", "close", "-s", "session:s", "window:block:old") in runner.calls


def test_open_session_needs_a_session(tmp_path, fake, monkeypatch):
    fake()
    monkeypatch.delenv("REX_SESSION")
    with pytest.raises(LaunchError, match="set `rex_session`"):
        launch(tmp_path)


def test_send_prompt_runs_prompt_script_without_waiting(fake):
    runner = fake(script=(0, ""))
    rex.send_prompt("block:b", "line one\nline two")
    assert runner.scripts == [
        ("prompt.lua", ("-b", "block:b"), {"text": "line one\nline two", "wait": False}),
    ]


def test_prompt_and_wait_maps_script_exits_to_errors(fake):
    runner = fake(script=(0, ""))
    rex.prompt_and_wait("block:b", "review it")
    assert runner.scripts[0][2] == {"text": "review it", "wait": True}
    fake(script=(2, "error rate limited\n"))
    with pytest.raises(LaunchError, match="reported an error: rate limited"):
        rex.prompt_and_wait("block:b", "review it")
    fake(script=(3, ""))
    with pytest.raises(LaunchError, match="exited mid-turn"):
        rex.prompt_and_wait("block:b", "review it")


def test_active_review_keys_matches_tab_labels_in_any_session(tmp_path, fake):
    fake(windows=[window(LABEL, "block:a"), window("notes", "block:n")])
    keys = rex.active_review_keys(rex_config(tmp_path), [candidate(), candidate(13)])
    assert keys == {"acme/demo#12"}


def test_script_passes_args_as_a_json_file_and_raises_on_script_failure(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["args"] = json.loads(Path(command[-1][1:]).read_text(encoding="utf-8"))
        assert Path(command[4]).name == "prompt.lua" and Path(command[4]).is_file()
        return subprocess.CompletedProcess(command, 1, "", "Error: boom")

    monkeypatch.setattr(rex.subprocess, "run", fake_run)
    with pytest.raises(LaunchError, match="prompt.lua failed: Error: boom"):
        rex._script("prompt.lua", ("-b", "block:b"), {"text": "hi"}, None)
    assert seen["command"][:3] == ["rex", "do", "-b"]
    assert seen["args"] == {"text": "hi"}
    assert not Path(seen["command"][-1][1:]).exists()


@pytest.mark.skipif(
    not (shutil.which("rex") and os.environ.get("REX_SESSION")),
    reason="needs a running Rex server and session",
)
def test_scripts_drive_a_real_program_in_rex(tmp_path):
    """Launch a fake agent that reports OSC 7501 status, prompt it, and wait for its turn."""
    agent = tmp_path / "agent.sh"
    agent.write_text(
        "#!/bin/sh\n"
        "status() { printf '\\033]7501;state=%s:app=fake\\033\\\\' \"$1\"; }\n"
        "status idle\n"
        "while IFS= read -r line; do\n"
        "  case \"$line\" in *go*) status working; sleep 1; echo \"$line\" > out.txt;"
        " status done;; esac\n"
        "done\n",
        encoding="utf-8",
    )
    agent.chmod(0o755)
    session = os.environ["REX_SESSION"]
    code, output = rex._script(
        "launch.lua", ("-s", session),
        {"label": "reviewbot live test", "cwd": str(tmp_path), "command": [str(agent)],
         "timeout": 20},
        timeout=40,
    )
    block = output.split()[1]
    try:
        assert code == 0
        rex.prompt_and_wait(block, "go now")
        assert (tmp_path / "out.txt").read_text(encoding="utf-8").strip().endswith("go now")
    finally:
        window = rex._json("rex", "block", "inspect", block, "--json")["block"]["window_id"]
        rex._run("rex", "window", "close", "-s", session, window)
