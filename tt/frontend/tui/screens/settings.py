"""The settings overlay: pick a theme.

An ``OptionList`` over the themes, opened on the current one. Picking a theme
dismisses with its ``ThemeName``; escape dismisses with ``None`` and changes
nothing. The main screen applies and persists the choice — nothing here touches the
app's theme, so cancelling is genuinely inert.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from tt.platform.config import ThemeName

# The theme options the overlay offers, each paired with the name it persists as.
_THEME_OPTIONS: tuple[tuple[str, ThemeName], ...] = (
    ("Dark", ThemeName.DARK),
    ("Light", ThemeName.LIGHT),
)


class SettingsScreen(ModalScreen["ThemeName | None"]):
    """The theme picker. Its result is the chosen ``ThemeName``, or ``None``."""

    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, current: ThemeName) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="panel"):
            yield Static("[b]Settings[/]", classes="ov-header")
            yield Static("[$text-muted]Theme[/]", classes="f-label")
            yield OptionList(
                *(Option(label, id=str(i)) for i, (label, _) in enumerate(_THEME_OPTIONS)),
                id="theme-list",
            )
            yield Static(
                "[$text-disabled]↑/↓ choose · enter save · esc cancel[/]", classes="ov-foot"
            )

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        options.highlighted = next(
            i for i, (_, name) in enumerate(_THEME_OPTIONS) if name == self._current
        )
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        assert event.option_id is not None
        self.dismiss(_THEME_OPTIONS[int(event.option_id)][1])

    def action_cancel(self) -> None:
        self.dismiss(None)
