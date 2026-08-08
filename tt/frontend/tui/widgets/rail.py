"""The ultra-wide rail: the grouping, the facets in force and the saved views, standing
in a column of their own.

Only drawn in the ultra-wide band, where a third column is width the list and the detail
pane do not need. The rail holds nothing of its own — the screen's grouping, filter and
saved views are what it draws, in one recomposing value so a change repaints it once —
and each row steers with the same ``Navigate`` the overlays behind ``g``/``f``/``v``
post. That is what makes the collapse below the band lossless: the rail is another way
in, never the only one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rich.markup import escape as esc
from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static

from tt.frontend.tui.domainview import (
    NO_FILTER,
    Filter,
    Grouping,
    Navigate,
    View,
    chips,
    grouping_label,
    view_label,
)


@dataclass(frozen=True)
class RailState:
    """Everything the rail draws, as one value: the grouping in force, what narrows the
    list, and the views there are to recall."""

    by: Grouping
    filter: Filter
    views: tuple[View, ...]


EMPTY = RailState(("none",), NO_FILTER, ())


class RailRow(Static):
    """A line of the rail that steers when it is clicked. The mouse is the rail's whole
    input — it carries no cursor of its own, so a row is a target rather than a stop."""

    def __init__(self, markup: str, nav: Navigate, classes: str = "rail-row") -> None:
        super().__init__(markup, classes=classes)
        self.nav = nav

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(Rail.Steer(self.nav))


def _head(title: str, key: str, nav: Navigate) -> RailRow:
    """A section head: what the section is, and the key that opens it as an overlay —
    the same move clicking the head makes."""
    return RailRow(f"[$text-muted]{title}[/]  [$text-disabled]{key}[/]", nav, "rail-head")


class Rail(VerticalScroll):
    """The rail. ``state`` is a recomposing reactive the screen assigns through ``show``;
    the screen also takes the widget off the page outside the ultra-wide band.

    Scrollable so a long list of views is reachable, but not focusable: browse mode holds
    no focus, and a rail that took it would swallow the keys the screen binds.
    """

    can_focus = False

    state: reactive[RailState] = reactive[RailState](EMPTY, recompose=True)

    class Steer(Message):
        """A rail row was picked. The screen runs it through the same dispatch a menu
        row goes through, so the rail adds no vocabulary of its own."""

        def __init__(self, nav: Navigate) -> None:
            super().__init__()
            self.nav = nav

    def show(self, by: Grouping, current: Filter, views: Sequence[View]) -> None:
        self.state = RailState(by, current, tuple(views))

    def compose(self) -> ComposeResult:
        state = self.state
        yield _head("GROUP", "g", Navigate("group"))
        yield RailRow(f"[b $primary]{esc(grouping_label(state.by))}[/]", Navigate("group"))

        yield _head("FILTERS", "f", Navigate("filter"))
        held = chips(state.filter)
        if not held:
            yield Static("[$text-disabled]none[/]", classes="rail-empty")
        for chip in held:
            # Picking a chip takes that facet off, the way the filter menu's remove row
            # does — the chip is what is in force, so the click drops it.
            yield RailRow(esc(chip.label), Navigate("unfacet", chip.facet), "rail-chip")

        yield _head("VIEWS", "v", Navigate("views"))
        if not state.views:
            yield Static("[$text-disabled]none saved[/]", classes="rail-empty")
        for view in state.views:
            yield RailRow(esc(view_label(view)), Navigate("view", view.name))
