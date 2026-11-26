"""Configuration for the GitHub review queue, review agents, and their multiplexer."""

from __future__ import annotations

import fcntl
import os
import re
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

CONFIG_DIR = Path("~/.config/reviewbot").expanduser()
CONFIG_PATH = CONFIG_DIR / "config.toml"

RESERVED_KEYS = frozenset("qro123aef")
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,15}")
KEY_PATTERN = re.compile(r"[A-Za-z0-9]")
MULTIPLEXERS = {"herdr": "Herdr", "rex": "Rex"}
BINDS = ("localhost", "tailnet", "tailscale-node")
NODE_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Scope:
    """Which repositories to watch: whole owners, specific repos, or everything (both empty)."""

    owners: tuple[str, ...] = ()
    repos: tuple[str, ...] = ()

    @classmethod
    def parse(cls, entries: list[str] | tuple[str, ...]) -> Scope:
        """Split entries into owners (`acme`) and repos (`acme/api`); `*` or `all` means everything."""
        owners: list[str] = []
        repos: list[str] = []
        for raw in entries:
            entry = str(raw).strip().removeprefix("https://github.com/").strip("/")
            if not entry:
                continue
            if entry in {"*", "all"}:
                return cls()
            parts = entry.split("/")
            if len(parts) == 1:
                target = owners
            elif len(parts) == 2 and all(parts):
                target = repos
            else:
                raise ConfigError(f"watch entry `{raw}` must be OWNER, OWNER/REPO, or `*`")
            if entry.lower() not in {item.lower() for item in target}:
                target.append(entry)
        return cls(owners=tuple(owners), repos=tuple(repos))

    @property
    def is_all(self) -> bool:
        return not self.owners and not self.repos

    @property
    def single_owner(self) -> str | None:
        """The one owner every watched repo belongs to, when there is exactly one."""
        found = {owner.lower() for owner in self.owners}
        found |= {repo.split("/", 1)[0].lower() for repo in self.repos}
        return found.pop() if len(found) == 1 else None

    def label(self, repo: str) -> str:
        """Short repo name when everything watched belongs to one owner, else OWNER/REPO."""
        owner, _, name = repo.partition("/")
        single = self.single_owner
        return name if single and owner.lower() == single else repo

    def contains(self, repo: str) -> bool:
        """Whether OWNER/REPO is watched, ignoring case."""
        if self.is_all:
            return True
        wanted = repo.lower()
        owner = wanted.split("/", 1)[0]
        return (
            any(owner == item.lower() for item in self.owners)
            or any(wanted == item.lower() for item in self.repos)
        )

    def describe(self) -> str:
        if self.is_all:
            return "all repositories"
        return ", ".join([*self.owners, *self.repos])


@dataclass(frozen=True)
class Checkout:
    """An existing local clone to reuse instead of cloning into the work directory."""

    slug: str
    path: Path


@dataclass(frozen=True)
class Agent:
    """A review agent: its kind, the command Rex runs, launch args, TUI key, and prompt file."""

    name: str
    kind: str
    prompt: Path
    key: str | None = None
    args: tuple[str, ...] = ()
    command: str | None = None

    def prompt_template(self) -> str:
        return read_prompt(self.prompt, f"agent `{self.name}`")


def read_prompt(path: Path, owner: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"cannot read prompt for {owner}: {error}") from None


@dataclass(frozen=True)
class Group:
    """A named set of agents launched together on one PR."""

    name: str
    agents: tuple[str, ...]
    key: str | None = None


COMBAT_STYLES = ("parallel", "handoff")


@dataclass(frozen=True)
class Combat:
    """Agents that review a PR and then answer each other's reviews.

    `parallel`: every agent reviews independently, then each round every agent
    answers the others' latest reviews. `handoff`: agents take turns, each one
    seeing every review before it. An optional opener runs a walkthrough first,
    and an optional judge merges everything into a final verdict.
    """

    name: str
    agents: tuple[str, ...]
    style: str
    rounds: int
    challenge_prompt: Path
    key: str | None = None
    opener: str | None = None
    opener_prompt: Path | None = None
    judge: str | None = None
    judge_prompt: Path | None = None


