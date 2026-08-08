"""The one browse screen: the dense issue list you never leave.

Bindings map every browse key to an ``action_*`` method; nothing here parses a key
event. The screen holds the scope, the grouping and its render, rows, selection and
filter as reactives, and mutation methods assign them so watchers repaint the
affected widget. The body is grouped by the one or two dimensions ``g`` picks — nested
as a tree when there are two — and drawn stacked or as columns by the ``[``/``]``
toggle; the board is the status groups as columns. ``z`` opens the fold chord over that
tree, and which of its nodes are shut is remembered per grouping for the session.
Everything you can do to the selected object is reached through what that offers — a
``MenuScreen`` overlay for the list, and for an action with fields the ``EditPane``
that takes over the right pane where the read detail was — so this screen names only
the accelerators (``d`` delete, ``e`` edit, ``s`` status cycle, ``c`` add comment)
and derives the rest. They act on the selected issue in browse mode; once the detail
pane holds focus its comment thread is what the cursor is in, so ``x``/``e``/``d``
address the selected comment instead.
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

from tt.domains.comment import api as comment_api
from tt.domains.comment.schemas import CommentView
from tt.domains.issue import api as issue_api
from tt.domains.issue.schemas import IssueListItem
from tt.domains.project import api as project_api
from tt.domains.project.schemas import ProjectListItem
from tt.frontend.tui import data
from tt.frontend.tui.domainview import (
    EXPANDED,
    FAR,
    GROUP_LABELS,
    HALF,
    LEVEL_SEPARATOR,
    AllScope,
    Collapsed,
    Command,
    CommentTarget,
    GroupBy,
    Grouping,
    GroupNode,
    GroupRender,
    IssueTarget,
    Navigate,
    ProjectScope,
    ProjectTarget,
    RunAction,
    Scope,
    Split,
    Target,
    comment_commands,
    fits_columns,
    fold_all,
    fold_target,
    group_by,
    group_commands,
    group_tree,
    grouping,
    index_of,
    issue_commands,
    move_selection,
    nest_commands,
    next_render,
    next_status,
    opening_view,
    pane_split,
    project_commands,
    saved_layout,
    surviving_id,
    switcher_commands,
    toggle_fold,
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
from tt.platform.actions import REFUSALS, Offer, Refused
from tt.platform.config import ThemeName

BROWSING = "j/k move · enter open · x actions · s status · g group · / filter · ? keys · q quit"
READING = "j/k comments · x/e/d act on the one selected · esc back"
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
        Binding("less_than_sign", "top", "top", show=False),
        Binding("G,greater_than_sign", "bottom", "bottom", show=False),
        Binding("h,left,ctrl+b", "col_left", "left", show=False),
        Binding("l,right,ctrl+f", "col_right", "right", show=False),
        Binding("g", "group_menu", "group by", show=False),
        Binding("left_square_bracket,right_square_bracket", "cycle_render", "render", show=False),
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
        Binding("c", "comment", "add comment", show=False),
        Binding("d", "delete", "delete", show=False),
        Binding("e", "edit", "edit", show=False),
        Binding("s", "set_status", "status", show=False),
        Binding("escape", "escape", "back", show=False),
        Binding("q", "quit", "quit", show=False),
    ]

    # ``view_render`` rather than ``render``: a ``Screen`` is a ``Widget`` whose
    # ``render`` draws it, and a reactive of that name would shadow it.
    scope: reactive[Scope] = reactive[Scope](AllScope())
    view_group: reactive[Grouping] = reactive[Grouping](("none",))
    view_render: reactive[GroupRender] = reactive[GroupRender]("stacked")
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
        # The folded nodes of each grouping, for this session only: going off to
        # another dimension and back finds the same subtrees shut, and a restart opens
        # everything. Nothing about a fold belongs in the preferences file.
        self._folds: dict[Grouping, Collapsed] = {}
        # ``z`` opens a fold chord and this holds it until the next keystroke closes it.
        self._fold_chord = False

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
        # Reopen on the view you left; a saved board that no longer fits this terminal
        # falls back to the flat list rather than opening cramped.
        by, render = opening_view(config.load().layout)
        if render == "stacked" or fits_columns(
            by, len(group_tree(self._visible(), by)), self.size.width, self.size.height
        ):
            self.view_group = by
            self.view_render = render
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

    def _selected_comment(self) -> CommentView | None:
        """The comment the thread's cursor is on, and only while the detail pane holds
        focus: in browse mode ``x``/``e``/``d`` address the issue, and inside the
        thread they address the comment under the bar."""
        pane = self.query_one(DetailPane)
        if self.focused is not pane or pane.detail is None:
            return None
        return next((c for c in pane.detail.comments if c.id == pane.selected_comment_id), None)

    def _slug(self) -> str | None:
        return self.scope.slug if isinstance(self.scope, ProjectScope) else None

    def _tree(self) -> list[GroupNode]:
        """The visible issues as the tree the current grouping files them under — what
        the body draws and what the cursor walks, computed once for both."""
        return group_tree(self._visible(), self.view_group)

    def _collapsed(self) -> Collapsed:
        """The nodes folded shut under the grouping in force; a grouping first reached
        this session opens all expanded."""
        return self._folds.get(self.view_group, EXPANDED)

    def _first_visible(self) -> int | None:
        return move_selection(self._tree(), self.view_render, self._collapsed(), None, 0, 0)

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
            self.scope, self.projects, self.view_group, self.view_render, len(self._visible())
        )

    def _paint_body(self) -> None:
        body = self.query_one(Body)
        body.scoped = isinstance(self.scope, ProjectScope)
        body.view_render = self.view_render
        body.nodes = self._tree()
        body.collapsed = self._collapsed()
        body.selected_id = self.selected_id

    def _paint_detail(self) -> None:
        issue = self._selected_issue()
        detail = issue_api.issue_get(self.engine, issue.ref) if issue is not None else None
        pane = self.query_one(DetailPane)
        # The whole loaded scope, not the visible slice: an epic's standing is what it
        # is, whatever the filter line is currently narrowing the list to.
        pane.peers = self.issues
        pane.detail = detail

    def _paint_footer(self) -> None:
        self.query_one(StatusBar).show(self.status)

    def watch_scope(self) -> None:
        if self._ready:
            self._paint_topbar()
            self._paint_body()

    def watch_view_group(self) -> None:
        if self._ready:
            self._paint_topbar()
            self._paint_body()

    def watch_view_render(self) -> None:
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
            self._tree(), self.view_render, self._collapsed(), self.selected_id, dr, dc
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

    def action_cycle_render(self) -> None:
        self.view_render = next_render(
            self.view_render,
            self.view_group,
            len(self._tree()),
            self.size.width,
            self.size.height,
        )
        self._save_view()

    # --- grouping ---------------------------------------------------------

    def action_group_menu(self) -> None:
        commands = group_commands(self.view_group)
        self.app.push_screen(MenuScreen("Group by", commands), self._on_command)

    def _set_group(self, name: str) -> None:
        """The picker's first step. A dimension to group by leads to the second step —
        what to nest inside it — while grouping by nothing has no inside to fill."""
        by = group_by(name)
        if by is None:
            self.status = f"{name}: not a dimension"
            return
        if by == "none":
            self._apply_group(grouping("none"))
            return
        commands = nest_commands(by, self.view_group)
        header = f"{GROUP_LABELS[by]} {LEVEL_SEPARATOR} then by"
        self.app.push_screen(
            MenuScreen(header, commands), lambda picked: self._nest_group(by, picked)
        )

    def _nest_group(self, first: GroupBy, command: Command | None) -> None:
        """The picker's second step. Backing out of it leaves the grouping alone rather
        than applying the first dimension half-picked."""
        if command is None or not isinstance(command.run, Navigate) or command.run.arg is None:
            return
        second = group_by(command.run.arg)
        if second is None:
            self.status = f"{command.run.arg}: not a dimension"
            return
        self._apply_group(grouping(first, second))

    def _apply_group(self, by: Grouping) -> None:
        self.view_group = by
        # The groups the previous grouping fanned across the width are not these ones,
        # so a render that no longer fits falls back to the stack.
        if not fits_columns(by, len(self._tree()), self.size.width, self.size.height):
            self.view_render = "stacked"
        self._save_view()

    def _save_view(self) -> None:
        config.save_layout(saved_layout(self.view_group, self.view_render))

    # --- folding ----------------------------------------------------------

    def _set_folds(self, collapsed: Collapsed) -> None:
        self._folds[self.view_group] = collapsed
        self._paint_body()

    def action_toggle_fold(self) -> None:
        path = fold_target(self._tree(), self.selected_id)
        if path is None:
            self.status = "nothing to fold here"
            return
        self._set_folds(toggle_fold(self._collapsed(), path))

    def action_fold_all(self) -> None:
        self._set_folds(fold_all(self._tree()))

    def action_expand_all(self) -> None:
        self._set_folds(EXPANDED)

    def on_key(self, event: events.Key) -> None:
        """The ``z`` fold chord. A screen binding names one key, so the prefix is read
        here instead: the keystroke after ``z`` is the other half of the chord and is
        stopped before the bindings see it — otherwise ``zR`` would also refresh."""
        if self.focused is not None:
            return
        if self._fold_chord:
            self._fold_chord = False
            event.stop()
            match event.key:
                case "a":
                    self.action_toggle_fold()
                case "M":
                    self.action_fold_all()
                case "R":
                    self.action_expand_all()
                case _:
                    self.status = f"z{event.key}: not a fold key"
            return
        if event.key == "z":
            self._fold_chord = True
            event.stop()

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
        comment = self._selected_comment()
        if comment is not None:
            detail, offers = comment_api.comment_detail(self.engine, comment.id)
            commands = comment_commands(offers, comment.id, detail.model_dump(mode="json"))
            self.app.push_screen(MenuScreen(f"Comment {comment.id}", commands), self._on_command)
            return
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
                Command("Group by…", None, "g", Navigate("group")),
                Command("Toggle columns", None, "[", Navigate("render")),
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
            case "group" if nav.arg is not None:
                self._set_group(nav.arg)
            case "group":
                self.action_group_menu()
            case "render":
                self.action_cycle_render()
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
        pane = self.query_one(DetailPane)
        pane.display = True
        # The form gives the focus back to browse, so the thread's cursor goes with
        # it — the same rule as escaping the pane.
        pane.selected_comment_id = None
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

    def _run_offer(
        self,
        target: Target,
        offers: list[Offer],
        key: str,
        seed: dict[str, Any] | None = None,
    ) -> None:
        """The accelerator path: pick the named action out of what the target offers
        and run it. An action the target does not offer, or offers refused, is the
        object's answer and lands in the status line rather than writing."""
        offer = next((o for o in offers if o.key == key), None)
        if offer is None:
            self.status = f"{key}: not available"
            return
        if isinstance(offer.state, Refused):
            self.status = f"{key}: {offer.state.reason}"
            return
        self._run_action(RunAction(target, key, offer.fields, seed), offer.label)

    def action_delete(self) -> None:
        comment = self._selected_comment()
        if comment is not None:
            _, offers = comment_api.comment_detail(self.engine, comment.id)
            self._run_offer(CommentTarget(comment.id), offers, "delete")
            return
        issue = self._selected_issue()
        if issue is None:
            self.status = "no issue selected"
            return
        _, offers = issue_api.issue_detail(self.engine, issue.ref)
        self._run_offer(IssueTarget(issue.ref), offers, "delete")

    def action_edit(self) -> None:
        # Seed the form from the target's current values so the pane opens pre-filled,
        # the same way the menu builds its edit command.
        comment = self._selected_comment()
        if comment is not None:
            detail, offers = comment_api.comment_detail(self.engine, comment.id)
            self._run_offer(
                CommentTarget(comment.id), offers, "edit", detail.model_dump(mode="json")
            )
            return
        issue = self._selected_issue()
        if issue is None:
            self.status = "no issue selected"
            return
        issue_detail, offers = issue_api.issue_detail(self.engine, issue.ref)
        self._run_offer(
            IssueTarget(issue.ref), offers, "edit", issue_detail.model_dump(mode="json")
        )

    def action_comment(self) -> None:
        # A comment is created through the issue it hangs off, so ``c`` runs the
        # issue's ``addComment`` — a create, whose form opens blank.
        issue = self._selected_issue()
        if issue is None:
            self.status = "no issue selected"
            return
        _, offers = issue_api.issue_detail(self.engine, issue.ref)
        self._run_offer(IssueTarget(issue.ref), offers, "addComment")

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
            # Drop the comment cursor with the focus: browse keys act on the issue
            # again, so a bar left in the thread would claim a selection they ignore.
            self.focused.selected_comment_id = None
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
