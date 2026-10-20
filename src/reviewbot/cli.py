"""ReviewBot command line: GitHub review queue to interactive agents in Herdr or Rex."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from importlib import resources
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from . import config as cfg
from .github import GhError, ReviewCandidate, authored_prs, review_queue
from .launch import LaunchError
from .multiplexer import active_review_keys
from .run import collects, execute, launch_review, list_runs


def _resource_text(name: str) -> str:
    return resources.files("reviewbot").joinpath("_resources", *name.split("/")).read_text(
        encoding="utf-8"
    )


def _write_if_absent(target: Path, content: str, *, force: bool) -> bool:
    if target.exists() and not force:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return True


def _load(watch: tuple[str, ...] = (), watch_all: bool = False) -> cfg.Config:
    """Load the config file, letting `--watch`/`--all` replace its configured scope."""
    try:
        config = cfg.load()
        if watch_all:
            return config.with_scope(cfg.Scope())
        if watch:
            return config.with_scope(cfg.Scope.parse(watch))
        return config
    except cfg.ConfigError as error:
        raise click.ClickException(str(error)) from None


def scope_options(command):
    """Add `--watch` and `--all` scope overrides to a command."""
    command = click.option(
        "--all", "watch_all", is_flag=True,
        help="Watch every repository you can access, ignoring the configured scope.",
    )(command)
    return click.option(
        "-w", "--watch", multiple=True, metavar="OWNER[/REPO]",
        help="Watch this owner or repository instead of the configured scope (repeatable).",
    )(command)


def _queue(config: cfg.Config) -> list[ReviewCandidate]:
    try:
        return asyncio.run(review_queue(config.scope))
    except (GhError, OSError) as error:
        raise click.ClickException(f"GitHub query failed: {error}") from None


def _print_candidate(candidate: ReviewCandidate) -> None:
    console = Console()
    console.print(f"[bold]{candidate.key}[/bold] — {candidate.title}")
    console.print(
        f"  {candidate.status.value} · {candidate.request_source} request · "
        f"checks {candidate.checks} · @{candidate.author}"
    )
    if candidate.last_review_state:
        console.print(
            f"  previous {candidate.last_review_state.lower()} at "
            f"{(candidate.last_review_sha or '')[:7]} · "
            f"{candidate.commits_since_review} newer commit(s)"
        )
    console.print(f"  {candidate.url}")


def _require_agent(config: cfg.Config, agent: str) -> None:
    try:
        config.resolve(agent)
    except cfg.ConfigError as error:
        raise click.ClickException(str(error)) from None


def _do_launch(config: cfg.Config, candidate: ReviewCandidate, agent: str) -> None:
    try:
        results = launch_review(config, candidate, agent=agent)
    except LaunchError as error:
        raise click.ClickException(str(error)) from None
    for result in results:
        click.secho(
            f"started {result.agent} in {config.multiplexer_name} tab "
            f"`{result.tab_label}` ({result.checkout})",
            fg="green",
        )
    if collects(config, agent):
        click.echo("A background runner is driving the review; follow it with `reviewbot runs`.")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="reviewbot")
def cli() -> None:
    """Review requested PRs interactively with your configured agents in Herdr or Rex."""


PROMPT_TEMPLATES = (
    "review.md", "walkthrough.md", "tests.md", "security.md", "challenge.md", "verdict.md",
)


def _toml_list(values: list[str] | tuple[str, ...]) -> str:
    return "[" + ", ".join(f'"{value}"' for value in values) + "]"


def _agent_spec(spec: str) -> tuple[str, str | None]:
    """Split an `init --agent KIND[:KEY]` value."""
    kind, _, key = spec.partition(":")
    if not cfg.NAME_PATTERN.fullmatch(kind) or (key and not cfg.KEY_PATTERN.fullmatch(key)):
        raise click.BadParameter(f"`{spec}` must be KIND or KIND:KEY, e.g. claude:c")
    return kind, key or None


def render_config(
    scope: cfg.Scope, multiplexer: str, agents: list[tuple[str, str | None]]
) -> str:
    """Build a starter config.toml for the given watch scope, multiplexer, and agents."""
    entries = ["*"] if scope.is_all else [*scope.owners, *scope.repos]
    rex = multiplexer == "rex"
    agent_blocks = "\n".join(
        f'[[agents]]\nname = "{kind}"\nkind = "{kind}"\n'
        + (f'command = "{kind}"\n' if rex else "")
        + (f'key = "{key}"\n' if key else "")
        for kind, key in agents
    ) or (
        "# [[agents]]\n"
        '# name = "claude"        # used with --agent and in tab names\n'
        '# kind = "claude"        # the agent kind (Herdr: any `herdr agent start --kind`)\n'
        + ('# command = "claude"     # the program Rex runs in the tab\n' if rex else "")
        +
        '# key = "c"              # TUI shortcut (single letter or digit)\n'
        '# args = ["--model", "opus"]   # extra arguments for the agent CLI\n'
        '# prompt = "claude.md"   # overrides the top-level prompt\n'
    )
    return _resource_text("config.example.toml").replace(
        "@WATCH@", _toml_list(entries)
    ).replace("@MULTIPLEXER@", multiplexer).replace("@AGENTS@\n", agent_blocks)


@cli.command()
@click.option("--force", is_flag=True, help="Overwrite existing config and prompt files.")
@click.option(
    "-w", "--watch", multiple=True, metavar="OWNER[/REPO]",
    help="Owner or repository to watch (repeatable); `*` watches everything.",
)
@click.option(
    "--agent", "agent_specs", multiple=True, metavar="KIND[:KEY]",
    help="Add a review agent of this kind, with an optional TUI key (repeatable).",
)
@click.option(
    "--multiplexer", type=click.Choice(list(cfg.MULTIPLEXERS)),
    help="Terminal multiplexer that hosts the review agents.",
)
def init(
    force: bool, watch: tuple[str, ...], agent_specs: tuple[str, ...], multiplexer: str | None
) -> None:
    """Write ~/.config/reviewbot/config.toml and the review prompt it points at."""
    if not watch:
        answer = click.prompt(
            "Owners or OWNER/REPO entries to watch (comma-separated, * for everything)"
        )
        watch = tuple(item for item in answer.replace(",", " ").split() if item)
    if not multiplexer:
        multiplexer = click.prompt(
            "Terminal multiplexer that hosts the agents", type=click.Choice(list(cfg.MULTIPLEXERS)),
        )
    try:
        scope = cfg.Scope.parse(watch)
    except cfg.ConfigError as error:
        raise click.ClickException(str(error)) from None
    agents = [_agent_spec(spec) for spec in agent_specs]
    files = [(cfg.CONFIG_PATH, render_config(scope, multiplexer, agents))]
    files += [
        (cfg.CONFIG_PATH.parent / "prompts" / name, _resource_text(f"prompts/{name}"))
        for name in PROMPT_TEMPLATES
    ]
    kept_config = False
    for target, content in files:
        written = _write_if_absent(target, content, force=force)
        kept_config = kept_config or (target == cfg.CONFIG_PATH and not written)
        click.secho(
            f"{'wrote' if written else 'exists'} {target}", fg="green" if written else "yellow",
        )
    try:
        cfg.load(cfg.CONFIG_PATH)
    except cfg.ConfigError as error:
        if not kept_config:
            raise click.ClickException(f"{cfg.CONFIG_PATH} is invalid: {error}") from None
        raise click.ClickException(
            f"kept your existing {cfg.CONFIG_PATH} (init never overwrites it without "
            f"--force), but it is not valid for this version: {error}\n"
            f"Move it aside and run init again, e.g. "
            f"`mv {cfg.CONFIG_PATH} {cfg.CONFIG_PATH.with_name('config.old.toml')}`, "
            "or rerun init with --force to replace it."
        ) from None
    if kept_config and (watch or agents):
        click.secho(
            f"--watch/--agent were not applied because {cfg.CONFIG_PATH} already exists; "
            "edit it, or rerun with --force to replace it.", fg="yellow",
        )
        return
    if not agents:
        click.echo(f"Add [[agents]] entries to {cfg.CONFIG_PATH} to launch reviews.")


def _check(label: str, ok: bool, detail: str) -> bool:
    mark = click.style(f"{'ok' if ok else 'missing':<8}", fg="green" if ok else "red")
    click.echo(f"{mark} {label}: {detail}")
    return ok


def _multiplexer_check(config: cfg.Config) -> bool:
    """Check the configured multiplexer is installed and, for Rex, that its server answers."""
    tool = config.multiplexer
    path = shutil.which(tool)
    if not path:
        return _check(tool, False, "not on PATH")
    if tool != "rex":
        return _check(tool, True, path)
    whoami = subprocess.run(
        ["rex", "whoami", "--json"], capture_output=True, text=True, check=False
    )
    if whoami.returncode:
        return _check("rex", False, whoami.stderr.strip() or "Rex server not reachable")
    session = config.rex_session or os.environ.get("REX_SESSION")
    where = f"tabs open in session {session}" if session else (
        "no session: set `rex_session` or run inside Rex"
    )
    return _check("rex", bool(session), f"{path} · {where}")


@cli.command()
def doctor() -> None:
    """Check that gh, git, the multiplexer, the config, and each configured agent are ready."""
    required = []
    for tool in ("gh", "git"):
        path = shutil.which(tool)
        required.append(_check(tool, bool(path), path or "not on PATH"))
    if shutil.which("gh"):
        auth = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, check=False
        )
        required.append(_check(
            "gh auth", auth.returncode == 0,
            "authenticated" if auth.returncode == 0 else "run `gh auth login`",
        ))
    try:
        config = cfg.load()
    except cfg.ConfigError as error:
        required.append(_check("config", False, str(error)))
        raise click.ClickException("setup incomplete") from None
    required.append(_check(
        "config", True, f"{config.path} · watching {config.scope.describe()}",
    ))
    required.append(_multiplexer_check(config))
    if not config.agents:
        required.append(_check("agents", False, "no [[agents]] configured"))
    for agent in config.agents:
        program = agent.command if config.multiplexer == "rex" else agent.kind
        path = shutil.which(program)
        required.append(_check(
            f"agent {agent.name}", bool(path),
            path or f"`{program}` not on PATH",
        ))
        required.append(_check(
            f"agent {agent.name} prompt", agent.prompt.is_file(), str(agent.prompt),
        ))
    for combat in config.combats:
        for role, path in (
            ("challenge", combat.challenge_prompt), ("opener", combat.opener_prompt),
            ("judge", combat.judge_prompt),
        ):
            if path is not None:
                required.append(_check(f"combat {combat.name} {role}", path.is_file(), str(path)))
    if config.output:
        _check("output", True, f"saving finished reviews to {config.output.dir}")
    if not all(required):
        raise click.ClickException("setup incomplete")


@cli.command("agents")
def agents_command() -> None:
    """List the configured review agents, groups, and their TUI keys."""
    config = _load()
    table = Table(title=f"Agents in {config.path}")
    for heading in ("Name", "Key", "Launches", "Prompt"):
        table.add_column(heading)
    for agent in config.agents:
        program = agent.command if config.multiplexer == "rex" else agent.kind
        launches = " ".join([program, *agent.args])
        table.add_row(agent.name, agent.key or "", launches, str(agent.prompt))
    for group in config.groups:
        table.add_row(group.name, group.key or "", ", ".join(group.agents), "per agent")
    for combat in config.combats:
        order = " -> " if combat.style == "handoff" else " | "
        parts = [f"opener {combat.opener}"] if combat.opener else []
        parts.append(f"{combat.style}: {order.join(combat.agents)} x{combat.rounds} round(s)")
        if combat.judge:
            parts.append(f"judge {combat.judge}")
        table.add_row(combat.name, combat.key or "", "; ".join(parts), str(combat.challenge_prompt))
    Console().print(table)
    if config.output:
        click.echo(f"Finished reviews are saved to {config.output.dir}")


@cli.command("runs")
@click.option("--limit", default=10, show_default=True, help="How many recent runs to show.")
def runs_command(limit: int) -> None:
    """Show recent background review runs and each step's progress."""
    config = _load()
    runs = list_runs(config)[:limit]
    if not runs:
        click.echo("No review runs yet.")
        return
    console = Console()
    for run in runs:
        color = {"done": "green", "failed": "red"}.get(run.get("status"), "yellow")
        console.print(
            f"[bold]{run['key']}[/bold] {run['launcher']} "
            f"[{color}]{run.get('status')}[/{color}] · {run.get('started_at')} · {run['run']}"
        )
        for step in run.get("steps", []):
            where = step.get("saved") or step.get("path") or ""
            console.print(
                f"  {step['step']:>2}. {step['agent']} {step['role']}"
                f"{' r' + str(step['round']) if step['round'] else ''} · {step['status']} {where}"
            )
        if run.get("error"):
            console.print(f"  [red]{run['error']}[/red]")


