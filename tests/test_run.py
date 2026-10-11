import json
import re
from pathlib import Path

import pytest

from reviewbot import run
from reviewbot.config import load
from reviewbot.github import QueueStatus, ReviewCandidate
from reviewbot.herdr import LaunchResult

PROMPTS = {
    "review.md": "REVIEW {key} by {agent}",
    "walk.md": "WALK {key}",
    "challenge.md": "CHALLENGE\n{prior_reviews}",
    "verdict.md": "VERDICT\n{prior_reviews}",
}


def candidate() -> ReviewCandidate:
    return ReviewCandidate(
        repo="acme/demo", number=12, title="Fix", author="author",
        url="https://github.com/acme/demo/pull/12", updated_at="now",
        head_sha="abcdef123456", head_branch="fix", base_branch="main",
        is_draft=False, review_decision="REVIEW_REQUIRED", request_source="direct",
        last_review_state=None, last_review_sha=None,
        commits_since_review=0, checks="passing", status=QueueStatus.READY,
    )


def make_config(tmp_path: Path, combats: str = "", output: bool = False):
    for name, text in PROMPTS.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    text = (
        f'watch = ["*"]\nprompt = "review.md"\nwork_dir = "{tmp_path / "work"}"\n'
        '[[agents]]\nname = "a"\nkind = "claude"\n'
        '[[agents]]\nname = "b"\nkind = "codex"\n'
        '[[agents]]\nname = "j"\nkind = "gemini"\n'
        '[[groups]]\nname = "pair"\nagents = ["a", "b"]\n'
        + combats
    )
    if output:
        text += f'[output]\ndir = "{tmp_path / "saved"}"\n'
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return load(path)


def combat(style: str, rounds: int, opener: bool = False, judge: bool = False) -> str:
    text = (
        f'[[combats]]\nname = "duel"\nkey = "d"\nagents = ["a", "b"]\nstyle = "{style}"\n'
        f'rounds = {rounds}\nchallenge_prompt = "challenge.md"\n'
    )
    if opener:
        text += 'opener = "a"\nopener_prompt = "walk.md"\n'
    if judge:
        text += 'judge = "j"\njudge_prompt = "verdict.md"\n'
    return text


def shape(phases):
    return [[(s.agent, s.role, s.round, s.sees) for s in phase] for phase in phases]


def test_plan_for_agent_and_group(tmp_path):
    config = make_config(tmp_path)
    assert shape(run.plan(config, "a")) == [[("a", "review", 0, ())]]
    assert shape(run.plan(config, "pair")) == [[("a", "review", 0, ()), ("b", "review", 0, ())]]


def test_plan_parallel_combat_with_opener_and_judge(tmp_path):
    config = make_config(tmp_path, combat("parallel", 2, opener=True, judge=True))
    assert shape(run.plan(config, "duel")) == [
        [("a", "walkthrough", 0, ())],
        [("a", "review", 0, (0,)), ("b", "review", 0, (0,))],
        [("a", "challenge", 1, (2,)), ("b", "challenge", 1, (1,))],
        [("a", "challenge", 2, (4,)), ("b", "challenge", 2, (3,))],
        [("j", "verdict", 0, (0, 1, 2, 3, 4, 5, 6))],
    ]


def test_plan_handoff_combat_alternates_and_hides_own_turns(tmp_path):
    config = make_config(tmp_path, combat("handoff", 3))
    phases = run.plan(config, "duel")
    assert shape(phases) == [
        [("a", "review", 0, ())],
        [("b", "challenge", 1, (0,))],
        [("a", "challenge", 2, (1,))],
        [("b", "challenge", 3, (0, 2))],
    ]
    assert [phase[0].first_turn for phase in phases] == [True, True, False, False]


def test_step_prompt_handoff_first_turn_includes_own_review_and_output_path(tmp_path):
    config = make_config(tmp_path, combat("handoff", 1))
    steps = [s for phase in run.plan(config, "duel") for s in phase]
    text = run.step_prompt(
        config, "duel", candidate(), steps[1], steps, {0: "a found a bug"}, ".reviewbot/x.md"
    )
    assert text.startswith("REVIEW acme/demo#12 by b")
    assert "CHALLENGE\n### Review by a (step 1)\n\na found a bug" in text
    assert "write your complete challenge as Markdown to `.reviewbot/x.md`" in text


def test_step_prompt_appends_earlier_reviews_when_template_lacks_placeholder(tmp_path):
    config = make_config(tmp_path, combat("parallel", 1, opener=True))
    steps = [s for phase in run.plan(config, "duel") for s in phase]
    text = run.step_prompt(config, "duel", candidate(), steps[1], steps, {0: "walked"}, None)
    assert text.startswith("REVIEW acme/demo#12 by a")
    assert "## Earlier reviews of this PR\n\n### Walkthrough by a (step 1)\n\nwalked" in text
    assert "write your complete" not in text


