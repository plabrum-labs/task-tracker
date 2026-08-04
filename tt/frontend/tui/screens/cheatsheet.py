"""The key reference overlay: a static list of what every key does. Any key closes it."""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

_CHEATS: list[tuple[str, str]] = [
    ("j / k", "move"),
    ("J / K", "half page"),
    ("g / G", "top / bottom"),
    ("h / l", "columns (board)"),
    ("tab", "cycle layout"),
    ("[ / ]", "prev / next project"),
    ("enter", "read detail (esc to leave)"),
    ("x / space", "issue actions"),
    ("X", "project actions"),
    (":", "command palette"),
    ("s", "cycle issue status"),
    ("d", "delete issue"),
    ("n", "new issue"),
    ("/", "filter"),
    (",", "settings"),
    ("P", "switch project"),
    ("R", "refresh"),
    ("q", "quit"),
]


class CheatsheetScreen(ModalScreen[None]):
    """The key list. Any key dismisses it."""

    def compose(self) -> ComposeResult:
        with Vertical(id="panel"):
            yield Static("[b]Keys[/]", classes="ov-header")
            for keys, what in _CHEATS:
                yield Static(
                    f"  [$primary]{keys:<14}[/] [$overlay-text-muted]{what}[/]", classes="cheat"
                )
            yield Static("  [$overlay-text-disabled]any key to close[/]", classes="cheat")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.dismiss()
