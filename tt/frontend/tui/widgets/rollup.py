"""How a set of issues stands, in one row.

An identity, an optional related field, a progress bar, and the per-status counts.
The row is told what it is drawing rather than working it out: it takes a
``Rollup`` and the two labels, so the detail pane can summarise the selected
issue's epic with the same widget a grouped list would put at the head of a group.
The bar and the counts mix colour into one markup string each, so they name their
theme variables through ``status_var`` rather than a per-cell CSS class.
"""

from __future__ import annotations

from rich.markup import escape as esc
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from tt.frontend.tui.domainview import Rollup, glyph, status_var

# Cells wide, so the bar is a fixed ruler across rows rather than one scaled to the
# group that happens to be biggest.
BAR_WIDTH = 12


def bar(standing: Rollup) -> str:
    """The progress bar as markup: the done share filled, the rest drawn in the
    track colour. An empty set of issues has nothing done, so its bar is all track."""
    filled = round(BAR_WIDTH * standing.done / standing.total) if standing.total else 0
    return f"[$success]{'━' * filled}[/][$text-disabled]{'━' * (BAR_WIDTH - filled)}[/]"


def breakdown(standing: Rollup) -> str:
    """The per-status counts as markup: each status's glyph in its own colour, with
    how many issues sit at it."""
    return "  ".join(
        f"[{status_var(status)}]{glyph(status)} {count}[/]"
        for status, count in standing.counts.items()
    )


class RollupRow(Horizontal):
    """One rollup line: ``identity`` names the set, ``related`` is the second field
    it is filed under (drawn only when there is one), and ``standing`` is the counts
    behind the bar."""

    def __init__(self, identity: str, related: str | None, standing: Rollup) -> None:
        super().__init__(classes="rollup")
        self._identity = identity
        self._related = related
        self._standing = standing

    def compose(self) -> ComposeResult:
        yield Static(esc(self._identity), classes="r-id")
        if self._related is not None:
            yield Static(f"[$text-muted]{esc(self._related)}[/]", classes="r-related")
        standing = self._standing
        yield Static(
            f"{bar(standing)} [$text-muted]{standing.done}/{standing.total}[/]", classes="r-bar"
        )
        yield Static(breakdown(standing), classes="r-counts")
