"""Plan, launch, and drive review runs: single agents, groups, and combat reviews."""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import config as cfg
from .config import Config, ConfigError
from .github import QueueStatus, ReviewCandidate
from .herdr import (
    LaunchError,
    LaunchResult,
    open_session,
    prepare_checkout,
    prompt_and_wait,
    render_prompt,
    send_prompt,
)

ROLE_TITLES = {
    "walkthrough": "Walkthrough",
    "review": "Review",
    "challenge": "Rebuttal",
    "verdict": "Verdict",
}
OUTPUT_DIR = ".reviewbot"


@dataclass(frozen=True)
class Step:
    """One prompt to one agent; `sees` lists earlier steps whose output it is given."""

    index: int
    agent: str
    role: str
    round: int
    sees: tuple[int, ...] = ()
    first_turn: bool = True

    @property
    def filename(self) -> str:
        return f"{self.index + 1:02d}-{self.agent}-{self.role}.md"


def plan(config: Config, name: str) -> list[list[Step]]:
    """Turn an agent, group, or combat name into phases of steps; steps in a phase run in parallel."""
    combat = config.combat(name)
    if combat is None:
        agents = [agent.name for agent in config.resolve(name)]
        return [[Step(index, agent, "review", 0) for index, agent in enumerate(agents)]]

    phases: list[list[Step]] = []
    steps: list[Step] = []
    spoken: set[str] = set()

    def add(agent: str, role: str, round_: int, sees: tuple[int, ...]) -> Step:
        step = Step(len(steps), agent, role, round_, sees, first_turn=agent not in spoken)
        spoken.add(agent)
        steps.append(step)
        return step

    opening: tuple[int, ...] = ()
    if combat.opener:
        phases.append([add(combat.opener, "walkthrough", 0, ())])
        opening = (0,)

    if combat.style == "parallel":
        latest: dict[str, int] = {}
        phase = [add(agent, "review", 0, opening) for agent in combat.agents]
        latest.update({step.agent: step.index for step in phase})
        phases.append(phase)
        for round_ in range(1, combat.rounds + 1):
            phase = [
                add(agent, "challenge", round_, tuple(
                    latest[other] for other in combat.agents if other != agent
                ))
                for agent in combat.agents
            ]
            latest.update({step.agent: step.index for step in phase})
            phases.append(phase)
    else:
        phases.append([add(combat.agents[0], "review", 0, opening)])
        for round_ in range(1, combat.rounds + 1):
            agent = combat.agents[round_ % len(combat.agents)]
            others = tuple(
                step.index for step in steps if step.agent != agent and step.role != "walkthrough"
            )
            phases.append([add(agent, "challenge", round_, opening + others)])

    if combat.judge:
        phases.append([add(combat.judge, "verdict", 0, tuple(range(len(steps))))])
    return phases


def collects(config: Config, name: str) -> bool:
    """Whether a launch needs the background runner to wait for and gather written reviews."""
    return config.output is not None or config.combat(name) is not None


def _template(config: Config, launcher: str, step: Step) -> str:
    combat = config.combat(launcher)
    agent = config.agent(step.agent)
    if step.role == "review" or combat is None:
        return agent.prompt_template()
    if step.role == "walkthrough":
        return cfg.read_prompt(combat.opener_prompt, f"combat `{combat.name}` opener")
    if step.role == "verdict":
        return cfg.read_prompt(combat.judge_prompt, f"combat `{combat.name}` judge")
    challenge = cfg.read_prompt(combat.challenge_prompt, f"combat `{combat.name}` challenge")
    if step.first_turn:
        return agent.prompt_template() + "\n\n---\n\n" + challenge
    return challenge


def format_prior(steps: list[Step], outputs: dict[int, str], sees: tuple[int, ...]) -> str:
    """Render earlier outputs for the `{prior_reviews}` placeholder."""
    sections = []
    for index in sees:
        step = steps[index]
        title = ROLE_TITLES.get(step.role, step.role.title())
        sections.append(f"### {title} by {step.agent} (step {index + 1})\n\n{outputs[index].strip()}")
    return "\n\n".join(sections) or "none"


