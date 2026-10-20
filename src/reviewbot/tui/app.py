"""Textual picker for the review-request queue and the user's own open PRs."""

from __future__ import annotations

import asyncio
import subprocess
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    SelectionList,
    Static,
    TabbedContent,
    TabPane,
)

from ..config import Config
from ..github import (
    AuthoredPR,
    GhError,
    ReviewCandidate,
    authored_prs,
    request_reviewers,
    review_queue,
)
from ..launch import LaunchError
from ..multiplexer import active_review_keys
from ..run import collects, launch_review


def _display_status(candidate: ReviewCandidate, active: set[str]) -> str:
    return "in-progress" if candidate.key in active else candidate.status.value


QUEUE_TAB = "queue-tab"
MINE_TAB = "mine-tab"
QUEUE_ACTIONS = {"launch"}
MINE_ACTIONS = {"request_review", "rerequest_stale"}


def _parse_reviewers(text: str) -> list[str]:
    return [item.strip().removeprefix("@") for item in text.replace(",", " ").split() if item.strip()]


class ReviewerPicker(ModalScreen[list[str] | None]):
    """Choose existing or suggested reviewers, or type new ones, for an authored PR."""

    CSS = """
    ReviewerPicker { align: center middle; }
    #picker {
        width: 80;
        max-width: 95%;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #picker SelectionList { height: auto; max-height: 16; margin: 1 0; }
    #picker .hint { color: $text-muted; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Request"),
    ]

    def __init__(self, pr: AuthoredPR) -> None:
        super().__init__()
        self.pr = pr

    def compose(self) -> ComposeResult:
        options: list[tuple[str, str, bool]] = []
        for reviewer in self.pr.reviewers:
            if reviewer.requested:
                continue
            name = f"{self.pr.repo.split('/', 1)[0]}/{reviewer.name}" if reviewer.is_team else reviewer.name
            options.append((f"{name}  ({reviewer.label})", name, reviewer.needs_rerequest))
        for login in self.pr.suggested:
            options.append((f"{login}  (suggested)", login, False))
        pending = [reviewer.name for reviewer in self.pr.reviewers if reviewer.requested]
        with Vertical(id="picker"):
            yield Label(f"[bold]Request review[/bold] for {self.pr.key}")
            if pending:
                yield Label(f"Already waiting on: {', '.join(pending)}", classes="hint")
            if options:
                yield SelectionList[str](*options, id="picker-options")
            else:
                yield Label("No previous or suggested reviewers.", classes="hint")
            yield Input(placeholder="Other reviewers: login, org/team (comma separated)", id="picker-input")
            yield Label("space toggles · enter in field or ctrl+s requests · esc cancels", classes="hint")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        chosen: list[str] = []
        if self.query("#picker-options"):
            chosen.extend(self.query_one("#picker-options", SelectionList).selected)
        chosen.extend(_parse_reviewers(self.query_one("#picker-input", Input).value))
        self.dismiss(list(dict.fromkeys(chosen)))

    def on_input_submitted(self, _: Input.Submitted) -> None:
        self.action_submit()


class ReviewQueueApp(App[None]):
    """Browse requested reviews and the user's own PRs, handing reviews to agents."""

    CSS = """
    Screen { layout: vertical; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0; }
    DataTable { height: 1fr; }
    #detail {
        height: 7;
        padding: 0 1;
        border-top: solid $accent;
    }
    #status-line {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("o", "open_pr", "Open PR"),
        Binding("1", "show_tab('queue-tab')", "Reviews", show=False),
        Binding("2", "show_tab('mine-tab')", "My PRs", show=False),
        Binding("a", "request_review", "Request review"),
        Binding("e", "rerequest_stale", "Re-request stale"),
    ]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        for launcher in config.launchers:
            if launcher.key:
                self.bind(launcher.key, f"launch({launcher.name!r})", description=launcher.name)
        self._table: DataTable[str]
        self._mine_table: DataTable[str]
        self._detail: Static
        self._status_line: Static
        self._candidates: list[ReviewCandidate] = []
        self._authored: list[AuthoredPR] = []
        self._active: set[str] = set()
        self._launching = False
        self._requesting = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with TabbedContent(initial=QUEUE_TAB, id="tabs"):
            with TabPane("Review requests [1]", id=QUEUE_TAB):
                self._table = DataTable(id="queue", cursor_type="row", zebra_stripes=True)
                self._table.add_columns("Status", "Repository", "PR", "Checks", "Request", "Author", "Title")
                yield self._table
            with TabPane("My PRs [2]", id=MINE_TAB):
                self._mine_table = DataTable(id="mine", cursor_type="row", zebra_stripes=True)
                self._mine_table.add_columns("Status", "Repository", "PR", "Checks", "Reviews", "Title")
                yield self._mine_table
        self._detail = Static("Loading review requests…", id="detail")
        yield self._detail
        self._status_line = Static("", id="status-line")
        yield self._status_line
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "ReviewBot"
        self.sub_title = self.config.scope.describe()
        await self._refresh_queue()
        self.run_worker(self._refresh_mine(quiet=True), exclusive=False)

    @property
    def _on_mine(self) -> bool:
        return self.query_one("#tabs", TabbedContent).active == MINE_TAB

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if isinstance(self.screen, ModalScreen):
            return False
        if action in QUEUE_ACTIONS:
            return not self._on_mine
        if action in MINE_ACTIONS:
            return self._on_mine
        return True

    def on_tabbed_content_tab_activated(self, _: TabbedContent.TabActivated) -> None:
        self.refresh_bindings()
        self._show_selected()
        self._show_summary()
        (self._mine_table if self._on_mine else self._table).focus()

    def action_show_tab(self, tab: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab

    def _selected(self) -> ReviewCandidate | None:
        row = self._table.cursor_row
        return self._candidates[row] if 0 <= row < len(self._candidates) else None

    def _selected_mine(self) -> AuthoredPR | None:
        row = self._mine_table.cursor_row
        return self._authored[row] if 0 <= row < len(self._authored) else None

    def _show_selected(self) -> None:
        if self._on_mine:
            self._show_selected_mine()
            return
        candidate = self._selected()
        if candidate is None:
            self._detail.update("No review request selected.")
            return
        previous = ""
        if candidate.last_review_state:
            previous = (
                f"\nPrevious: {candidate.last_review_state.lower()} at "
                f"{(candidate.last_review_sha or '')[:7]}; "
                f"{candidate.commits_since_review} newer commit(s)"
            )
        self._detail.update(
            f"[bold]{candidate.key}[/bold] — {candidate.title}\n"
            f"{candidate.base_branch} ← {candidate.head_branch} ({candidate.head_sha[:7]})"
            f"{previous}\n{candidate.url}"
        )

    def _show_selected_mine(self) -> None:
        pr = self._selected_mine()
        if pr is None:
            self._detail.update("No open PRs authored by you.")
            return
        reviewers = ", ".join(f"{r.name} ({r.label})" for r in pr.reviewers) or "none"
        suggested = f"\nSuggested: {', '.join(pr.suggested)}" if pr.suggested else ""
        mergeable = pr.mergeable.lower() or "unknown"
        decision = pr.review_decision.lower().replace("_", " ") or "no decision"
        self._detail.update(
            f"[bold]{pr.key}[/bold] — {pr.title}\n"
            f"{pr.base_branch} ← {pr.head_branch} ({pr.head_sha[:7]}) · "
            f"{decision} · {mergeable} · checks {pr.checks}\n"
            f"Reviewers: {reviewers}{suggested}\n{pr.url}"
        )

    async def action_refresh(self) -> None:
        if self._on_mine:
            await self._refresh_mine()
        else:
            await self._refresh_queue()

    async def _refresh_queue(self) -> None:
        if self._launching:
            return
        self._status_line.update(f"Fetching review requests in {self.config.scope.describe()}…")
        try:
            candidates = await review_queue(self.config.scope)
            active = await asyncio.to_thread(active_review_keys, self.config, candidates)
        except (GhError, LaunchError, OSError) as error:
            self._status_line.update(f"[red]Refresh failed:[/red] {error}")
            return
        self._candidates = candidates
        self._active = active
        self._table.clear()
        for candidate in candidates:
            self._table.add_row(
                _display_status(candidate, active),
                self.config.scope.label(candidate.repo),
                str(candidate.number),
                candidate.checks,
                candidate.request_source,
                f"@{candidate.author}",
                candidate.title,
            )
        if not self._on_mine:
            self._show_summary()
            self._show_selected()

    async def _refresh_mine(self, *, quiet: bool = False) -> None:
        if not quiet:
            self._status_line.update(f"Fetching your open PRs in {self.config.scope.describe()}…")
        try:
            authored = await authored_prs(self.config.scope)
        except (GhError, OSError) as error:
            self._status_line.update(f"[red]My PRs refresh failed:[/red] {error}")
            return
        row = self._mine_table.cursor_row
        self._authored = authored
        self._mine_table.clear()
        for pr in authored:
            self._mine_table.add_row(
                pr.status.value,
                self.config.scope.label(pr.repo),
                str(pr.number),
                pr.checks,
                pr.reviewer_summary,
                pr.title,
            )
        if authored:
            self._mine_table.move_cursor(row=min(row, len(authored) - 1))
        if self._on_mine:
            self._show_summary()
            self._show_selected()

    def _show_summary(self) -> None:
        if self._on_mine:
            needing = sum(bool(pr.rerequest_targets) for pr in self._authored)
            self._status_line.update(
                f"{len(self._authored)} open PR(s) · {needing} with reviewers to re-request"
            )
            return
        actionable = sum(
            candidate.actionable and candidate.key not in self._active
            for candidate in self._candidates
        )
        hint = "" if self.config.agents else f" · no agents configured in {self.config.path}"
        self._status_line.update(
            f"{actionable} actionable · {len(self._candidates)} current request(s){hint}"
        )

    def on_data_table_row_highlighted(self, _: DataTable.RowHighlighted) -> None:
        self._show_selected()

    def action_open_pr(self) -> None:
        target = self._selected_mine() if self._on_mine else self._selected()
        if target is None:
            return
        subprocess.Popen(
            ["gh", "pr", "view", str(target.number), "--repo", target.repo, "--web"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._status_line.update(f"Opening {target.url}")

    async def _request(self, pr: AuthoredPR, reviewers: list[str]) -> None:
        if not reviewers or self._requesting:
            return
        self._requesting = True
        names = ", ".join(reviewers)
        self._status_line.update(f"Requesting review from {names} on {pr.key}…")
        try:
            await request_reviewers(pr.repo, pr.number, reviewers)
        except (GhError, OSError) as error:
            self._status_line.update(f"[red]Request failed:[/red] {error}")
            return
        finally:
            self._requesting = False
        await self._refresh_mine(quiet=True)
        self._status_line.update(f"Requested review from {names} on {pr.key}")

    def action_request_review(self) -> None:
        pr = self._selected_mine()
        if pr is None:
            return

        def picked(reviewers: list[str] | None) -> None:
            if reviewers:
                self.run_worker(self._request(pr, reviewers))
            elif reviewers is not None:
                self._status_line.update("No reviewers selected")

        self.push_screen(ReviewerPicker(pr), picked)

    async def action_rerequest_stale(self) -> None:
        pr = self._selected_mine()
        if pr is None:
            return
        targets = pr.rerequest_targets
        if not targets:
            self._status_line.update(f"{pr.key} has no stale or changes-requested reviewers")
            return
        await self._request(pr, targets)

    async def _launch(self, agent: str) -> None:
        candidate = self._selected()
        if candidate is None or self._launching:
            return
        if candidate.key in self._active:
            self._status_line.update(f"{candidate.key} already has a {self.config.multiplexer_name} review tab")
            return
        if not candidate.actionable:
            self._status_line.update(
                f"{candidate.key} is {candidate.status.value}, not actionable"
            )
            return
        self._launching = True
        self._status_line.update(f"Launching {agent} for {candidate.key}…")
        try:
            results = await asyncio.to_thread(
                launch_review, self.config, candidate, agent=agent
            )
        except LaunchError as error:
            self._status_line.update(f"[red]Launch failed:[/red] {error}")
        else:
            self._active.add(candidate.key)
            self._table.update_cell_at(
                (self._table.cursor_row, 0), "in-progress"
            )
            names = ", ".join(result.agent for result in results)
            follow = " · progress: `reviewbot runs`" if collects(self.config, agent) else ""
            self._status_line.update(f"Started {agent} ({names}) for {candidate.key}{follow}")
        finally:
            self._launching = False

    async def action_launch(self, agent: str) -> None:
        await self._launch(agent)


def run_tui(config: Config) -> None:
    ReviewQueueApp(config).run()
