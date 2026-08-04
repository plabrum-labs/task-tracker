"""The footer line: a status message, and the filter box that replaces it.

Both dock the bottom row. The screen shows the ``FilterInput`` and hides the
``StatusBar`` while ``/`` is active, and swaps them back when the filter closes, so
only one of the two ever occupies the row.
"""

from __future__ import annotations

from rich.markup import escape as esc
from textual.widgets import Input, Static


class StatusBar(Static):
    """The idle footer: whatever the last action had to say."""

    def show(self, status: str) -> None:
        self.update(f"  [$text-disabled]{esc(status)}[/]")


class FilterInput(Input):
    """The substring filter over issue titles, hidden until ``/`` opens it.

    Not focusable while hidden: a ``display: none`` widget still reports focusable, so
    the screen would auto-focus it and swallow browse keys. The screen flips
    ``can_focus`` on when it opens the filter and off when it closes it."""

    can_focus = False