@dataclass(frozen=True)
class Output:
    """Where finished reviews are saved as Markdown, with an append-only index."""

    dir: Path


@dataclass(frozen=True)
class Serve:
    """The local dashboard and MCP endpoint: where it listens and which browser opens PRs.

    `bind` is "localhost" (this machine only), "tailnet" (this machine's Tailscale
    address only), or "tailscale-node" (ReviewBot's own tailnet device, named `hostname`,
    macOS only). `browser` is a macOS application name for `open -a`, or on other
    platforms a command that is run with the URL as its argument.
    """

    port: int
    bind: str
    browser: str
    hostname: str | None = None


OPEN_FILTERS = ("requested", "involves", "all", "stale")
MINE_FILTERS = ("all", "ready", "needs-action", "stale")
NOTIFICATION_FILTERS = ("all", "participating", "reviews")
NOTIFICATION_KINDS = ("pr", "issue")
MAX_STALE_DAYS = 3650
FILTERS_TABLE = (
    "[filters]\n"
    'open = "requested"          # requested | involves | all | stale\n'
    'mine = "all"                # all | ready | needs-action | stale\n'
    'notifications = "all"       # all | participating | reviews\n'
    'notification_kinds = ["pr", "issue"]\n'
    "stale_days = 14\n"
)


@dataclass(frozen=True)
class Filters:
    """What each view shows inside the watched repositories, as set in `[filters]`.

    `open` narrows the Open PRs tab, `mine` the My PRs tab, and `notifications` the
    Notifications tab, which shows only `notification_kinds`. `stale` means no activity
    for `stale_days`. The TUI and dashboard can change a filter for one session.
    """

    open: str
    mine: str
    notifications: str
    notification_kinds: tuple[str, ...]
    stale_days: int


@dataclass(frozen=True)
class Config:
    scope: Scope
    work_dir: Path
    multiplexer: str
    filters: Filters
    herdr_workspace: str | None
    rex_session: str | None = None
    agents: tuple[Agent, ...] = ()
    groups: tuple[Group, ...] = ()
    combats: tuple[Combat, ...] = ()
    checkouts: tuple[Checkout, ...] = ()
    output: Output | None = None
    serve: Serve | None = None
    path: Path = field(default=CONFIG_PATH)

    @property
    def multiplexer_name(self) -> str:
        return MULTIPLEXERS[self.multiplexer]

    def with_scope(self, scope: Scope) -> Config:
        return replace(self, scope=scope)

    @property
    def launchers(self) -> tuple[Agent | Group | Combat, ...]:
        """Everything that can be launched, in TUI order: agents, groups, combats."""
        return (*self.agents, *self.groups, *self.combats)

    @property
    def target_names(self) -> list[str]:
        """Every name accepted by `--agent`: agents, then groups, then combats."""
        return [item.name for item in self.launchers]

    def agent(self, name: str) -> Agent:
        for agent in self.agents:
            if agent.name == name:
                return agent
        raise ConfigError(f"unknown agent `{name}`")

    def combat(self, name: str) -> Combat | None:
        return next((combat for combat in self.combats if combat.name == name), None)

    def resolve(self, name: str) -> list[Agent]:
        """The agents taking part in an agent, group, or combat, in order."""
        by_name = {agent.name: agent for agent in self.agents}
        if name in by_name:
            return [by_name[name]]
        for group in self.groups:
            if group.name == name:
                return [by_name[member] for member in group.agents]
        combat = self.combat(name)
        if combat:
            members = [combat.opener, *combat.agents, combat.judge]
            return [by_name[member] for member in dict.fromkeys(m for m in members if m)]
        available = ", ".join(self.target_names) or "none configured"
        raise ConfigError(f"unknown agent `{name}` (available: {available})")


def _expand(value: str, base: Path) -> Path:
    return base / Path(value).expanduser()


def _work_dir(raw: dict, base: Path) -> Path:
    value = raw.get("work_dir", "~/reviewbot-workspaces")
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("`work_dir` must be a directory path.")
    return _expand(value, base)