@cli.command("run-plan", hidden=True)
@click.argument("run_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
def run_plan_command(run_dir: Path) -> None:
    """Drive a launched run to completion (started in the background by launches)."""
    execute(run_dir)


@cli.command("status")
@scope_options
def status_command(watch: tuple[str, ...], watch_all: bool) -> None:
    """List PRs currently requesting your review."""
    config = _load(watch, watch_all)
    candidates = _queue(config)
    active = active_review_keys(config, candidates)
    table = Table(title=f"Review requests in {config.scope.describe()}")
    for heading in ("Status", "PR", "Checks", "Request", "Author", "Title"):
        table.add_column(heading)
    for candidate in candidates:
        short_key = f"{config.scope.label(candidate.repo)}#{candidate.number}"
        table.add_row(
            "in-progress" if candidate.key in active else candidate.status.value,
            short_key, candidate.checks,
            candidate.request_source, f"@{candidate.author}", candidate.title,
        )
    Console().print(table)
    actionable = sum(candidate.actionable for candidate in candidates)
    click.echo(f"{actionable} actionable of {len(candidates)} current request(s)")


@cli.command("mine")
@scope_options
def mine_command(watch: tuple[str, ...], watch_all: bool) -> None:
    """List your open PRs with review, check, and reviewer status."""
    config = _load(watch, watch_all)
    try:
        prs = asyncio.run(authored_prs(config.scope))
    except (GhError, OSError) as error:
        raise click.ClickException(f"GitHub query failed: {error}") from None
    table = Table(title=f"Your open PRs in {config.scope.describe()}")
    for heading in ("Status", "PR", "Checks", "Reviewers", "Title"):
        table.add_column(heading)
    for pr in prs:
        reviewers = "\n".join(f"{r.name} ({r.label})" for r in pr.reviewers) or "none"
        table.add_row(
            pr.status.value, f"{config.scope.label(pr.repo)}#{pr.number}",
            pr.checks, reviewers, pr.title,
        )
    Console().print(table)
    needing = sum(bool(pr.rerequest_targets) for pr in prs)
    click.echo(f"{len(prs)} open PR(s) · {needing} with reviewers to re-request")


@cli.command("next")
@scope_options
@click.option("--agent", default=None, help="Agent or group to offer to launch.")
@click.option("--yes", is_flag=True, help="Launch without asking for confirmation.")
def next_command(
    agent: str | None, yes: bool, watch: tuple[str, ...], watch_all: bool
) -> None:
    """Show the next actionable PR and optionally launch its review."""
    config = _load(watch, watch_all)
    if agent is not None:
        _require_agent(config, agent)
    queue = _queue(config)
    active = active_review_keys(config, queue)
    candidates = [
        candidate for candidate in queue
        if candidate.actionable and candidate.key not in active
    ]
    if not candidates:
        click.echo("No actionable review requests.")
        return
    candidate = candidates[0]
    _print_candidate(candidate)
    if agent is None:
        names = ", ".join(config.target_names) or "none configured"
        click.echo(f"Pass --agent to launch it (agents: {names}).")
        return
    if yes or click.confirm(f"Launch {agent} in a new {config.multiplexer_name} tab?"):
        _do_launch(config, candidate, agent)


@cli.command("launch")
@click.argument("target")
@click.option("--agent", required=True, help="Agent or group name from the config.")
def launch_command(target: str, agent: str) -> None:
    """Launch a requested PR, identified as OWNER/REPO#NUMBER or its URL."""
    normalized = target.removeprefix("https://github.com/").replace("/pull/", "#").rstrip("/")
    repo, _, number = normalized.partition("#")
    if repo.count("/") != 1 or not number.isdigit():
        raise click.ClickException(f"{target} is not OWNER/REPO#NUMBER or a PR URL")
    config = _load((repo,))
    _require_agent(config, agent)
    candidate = next((item for item in _queue(config) if item.key == normalized), None)
    if candidate is None:
        raise click.ClickException(f"{target} is not in your current review-request queue")
    _print_candidate(candidate)
    _do_launch(config, candidate, agent)


@cli.command("tui")
@scope_options
def tui_command(watch: tuple[str, ...], watch_all: bool) -> None:
    """Open an interactive picker for the current review-request queue."""
    from .tui.app import run_tui

    run_tui(_load(watch, watch_all))


if __name__ == "__main__":
    cli()