class FakeHerdr:
    """Stands in for Herdr: each prompt makes the agent write a review naming what it saw."""

    def __init__(self, silent=()):
        self.prompts = []
        self.silent = set(silent)

    def open_session(self, config, cand, *, agent, checkout):
        return LaunchResult(agent.name, f"tab {agent.name}", "p", checkout, f"rb-{agent.name}")

    def prompt_and_wait(self, agent_name, text):
        self.prompts.append((agent_name, text))
        match = re.search(r"Markdown to `([^`]+)`", text)
        if match and agent_name not in self.silent:
            path = self.checkout / match.group(1)
            path.parent.mkdir(parents=True, exist_ok=True)
            seen = re.findall(r"### \w+ by (\w+) \(step (\d+)\)", text)
            path.write_text(f"{agent_name} saw {seen}", encoding="utf-8")


def start(tmp_path, monkeypatch, config, launcher, fake):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    fake.checkout = checkout
    popen = []
    monkeypatch.setattr(run, "prepare_checkout", lambda c, cand: checkout)
    monkeypatch.setattr(run, "open_session", fake.open_session)
    monkeypatch.setattr(run, "prompt_and_wait", fake.prompt_and_wait)
    monkeypatch.setattr(run.subprocess, "Popen", lambda *a, **k: popen.append(a))
    results = run.launch_review(config, candidate(), agent=launcher)
    assert len(popen) == 1
    run_dir = Path(popen[0][0][-1])
    return results, run_dir


def test_combat_runs_in_background_and_saves_every_review(tmp_path, monkeypatch):
    config = make_config(tmp_path, combat("handoff", 2, judge=True), output=True)
    fake = FakeHerdr()
    results, run_dir = start(tmp_path, monkeypatch, config, "duel", fake)
    assert [r.agent for r in results] == ["a"]
    assert run.read_state(run_dir)["status"] == "starting"

    run.execute(run_dir)

    state = run.read_state(run_dir)
    assert state["status"] == "done", state
    assert [(s["agent"], s["role"], s["status"]) for s in state["steps"]] == [
        ("a", "review", "done"), ("b", "challenge", "done"),
        ("a", "challenge", "done"), ("j", "verdict", "done"),
    ]
    assert [name for name, _ in fake.prompts] == ["rb-a", "rb-b", "rb-a", "rb-j"]
    verdict = (run_dir / "04-j-verdict.md").read_text(encoding="utf-8")
    assert verdict == "rb-j saw [('a', '1'), ('b', '2'), ('a', '3')]"

    index = [json.loads(line) for line in (tmp_path / "saved" / "index.jsonl").open()]
    assert [(e["agent"], e["role"], e["kind"], e["step"]) for e in index] == [
        ("a", "review", "claude", 1), ("b", "challenge", "codex", 2),
        ("a", "challenge", "claude", 3), ("j", "verdict", "gemini", 4),
    ]
    saved = Path(index[3]["path"]).read_text(encoding="utf-8")
    assert saved.startswith('---\nrepo: "acme/demo"\npr: 12\n')
    assert saved.endswith("rb-j saw [('a', '1'), ('b', '2'), ('a', '3')]\n")
    assert (tmp_path / "checkout" / ".reviewbot" / ".gitignore").read_text() == "*\n"


def test_output_alone_uses_runner_for_a_single_agent(tmp_path, monkeypatch):
    config = make_config(tmp_path, output=True)
    _, run_dir = start(tmp_path, monkeypatch, config, "a", FakeHerdr())
    run.execute(run_dir)
    assert run.read_state(run_dir)["status"] == "done"
    assert len(list((tmp_path / "saved").rglob("01-a-review.md"))) == 1


def test_agent_that_never_writes_is_nudged_then_fails_the_run(tmp_path, monkeypatch):
    config = make_config(tmp_path, combat("parallel", 1))
    fake = FakeHerdr(silent={"rb-b"})
    _, run_dir = start(tmp_path, monkeypatch, config, "duel", fake)
    run.execute(run_dir)
    state = run.read_state(run_dir)
    assert state["status"] == "failed"
    assert "rb-b did not write its review" in state["error"]
    assert [name for name, text in fake.prompts if "have not written" in text] == ["rb-b"]
    assert [s["status"] for s in state["steps"]] == ["done", "failed", "pending", "pending"]


def test_plain_launch_without_output_sends_prompts_directly(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    sent = []
    monkeypatch.setattr(run, "prepare_checkout", lambda c, cand: tmp_path)
    monkeypatch.setattr(run, "open_session", FakeHerdr().open_session)
    monkeypatch.setattr(run, "send_prompt", lambda name, text: sent.append((name, text)))
    monkeypatch.setattr(run.subprocess, "Popen", lambda *a, **k: pytest.fail("runner started"))
    run.launch_review(config, candidate(), agent="pair")
    assert sent == [("rb-a", "REVIEW acme/demo#12 by a"), ("rb-b", "REVIEW acme/demo#12 by b")]