def _scope(raw: dict) -> Scope:
    """Read the required `watch` list, accepting the legacy single `organization` key."""
    if "watch" not in raw:
        organization = str(raw.get("organization", "")).strip()
        if organization:
            return Scope.parse([organization])
        raise ConfigError(
            '`watch` is required: list owners or OWNER/REPO entries, or use ["*"] for everything.'
        )
    watch = raw["watch"]
    if isinstance(watch, str):
        watch = [watch]
    if not isinstance(watch, list):
        raise ConfigError("`watch` must be a list of OWNER or OWNER/REPO entries.")
    return Scope.parse(watch)


def _checkouts(raw: dict, base: Path) -> tuple[Checkout, ...]:
    """Read `[[checkouts]]`, also accepting the legacy `[[repos]]` owner/name/path tables."""
    result = []
    for item in [*raw.get("checkouts", []), *raw.get("repos", [])]:
        slug = item.get("repo") or f"{item.get('owner', '')}/{item.get('name', '')}"
        if "path" not in item or slug.startswith("/") or slug.endswith("/"):
            raise ConfigError(f"checkout entry {item!r} needs `repo` (OWNER/REPO) and `path`.")
        result.append(Checkout(slug=slug, path=_expand(item["path"], base)))
    return tuple(result)


def _key(value: object, owner: str, taken: dict[str, str]) -> str | None:
    """Validate a TUI shortcut: one letter or digit, not reserved, not already assigned."""
    if value is None:
        return None
    key = str(value)
    if not KEY_PATTERN.fullmatch(key):
        raise ConfigError(f"`{owner}` key `{key}` must be a single letter or digit.")
    if key in RESERVED_KEYS:
        reserved = ", ".join(sorted(RESERVED_KEYS))
        raise ConfigError(f"`{owner}` key `{key}` is reserved by the TUI ({reserved}).")
    if key in taken:
        raise ConfigError(f"`{owner}` key `{key}` is already used by `{taken[key]}`.")
    taken[key] = owner
    return key


def _name(value: object, kind: str, seen: set[str]) -> str:
    name = str(value or "")
    if not NAME_PATTERN.fullmatch(name):
        raise ConfigError(
            f"{kind} name `{name}` must be 1-16 lowercase letters, digits, '-' or '_'."
        )
    if name in seen:
        raise ConfigError(f"name `{name}` is defined more than once across agents, groups, and combats.")
    seen.add(name)
    return name


def _multiplexer(raw: dict) -> str:
    """Read the required `multiplexer`: which terminal multiplexer hosts the agents."""
    value = raw.get("multiplexer")
    choices = " or ".join(f'"{name}"' for name in MULTIPLEXERS)
    if value is None:
        raise ConfigError(
            f"`multiplexer` is missing: set it to {choices} at the top of the config, "
            "or run `reviewbot doctor` to add it."
        )
    if value not in MULTIPLEXERS:
        raise ConfigError(
            f"`multiplexer` must be {choices}: the terminal multiplexer that hosts review agents."
        )
    return value


def _agents(
    raw: dict, base: Path, multiplexer: str
) -> tuple[tuple[Agent, ...], tuple[Group, ...], tuple[Combat, ...]]:
    """Read `[[agents]]`, `[[groups]]`, and `[[combats]]`, validating names, keys, and members."""
    seen: set[str] = set()
    keys: dict[str, str] = {}
    shared_prompt = raw.get("prompt")
    agents = []
    for item in raw.get("agents", []):
        name = _name(item.get("name"), "agent", seen)
        kind = str(item.get("kind") or "").strip()
        if not kind:
            raise ConfigError(f"agent `{name}` needs a `kind` (an agent kind such as claude).")
        command = str(item.get("command") or "").strip() or None
        if multiplexer == "rex" and not command:
            raise ConfigError(
                f"agent `{name}` needs a `command` (the program Rex runs, such as claude); "
                "run `reviewbot doctor` to add it."
            )
        prompt = item.get("prompt") or shared_prompt
        if not prompt:
            raise ConfigError(f"agent `{name}` has no `prompt`, and no top-level `prompt` is set.")
        args = item.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ConfigError(f"agent `{name}` `args` must be a list of strings.")
        agents.append(Agent(
            name=name, kind=kind, prompt=_expand(str(prompt), base),
            key=_key(item.get("key"), name, keys), args=tuple(args), command=command,
        ))
    known = {agent.name for agent in agents}
    groups = []
    for item in raw.get("groups", []):
        name = _name(item.get("name"), "group", seen)
        members = item.get("agents", [])
        if not isinstance(members, list) or len(members) < 2:
            raise ConfigError(f"group `{name}` needs an `agents` list of at least two agents.")
        missing = [member for member in members if member not in known]
        if missing:
            raise ConfigError(f"group `{name}` references unknown agent(s): {', '.join(missing)}")
        groups.append(Group(
            name=name, agents=tuple(members), key=_key(item.get("key"), name, keys),
        ))
    combats = [_combat(item, base, known, seen, keys) for item in raw.get("combats", [])]
    return tuple(agents), tuple(groups), tuple(combats)


