"""The quick-capture overlay: one text box for a new issue's title.

Enter dismisses with the trimmed title (or ``None`` when it is blank); escape
dismisses with ``None``. The screen writes nothing itself — the main screen takes
the title and runs ``addIssue`` against the scoped project.
"""

from __future__ import annotations

from rich.markup import escape as esc
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class CaptureScreen(ModalScreen["str | None"]):
    """A single-field prompt. Its result is the new title, or ``None``."""

    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, slug: str) -> None:
        super().__init__()
        self._slug = slug

    def compose(self) -> ComposeResult:
        with Vertical(id="panel"):
            yield Static(
                f"[$text-muted]New issue in [b]{esc(self._slug)}[/][/]", classes="ov-header"
            )
            yield Input(placeholder="title", id="capture-input")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
