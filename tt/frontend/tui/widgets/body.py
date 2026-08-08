"""The issue body: the tree of groups one dimension — or two nested — files the issues
under, stacked down the page or fanned across it as columns.

The rows and cards are custom ``Static``-based widgets rather than a ``DataTable``:
the gutter bar, the per-status glyph colour, and the priority column each need their
own styled cell, which a table fights. A node's header is the rollup row, the same
widget the detail pane summarises an epic with, prefixed with the caret that says
whether the node is open. ``Body`` holds the tree, the fold state, the render and the
selection as reactives with ``recompose=True``, so the screen sets them and the
subtree rebuilds itself; colour and the nesting indents live in ``style.tcss``
component classes, not in the content.
"""

from __future__ import annotations

from rich.markup import escape as esc
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static

from tt.domains.issue.schemas import IssueListItem
from tt.frontend.tui.domainview import (
    EXPANDED,
    WAITING_VAR,
    Collapsed,
    GroupNode,
    GroupRender,
    NodePath,
    all_issues,
    folded,
    glyph,
    path_of,
    priority_mark,
    waiting_mark,
)
from tt.frontend.tui.widgets.rollup import RollupRow

# The caret a header carries: open downward into what it holds, shut sideways.
CARET_OPEN = "▾"
CARET_SHUT = "▸"


def _level(depth: int) -> str:
    """The indent class a header or row at this depth carries. The outermost level
    draws flush against the gutter, so only a nested one names a class."""
    return f"lvl-{depth}" if depth else ""


class IssueRow(Horizontal):
    """One row of the flat list: gutter bar, status glyph, id, title, priority."""

    def __init__(self, issue: IssueListItem, selected: bool, depth: int = 0) -> None:
        super().__init__(classes=" ".join(filter(None, ("sel" if selected else "", _level(depth)))))
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


def _header(node: GroupNode, depth: int, shut: bool) -> RollupRow:
    """A node's header: its caret and label, and how it stands, drawn in the rollup
    row. Display only — the cursor walks issues, never headers."""
    caret = CARET_SHUT if shut else CARET_OPEN
    row = RollupRow(f"{caret} {node.key.label}", node.key.related, node.rollup)
    if depth:
        row.add_class(_level(depth))
    return row


class GroupColumn(Vertical):
    """One group as a column of the fan: its header over its cards, and nothing under
    the header while it is folded shut."""

    def __init__(self, node: GroupNode, collapsed: Collapsed, selected_id: int | None) -> None:
        super().__init__()
        self._node = node
        self._shut = folded(node, path_of((), node), collapsed)
        self._selected_id = selected_id

    def compose(self) -> ComposeResult:
        yield _header(self._node, 0, self._shut)
        if not self._shut:
            for issue in self._node.issues:
                yield Card(issue, issue.id == self._selected_id)


class Body(VerticalScroll):
    """The scrolling issue area. The screen assigns ``nodes``/``collapsed``/
    ``view_render``/``selected_id``/``scoped``; each is a recomposing reactive, so the
    subtree rebuilds from the new values. The render reactive is ``view_render``, not
    ``render``: ``Widget.render`` is the method that draws the widget, and a reactive of
    that name would shadow it.

    Not focusable: in browse mode no widget holds focus, so every keystroke reaches
    the screen's bindings rather than being eaten by the scroll container's own
    arrow-key handling."""

    can_focus = False

    nodes: reactive[list[GroupNode]] = reactive(list, recompose=True)
    collapsed: reactive[Collapsed] = reactive[Collapsed](EXPANDED, recompose=True)
    view_render: reactive[GroupRender] = reactive[GroupRender]("stacked", recompose=True)
    selected_id: reactive[int | None] = reactive[int | None](None, recompose=True)
    scoped: reactive[bool] = reactive(False, recompose=True)

    def compose(self) -> ComposeResult:
        nodes = self.nodes
        # What the tree holds, not what is on screen: a body folded shut still draws
        # its headers, and only an empty scope has nothing to say.
        if not all_issues(nodes):
            hint = "no issues — n to add one" if self.scoped else "no issues"
            yield Static(f"  [$text-disabled]{hint}[/]", classes="empty")
            return
        if self.view_render == "columns":
            # The columns are ``width: 1fr``, which only lays them across a row inside a
            # horizontal container — yielded straight into this ``VerticalScroll`` they
            # would stack down the left edge instead of forming a fan.
            yield Horizontal(
                *(GroupColumn(node, self.collapsed, self.selected_id) for node in nodes),
                classes="board",
            )
            return
        yield from self._stack(nodes, (), 0)

    def _stack(self, nodes: list[GroupNode], prefix: NodePath, depth: int) -> ComposeResult:
        """One level of the tree down the page: each node's header, then what it holds —
        its own issues, or the nested level indented under it."""
        for node in nodes:
            path = path_of(prefix, node)
            shut = folded(node, path, self.collapsed)
            # The flat list is one group holding everything, and it has nothing to say
            # that the list below it does not — so it is the one group with no header.
            if node.key.label:
                yield _header(node, depth, shut)
            if not shut:
                for issue in node.issues:
                    yield IssueRow(issue, issue.id == self.selected_id, depth)
                yield from self._stack(node.children, path, depth + 1)