def _combat(
    item: dict, base: Path, known: set[str], seen: set[str], keys: dict[str, str]
) -> Combat:
    """Validate one `[[combats]]` table; every setting is explicit, nothing is defaulted."""
    name = _name(item.get("name"), "combat", seen)
    members = item.get("agents", [])
    if not isinstance(members, list) or len(set(members)) < 2 or len(set(members)) != len(members):
        raise ConfigError(f"combat `{name}` needs an `agents` list of at least two distinct agents.")
    for role in ("opener", "judge"):
        if item.get(role) is not None:
            members = [*members, item[role]]
    missing = [member for member in members if member not in known]
    if missing:
        raise ConfigError(f"combat `{name}` references unknown agent(s): {', '.join(missing)}")
    style = item.get("style")
    if style not in COMBAT_STYLES:
        raise ConfigError(f"combat `{name}` `style` must be one of: {', '.join(COMBAT_STYLES)}.")
    rounds = item.get("rounds")
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 1:
        raise ConfigError(f"combat `{name}` `rounds` must be a whole number of at least 1.")

    def prompt_for(field_name: str, required: bool) -> Path | None:
        value = item.get(field_name)
        if required and not value:
            raise ConfigError(f"combat `{name}` needs `{field_name}`.")
        return _expand(str(value), base) if value else None

    opener = item.get("opener")
    judge = item.get("judge")
    return Combat(
        name=name, agents=tuple(item["agents"]), style=style, rounds=rounds,
        challenge_prompt=prompt_for("challenge_prompt", True),
        key=_key(item.get("key"), name, keys),
        opener=opener, opener_prompt=prompt_for("opener_prompt", bool(opener)),
        judge=judge, judge_prompt=prompt_for("judge_prompt", bool(judge)),
    )


def _output(raw: dict, base: Path) -> Output | None:
    table = raw.get("output")
    if table is None:
        return None
    if not isinstance(table, dict) or not table.get("dir"):
        raise ConfigError("`[output]` needs `dir`, the directory finished reviews are saved to.")
    return Output(dir=_expand(str(table["dir"]), base))


def _serve(raw: dict) -> Serve | None:
    """Parse `[serve]`, requiring every field so nothing about exposure is assumed."""
    table = raw.get("serve")
    if table is None:
        return None
    if not isinstance(table, dict):
        raise ConfigError("`[serve]` must be a table with `port`, `bind`, and `browser`.")
    missing = [name for name in ("port", "bind", "browser") if name not in table]
    if missing:
        raise ConfigError(f"`[serve]` is missing {', '.join(f'`{m}`' for m in missing)}.")
    port = table["port"]
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ConfigError("`[serve]` `port` must be an integer from 0 to 65535.")
    bind = table["bind"]
    if bind not in BINDS:
        raise ConfigError(f"`[serve]` `bind` must be one of {', '.join(BINDS)}, not `{bind}`.")
    browser = table["browser"]
    if not isinstance(browser, str) or not browser.strip():
        raise ConfigError("`[serve]` `browser` must name the browser that opens PRs.")
    return Serve(port=port, bind=bind, browser=browser.strip(), hostname=_node_name(table, bind))


