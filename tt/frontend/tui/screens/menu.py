"""The reusable command list overlay: the issue menu, project menu, palette, switcher.

An ``OptionList`` of the object's offered commands opens focused, navigated by the
arrow keys and by each command's single-key accelerator. A refused command is a
disabled option, rendered greyed with its reason, so it reads but cannot be picked.
``/`` reveals a filter that live-narrows the list by substring; Enter (or a click)
dismisses with the chosen ``Command``; escape closes the filter, then the menu.
"""

from __future__ import annotations

from rich.markup import escape as esc
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from tt.frontend.tui.domainview import ACCELERATORS, Command, visible


class MenuScreen(ModalScreen["Command | None"]):
    """A floating command list. Its result is the picked ``Command``, or ``None``."""

    # Open on the list, not the hidden filter, so the arrow keys and accelerators
    # reach it straight away.
    AUTO_FOCUS = "#menu-list"

    BINDINGS = [
        ("escape", "cancel", "cancel"),
        ("down", "cursor_down", "down"),
        ("up", "cursor_up", "up"),
        ("slash", "reveal_filter", "search"),
        # Each accelerator runs its command in place. A focused filter box swallows
        # the letter before this fires, so it only acts while the list has focus.
        *[(key, f"accelerator('{key}')", action) for key, action in ACCELERATORS.items()],
    ]

    def __init__(self, header: str, commands: list[Command]) -> None:
        super().__init__()
        self._header = header
        self._commands = commands
        # The command index behind each shown option, in shown order — the filter
        # hides rows, so a list position is not a command index.
        self._shown: list[int] = list(range(len(commands)))

    def compose(self) -> ComposeResult:
        with Vertical(id="panel"):
            yield Static(f"[$overlay-text-muted]{esc(self._header)}[/]", classes="ov-header")
            # Hidden until ``/``. Not focusable while hidden: a ``display: none`` box
            # still reports focusable, so the accelerators would type into it.
            filter_box = Input(placeholder="Search…", id="menu-filter")
            filter_box.can_focus = False
            yield filter_box
            yield OptionList(id="menu-list")

    def on_mount(self) -> None:
        self._populate("")

    def _option_markup(self, command: Command) -> str:
        if command.reason is not None:
            return f"[$overlay-text-disabled]{esc(command.label)} — {esc(command.reason)}[/]"
        hint = f"  [$overlay-text-disabled]{command.hint}[/]" if command.hint else ""
        return f"{esc(command.label)}{hint}"

    def _populate(self, query: str) -> None:
        self._shown = visible(self._commands, query)
        options = self.query_one(OptionList)
        options.clear_options()
        for pos, ci in enumerate(self._shown):
            command = self._commands[ci]
            options.add_option(
                Option(
                    self._option_markup(command),
                    id=str(pos),
                    disabled=command.reason is not None,
                )
            )
        options.highlighted = self._first_enabled()

    def _first_enabled(self) -> int | None:
        return next(
            (pos for pos, ci in enumerate(self._shown) if self._commands[ci].reason is None),
            None,
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        self._populate(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        highlighted = self.query_one(OptionList).highlighted
        if highlighted is not None:
            self._pick(highlighted)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._pick(event.option_index)

    def _pick(self, position: int) -> None:
        command = self._commands[self._shown[position]]
        # A disabled option cannot be highlighted or clicked, but the guard keeps a
        # refused command a no-op even if one is reached.
        if command.reason is not None:
            return
        self.dismiss(command)

    def action_accelerator(self, key: str) -> None:
        hit = next(
            (
                pos
                for pos, ci in enumerate(self._shown)
                if self._commands[ci].hint == key and self._commands[ci].reason is None
            ),
            None,
        )
        if hit is not None:
            self._pick(hit)

    def action_reveal_filter(self) -> None:
        filter_box = self.query_one(Input)
        filter_box.can_focus = True
        filter_box.display = True
        filter_box.focus()

    def action_cursor_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()

    def action_cancel(self) -> None:
        # First escape retreats from an open filter back to the list; a second (or an
        # escape with no filter open) dismisses the menu.
        filter_box = self.query_one(Input)
        if filter_box.display:
            filter_box.value = ""
            filter_box.display = False
            filter_box.can_focus = False
            self._populate("")
            self.query_one(OptionList).focus()
            return
        self.dismiss(None)
