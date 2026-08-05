"""The right pane in edit mode: an action's form, hosted beside the list.

Any action that carries fields — an issue edit, a project create, a status pick —
runs here rather than in a floating modal, so editing keeps the browse list in
view where the read detail was. One control per field, seeded from the target for
an edit; Enter builds the payload and calls the screen's ``submit`` runner, a
refusal shows inline and keeps the pane open, Escape abandons it. The screen owns
what save and cancel do: the pane posts ``Saved``/``Cancelled`` and gathers the
values, and knows nothing of the engine or the reload behind them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from rich.markup import escape as esc
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input, Select, Static

from tt.platform.actions import REFUSALS, Date, Enum, Field, OptionalText, Reference, Text
from tt.platform.actions import payload as build_payload

# What the pane calls to perform the write: a payload in, the action's message out,
# a refusal raised. The screen builds it closed over the engine and the target.
type Submit = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class Edit:
    """One action's form: its title, the controls to draw, the target's current
    values to seed an edit (``None`` for a create), and the runner that performs the
    write. ``None`` on the pane is its inactive state."""

    label: str
    fields: list[Field]
    seed: dict[str, Any] | None
    submit: Submit


class EditPane(VerticalScroll):
    """The pane's edit mode. Its ``edit`` reactive rebuilds the controls on assign
    and is ``None`` when idle; focusable and scrolling so a tall form reads like the
    detail it replaces. Its result leaves as a ``Saved`` or ``Cancelled`` message."""

    BINDINGS = [Binding("escape", "cancel", "cancel", show=False)]

    # Tab moves between the field controls (the screen no longer binds it), so the
    # scroll container itself stays out of the focus ring — focusing a field scrolls
    # it into view, so a tall form still reaches every control without this stop.
    can_focus = False

    # Assigning a new ``Edit`` recomposes the controls, the way the detail pane
    # rebuilds from its own reactive.
    edit: reactive[Edit | None] = reactive[Edit | None](None, recompose=True)

    class Saved(Message):
        """The write succeeded; its message is the status to show."""

        def __init__(self, message: str) -> None:
            self.message = message
            super().__init__()

    class Cancelled(Message):
        """The edit was abandoned with no write."""

    def compose(self) -> ComposeResult:
        edit = self.edit
        if edit is None:
            return
        yield Static(f"[b]{esc(edit.label)}[/]", classes="e-header")
        for field in edit.fields:
            yield Static(self._field_label(field), classes="f-label")
            yield self._control(field, edit.seed)
        yield Static("", classes="f-error", id="edit-error")
        yield Static("[$text-disabled]enter save · esc cancel[/]", classes="ov-foot")

    def begin(self, edit: Edit) -> None:
        """Host one action's form, replacing whatever the pane last held. Focus lands
        on the first control once the rebuilt controls have mounted."""
        self.edit = edit
        self.call_after_refresh(self._focus_first)

    def _focus_first(self) -> None:
        if self.edit is not None and self.edit.fields:
            self.query_one(f"#field-{self.edit.fields[0].name}").focus()

    def _field_label(self, field: Field) -> str:
        mark = " [$priority-high]*[/]" if field.required else ""
        desc = f"  [$text-disabled]{esc(field.description)}[/]" if field.description else ""
        return f"[$text-muted]{esc(field.name)}[/]{mark}{desc}"

    def _control(self, field: Field, seed: dict[str, Any] | None) -> Input | Select[str]:
        widget_id = f"field-{field.name}"
        value = None if seed is None else seed.get(field.name)
        match field.kind:
            case Text() | OptionalText() | Date() | Reference():
                # A date is typed as its ISO string and a reference as the target's
                # id; a seeded value renders through ``str``. The pickers a ``Date``
                # and a ``Reference`` leave room for are future TUI work.
                return Input(value=str(value or ""), id=widget_id)
            case Enum(values=values):
                chosen = value if isinstance(value, str) and value in values else values[0]
                return Select(
                    [(v, v) for v in values], value=chosen, allow_blank=False, id=widget_id
                )

    def _values(self, fields: list[Field]) -> list[tuple[Field, str]]:
        out: list[tuple[Field, str]] = []
        for field in fields:
            widget = self.query_one(f"#field-{field.name}")
            if isinstance(widget, Input):
                out.append((field, widget.value))
            else:
                # allow_blank is False and every enum is seeded, so the value is a
                # real option string, never the BLANK sentinel.
                out.append((field, cast("str", cast("Select[str]", widget).value)))
        return out

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # The field inputs submit the whole form; the screen's own handler only
        # cares about the filter box, so stop this from bubbling into it.
        event.stop()
        self._attempt()

    def _attempt(self) -> None:
        if self.edit is None:
            return
        payload = build_payload(self._values(self.edit.fields))
        try:
            message = self.edit.submit(payload)
        except REFUSALS as error:
            self.query_one("#edit-error", Static).update(f"[$warning]{esc(str(error))}[/]")
            return
        self.post_message(self.Saved(message))

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())