def _node_name(table: dict, bind: str) -> str | None:
    """The tailnet device name, required with `tailscale-node` and meaningless otherwise."""
    hostname = table.get("hostname")
    if bind != "tailscale-node":
        if hostname is not None:
            raise ConfigError('`[serve]` `hostname` only applies with bind = "tailscale-node".')
        return None
    if hostname is None:
        raise ConfigError('`[serve]` bind = "tailscale-node" needs `hostname`, the tailnet device name.')
    if not isinstance(hostname, str) or not NODE_NAME.fullmatch(hostname):
        raise ConfigError(
            "`[serve]` `hostname` must be 1-63 lowercase letters, digits, or '-', "
            "not starting or ending with '-'."
        )
    return hostname


def _choice(table: dict, key: str, choices: tuple[str, ...]) -> str:
    value = table.get(key)
    if value not in choices:
        raise ConfigError(
            f"`[filters]` `{key}` must be one of {', '.join(choices)}"
            + (f", not `{value}`." if value is not None else "; it is missing.")
        )
    return value


def _filters(raw: dict) -> Filters:
    """Parse the required `[filters]` table; every key must be written out."""
    table = raw.get("filters")
    if table is None:
        raise ConfigError(
            "`[filters]` is missing, and ReviewBot could not add it to the config. It sets "
            "what each view shows. Add this table:\n\n" + FILTERS_TABLE
        )
    if not isinstance(table, dict):
        raise ConfigError("`[filters]` must be a table.")
    kinds = table.get("notification_kinds")
    if (
        not isinstance(kinds, list) or not kinds
        or any(kind not in NOTIFICATION_KINDS for kind in kinds)
    ):
        raise ConfigError(
            "`[filters]` `notification_kinds` must list one or more of "
            f"{', '.join(NOTIFICATION_KINDS)}."
        )
    days = table.get("stale_days")
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_STALE_DAYS:
        raise ConfigError(
            f"`[filters]` `stale_days` must be a whole number of days from 1 to {MAX_STALE_DAYS}."
        )
    return Filters(
        open=_choice(table, "open", OPEN_FILTERS),
        mine=_choice(table, "mine", MINE_FILTERS),
        notifications=_choice(table, "notifications", NOTIFICATION_FILTERS),
        notification_kinds=tuple(dict.fromkeys(kinds)),
        stale_days=days,
    )


def _write_filters(path: Path, text: str) -> bool:
    """Add the `[filters]` table to an otherwise valid config written before it existed.

    It runs only after the rest of the config has loaded, so a broken config is left
    alone. The table is written into the file, set to what earlier versions showed, so every
    setting stays visible in the config. The previous file is kept beside it, with
    `.before-0.5` added to its name.

    A symlinked config is updated at its target. ReviewBot processes take a lock on the
    config while they do this. Both files are first staged as private temporary files
    with the permissions the locked config has; only then is the config checked again,
    and if it is a different file, has other permissions, or no longer holds what was
    loaded, the staged files are discarded. Otherwise they are
    moved into place at once. Returns whether the file
    now differs from `text` (so the caller loads it again from the start), or False if
    it could not be written.
    """
    target = path.resolve()
    staged: list[str] = []
    try:
        with open(target, "rb") as locked:
            fcntl.flock(locked, fcntl.LOCK_EX)
            source = os.fstat(locked.fileno())
            mode = source.st_mode & 0o777
            backup = _stage(target.with_name(f"{target.name}.before-0.5"), text, mode, staged)
            updated = _stage(target, add_filters(text), mode, staged)
            now = os.stat(target)
            if (
                (now.st_ino, now.st_dev, now.st_mode) != (source.st_ino, source.st_dev, source.st_mode)
                or target.read_text(encoding="utf-8") != text
            ):
                return True
            os.replace(backup, target.with_name(f"{target.name}.before-0.5"))
            os.replace(updated, target)
    except OSError:
        return False
    finally:
        for leftover in staged:
            if os.path.exists(leftover):
                os.unlink(leftover)
    sys.stderr.write(
        f"reviewbot: added a [filters] table to {target}, set to show what earlier versions "
        f"showed (previous file saved as {target.name}.before-0.5)\n"
    )
    return True


def _stage(path: Path, text: str, mode: int, staged: list[str]) -> str:
    """Write `text` to a private temporary file beside `path`, ready to move into place."""
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged.append(temporary)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(text)
    os.chmod(temporary, mode)
    return temporary