def step_prompt(
    config: Config, launcher: str, candidate: ReviewCandidate, step: Step,
    steps: list[Step], outputs: dict[int, str], output_path: str | None,
) -> str:
    """Render a step's prompt, attaching earlier reviews and the write-your-review instruction."""
    template = _template(config, launcher, step)
    if step.sees and "{prior_reviews}" not in template:
        template += "\n\n## Earlier reviews of this PR\n\n{prior_reviews}"
    text = render_prompt(
        template, candidate, step.agent,
        {"prior_reviews": format_prior(steps, outputs, step.sees)},
    )
    if output_path:
        text += (
            f"\n\n---\n\nWhen you are finished, write your complete {step.role} as Markdown "
            f"to `{output_path}` (relative to the repository root; create the directory if "
            "needed). That file is the only one you may create or change. Then reply briefly "
            "that it is written."
        )
    return text


def _run_id(candidate: ReviewCandidate) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{candidate.repo.replace('/', '-')}-{candidate.number}-{stamp}"


def _candidate_dict(candidate: ReviewCandidate) -> dict:
    return {**asdict(candidate), "status": candidate.status.value}


def _candidate_from(data: dict) -> ReviewCandidate:
    return ReviewCandidate(**{**data, "status": QueueStatus(data["status"])})


