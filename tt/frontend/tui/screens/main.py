"""The one browse screen: the dense issue list you never leave.

Bindings map every browse key to an ``action_*`` method; nothing here parses a key
event. The screen holds the scope, the grouping and its render, rows, selection and
filter as reactives, and mutation methods assign them so watchers repaint the
affected widget. The list is narrowed by the facets ``f`` picks — each drawn as a chip
under the top bar, ``F`` dropping the lot — and ``/`` is the quick title search, which
is the text facet under another key. Entering an epic, a milestone or a tag applies
that facet rather than navigating anywhere. The body is grouped by the one or two
dimensions ``g`` picks — nested
as a tree when there are two — and drawn stacked or as columns by the ``[``/``]``
toggle; the board is the status groups as columns. ``z`` opens the fold chord over that
tree, and which of its nodes are shut is remembered per grouping for the session. ``v``
opens the saved views: a name recalls the filter, grouping and scope it was saved with,
and the overlay is also where the live state is saved under a name or one is deleted.
An ultra-wide terminal stands those three — the grouping, the chips and the views — in a
rail left of the list rather than behind their overlays; a narrower one collapses it,
and since the rail only mirrors what ``g``/``f``/``v`` already reach, nothing goes with
it. Everything you can do to the selected object is reached through what that offers — a
``MenuScreen`` overlay for the list, and for an action with fields the ``EditPane``
that takes over the right pane where the read detail was — so this screen names only
the accelerators (``d`` delete, ``e`` edit, ``s`` status cycle, ``c`` add comment)
and derives the rest. They act on the selected issue in browse mode; once the detail
pane holds focus its comment thread is what the cursor is in, so ``x``/``e``/``d``
address the selected comment instead.
"""

from __future__ import annotations

from datetime import date
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
from tt.domains.epic import api as epic_api
from tt.domains.issue import api as issue_api
from tt.domains.issue.schemas import IssueListItem
from tt.domains.milestone import api as milestone_api
from tt.domains.project import api as project_api
from tt.domains.project.schemas import ProjectListItem
from tt.domains.tag import api as tag_api
from tt.frontend.tui import data
from tt.frontend.tui.domainview import (
    EVERYWHERE,
    EXPANDED,
    FACET_LABELS,
    FAR,
    GROUP_LABELS,
    HALF,
    LEVEL_SEPARATOR,
    NO_FILTER,
    AllScope,
    Collapsed,
    Command,
    CommentTarget,
    Cursor,
    Filter,
    GroupBy,
    Grouping,
    GroupNode,
    GroupRender,
    HeaderAt,
    IssueTarget,
    Navigate,
    ProjectScope,
    ProjectTarget,
    RunAction,
    Scope,
    Split,
    Target,
    ValueFacet,
    View,
    apply,
    comment_commands,
    drill_commands,
    drill_facet,
    epic_commands,
    facet_commands,
    facet_of,
    facet_values,
    filter_commands,
    fits_columns,
    fold_all,
    fold_target,
    group_by,
    group_commands,
    group_tree,
    grouping,
    index_of,
    issue_commands,
    milestone_commands,
    move_selection,
    nest_commands,
    next_render,
    next_status,
    opening_view,
    pane_split,
    project_commands,
    saved_layout,
    saved_view,
    shows_rail,
    surviving_id,
    switcher_commands,
    tag_commands,
    toggle_fold,
    view_commands,
    view_of,
    visible_stops,
    with_due,
    with_facet,
    without_facet,
)
from tt.frontend.tui.screens.capture import CaptureScreen
from tt.frontend.tui.screens.cheatsheet import CheatsheetScreen
from tt.frontend.tui.screens.menu import MenuScreen
from tt.frontend.tui.screens.settings import SettingsScreen
from tt.frontend.tui.widgets.body import Body
from tt.frontend.tui.widgets.detail import DetailPane
from tt.frontend.tui.widgets.edit import Edit, EditPane
from tt.frontend.tui.widgets.footer import FilterInput, StatusBar
from tt.frontend.tui.widgets.rail import Rail
from tt.frontend.tui.widgets.topbar import ChipsBar, TopBar
from tt.platform import config
from tt.platform.actions import REFUSALS, Date, Field, Invalid, Offer, Refused
from tt.platform.config import ThemeName

