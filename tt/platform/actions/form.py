"""The form a payload derives, and the decode that turns a blob into one.

A payload is a pydantic model and knows nothing about a terminal or a menu. The
``Field``\\ s ``fields_of`` reads off it are the whole of what a CLI turns into
options and a TUI into controls, in declaration order; ``payload`` is the inverse,
turning what was typed back into the blob ``_decode`` validates. ``name_of`` is the
wire name of a value, the one place the enum-to-string convention lives.
"""

from __future__ import annotations

import datetime
import enum
import types
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Union, cast, get_args, get_origin

from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo

from tt.platform.actions.base import Invalid

if TYPE_CHECKING:
    from tt.platform.actions.base import ObjectAction, TopLevelAction


def _decode(payload_type: type[BaseModel], raw: Any) -> BaseModel:
    """The one place a validation failure becomes an ``Invalid``. Pydantic's
    ``extra="forbid"`` rejects an unadvertised field, a wrong type or a value
    outside an enum here, so a bad blob is a refusal and not a crash."""
    try:
        return payload_type.model_validate(raw)
    except ValidationError as e:
        raise Invalid(str(e)) from e


def name_of(value: object) -> str:
    """The wire name of a value. An enum member's wire form is its name lowered
    (``Priority.HIGH`` → ``"high"``, ``Status.TODO`` → ``"todo"``), which is what a
    frontend prints and what a payload's serializer emits."""
    if isinstance(value, enum.Enum):
        return value.name.lower()
    return str(value)


# --- the form a payload derives -------------------------------------------


@dataclass(frozen=True)
class Text:
    """A required text box."""


@dataclass(frozen=True)
class OptionalText:
    """A text box whose blank state is a real ``null``."""


@dataclass(frozen=True)
class Date:
    """A calendar date, entered as an ISO string. Its blank state is a real
    ``null``, the same rule ``OptionalText`` carries, because every date a column
    holds is nullable."""


@dataclass(frozen=True)
class Enum:
    """A cycling selector over the closed list an enum advertises."""

    values: list[str]


type Kind = Text | OptionalText | Date | Enum


@dataclass(frozen=True)
class Field:
    name: str
    required: bool
    kind: Kind
    # The field's description, which Pydantic took from ``Field(description=...)``
    # — the ``--help`` text of a CLI option and the label beside a TUI field.
    description: str | None


def _peel(annotation: Any) -> tuple[Any, bool]:
    """The base type of a field, and whether it was optional. Unwraps an
    ``Annotated`` wrapper (the ``BeforeValidator``/``PlainSerializer`` a wire enum
    carries) and a ``X | None`` union, in either order."""
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if get_origin(annotation) in (types.UnionType, Union):
        arms = get_args(annotation)
        present = [a for a in arms if a is not type(None)]
        base = present[0] if len(present) == 1 else annotation
        if get_origin(base) is Annotated:
            base = get_args(base)[0]
        return base, len(present) != len(arms)
    return annotation, False


def _kind_of(base: Any, *, optional: bool) -> Kind:
    if isinstance(base, type) and issubclass(base, enum.Enum):
        return Enum([member.name.lower() for member in base])
    if base is str:
        return OptionalText() if optional else Text()
    if base is datetime.date:
        return Date()
    # A field the form cannot render is a programming error, not runtime input:
    # dropping it would draw a form that cannot express the action.
    raise TypeError(f"no form field for {base!r}")


def fields_of(schema_cls: type[BaseModel]) -> list[Field]:
    """The fields to render, in the order the payload model declares them.

    A model with no fields is a form with no fields, which is what an action
    taking no arguments derives. The order is the model's declaration order,
    which Pydantic preserves in ``model_fields``.
    """
    return [_field_of(name, info) for name, info in schema_cls.model_fields.items()]


def _field_of(name: str, info: FieldInfo) -> Field:
    base, optional = _peel(info.annotation)
    return Field(
        name=name,
        required=info.is_required(),
        kind=_kind_of(base, optional=optional),
        description=info.description,
    )


def payload(values: list[tuple[Field, str]]) -> dict[str, Any]:
    """What the typed-in values make, ready to decode.

    A blank ``OptionalText`` or ``Date`` is sent as ``null``. A blank ``Text`` is
    sent as ``""`` rather than withheld: the action's ``execute`` is the
    enforcement point — ``editTitle`` is what decides a blank title is refused — and
    a form that second-guessed it would be a second place that has to agree. An
    ``Enum_`` is always sent, because a selector over a closed list has no blank
    state.
    """
    out: dict[str, Any] = {}
    for field, value in values:
        if isinstance(field.kind, OptionalText | Date) and value == "":
            out[field.name] = None
        else:
            out[field.name] = value
    return out


def decode[P: BaseModel](
    action_cls: type[ObjectAction[Any, P]] | type[TopLevelAction[P]], raw: Any
) -> P:
    """Decode a blob against one action's payload schema, raising ``Invalid`` on a
    bad one. Split out so a group's ``trigger`` reads as decode-then-run."""
    return cast("P", _decode(action_cls.Payload, raw))
