"""The action form: one control per field, seeded from the object for an edit.

Each ``Field`` becomes an ``Input`` (text) or a ``Select`` (enum), holding its own
value — there is no separate control state machine. The screen owns the write: on
submit it builds the payload and calls the ``submit`` runner, which raises through
``REFUSALS``. A refusal shows an error and leaves the form open with its values
intact; success dismisses with the action's message.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from rich.markup import escape as esc
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Select, Static

from tt.platform.actions import REFUSALS, Enum, Field, OptionalText, Text
from tt.platform.actions import payload as build_payload

# What the screen calls to perform the write: a payload in, the action's message out,
# a refusal raised. It closes over the engine and the addressed target.
type Submit = Callable[[dict[str, Any]], str]


class FormScreen(ModalScreen["str | None"]):
    """A modal form. Its result is the action's message on success, ``None`` on
    cancel; a refusal keeps it open rather than resolving."""

    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(
        self, label: str, fields: list[Field], seed: dict[str, Any] | None, submit: Submit
    ) -> None:
        super().__init__()
        self._label = label
        self._fields = fields
        self._seed = seed
        self._submit = submit

    def compose(self) -> ComposeResult:
        with Vertical(id="panel"):
            yield Static(f"[b]{esc(self._label)}[/]", classes="ov-header")
            for field in self._fields:
                yield Static(self._field_label(field), classes="f-label")
                yield self._control(field)
            yield Static("", classes="f-error", id="form-error")
            yield Static(
                "[$overlay-text-disabled]tab move · enter submit · esc cancel[/]",
                classes="ov-foot",
            )

    def on_mount(self) -> None:
        if self._fields:
            self.query_one(f"#field-{self._fields[0].name}").focus()

    def _field_label(self, field: Field) -> str:
        mark = " [$priority-high]*[/]" if field.required else ""
        desc = f"  [$overlay-text-disabled]{esc(field.description)}[/]" if field.description else ""
        return f"[$overlay-text-muted]{esc(field.name)}[/]{mark}{desc}"

    def _control(self, field: Field) -> Input | Select[str]:
        widget_id = f"field-{field.name}"
        seed = None if self._seed is None else self._seed.get(field.name)
        match field.kind:
            case Text() | OptionalText():
                return Input(value=str(seed or ""), id=widget_id)
            case Enum(values=values):
                value = seed if isinstance(seed, str) and seed in values else values[0]
                return Select(
                    [(v, v) for v in values], value=value, allow_blank=False, id=widget_id
                )

    def _values(self) -> list[tuple[Field, str]]:
        out: list[tuple[Field, str]] = []
        for field in self._fields:
            widget = self.query_one(f"#field-{field.name}")
            if isinstance(widget, Input):
                out.append((field, widget.value))
            else:
                # allow_blank is False and every enum is seeded, so the value is a
                # real option string, never the BLANK sentinel.
                out.append((field, cast("str", cast("Select[str]", widget).value)))
        return out

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._attempt()

    def _attempt(self) -> None:
        payload = build_payload(self._values())
        try:
            message = self._submit(payload)
        except REFUSALS as error:
            self.query_one("#form-error", Static).update(f"[$warning]{esc(str(error))}[/]")
            return
        self.dismiss(message)

    def action_cancel(self) -> None:
        self.dismiss(None)