BROWSING = "j/k move · enter open · x actions · s status · g group · f filter · ? keys · q quit"
READING = "j/k comments · x/e/d act on the one selected · esc back"
EDITING = "^s save · esc cancel"

# The due facet is a date rather than a pick off a list, so it is typed into the same
# one-field form an action with a date field opens.
DUE_FIELD = Field(
    name="due_before",
    required=False,
    kind=Date(),
    description="Show only issues due before this date, as YYYY-MM-DD. Blank clears it.",
)


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
        Binding("f", "filter_menu", "filter facets", show=False),
        Binding("F", "clear_filters", "clear filters", show=False),
        Binding("v", "views", "saved views", show=False),
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
    # Whether the ultra-wide band's third column is on the page.
    rail: reactive[bool] = reactive(False)
    projects: reactive[list[ProjectListItem]] = reactive(list)
    issues: reactive[list[IssueListItem]] = reactive(list)
    # What the cursor is on: an issue by id, or the header of a group standing for an
    # epic, a milestone or a tag.
    cursor: reactive[Cursor | None] = reactive[Cursor | None](None)
    view_filter: reactive[Filter] = reactive[Filter](NO_FILTER)
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
        yield ChipsBar(id="chips")
        with Horizontal(id="content"):
            yield Rail(id="rail")
            yield Body(id="body")
            yield DetailPane(id="detail")
            yield EditPane(id="edit")
        yield StatusBar(id="statusbar")
        yield FilterInput(id="filter", placeholder="filter")

    def on_mount(self) -> None:
        self.scope = data.initial_scope(self.engine)
        self.reload()
        self.split = pane_split(self.size.width)
        self.rail = shows_rail(self.size.width)
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
        return apply(self.issues, self.view_filter)

    def _selected_issue(self) -> IssueListItem | None:
        cursor = self.cursor
        if not isinstance(cursor, int):
            return None
        return next((i for i in self._visible() if i.id == cursor), None)

    def _selected_header(self) -> HeaderAt | None:
        """The group header the cursor is on, or ``None`` while it is on an issue."""
        cursor = self.cursor
        return cursor if isinstance(cursor, HeaderAt) else None

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

    def _first_visible(self) -> Cursor | None:
        return move_selection(self._tree(), self.view_render, self._collapsed(), None, 0, 0)

    # --- loading ----------------------------------------------------------

    def reload(self) -> None:
        """Read the scope back and reconcile the selection onto a survivor."""
        issue = self._selected_issue()
        fallback = index_of(self._visible(), issue.id if issue is not None else None)
        scope, projects, issues = data.resolve(self.engine, self.scope)
        self.scope = scope
        self.projects = projects
        self.issues = issues
        self.cursor = self._surviving_cursor(fallback)
        # An edit that leaves the selection where it was does not fire the cursor
        # watcher, so repaint the detail here to reflect the write behind the reload.
        if self._ready:
            self._paint_detail()

    def _surviving_cursor(self, fallback: int) -> Cursor | None:
        """The cursor after a reload. A header holds while its group is still on the
        tree — deleting the epic it stood for takes it off, and the cursor falls back to
        the first row — and an issue reconciles onto its neighbour."""
        cursor = self.cursor
        if isinstance(cursor, HeaderAt):
            gone = cursor not in visible_stops(self._tree(), self._collapsed())
            return self._first_visible() if gone else cursor
        return surviving_id(self._visible(), cursor, fallback)

    # --- painting ---------------------------------------------------------

    def _paint_all(self) -> None:
        self._paint_topbar()
        self._paint_body()
        self._paint_detail()
        self._paint_rail()
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
        self.query_one(ChipsBar).show(self.view_filter)

    def _paint_body(self) -> None:
        body = self.query_one(Body)
        body.scoped = isinstance(self.scope, ProjectScope)
        body.filtered = self.view_filter != NO_FILTER
        body.view_render = self.view_render
        body.nodes = self._tree()
        body.collapsed = self._collapsed()
        body.cursor = self.cursor

    def _paint_detail(self) -> None:
        issue = self._selected_issue()
        detail = issue_api.issue_get(self.engine, issue.ref) if issue is not None else None
        pane = self.query_one(DetailPane)
        # The whole loaded scope, not the visible slice: an epic's standing is what it
        # is, whatever the filter line is currently narrowing the list to.
        pane.peers = self.issues
        pane.detail = detail

    def _paint_rail(self) -> None:
        # Outside the ultra-wide band the rail is off the page entirely, so there is
        # nothing to draw for it — and no saved views to read off the file either.
        rail = self.query_one(Rail)
        rail.display = self.rail
        if self.rail:
            rail.show(self.view_group, self.view_filter, self._views())

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
            self._paint_rail()

    def watch_view_render(self) -> None:
        if self._ready:
            self._paint_topbar()
            self._paint_body()

    def watch_split(self) -> None:
        if self._ready:
            self._apply_split()

    def watch_rail(self) -> None:
        if self._ready:
            self._paint_rail()

    def watch_projects(self) -> None:
        if self._ready:
            self._paint_topbar()

    def watch_issues(self) -> None:
        if self._ready:
            self._paint_topbar()
            self._paint_body()

    def watch_cursor(self) -> None:
        if self._ready:
            self.query_one(Body).cursor = self.cursor
            self._paint_detail()

    def watch_view_filter(self) -> None:
        if self._ready:
            self._paint_topbar()
            self._paint_body()
            self._paint_rail()

    def watch_status(self) -> None:
        if self._ready:
            self._paint_footer()

    # --- movement ---------------------------------------------------------

    def _move(self, dr: int, dc: int) -> None:
        self.cursor = move_selection(
            self._tree(), self.view_render, self._collapsed(), self.cursor, dr, dc
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
        self._save_layout()

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
        self._save_layout()

    def _save_layout(self) -> None:
        config.save_layout(saved_layout(self.view_group, self.view_render))

    # --- filtering --------------------------------------------------------

    def action_filter_menu(self) -> None:
        commands = filter_commands(self.view_filter)
        self.app.push_screen(MenuScreen("Filter", commands), self._on_command)

    def _apply_filter(self, narrowed: Filter) -> None:
        """Put a filter in force, keeping the selection when the row it is on survives
        the narrowing and falling to the first visible stop when it does not."""
        self.view_filter = narrowed
        if self.cursor not in visible_stops(self._tree(), self._collapsed()):
            self.cursor = self._first_visible()

    def _pick_facet(self, name: str) -> None:
        """The filter menu's first step. A facet with a closed or loaded list of values
        opens its value menu; the two that are typed rather than picked open their own
        input — the quick title line for text, a one-field form for the due cutoff."""
        facet = facet_of(name)
        if facet is None:
            self.status = f"{name}: not a facet"
            return
        match facet:
            case "due":
                self._due_form()
            case "text":
                self.action_filter()
            case _:
                self._facet_menu(facet)

    def _facet_menu(self, facet: ValueFacet) -> None:
        values = facet_values(self.issues, facet)
        if not values:
            self.status = f"no {FACET_LABELS[facet].lower()} to filter by"
            return
        self.app.push_screen(
            MenuScreen(
                f"Filter · {FACET_LABELS[facet]}",
                facet_commands(facet, values, self.view_filter),
            ),
            lambda picked: self._set_facet(facet, picked),
        )

    def _set_facet(self, facet: ValueFacet, command: Command | None) -> None:
        """The filter menu's second step. Backing out of it leaves the filter alone
        rather than applying the facet half-picked."""
        if command is None or not isinstance(command.run, Navigate) or command.run.arg is None:
            return
        self._apply_filter(with_facet(self.view_filter, facet, command.run.arg))
        self.status = BROWSING

    def _drop_facet(self, name: str) -> None:
        facet = facet_of(name)
        if facet is None:
            self.status = f"{name}: not a facet"
            return
        if facet == "text":
            # The quick line holds the text facet's value, so clearing one clears both.
            self.query_one(FilterInput).value = ""
        self._apply_filter(without_facet(self.view_filter, facet))
        self.status = BROWSING

    def action_clear_filters(self) -> None:
        if self.view_filter == NO_FILTER:
            self.status = "nothing filtered"
            return
        self.query_one(FilterInput).value = ""
        self._apply_filter(NO_FILTER)
        self.status = BROWSING

    def _due_form(self) -> None:
        cutoff = self.view_filter.due_before
        seed = {"due_before": f"{cutoff:%Y-%m-%d}"} if cutoff is not None else None
        self._begin_edit(
            Edit(f"Filter · {FACET_LABELS['due']}", [DUE_FIELD], seed, self._submit_due)
        )

    def _submit_due(self, payload: dict[str, Any]) -> str:
        """The due facet's form runs through the edit pane the way an action's does, but
        it narrows the list rather than writing. A date that does not parse is raised as
        a refusal, which is what keeps the pane open with the reason shown inline."""
        typed = payload.get("due_before")
        if typed is None:
            self._apply_filter(with_due(self.view_filter, None))
            return "due filter cleared"
        try:
            cutoff = date.fromisoformat(str(typed))
        except ValueError as error:
            raise Invalid(f"{typed}: not a date (YYYY-MM-DD)") from error
        self._apply_filter(with_due(self.view_filter, cutoff))
        return f"filtered to due before {cutoff:%Y-%m-%d}"

    def _drill(self, name: str, value: str) -> None:
        """Enter an epic, a milestone or a tag: applying that facet is the whole of the
        move, so there is nothing to navigate to."""
        facet = facet_of(name)
        if facet is None or facet == "due":
            self.status = f"{name}: not something to enter"
            return
        self._apply_filter(with_facet(self.view_filter, facet, value))
        self.status = BROWSING

    # --- saved views ------------------------------------------------------

    def _views(self) -> list[View]:
        """The saved views, read off the preferences file each time: the overlay is the
        only thing that holds them, so there is no copy here to fall out of date."""
        return [view_of(stored) for stored in config.load().views]

    def action_views(self) -> None:
        self.app.push_screen(MenuScreen("Views", view_commands(self._views())), self._on_command)

    def _recall_view(self, name: str) -> None:
        view = next((v for v in self._views() if v.name == name), None)
        if view is None:
            self.status = f"{name}: no such view"
            return
        self._apply_view(view)

    def _apply_view(self, view: View) -> None:
        """Recall a view: its filter, its grouping and its scope all go into force at
        once, and the list is read back over the project it pins."""
        # The quick line holds the text facet's value, so it is put back with the rest —
        # otherwise a line left over from before would still read as what is filtered.
        self.query_one(FilterInput).value = view.filter.text
        self.view_filter = view.filter
        self._apply_group(view.by)
        self._switch_scope(view.scope)
        self.status = f"view {view.name}"

    def _save_current_view(self) -> None:
        where = self._slug() or EVERYWHERE
        self.app.push_screen(CaptureScreen("Save this view of", where, "name"), self._name_view)

    def _name_view(self, name: str | None) -> None:
        if name is None:
            return
        current = saved_view(View(name, self.scope, self.view_group, self.view_filter))
        # A name addresses one view, so saving over one replaces it rather than leaving
        # two rows nothing can tell apart.
        kept = [view for view in config.load().views if view.name != name]
        config.save_views([*kept, current])
        self.status = f"saved view {name}"
        # The views are read off the file rather than held here, so nothing else tells
        # the rail its list just changed.
        self._paint_rail()

    def _delete_view(self, name: str) -> None:
        views = config.load().views
        if all(view.name != name for view in views):
            self.status = f"{name}: no such view"
            return
        config.save_views([view for view in views if view.name != name])
        self.status = f"deleted view {name}"
        self._paint_rail()

    # --- folding ----------------------------------------------------------

    def _set_folds(self, collapsed: Collapsed) -> None:
        self._folds[self.view_group] = collapsed
        self._paint_body()

    def action_toggle_fold(self) -> None:
        path = fold_target(self._tree(), self.cursor)
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
        self.cursor = None
        self.status = BROWSING
        self.reload()

    # --- overlays ---------------------------------------------------------

    def action_focus_detail(self) -> None:
        # Enter drills into what the cursor is on. On a group header that is the epic,
        # the milestone or the tag it stands for, and entering one is applying its facet.
        # On an issue it is the detail: move focus into the always-present pane so its
        # ``j``/``k`` scroll a body taller than it. Escape returns to browse.
        header = self._selected_header()
        if header is not None:
            facet = drill_facet(header.by)
            if facet is None:
                self.status = f"{header.value}: nothing to enter"
                return
            self._drill(facet, header.value)
            return
        if self._selected_issue() is None:
            self.status = "no issue selected"
            return
        self.set_focus(self.query_one(DetailPane))
        self.status = READING

    def _tag_id(self, name: str) -> int | None:
        """The id of the tag a header names. Issues wear a tag by name and the tag api
        addresses one by id, so this is the one lookup between the two."""
        return next((t.id for t in tag_api.tag_list(self.engine) if t.name == name), None)

    def _header_commands(self, header: HeaderAt) -> list[Command] | None:
        """What the object a group header stands for offers, as menu rows — the epic,
        milestone or tag the group is filed under, each addressed the way its own api
        takes it. ``None`` when that object is no longer there to ask."""
        match header.by:
            case "epic":
                if header.project is None:
                    return None
                epic, offers = epic_api.epic_detail(self.engine, header.project, header.value)
                return epic_commands(
                    offers, header.project, header.value, epic.model_dump(mode="json")
                )
            case "milestone":
                if header.project is None:
                    return None
                milestone, offers = milestone_api.milestone_detail(
                    self.engine, header.project, header.value
                )
                return milestone_commands(
                    offers, header.project, header.value, milestone.model_dump(mode="json")
                )
            case "tag":
                tag_id = self._tag_id(header.value)
                if tag_id is None:
                    return None
                tag, offers = tag_api.tag_detail(self.engine, tag_id)
                return tag_commands(offers, tag_id, tag.model_dump(mode="json"))
            case "none" | "status" | "priority" | "due":
                # A header of one of these stands for no row, so the cursor never lands
                # on it and there is nothing to offer.
                return None

    def _open_header_menu(self, header: HeaderAt) -> None:
        commands = self._header_commands(header)
        if commands is None:
            self.status = f"{header.value}: gone"
            return
        title = f"{GROUP_LABELS[header.by]} · {header.value}"
        self.app.push_screen(MenuScreen(title, commands), self._on_command)

    def action_issue_menu(self) -> None:
        comment = self._selected_comment()
        if comment is not None:
            detail, offers = comment_api.comment_detail(self.engine, comment.id)
            commands = comment_commands(offers, comment.id, detail.model_dump(mode="json"))
            self.app.push_screen(MenuScreen(f"Comment {comment.id}", commands), self._on_command)
            return
        header = self._selected_header()
        if header is not None:
            self._open_header_menu(header)
            return
        issue = self._selected_issue()
        if issue is None:
            self.status = "no issue selected"
            return
        detail, offers = issue_api.issue_detail(self.engine, issue.ref)
        commands = issue_commands(offers, issue.ref, detail.model_dump(mode="json"))
        header = f"{issue.ref} · {issue.title}"
        self.app.push_screen(MenuScreen(header, commands), self._on_command)

    def _create_commands(self) -> list[Command]:
        """The creates that hang off no selected row: a tag, which is global and
        belongs to no project. A project's own creates — including a milestone, which
        is project-level — come from what the project offers."""
        return tag_commands(tag_api.top_level_offers(), None)

    def action_project_menu(self) -> None:
        slug = self._slug()
        if slug is None:
            self.status = "pick a project first (P)"
            return
        detail, offers = project_api.project_detail(self.engine, slug)
        commands = project_commands(offers, slug, detail.model_dump(mode="json"))
        commands.extend(self._create_commands())
        self.app.push_screen(MenuScreen(f"Project · {slug}", commands), self._on_command)

    def action_palette(self) -> None:
        commands: list[Command] = []
        header = self._selected_header()
        if header is not None:
            commands.extend(self._header_commands(header) or [])
        issue = self._selected_issue()
        if issue is not None:
            detail, offers = issue_api.issue_detail(self.engine, issue.ref)
            commands.extend(issue_commands(offers, issue.ref, detail.model_dump(mode="json")))
            # What the detail shows the issue is filed under, as the same enter-is-filter
            # move a group header makes.
            commands.extend(drill_commands(issue))
        slug = self._slug()
        if slug is not None:
            project_detail, offers = project_api.project_detail(self.engine, slug)
            commands.extend(project_commands(offers, slug, project_detail.model_dump(mode="json")))
        commands.extend(self._create_commands())
        commands.extend(
            [
                Command("Switch project", None, "P", Navigate("switcher")),
                Command("Group by…", None, "g", Navigate("group")),
                Command("Filter…", None, "f", Navigate("filter")),
                Command("Views…", None, "v", Navigate("views")),
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

    def on_rail_steer(self, event: Rail.Steer) -> None:
        """A rail row picked with the mouse. It carries the same ``Navigate`` a menu row
        does, so the rail steers through the one dispatch rather than its own path."""
        self._navigate(event.nav)

    def _navigate(self, nav: Navigate) -> None:
        match nav.what:
            case "switch" if nav.arg is not None:
                self._switch_scope(ProjectScope(nav.arg))
            case "group" if nav.arg is not None:
                self._set_group(nav.arg)
            case "group":
                self.action_group_menu()
            case "facet" if nav.arg is not None:
                self._pick_facet(nav.arg)
            case "unfacet" if nav.arg is not None:
                self._drop_facet(nav.arg)
            case "clearFilters":
                self.action_clear_filters()
            case "filter":
                self.action_filter_menu()
            case "views":
                self.action_views()
            case "view" if nav.arg is not None:
                self._recall_view(nav.arg)
            case "saveView":
                self._save_current_view()
            case "deleteView" if nav.arg is not None:
                self._delete_view(nav.arg)
            case "enter" if nav.arg is not None and nav.value is not None:
                self._drill(nav.arg, nav.value)
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

    def _run_header_action(self, header: HeaderAt, key: str) -> None:
        """The accelerator path over a group header: run the named action against the
        object the header stands for. The commands are already built and seeded, so a
        refusal is read off the row the menu would have greyed."""
        commands = self._header_commands(header) or []
        found = next(
            ((c, c.run) for c in commands if isinstance(c.run, RunAction) and c.run.key == key),
            None,
        )
        if found is None:
            self.status = f"{key}: not available"
            return
        command, run = found
        if command.reason is not None:
            self.status = f"{key}: {command.reason}"
            return
        self._run_action(run, command.label)

    def action_delete(self) -> None:
        comment = self._selected_comment()
        if comment is not None:
            _, offers = comment_api.comment_detail(self.engine, comment.id)
            self._run_offer(CommentTarget(comment.id), offers, "delete")
            return
        header = self._selected_header()
        if header is not None:
            self._run_header_action(header, "delete")
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
        header = self._selected_header()
        if header is not None:
            self._run_header_action(header, "edit")
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
        self.app.push_screen(
            CaptureScreen("New issue in", slug, "title"),
            lambda title: self._add_issue(slug, title),
        )

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
        filter_input.value = self.view_filter.text
        filter_input.can_focus = True
        filter_input.display = True
        self.query_one(StatusBar).display = False
        filter_input.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        # The quick line is the text facet under another key, so what is typed goes into
        # the filter and ANDs with whatever else is in force.
        if isinstance(event.input, FilterInput):
            self.view_filter = with_facet(self.view_filter, "text", event.value)
            self.cursor = self._first_visible()

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
        if self.query_one(FilterInput).display or self.view_filter.text:
            self._close_filter(keep=False)

    def _close_filter(self, *, keep: bool) -> None:
        # Only the text facet is the quick line's; escaping it drops that one and leaves
        # the chips picked from the facet menu standing.
        filter_input = self.query_one(FilterInput)
        filter_input.display = False
        filter_input.can_focus = False
        self.query_one(StatusBar).display = True
        self.set_focus(None)
        if not keep:
            self.view_filter = without_facet(self.view_filter, "text")
            filter_input.value = ""
            self.cursor = self._first_visible()
        self.status = BROWSING

    # --- lifecycle --------------------------------------------------------

    def on_resize(self, event: events.Resize) -> None:
        # The pane sits beside the list when the terminal is wide, and drops to a
        # stack below it when the column narrows past what the two need side by side.
        # Wider still, the rail comes in as a third column beside the two.
        self.split = pane_split(event.size.width)
        self.rail = shows_rail(event.size.width)

    def action_refresh(self) -> None:
        self.reload()

    def action_quit(self) -> None:
        self.app.exit()