def launch_review(config: Config, candidate: ReviewCandidate, *, agent: str) -> list[LaunchResult]:
    """Check out the PR, start the first agents in Herdr, and hand off to the runner if needed.

    Plain launches send their prompts directly. Combats, and any launch while `[output]` is
    configured, start a detached `reviewbot run-plan` process that sends each prompt, waits
    for the agent to finish, collects the written review, and moves on to the next phase.
    """
    if not candidate.actionable:
        raise LaunchError(f"{candidate.key} is {candidate.status.value}, not actionable")
    try:
        phases = plan(config, agent)
        steps = [step for phase in phases for step in phase]
        background = collects(config, agent)
        if not background:
            prompts = {step.agent: step_prompt(config, agent, candidate, step, steps, {}, None)
                       for step in steps}
        else:
            for step in steps:
                _template(config, agent, step)
    except ConfigError as error:
        raise LaunchError(str(error)) from None

    checkout = prepare_checkout(config, candidate)
    first = phases[0]
    results = [
        open_session(config, candidate, agent=config.agent(step.agent), checkout=checkout)
        for step in first
    ]
    if not background:
        for result in results:
            send_prompt(result.agent_name, prompts[result.agent])
        return results

    run_dir = config.work_dir / "runs" / _run_id(candidate)
    run_dir.mkdir(parents=True)
    (run_dir / "plan.json").write_text(json.dumps({
        "config": str(config.path),
        "launcher": agent,
        "candidate": _candidate_dict(candidate),
        "checkout": str(checkout),
        "sessions": {result.agent: result.agent_name for result in results},
    }, indent=2), encoding="utf-8")
    _write_state(run_dir, {
        "run": run_dir.name, "key": candidate.key, "title": candidate.title,
        "launcher": agent, "status": "starting", "started_at": _now(),
        "steps": [{**_step_summary(step), "status": "pending"} for step in steps],
    })
    with (run_dir / "runner.log").open("ab") as log:
        subprocess.Popen(
            [sys.executable, "-m", "reviewbot.cli", "run-plan", str(run_dir)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return results


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _step_summary(step: Step) -> dict:
    return {"step": step.index + 1, "agent": step.agent, "role": step.role, "round": step.round}


def _write_state(run_dir: Path, state: dict) -> None:
    state["updated_at"] = _now()
    tmp = run_dir / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(run_dir / "state.json")


def read_state(run_dir: Path) -> dict | None:
    try:
        return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_runs(config: Config) -> list[dict]:
    """Every run's state, newest first."""
    root = config.work_dir / "runs"
    states = [read_state(path) for path in root.iterdir()] if root.is_dir() else []
    return sorted((s for s in states if s), key=lambda s: s.get("started_at", ""), reverse=True)


def save_review(
    output: cfg.Output, candidate: ReviewCandidate, run: str, launcher: str,
    step: Step, kind: str, body: str,
) -> Path:
    """Save one review as Markdown with metadata and append it to the index."""
    target = (
        output.dir / candidate.repo.replace("/", "-") / f"pr-{candidate.number}" / run
        / step.filename
    )
    meta = {
        "repo": candidate.repo, "pr": candidate.number, "url": candidate.url,
        "title": candidate.title, "head_sha": candidate.head_sha, "run": run,
        "launcher": launcher, "step": step.index + 1, "agent": step.agent, "kind": kind,
        "role": step.role, "round": step.round, "saved_at": _now(),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    front = "\n".join(f"{key}: {json.dumps(value)}" for key, value in meta.items())
    target.write_text(f"---\n{front}\n---\n\n{body.strip()}\n", encoding="utf-8")
    with (output.dir / "index.jsonl").open("a", encoding="utf-8") as index:
        index.write(json.dumps({**meta, "path": str(target)}) + "\n")
    return target


class RunFailed(Exception):
    pass


RUN_ERRORS = (ConfigError, LaunchError, RunFailed, OSError, KeyError, ValueError)


def execute(run_dir: Path) -> None:
    """Drive a planned run to completion: the body of the detached `run-plan` process."""
    spec = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    state = read_state(run_dir) or {}
    launcher = spec["launcher"]
    try:
        config = cfg.load(Path(spec["config"]))
        candidate = _candidate_from(spec["candidate"])
        checkout = Path(spec["checkout"])
        sessions: dict[str, str] = dict(spec["sessions"])
        phases = plan(config, launcher)
        steps = [step for phase in phases for step in phase]
        out_dir = checkout / OUTPUT_DIR / run_dir.name
        out_dir.mkdir(parents=True, exist_ok=True)
        (checkout / OUTPUT_DIR / ".gitignore").write_text("*\n", encoding="utf-8")
        outputs: dict[int, str] = {}
        state["status"] = "running"
        _write_state(run_dir, state)

        for phase in phases:
            for step in phase:
                if step.agent not in sessions:
                    result = open_session(
                        config, candidate, agent=config.agent(step.agent), checkout=checkout
                    )
                    sessions[step.agent] = result.agent_name
                state["steps"][step.index]["status"] = "running"
            _write_state(run_dir, state)

            def run_step(step: Step) -> str:
                relative = f"{OUTPUT_DIR}/{run_dir.name}/{step.filename}"
                prompt = step_prompt(config, launcher, candidate, step, steps, outputs, relative)
                return _collect(sessions[step.agent], prompt, checkout / relative)

            with ThreadPoolExecutor(max_workers=len(phase)) as pool:
                futures = [pool.submit(run_step, step) for step in phase]
            failures = []
            for step, future in zip(phase, futures, strict=True):
                entry = state["steps"][step.index]
                try:
                    body = future.result()
                except RUN_ERRORS as error:
                    entry["status"] = "failed"
                    failures.append(error)
                    continue
                outputs[step.index] = body
                (run_dir / step.filename).write_text(body, encoding="utf-8")
                entry.update(status="done", path=str(run_dir / step.filename))
                if config.output:
                    kind = config.agent(step.agent).kind
                    saved = save_review(
                        config.output, candidate, run_dir.name, launcher, step, kind, body
                    )
                    entry["saved"] = str(saved)
            _write_state(run_dir, state)
            if failures:
                raise failures[0]
        state["status"] = "done"
    except RUN_ERRORS as error:
        state["status"] = "failed"
        state["error"] = str(error)
        for entry in state.get("steps", []):
            if entry.get("status") == "running":
                entry["status"] = "failed"
    _write_state(run_dir, state)


def _collect(agent_name: str, prompt: str, path: Path) -> str:
    """Prompt an agent, wait for its turn to end, and read the review it wrote; nudge once."""
    prompt_and_wait(agent_name, prompt)
    if not path.is_file():
        prompt_and_wait(
            agent_name,
            f"You have not written your review to `{path}` yet. Write the complete review "
            "there now as Markdown; it is the only file you may create or change.",
        )
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        raise RunFailed(f"{agent_name} did not write its review to {path}") from None
    if not body.strip():
        raise RunFailed(f"{agent_name} wrote an empty review to {path}")
    return body
