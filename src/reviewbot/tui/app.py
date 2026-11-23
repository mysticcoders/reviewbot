"""Textual picker for the review-request queue, the user's own open PRs, and notifications."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
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

from ..board import Board, OpenError, open_in_browser
from ..config import Config
from ..github import (
    AuthoredPR,
    GhError,
    Notification,
    ReviewCandidate,
    authored_prs,
    mark_notification_read,
    notifications,
    request_reviewers,
    review_queue,
)
from ..launch import LaunchError
from ..multiplexer import active_review_keys
from ..run import collects, launch_review
from ..server import Dashboard, ServeError

TAB_POLL_SECONDS = 3
NOTES_POLL_SECONDS = 120


def _display_status(candidate: ReviewCandidate, active: set[str]) -> str:
    return "in-progress" if candidate.key in active else candidate.status.value


QUEUE_TAB = "queue-tab"
MINE_TAB = "mine-tab"
NOTES_TAB = "notes-tab"
QUEUE_ACTIONS = {"launch"}
MINE_ACTIONS = {"request_review", "rerequest_stale"}
UNREAD = " *"


def escape(text: str) -> str:
    """Make outside text (titles, errors, URLs) display as typed instead of as markup.

    Textual reads any `[...]`, including `[WIP]` and `[$x]`, as a tag, and its own escape
    misses those, so every opening bracket is escaped.
    """
    return text.replace("[", "\\[")


def _ago(iso: str) -> str:
    """Render an ISO timestamp as a short age such as 5m, 3h, or 2d."""
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    minutes = max(0, int((datetime.now(UTC) - then).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 1440:
        return f"{minutes // 60}h"
    return f"{minutes // 1440}d"


class NotesTable(DataTable[str]):
    """The notifications table; `m` marks the selected notification read."""

    BINDINGS: ClassVar[list[Binding]] = [Binding("m", "app.mark_read", "Mark read")]


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
            options.append((f"{escape(name)}  ({reviewer.label})", name, reviewer.needs_rerequest))
        for login in self.pr.suggested:
            options.append((f"{escape(login)}  (suggested)", login, False))
        pending = [reviewer.name for reviewer in self.pr.reviewers if reviewer.requested]
        with Vertical(id="picker"):
            yield Label(f"[bold]Request review[/bold] for {escape(self.pr.key)}")
            if pending:
                yield Label(f"Already waiting on: {escape(', '.join(pending))}", classes="hint")
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
        Binding("3", "show_tab('notes-tab')", "Notifications", show=False),
        Binding("a", "request_review", "Request review"),
        Binding("e", "rerequest_stale", "Re-request stale"),
        Binding("ctrl+w", "toggle_serve", "Serve"),
    ]

    def __init__(self, config: Config, *, serve: bool = False) -> None:
        super().__init__()
        self.config = config
        self.board = Board(config)
        self._dashboard: Dashboard | None = None
        self._serve_on_mount = serve
        self._starting_serve = False
        for launcher in config.launchers:
            if launcher.key:
                self.bind(launcher.key, f"launch({launcher.name!r})", description=launcher.name)
        self._table: DataTable[str]
        self._mine_table: DataTable[str]
        self._notes_table: NotesTable
        self._detail: Static
        self._status_line: Static
        self._candidates: list[ReviewCandidate] = []
        self._authored: list[AuthoredPR] = []
        self._notes: list[Notification] = []
        self._active: set[str] = set()
        self._launching = False
        self._requesting = False
        self._syncing = False
        self._marking = False

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
            with TabPane("Notifications [3]", id=NOTES_TAB):
                self._notes_table = NotesTable(id="notes", cursor_type="row", zebra_stripes=True)
                self._notes_table.add_columns("Reason", "Repository", "Number", "Kind", "Age", "Title")
                yield self._notes_table
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
        self.run_worker(self._refresh_notes(quiet=True), exclusive=False)
        self.set_interval(TAB_POLL_SECONDS, self._sync_active)
        self.set_interval(NOTES_POLL_SECONDS, self._poll_notes)
        if self._serve_on_mount:
            self.run_worker(self._start_serve(open_browser=False))

    def on_unmount(self) -> None:
        if self._dashboard is not None:
            self._dashboard.stop()
            self._dashboard = None

    def _announce_login(self, url: str) -> None:
        """Show a Tailscale sign-in link and open it; called from the joining thread."""

        def show() -> None:
            self._status_line.update(f"Approve ReviewBot's tailnet device: {escape(url)}")
            try:
                open_in_browser(self.config.serve, url)
            except OpenError:
                pass

        self.call_from_thread(show)

    def _announce_status(self, message: str) -> None:
        self.call_from_thread(self._status_line.update, escape(message))

    async def _start_serve(self, *, open_browser: bool) -> None:
        """Start the dashboard and HTTP MCP endpoint, joining the tailnet first if configured."""
        if self.config.serve is None:
            self._status_line.update(
                f"[red]Serve:[/red] add a `\\[serve]` table (port, bind, browser) to "
                f"{escape(str(self.config.path))}"
            )
            return
        if self._starting_serve:
            return
        self._starting_serve = True
        if self.config.serve.bind == "tailscale-node":
            self._status_line.update(
                f"Joining the tailnet as `{escape(self.config.serve.hostname or '')}`…"
            )
        try:
            dashboard = await asyncio.to_thread(
                Dashboard, self.config, self.board, on_login=self._announce_login,
                on_status=self._announce_status,
            )
        except ServeError as error:
            self._status_line.update(f"[red]Serve failed:[/red] {escape(str(error))}")
            return
        finally:
            self._starting_serve = False
        self._dashboard = dashboard
        dashboard.start()
        self.sub_title = f"{self.config.scope.describe()} · serving {dashboard.url}"
        self._status_line.update(
            f"Serving {dashboard.url} and MCP at {dashboard.mcp_url} · ctrl+w stops"
        )
        if open_browser:
            try:
                open_in_browser(self.config.serve, dashboard.login_url)
            except OpenError as error:
                self._status_line.update(f"[red]Could not open the dashboard:[/red] {escape(str(error))}")

    async def action_toggle_serve(self) -> None:
        if self._dashboard is None:
            self.run_worker(self._start_serve(open_browser=True))
            return
        dashboard, self._dashboard = self._dashboard, None
        self.sub_title = self.config.scope.describe()
        self.run_worker(asyncio.to_thread(dashboard.stop))
        self._status_line.update(f"Stopped serving {dashboard.url}")

    @property
    def _tab(self) -> str:
        return self.query_one("#tabs", TabbedContent).active

    @property
    def _on_mine(self) -> bool:
        return self._tab == MINE_TAB

    @property
    def _on_notes(self) -> bool:
        return self._tab == NOTES_TAB

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if isinstance(self.screen, ModalScreen):
            return False
        if action in QUEUE_ACTIONS:
            return self._tab == QUEUE_TAB
        if action in MINE_ACTIONS:
            return self._on_mine
        if action == "mark_read":
            return self._on_notes
        return True

    def on_tabbed_content_tab_activated(self, _: TabbedContent.TabActivated) -> None:
        self.refresh_bindings()
        self._show_selected()
        self._show_summary()
        {MINE_TAB: self._mine_table, NOTES_TAB: self._notes_table}.get(self._tab, self._table).focus()

    def action_show_tab(self, tab: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab

    def _selected(self) -> ReviewCandidate | None:
        row = self._table.cursor_row
        return self._candidates[row] if 0 <= row < len(self._candidates) else None

    def _selected_mine(self) -> AuthoredPR | None:
        row = self._mine_table.cursor_row
        return self._authored[row] if 0 <= row < len(self._authored) else None

    def _selected_note(self) -> Notification | None:
        row = self._notes_table.cursor_row
        return self._notes[row] if 0 <= row < len(self._notes) else None

    def _unread_reasons(self, key: str) -> str:
        """The reasons of unread notifications about one PR, for the detail pane."""
        reasons = dict.fromkeys(note.reason_label for note in self._notes if note.key == key)
        return f" · [bold]unread:[/bold] {escape(', '.join(reasons))}" if reasons else ""

    def _pr_cell(self, number: int, key: str) -> str:
        return f"{number}{UNREAD}" if any(note.key == key for note in self._notes) else str(number)

    def _show_selected(self) -> None:
        if self._on_mine:
            self._show_selected_mine()
            return
        if self._on_notes:
            self._show_selected_note()
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
            f"[bold]{escape(candidate.key)}[/bold] — {escape(candidate.title)}"
            f"{self._unread_reasons(candidate.key)}\n"
            f"{escape(candidate.base_branch)} ← {escape(candidate.head_branch)} "
            f"({candidate.head_sha[:7]}){previous}\n{escape(candidate.url)}"
        )

    def _show_selected_mine(self) -> None:
        pr = self._selected_mine()
        if pr is None:
            self._detail.update("No open PRs authored by you.")
            return
        reviewers = ", ".join(f"{escape(r.name)} ({r.label})" for r in pr.reviewers) or "none"
        suggested = f"\nSuggested: {escape(', '.join(pr.suggested))}" if pr.suggested else ""
        mergeable = pr.mergeable.lower() or "unknown"
        decision = pr.review_decision.lower().replace("_", " ") or "no decision"
        self._detail.update(
            f"[bold]{escape(pr.key)}[/bold] — {escape(pr.title)}{self._unread_reasons(pr.key)}\n"
            f"{escape(pr.base_branch)} ← {escape(pr.head_branch)} ({pr.head_sha[:7]}) · "
            f"{decision} · {mergeable} · checks {pr.checks}\n"
            f"Reviewers: {reviewers}{suggested}\n{escape(pr.url)}"
        )

    def _show_selected_note(self) -> None:
        note = self._selected_note()
        if note is None:
            self._detail.update("No unread notifications.")
            return
        kind = "pull request" if note.kind == "pr" else "issue"
        self._detail.update(
            f"[bold]{escape(note.key)}[/bold] — {escape(note.title)}\n"
            f"{kind} · {escape(note.reason_label)} · updated {_ago(note.updated_at)} ago\n"
            f"{escape(note.url)}\no opens it and marks it read · m marks it read"
        )

    async def action_refresh(self) -> None:
        if self._on_mine:
            await self._refresh_mine()
        elif self._on_notes:
            await self._refresh_notes()
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
            self._status_line.update(f"[red]Refresh failed:[/red] {escape(str(error))}")
            return
        selected = self._selected()
        self._candidates = candidates
        self._active = active
        self.board.update_queue(candidates, active)
        self._table.clear()
        for candidate in candidates:
            self._table.add_row(
                _display_status(candidate, active),
                escape(self.config.scope.label(candidate.repo)),
                self._pr_cell(candidate.number, candidate.key),
                candidate.checks,
                candidate.request_source,
                escape(f"@{candidate.author}"),
                escape(candidate.title),
            )
        keys = [candidate.key for candidate in candidates]
        if selected is not None and selected.key in keys:
            self._table.move_cursor(row=keys.index(selected.key))
        if not self._on_mine:
            self._show_summary()
            self._show_selected()

    async def _sync_active(self) -> None:
        """Re-check which PRs still have an open review tab, without asking GitHub.

        The Status column is redrawn only when the set of open tabs changes. When a tab
        has closed, the agent may have posted its review, so the queue is then refetched
        from GitHub once.
        """
        if self._syncing or self._launching or not self._candidates:
            return
        self._syncing = True
        try:
            candidates = self._candidates
            try:
                active = await asyncio.to_thread(active_review_keys, self.config, candidates)
            except (LaunchError, OSError):
                return
            if candidates is not self._candidates or self._launching or active == self._active:
                return
            closed = bool(self._active - active)
            self._active = active
            self.board.update_active(active)
            for row, candidate in enumerate(candidates):
                self._table.update_cell_at((row, 0), _display_status(candidate, active))
            if not self._on_mine:
                self._show_summary()
        finally:
            self._syncing = False
        if closed:
            await self._refresh_queue()

    async def _refresh_mine(self, *, quiet: bool = False) -> None:
        if not quiet:
            self._status_line.update(f"Fetching your open PRs in {self.config.scope.describe()}…")
        try:
            authored = await authored_prs(self.config.scope)
        except (GhError, OSError) as error:
            self._status_line.update(f"[red]My PRs refresh failed:[/red] {escape(str(error))}")
            return
        row = self._mine_table.cursor_row
        self._authored = authored
        self.board.update_mine(authored)
        self._mine_table.clear()
        for pr in authored:
            self._mine_table.add_row(
                pr.status.value,
                escape(self.config.scope.label(pr.repo)),
                self._pr_cell(pr.number, pr.key),
                pr.checks,
                escape(pr.reviewer_summary),
                escape(pr.title),
            )
        if authored:
            self._mine_table.move_cursor(row=min(row, len(authored) - 1))
        if self._on_mine:
            self._show_summary()
            self._show_selected()

    async def _poll_notes(self) -> None:
        await self._refresh_notes(quiet=True)

    async def _refresh_notes(self, *, quiet: bool = False) -> None:
        """Fetch unread notifications, redraw their tab, and mark PR rows that have any."""
        if not quiet:
            self._status_line.update(f"Fetching notifications in {self.config.scope.describe()}…")
        try:
            notes = await notifications(self.config.scope)
        except (GhError, OSError) as error:
            if not quiet or self._on_notes:
                self._status_line.update(f"[red]Notifications refresh failed:[/red] {escape(str(error))}")
            return
        self._show_notes(notes)
        self.board.update_notifications(notes)

    def _show_notes(self, notes: list[Notification]) -> None:
        selected = self._selected_note()
        self._notes = notes
        self._notes_table.clear()
        for note in notes:
            self._notes_table.add_row(
                escape(note.reason_label),
                escape(self.config.scope.label(note.repo)),
                str(note.number),
                "PR" if note.kind == "pr" else "issue",
                _ago(note.updated_at),
                escape(note.title),
            )
        ids = [note.id for note in notes]
        if selected is not None and selected.id in ids:
            self._notes_table.move_cursor(row=ids.index(selected.id))
        label = f"Notifications ({len(notes)}) [3]" if notes else "Notifications [3]"
        self.query_one("#tabs", TabbedContent).get_tab(NOTES_TAB).label = label
        for row, candidate in enumerate(self._candidates):
            self._table.update_cell_at((row, 2), self._pr_cell(candidate.number, candidate.key))
        for row, pr in enumerate(self._authored):
            self._mine_table.update_cell_at((row, 2), self._pr_cell(pr.number, pr.key))
        self._show_selected()
        if self._on_notes:
            self._show_summary()

    async def action_mark_read(self) -> None:
        note = self._selected_note()
        if note is not None:
            await self._mark_read(note)

    async def _mark_read(self, note: Notification) -> bool:
        if self._marking:
            return False
        self._marking = True
        try:
            await mark_notification_read(note.id)
        except (GhError, OSError) as error:
            self._status_line.update(f"[red]Mark read failed:[/red] {escape(str(error))}")
            return False
        finally:
            self._marking = False
        self._show_notes([item for item in self._notes if item.id != note.id])
        self.board.update_notifications(self._notes)
        self._status_line.update(f"Marked {escape(note.key)} read")
        return True

    def _show_summary(self) -> None:
        if self._on_notes:
            prs = sum(note.kind == "pr" for note in self._notes)
            self._status_line.update(
                f"{len(self._notes)} unread · {prs} on PRs · {len(self._notes) - prs} on issues"
            )
            return
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

    async def action_open_pr(self) -> None:
        if self._on_notes:
            note = self._selected_note()
            if note is None:
                return
            command = "pr" if note.kind == "pr" else "issue"
            self._open_web(command, note.number, note.repo)
            if await self._mark_read(note):
                self._status_line.update(f"Opening {escape(note.url)} · marked read")
            return
        target = self._selected_mine() if self._on_mine else self._selected()
        if target is None:
            return
        self._open_web("pr", target.number, target.repo)
        self._status_line.update(f"Opening {escape(target.url)}")

    def _open_web(self, command: str, number: int, repo: str) -> None:
        subprocess.Popen(
            ["gh", command, "view", str(number), "--repo", repo, "--web"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    async def _request(self, pr: AuthoredPR, reviewers: list[str]) -> None:
        if not reviewers or self._requesting:
            return
        self._requesting = True
        names = escape(", ".join(reviewers))
        self._status_line.update(f"Requesting review from {names} on {escape(pr.key)}…")
        try:
            await request_reviewers(pr.repo, pr.number, reviewers)
        except (GhError, OSError) as error:
            self._status_line.update(f"[red]Request failed:[/red] {escape(str(error))}")
            return
        finally:
            self._requesting = False
        await self._refresh_mine(quiet=True)
        self._status_line.update(f"Requested review from {names} on {escape(pr.key)}")

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
            self._status_line.update(f"[red]Launch failed:[/red] {escape(str(error))}")
        else:
            self._active.add(candidate.key)
            self.board.update_active(self._active)
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


def run_tui(config: Config, *, serve: bool = False) -> None:
    ReviewQueueApp(config, serve=serve).run()
