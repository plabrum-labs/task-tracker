"""The right pane in edit mode: an action's form, hosted beside the list.

Any action that carries fields — an issue edit, a project create, a status pick —
runs here rather than in a floating modal, so editing keeps the browse list in
view where the read detail was. One control per field, seeded from the target for
an edit; a prose field (an issue's body) becomes the pane's main editor with the
scalars in a column beside it, everything else stacks. ``ctrl+s`` builds the
payload and calls the screen's ``submit`` runner — the prose editor takes Enter
for a newline — a refusal shows inline and keeps the pane open, Escape abandons
it. The screen owns what save and cancel do: the pane posts ``Saved``/
``Cancelled`` and gathers the values, and knows nothing of the engine or the
reload behind them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from rich.markup import escape as esc
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input, Select, Static, TextArea

from tt.platform.actions import REFUSALS, Date, Enum, Field, OptionalText, Reference, Text
from tt.platform.actions import payload as build_payload

# What the pane calls to perform the write: a payload in, the action's message out,
# a refusal raised. The screen builds it closed over the engine and the target.
type Submit = Callable[[dict[str, Any]], str]


class _Column(VerticalScroll):
    """A split column that scrolls its own overflow but stays out of the focus ring,
    so Tab steps between the field controls and not the containers around them."""

    can_focus = False


def _is_multiline(field: Field) -> bool:
    return isinstance(field.kind, Text | OptionalText) and field.kind.multiline


def _split(fields: list[Field]) -> tuple[list[Field], list[Field], list[Field]] | None:
    """Partition a form around its prose field into header / content / attributes,
    or ``None`` when it has none. The content is every multiline field; the header
    is the plain fields ahead of the first one (the title above a body) and the
    attributes are the scalars after it (status, priority, the links). A form with
    no prose returns ``None`` and keeps the plain stacked layout."""
    content = [field for field in fields if _is_multiline(field)]
    if not content:
        return None
    first = fields.index(content[0])
    header = fields[:first]
    attrs = [field for field in fields[first + 1 :] if not _is_multiline(field)]
    return header, content, attrs


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

    # ``ctrl+s`` saves from anywhere in the form: the prose editor takes Enter for a
    # newline, so a single-line Input's Enter-to-submit cannot be the only way out.
    BINDINGS = [
        Binding("ctrl+s", "save", "save", show=False),
        Binding("escape", "cancel", "cancel", show=False),
    ]

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
        split = _split(edit.fields)
        if split is None:
            for field in edit.fields:
                yield from self._field_row(field, edit.seed)
        else:
            # The prose editor takes the pane's remaining height on the left; the
            # scalars sit in a narrow column beside it, with the title spanning the
            # top so the content reads as the subject and the rest as its attributes.
            header, content, attrs = split
            for field in header:
                yield from self._field_row(field, edit.seed)
            with Horizontal(classes="e-split"):
                with _Column(classes="e-content"):
                    for field in content:
                        yield from self._field_row(field, edit.seed)
                with _Column(classes="e-attrs"):
                    for field in attrs:
                        yield from self._field_row(field, edit.seed)
        yield Static("", classes="f-error", id="edit-error")
        yield Static("[$text-disabled]^s save · esc cancel[/]", classes="ov-foot")

    def _field_row(self, field: Field, seed: dict[str, Any] | None) -> ComposeResult:
        yield Static(self._field_label(field), classes="f-label")
        yield self._control(field, seed)

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

    def _control(self, field: Field, seed: dict[str, Any] | None) -> Input | Select[str] | TextArea:
        widget_id = f"field-{field.name}"
        value = None if seed is None else seed.get(field.name)
        match field.kind:
            case Text(multiline=True) | OptionalText(multiline=True):
                # Prose wraps and scrolls in a real editor; Enter is a newline here,
                # so ``ctrl+s`` is what submits the form from inside it.
                return TextArea(str(value or ""), id=widget_id, soft_wrap=True)
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
            elif isinstance(widget, TextArea):
                out.append((field, widget.text))
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

    def action_save(self) -> None:
        self._attempt()

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())
