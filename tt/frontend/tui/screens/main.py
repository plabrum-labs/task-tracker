"""The one browse screen: the dense issue list you never leave.

Bindings map every browse key to an ``action_*`` method; nothing here parses a key
event. The screen holds the scope, layout, rows, selection and filter as reactives,
and mutation methods assign them so watchers repaint the affected widget. Everything
you can do to the selected object is reached through what that object offers — a
``MenuScreen`` overlay for the list, and for an action with fields the ``EditPane``
that takes over the right pane where the read detail was — so this screen names only
the accelerators (``d`` delete, ``e`` edit, ``s`` status cycle) and derives the rest.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Input

from tt.domains.issue import api as issue_api
from tt.domains.issue.schemas import IssueListItem
from tt.domains.project import api as project_api
from tt.domains.project.schemas import ProjectListItem
from tt.frontend.tui import data
from tt.frontend.tui.domainview import (
    FAR,
    HALF,
    AllScope,
    Command,
    IssueTarget,
    Layout,
    Navigate,
    ProjectScope,
    ProjectTarget,
    RunAction,
    Scope,
    Split,
    fits,
    index_of,
    issue_commands,
    move_selection,
    next_layout,
    next_status,
    pane_split,
    project_commands,
    surviving_id,
    switcher_commands,
)
from tt.frontend.tui.screens.capture import CaptureScreen
from tt.frontend.tui.screens.cheatsheet import CheatsheetScreen
from tt.frontend.tui.screens.menu import MenuScreen
from tt.frontend.tui.screens.settings import SettingsScreen
from tt.frontend.tui.widgets.body import Body
from tt.frontend.tui.widgets.detail import DetailPane
from tt.frontend.tui.widgets.edit import Edit, EditPane
from tt.frontend.tui.widgets.footer import FilterInput, StatusBar
from tt.frontend.tui.widgets.topbar import TopBar
from tt.platform import config
from tt.platform.actions import REFUSALS, Refused
from tt.platform.config import ThemeName

BROWSING = "j/k move · enter open · x actions · s status · / filter · ? keys · q quit"
READING = "j/k scroll · esc back"
EDITING = "^s save · esc cancel"


class MainScreen(Screen[None]):
    """The browse screen. Assigns reactives; watchers repaint TopBar/Body/StatusBar."""

    # Browse mode holds no focus, so every keystroke reaches the bindings below rather
    # than being typed into a widget; ``/`` focuses the filter and ``enter`` the detail
    # pane, and leaving either blurs. The empty selector disables auto-focus outright:
    # ``None`` would inherit the app's ``*`` and grab the focusable detail pane on mount.
    AUTO_FOCUS = ""

    BINDINGS = [
        Binding("j,down,ctrl+n", "move_down", "down", show=False),
        Binding("k,up,ctrl+p", "move_up", "up", show=False),
        Binding("J,ctrl+d", "half_down", "half page down", show=False),
        Binding("K,ctrl+u", "half_up", "half page up", show=False),
        Binding("g,less_than_sign", "top", "top", show=False),
        Binding("G,greater_than_sign", "bottom", "bottom", show=False),
        Binding("h,left,ctrl+b", "col_left", "left", show=False),
        Binding("l,right,ctrl+f", "col_right", "right", show=False),
        Binding("left_square_bracket,right_square_bracket", "cycle_layout", "layout", show=False),
        Binding("enter", "focus_detail", "detail", show=False),
        Binding("x,space", "issue_menu", "actions", show=False),
        Binding("X", "project_menu", "project actions", show=False),
        Binding("colon", "palette", "commands", show=False),
        Binding("P", "switcher", "switch project", show=False),
        Binding("question_mark", "cheatsheet", "keys", show=False),
        Binding("comma", "settings", "settings", show=False),
        Binding("slash", "filter", "filter", show=False),
        Binding("R", "refresh", "refresh", show=False),
        Binding("n", "capture", "new issue", show=False),
        Binding("d", "delete", "delete", show=False),
        Binding("e", "edit", "edit", show=False),
        Binding("s", "set_status", "status", show=False),
        Binding("escape", "escape", "back", show=False),
        Binding("q", "quit", "quit", show=False),
    ]

    # ``view_layout`` rather than ``layout``: a ``Screen`` is a ``Widget`` whose
    # ``layout`` is a property, and a reactive of that name would shadow it.
    scope: reactive[Scope] = reactive[Scope](AllScope())
    view_layout: reactive[Layout] = reactive[Layout]("list")
    split: reactive[Split] = reactive[Split]("beside")
    projects: reactive[list[ProjectListItem]] = reactive(list)
    issues: reactive[list[IssueListItem]] = reactive(list)
    selected_id: reactive[int | None] = reactive[int | None](None)
    filter: reactive[str] = reactive("")
    status: reactive[str] = reactive(BROWSING)

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        self._ready = False

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with Horizontal(id="content"):
            yield Body(id="body")
            yield DetailPane(id="detail")
            yield EditPane(id="edit")
        yield StatusBar(id="statusbar")
        yield FilterInput(id="filter", placeholder="filter")

    def on_mount(self) -> None:
        self.scope = data.initial_scope(self.engine)
        self.reload()
        self.split = pane_split(self.size.width)
        # Reopen on the layout you left; a saved board that no longer fits this
        # terminal falls back to the list rather than opening cramped.
        saved = config.load().layout
        if fits(saved, self.size.width, self.size.height):
            self.view_layout = saved
        self._ready = True
        self._paint_all()
        # Browse mode holds no focus, so every keystroke reaches the bindings rather
        # than a focused widget; ``/`` focuses the filter and closing it blurs back.
        self.set_focus(None)

    # --- the visible slice and the selected row ---------------------------

    def _visible(self) -> list[IssueListItem]:
        if not self.filter:
            return self.issues
        needle = self.filter.lower()
        return [i for i in self.issues if needle in i.title.lower()]

    def _selected_issue(self) -> IssueListItem | None:
        return next((i for i in self._visible() if i.id == self.selected_id), None)

    def _slug(self) -> str | None:
        return self.scope.slug if isinstance(self.scope, ProjectScope) else None

    def _first_visible(self) -> int | None:
        return move_selection(self._visible(), self.view_layout, None, 0, 0)

    # --- loading ----------------------------------------------------------

    def reload(self) -> None:
        """Read the scope back and reconcile the selection onto a survivor."""
        fallback = index_of(self._visible(), self.selected_id)
        scope, projects, issues = data.resolve(self.engine, self.scope)
        self.scope = scope
        self.projects = projects
        self.issues = issues
        self.selected_id = surviving_id(self._visible(), self.selected_id, fallback)
        # An edit that leaves the selection where it was does not fire the id watcher,
        # so repaint the detail here to reflect the write behind the reload.
        if self._ready:
            self._paint_detail()

    # --- painting ---------------------------------------------------------

    def _paint_all(self) -> None:
        self._paint_topbar()
        self._paint_body()
        self._paint_detail()
        self._paint_footer()
        self._apply_split()

    def _apply_split(self) -> None:
        # The container is a ``Horizontal``; the class flips it to a vertical stack so
        # the detail pane sits under the list rather than beside it on a thin column.
        self.query_one("#content").set_class(self.split == "below", "below")

    def _paint_topbar(self) -> None:
        self.query_one(TopBar).show(
            self.scope, self.projects, self.view_layout, len(self._visible())
        )

    def _paint_body(self) -> None:
        body = self.query_one(Body)
        body.scoped = isinstance(self.scope, ProjectScope)
        body.view_layout = self.view_layout
        body.issues = self._visible()
        body.selected_id = self.selected_id

    def _paint_detail(self) -> None:
        issue = self._selected_issue()
        detail = issue_api.issue_get(self.engine, issue.ref) if issue is not None else None
        self.query_one(DetailPane).detail = detail

    def _paint_footer(self) -> None:
        self.query_one(StatusBar).show(self.status)

    def watch_scope(self) -> None:
        if self._ready:
            self._paint_topbar()
            self._paint_body()

    def watch_view_layout(self) -> None:
        if self._ready:
            self._paint_topbar()
            self._paint_body()

    def watch_split(self) -> None:
        if self._ready:
            self._apply_split()

    def watch_projects(self) -> None:
        if self._ready:
            self._paint_topbar()

    def watch_issues(self) -> None:
        if self._ready:
            self._paint_topbar()
            self._paint_body()

    def watch_selected_id(self) -> None:
        if self._ready:
            self.query_one(Body).selected_id = self.selected_id
            self._paint_detail()

    def watch_filter(self) -> None:
        if self._ready:
            self._paint_topbar()
            self._paint_body()

    def watch_status(self) -> None:
        if self._ready:
            self._paint_footer()

    # --- movement ---------------------------------------------------------

    def _move(self, dr: int, dc: int) -> None:
        self.selected_id = move_selection(
            self._visible(), self.view_layout, self.selected_id, dr, dc
        )

    def action_move_down(self) -> None:
        self._move(1, 0)

    def action_move_up(self) -> None:
        self._move(-1, 0)

    def action_half_down(self) -> None:
        self._move(HALF, 0)

    def action_half_up(self) -> None:
        self._move(-HALF, 0)

    def action_top(self) -> None:
        self._move(-FAR, 0)

    def action_bottom(self) -> None:
        self._move(FAR, 0)

    def action_col_left(self) -> None:
        self._move(0, -1)

    def action_col_right(self) -> None:
        self._move(0, 1)

    def action_cycle_layout(self) -> None:
        self.view_layout = next_layout(self.view_layout, self.size.width, self.size.height)
        config.save_layout(self.view_layout)

    # --- scope ------------------------------------------------------------

    def _switch_scope(self, scope: Scope) -> None:
        self.scope = scope
        self.selected_id = None
        self.status = BROWSING
        self.reload()

    # --- overlays ---------------------------------------------------------

    def action_focus_detail(self) -> None:
        # Enter drills into the selected issue: move focus into the always-present
        # detail pane so its ``j``/``k`` scroll a body taller than the pane. Escape
        # returns to browse. No selection means nothing to read.
        if self._selected_issue() is None:
            self.status = "no issue selected"
            return
        self.set_focus(self.query_one(DetailPane))
        self.status = READING

    def action_issue_menu(self) -> None:
        issue = self._selected_issue()
        if issue is None:
            self.status = "no issue selected"
            return
        detail, offers = issue_api.issue_detail(self.engine, issue.ref)
        commands = issue_commands(offers, issue.ref, detail.model_dump(mode="json"))
        header = f"{issue.ref} · {issue.title}"
        self.app.push_screen(MenuScreen(header, commands), self._on_command)

    def action_project_menu(self) -> None:
        slug = self._slug()
        if slug is None:
            self.status = "pick a project first (P)"
            return
        detail, offers = project_api.project_detail(self.engine, slug)
        commands = project_commands(offers, slug, detail.model_dump(mode="json"))
        self.app.push_screen(MenuScreen(f"Project · {slug}", commands), self._on_command)

    def action_palette(self) -> None:
        commands: list[Command] = []
        issue = self._selected_issue()
        if issue is not None:
            detail, offers = issue_api.issue_detail(self.engine, issue.ref)
            commands.extend(issue_commands(offers, issue.ref, detail.model_dump(mode="json")))
        slug = self._slug()
        if slug is not None:
            project_detail, offers = project_api.project_detail(self.engine, slug)
            commands.extend(project_commands(offers, slug, project_detail.model_dump(mode="json")))
        commands.extend(
            [
                Command("Switch project", None, "P", Navigate("switcher")),
                Command("Toggle layout", None, "[", Navigate("layout")),
                Command("Refresh", None, "R", Navigate("refresh")),
            ]
        )
        self.app.push_screen(MenuScreen("Commands", commands), self._on_command)

    def action_switcher(self) -> None:
        offers = project_api.top_level_offers()
        create = next((o for o in offers if o.key == "createProject"), None)
        commands = switcher_commands(self.projects, create)
        self.app.push_screen(MenuScreen("Switch project", commands), self._on_command)

    def _on_command(self, command: Command | None) -> None:
        if command is None:
            return
        match command.run:
            case RunAction():
                self._run_action(command.run, command.label)
            case Navigate():
                self._navigate(command.run)

    def _navigate(self, nav: Navigate) -> None:
        match nav.what:
            case "switch" if nav.arg is not None:
                self._switch_scope(ProjectScope(nav.arg))
            case "layout":
                self.action_cycle_layout()
            case "switcher":
                self.action_switcher()
            case "refresh":
                self.reload()
            case _:
                return

    # --- writes -----------------------------------------------------------

    def _run_action(self, run: RunAction, label: str) -> None:
        if run.fields:

            def submit(payload: dict[str, Any]) -> str:
                return data.dispatch(self.engine, run.target, run.key, payload)

            self._begin_edit(Edit(label, run.fields, run.seed, submit))
        else:
            self._write(run.key, lambda: data.dispatch(self.engine, run.target, run.key, {}))

    def _begin_edit(self, edit: Edit) -> None:
        # The form takes the right pane's slot: the read detail hides and the editor
        # shows in its place, seeded and focused on its first control.
        self.query_one(DetailPane).display = False
        pane = self.query_one(EditPane)
        pane.display = True
        pane.begin(edit)
        self.status = EDITING

    def _end_edit(self) -> None:
        self.query_one(EditPane).display = False
        self.query_one(DetailPane).display = True
        self.set_focus(None)

    def on_edit_pane_saved(self, event: EditPane.Saved) -> None:
        # The pane already ran the write and only reports Saved on success; a refusal
        # keeps it open, so this is always a success to reload the list behind.
        self._end_edit()
        self.status = event.message
        self.reload()

    def on_edit_pane_cancelled(self, event: EditPane.Cancelled) -> None:
        self._end_edit()
        self.status = BROWSING

    def _write(self, key: str, thunk: Any) -> None:
        try:
            message = thunk()
        except REFUSALS as error:
            self.status = f"{key}: {error}"
            return
        self.status = message
        self.reload()

    def action_delete(self) -> None:
        issue = self._selected_issue()
        if issue is None:
            self.status = "no issue selected"
            return
        _, offers = issue_api.issue_detail(self.engine, issue.ref)
        offer = next((o for o in offers if o.key == "delete"), None)
        if offer is None:
            self.status = "delete: not available"
            return
        if isinstance(offer.state, Refused):
            self.status = f"delete: {offer.state.reason}"
            return
        self._run_action(RunAction(IssueTarget(issue.ref), "delete", offer.fields), offer.label)

    def action_edit(self) -> None:
        issue = self._selected_issue()
        if issue is None:
            self.status = "no issue selected"
            return
        detail, offers = issue_api.issue_detail(self.engine, issue.ref)
        offer = next((o for o in offers if o.key == "edit"), None)
        if offer is None:
            self.status = "edit: not available"
            return
        if isinstance(offer.state, Refused):
            self.status = f"edit: {offer.state.reason}"
            return
        # Seed the form from the issue's current values so the pane opens pre-filled,
        # the same way the menu builds its edit command.
        self._run_action(
            RunAction(IssueTarget(issue.ref), "edit", offer.fields, detail.model_dump(mode="json")),
            offer.label,
        )

    def action_set_status(self) -> None:
        # The quick cycle: compute the next status off the selected row and dispatch
        # ``setStatus`` at once, skipping the menu's pick-a-status form. Dispatching
        # directly is safe — the domain ``run`` gate re-checks availability and
        # ``_write`` catches a refusal — and the issue has no status machine to refuse.
        issue = self._selected_issue()
        if issue is None:
            self.status = "no issue selected"
            return
        nxt = next_status(issue.status)
        target = IssueTarget(issue.ref)
        self._write(
            "setStatus",
            lambda: data.dispatch(self.engine, target, "setStatus", {"status": nxt}),
        )

    # --- capture ----------------------------------------------------------

    def action_capture(self) -> None:
        slug = self._slug()
        if slug is None:
            self.status = "pick a project first (P)"
            return
        self.app.push_screen(CaptureScreen(slug), lambda title: self._add_issue(slug, title))

    def _add_issue(self, slug: str, title: str | None) -> None:
        if title is None:
            return
        self._write(
            "addIssue",
            lambda: data.dispatch(self.engine, ProjectTarget(slug), "addIssue", {"title": title}),
        )

    # --- settings ---------------------------------------------------------

    def action_settings(self) -> None:
        current = ThemeName(self.app.theme)
        self.app.push_screen(SettingsScreen(current), self._on_settings)

    def _on_settings(self, theme: ThemeName | None) -> None:
        if theme is None:
            return
        self.app.theme = theme.value
        config.save_theme(theme)
        self.status = BROWSING

    def action_cheatsheet(self) -> None:
        self.app.push_screen(CheatsheetScreen())

    # --- filter -----------------------------------------------------------

    def action_filter(self) -> None:
        filter_input = self.query_one(FilterInput)
        filter_input.value = self.filter
        filter_input.can_focus = True
        filter_input.display = True
        self.query_one(StatusBar).display = False
        filter_input.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if isinstance(event.input, FilterInput):
            self.filter = event.value
            self.selected_id = self._first_visible()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if isinstance(event.input, FilterInput):
            self._close_filter(keep=True)

    def action_escape(self) -> None:
        # Escape leaves the detail pane back to browse before it touches the filter,
        # so reading an issue and then backing out does not also clear a live filter.
        if isinstance(self.focused, DetailPane):
            self.set_focus(None)
            self.status = BROWSING
            return
        if self.query_one(FilterInput).display or self.filter:
            self._close_filter(keep=False)

    def _close_filter(self, *, keep: bool) -> None:
        filter_input = self.query_one(FilterInput)
        filter_input.display = False
        filter_input.can_focus = False
        self.query_one(StatusBar).display = True
        self.set_focus(None)
        if not keep:
            self.filter = ""
            filter_input.value = ""
            self.selected_id = self._first_visible()
        self.status = BROWSING

    # --- lifecycle --------------------------------------------------------

    def on_resize(self, event: events.Resize) -> None:
        # The pane sits beside the list when the terminal is wide, and drops to a
        # stack below it when the column narrows past what the two need side by side.
        self.split = pane_split(event.size.width)

    def action_refresh(self) -> None:
        self.reload()

    def action_quit(self) -> None:
        self.app.exit()
