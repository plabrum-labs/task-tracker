"""The quick-capture overlay: one text box for one line — a new issue's title, the
name a view is saved under.

Enter dismisses with the trimmed line (or ``None`` when it is blank); escape
dismisses with ``None``. The screen writes nothing itself and knows nothing of what
it is prompting for — the main screen names the prompt and acts on what comes back.
"""

from __future__ import annotations

from rich.markup import escape as esc
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class CaptureScreen(ModalScreen["str | None"]):
    """A single-field prompt. Its result is the line that was typed, or ``None``."""

    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, prompt: str, subject: str, placeholder: str) -> None:
        super().__init__()
        self._prompt = prompt
        self._subject = subject
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="panel"):
            yield Static(
                f"[$overlay-text-muted]{esc(self._prompt)} [b]{esc(self._subject)}[/][/]",
                classes="ov-header",
            )
            yield Input(placeholder=self._placeholder, id="capture-input")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
