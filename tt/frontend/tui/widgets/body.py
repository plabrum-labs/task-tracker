"""The issue body: a dense flat list, or three status columns in board layout.

The rows and cards are custom ``Static``-based widgets rather than a ``DataTable``:
the gutter bar, the per-status glyph colour, and the priority column each need their
own styled cell, which a table fights. ``Body`` holds the visible issues, the
layout, and the selection as reactives with ``recompose=True``, so the screen sets
them and the subtree rebuilds itself; colour lives in ``style.tcss`` component
classes, not in the content.
"""

from __future__ import annotations

from rich.markup import escape as esc
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static

from tt.domains.issue.schemas import IssueListItem
from tt.frontend.tui.domainview import (
    Column,
    Layout,
    columns,
    glyph,
    issue_ref,
    marker,
    status_var,
)


class IssueRow(Horizontal):
    """One row of the flat list: gutter bar, status glyph, id, title, priority."""

    def __init__(self, issue: IssueListItem, selected: bool) -> None:
        super().__init__(classes="sel" if selected else "")
        self._issue = issue
        self._selected = selected

    def compose(self) -> ComposeResult:
        issue = self._issue
        yield Static("▌" if self._selected else " ", classes="bar")
        yield Static(glyph(issue.status), classes=f"glyph st-{issue.status}")
        yield Static(esc(issue_ref(issue.project, issue.id)), classes="ref")
        title_classes = "title done" if issue.status == "done" else "title"
        yield Static(esc(issue.title), classes=title_classes)
        yield Static("▲ high" if marker(issue.priority) else "", classes="pri")

    def on_mount(self) -> None:
        if self._selected:
            self.scroll_visible()


class Card(Static):
    """One board card: the priority marker and id over the title. A card mixes three
    colours in a single widget, so it uses theme-variable markup rather than the
    per-cell classes the flat row splits into."""

    def __init__(self, issue: IssueListItem, selected: bool) -> None:
        pri = "[$priority-high]▲[/] " if marker(issue.priority) else ""
        ref = f"[$text-disabled]{esc(issue_ref(issue.project, issue.id))}[/]"
        super().__init__(f"{pri}{ref}\n{esc(issue.title)}", classes="sel" if selected else "")
        self._selected = selected

    def on_mount(self) -> None:
        if self._selected:
            self.scroll_visible()


class BoardColumn(Vertical):
    """One status column of the board: a coloured header over its cards."""

    def __init__(self, column: Column, selected_id: int | None) -> None:
        super().__init__()
        self._column = column
        self._selected_id = selected_id

    def compose(self) -> ComposeResult:
        col = self._column
        status = col.status or ""
        var = status_var(status)
        head = (
            f"[{var}]{glyph(status)}[/] [$text-muted]{esc(col.title)}[/]  "
            f"[$text-disabled]{len(col.issues)}[/]"
        )
        yield Static(head, classes="col-h")
        for issue in col.issues:
            yield Card(issue, issue.id == self._selected_id)


class Body(VerticalScroll):
    """The scrolling issue area. The screen assigns ``issues``/``view_layout``/
    ``selected_id``/``scoped``; each is a recomposing reactive, so the subtree
    rebuilds from the new values. The layout reactive is ``view_layout``, not
    ``layout``: ``Widget.layout`` is a property, and a reactive of that name would
    shadow it.

    Not focusable: in browse mode no widget holds focus, so every keystroke reaches
    the screen's bindings rather than being eaten by the scroll container's own
    arrow-key handling."""

    can_focus = False

    issues: reactive[list[IssueListItem]] = reactive(list, recompose=True)
    view_layout: reactive[Layout] = reactive[Layout]("list", recompose=True)
    selected_id: reactive[int | None] = reactive[int | None](None, recompose=True)
    scoped: reactive[bool] = reactive(False, recompose=True)

    def compose(self) -> ComposeResult:
        issues = self.issues
        if not issues:
            hint = "no issues — n to add one" if self.scoped else "no issues"
            yield Static(f"  [$text-disabled]{hint}[/]", classes="empty")
            return
        if self.view_layout == "board":
            for col in columns(issues, "board"):
                yield BoardColumn(col, self.selected_id)
        else:
            for issue in issues:
                yield IssueRow(issue, issue.id == self.selected_id)
