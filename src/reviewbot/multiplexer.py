"""Route agent launches and prompts to the configured multiplexer (Herdr or Rex)."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from . import herdr, rex
from .config import Agent, Config
from .github import ReviewCandidate
from .launch import LaunchResult

BACKENDS: dict[str, ModuleType] = {"herdr": herdr, "rex": rex}


def backend(config: Config) -> ModuleType:
    return BACKENDS[config.multiplexer]


def open_session(
    config: Config, candidate: ReviewCandidate, *, agent: Agent, checkout: Path
) -> LaunchResult:
    return backend(config).open_session(config, candidate, agent=agent, checkout=checkout)


def send_prompt(config: Config, handle: str, text: str) -> None:
    backend(config).send_prompt(handle, text)


def prompt_and_wait(config: Config, handle: str, text: str) -> None:
    backend(config).prompt_and_wait(handle, text)


def active_review_keys(config: Config, candidates: list[ReviewCandidate]) -> set[str]:
    return backend(config).active_review_keys(config, candidates)
