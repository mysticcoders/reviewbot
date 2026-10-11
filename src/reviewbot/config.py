"""Configuration for the GitHub review queue, review agents, and Herdr launcher."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

CONFIG_DIR = Path("~/.config/reviewbot").expanduser()
CONFIG_PATH = CONFIG_DIR / "config.toml"

RESERVED_KEYS = frozenset("qro12ae")
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,15}")
KEY_PATTERN = re.compile(r"[A-Za-z0-9]")


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
    """A review agent: a Herdr agent kind plus its launch args, TUI key, and prompt file."""

    name: str
    kind: str
    prompt: Path
    key: str | None = None
    args: tuple[str, ...] = ()

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
class Config:
    scope: Scope
    work_dir: Path
    herdr_workspace: str | None
    agents: tuple[Agent, ...] = ()
    groups: tuple[Group, ...] = ()
    combats: tuple[Combat, ...] = ()
    checkouts: tuple[Checkout, ...] = ()
    output: Output | None = None
    path: Path = field(default=CONFIG_PATH)

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


def _agents(
    raw: dict, base: Path
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
            raise ConfigError(f"agent `{name}` needs a `kind` (a Herdr agent kind such as claude).")
        prompt = item.get("prompt") or shared_prompt
        if not prompt:
            raise ConfigError(f"agent `{name}` has no `prompt`, and no top-level `prompt` is set.")
        args = item.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ConfigError(f"agent `{name}` `args` must be a list of strings.")
        agents.append(Agent(
            name=name, kind=kind, prompt=_expand(str(prompt), base),
            key=_key(item.get("key"), name, keys), args=tuple(args),
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


def load(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"Config not found at {path}. Run `reviewbot init` to create it.")
    try:
        with path.open("rb") as config_file:
            raw = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: {error}") from None
    base = path.parent
    agents, groups, combats = _agents(raw, base)
    return Config(
        scope=_scope(raw),
        work_dir=_expand(raw.get("work_dir", "~/reviewbot-workspaces"), base),
        herdr_workspace=raw.get("herdr_workspace"),
        agents=agents,
        groups=groups,
        combats=combats,
        checkouts=_checkouts(raw, base),
        output=_output(raw, base),
        path=path,
    )