def load(path: Path | None = None, *, _attempts: int = 3) -> Config:
    """Load and validate the config, adding a missing `[filters]` table to the file first.

    When the table is added, or the file changes while it is being added, the config is
    loaded again from the start, so every setting comes from the same version of the file.
    """
    path = path or CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"Config not found at {path}. Run `reviewbot init` to create it.")
    try:
        text = path.read_text(encoding="utf-8")
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: {error}") from None
    base = path.parent
    scope = _scope(raw)
    multiplexer = _multiplexer(raw)
    agents, groups, combats = _agents(raw, base, multiplexer)
    checkouts, output, serve = _checkouts(raw, base), _output(raw, base), _serve(raw)
    work_dir = _work_dir(raw, base)
    for key in ("herdr_workspace", "rex_session"):
        if raw.get(key) is not None and not isinstance(raw[key], str):
            raise ConfigError(f"`{key}` must be a string.")
    if "filters" not in raw and _attempts > 0 and _write_filters(path, text):
        return load(path, _attempts=_attempts - 1)
    return Config(
        scope=scope,
        work_dir=work_dir,
        multiplexer=multiplexer,
        filters=_filters(raw),
        herdr_workspace=raw.get("herdr_workspace"),
        rex_session=raw.get("rex_session"),
        agents=agents,
        groups=groups,
        combats=combats,
        checkouts=checkouts,
        output=output,
        serve=serve,
        path=path,
    )


TABLE_HEADER = re.compile(r"\s*\[")
NAME_LINE = re.compile(r'\s*name\s*=\s*"([^"]*)"')
KIND_LINE = re.compile(r"\s*kind\s*=")


def add_multiplexer(text: str, multiplexer: str) -> str:
    """Insert a top-level `multiplexer` line before the first table and its leading comments.

    Top-level keys must come before any table, so the line goes above the first
    `[...]` header, and above the comment block that introduces it.
    """
    lines = text.splitlines(keepends=True)
    index = next((i for i, line in enumerate(lines) if TABLE_HEADER.match(line)), len(lines))
    while index > 0 and lines[index - 1].lstrip().startswith("#"):
        index -= 1
    entry = (
        '# Which terminal multiplexer hosts the review agents: "herdr" or "rex".\n'
        f'multiplexer = "{multiplexer}"\n\n'
    )
    if index == len(lines) and lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return "".join([*lines[:index], entry, *lines[index:]])


def add_filters(text: str) -> str:
    """Append a `[filters]` table set to what earlier versions showed."""
    if text and not text.endswith("\n"):
        text += "\n"
    return (
        text + "\n# What each view shows. See https://github.com/mysticcoders/reviewbot/blob/main/docs/reference/configuration.md#filters\n" + FILTERS_TABLE
    )


def agents_missing_command(raw: dict) -> list[tuple[str, str]]:
    """The (name, kind) of each `[[agents]]` entry that has no `command`."""
    return [
        (str(item.get("name") or ""), str(item.get("kind") or ""))
        for item in raw.get("agents", [])
        if not str(item.get("command") or "").strip()
    ]


def add_commands(text: str, commands: dict[str, str]) -> str:
    """Insert `command = "..."` into each named `[[agents]]` table, after its `kind` line."""
    lines = text.splitlines(keepends=True)
    tables: list[tuple[int, int]] = []
    start = None
    for i, line in enumerate(lines):
        if TABLE_HEADER.match(line):
            if start is not None:
                tables.append((start, i))
            start = i if line.strip() == "[[agents]]" else None
    if start is not None:
        tables.append((start, len(lines)))
    for begin, end in reversed(tables):
        body = range(begin + 1, end)
        name = next((m.group(1) for i in body if (m := NAME_LINE.match(lines[i]))), None)
        if name not in commands:
            continue
        anchor = next((i for i in body if KIND_LINE.match(lines[i])), begin)
        if not lines[anchor].endswith("\n"):
            lines[anchor] += "\n"
        lines.insert(anchor + 1, f'command = "{commands[name]}"\n')
    return "".join(lines)
