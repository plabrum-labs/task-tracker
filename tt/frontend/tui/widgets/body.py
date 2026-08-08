"""The issue body: the groups a dimension files the issues under, stacked down the
page or fanned across it as columns.

The rows and cards are custom ``Static``-based widgets rather than a ``DataTable``:
the gutter bar, the per-status glyph colour, and the priority column each need their
own styled cell, which a table fights. A group's header is the rollup row, the same
widget the detail pane summarises an epic with. ``Body`` holds the groups, the render
and the selection as reactives with ``recompose=True``, so the screen sets them and
the subtree rebuilds itself; colour lives in ``style.tcss`` component classes, not in
the content.
"""

from __future__ import annotations

from rich.markup import escape as esc
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static

from tt.domains.issue.schemas import IssueListItem
from tt.frontend.tui.domainview import (
    WAITING_VAR,
    Group,
    GroupRender,
    glyph,
    priority_mark,
    waiting_mark,
)
from tt.frontend.tui.widgets.rollup import RollupRow


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
        yield Static(esc(issue.ref), classes="ref")
        wmark = waiting_mark(issue.waiting)
        yield Static(f"[{WAITING_VAR}]{wmark}[/]" if wmark else " ", classes="waiting")
        title_classes = "title done" if issue.status == "done" else "title"
        yield Static(esc(issue.title), classes=title_classes)
        mark = priority_mark(issue.priority)
        pri = f"[{mark.var}]{mark.glyph} {esc(issue.priority)}[/]" if mark else ""
        yield Static(pri, classes="pri")

    def on_mount(self) -> None:
        if self._selected:
            self.scroll_visible()


class Card(Static):
    """One card of a column: the priority marker and id over the title. A card mixes
    three colours in a single widget, so it uses theme-variable markup rather than the
    per-cell classes the flat row splits into."""

    def __init__(self, issue: IssueListItem, selected: bool) -> None:
        mark = priority_mark(issue.priority)
        pri = f"[{mark.var}]{mark.glyph}[/] " if mark else ""
        ref = f"[$text-disabled]{esc(issue.ref)}[/]"
        wmark = waiting_mark(issue.waiting)
        waiting = f"  [{WAITING_VAR}]{wmark}[/]" if wmark else ""
        super().__init__(
            f"{pri}{ref}{waiting}\n{esc(issue.title)}", classes="sel" if selected else ""
        )
        self._selected = selected

    def on_mount(self) -> None:
        if self._selected:
            self.scroll_visible()


def _header(group: Group) -> RollupRow:
    """A group's header: how it stands, drawn in the rollup row. Display only — the
    cursor walks issues, never headers."""
    return RollupRow(group.key.label, group.key.related, group.rollup)


class GroupColumn(Vertical):
    """One group as a column of the fan: its header over its cards."""

    def __init__(self, group: Group, selected_id: int | None) -> None:
        super().__init__()
        self._group = group
        self._selected_id = selected_id

    def compose(self) -> ComposeResult:
        yield _header(self._group)
        for issue in self._group.issues:
            yield Card(issue, issue.id == self._selected_id)


class Body(VerticalScroll):
    """The scrolling issue area. The screen assigns ``groups``/``view_render``/
    ``selected_id``/``scoped``; each is a recomposing reactive, so the subtree rebuilds
    from the new values. The render reactive is ``view_render``, not ``render``:
    ``Widget.render`` is the method that draws the widget, and a reactive of that name
    would shadow it.

    Not focusable: in browse mode no widget holds focus, so every keystroke reaches
    the screen's bindings rather than being eaten by the scroll container's own
    arrow-key handling."""

    can_focus = False

    groups: reactive[list[Group]] = reactive(list, recompose=True)
    view_render: reactive[GroupRender] = reactive[GroupRender]("stacked", recompose=True)
    selected_id: reactive[int | None] = reactive[int | None](None, recompose=True)
    scoped: reactive[bool] = reactive(False, recompose=True)

    def compose(self) -> ComposeResult:
        groups = self.groups
        if not any(group.issues for group in groups):
            hint = "no issues — n to add one" if self.scoped else "no issues"
            yield Static(f"  [$text-disabled]{hint}[/]", classes="empty")
            return
        if self.view_render == "columns":
            # The columns are ``width: 1fr``, which only lays them across a row inside a
            # horizontal container — yielded straight into this ``VerticalScroll`` they
            # would stack down the left edge instead of forming a fan.
            yield Horizontal(
                *(GroupColumn(group, self.selected_id) for group in groups),
                classes="board",
            )
            return
        for group in groups:
            # The flat list is one group holding everything, and it has nothing to say
            # that the list below it does not — so it is the one group with no header.
            if group.key.label:
                yield _header(group)
            for issue in group.issues:
                yield IssueRow(issue, issue.id == self.selected_id)
