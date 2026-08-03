"""The argument form a frontend builds from an action's advertised schema.

This is the second half of the erased path. ``wire.available`` says which actions
apply; this says what each one wants typed in. Both come from the schema the
payload model derived, so a frontend needs no knowledge of any particular action
— ``cli`` turns what ``of_schema`` returns into options and ``tui`` turns it into
controls.

Pure, and separate from the rendering for that reason: the shape of a form is
decided here and the two frontends are left with drawing and key handling.
"""

from dataclasses import dataclass
from typing import Any


class FormError(Exception):
    """A schema the form cannot render. Raised rather than skipping the field:
    dropping it would render a form that cannot express the action, and
    submitting it would produce a payload the decoder refuses for a reason
    nothing on screen mentions."""


@dataclass(frozen=True)
class Text:
    """A required text box: ``{"type": "string"}``."""


@dataclass(frozen=True)
class OptionalText:
    """A text box whose blank state is a real ``null`` the schema advertises."""


@dataclass(frozen=True)
class Enum:
    """A cycling selector over a closed list, reached through a ``$ref``."""

    values: list[str]


type Kind = Text | OptionalText | Enum


@dataclass(frozen=True)
class Field:
    name: str
    required: bool
    kind: Kind
    # The field's description, which Pydantic took from ``Field(description=...)``
    # — the ``--help`` text of a CLI option and the label beside a TUI field.
    description: str | None


def _resolve(schema: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    """Follow one ``$ref`` into ``$defs``, or the single-armed ``allOf`` Pydantic
    emits when a ref carries a sibling description. Anything else is not a ref."""
    ref = spec.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        defs = schema.get("$defs", {})
        return defs.get(ref.removeprefix("#/$defs/"))
    all_of = spec.get("allOf")
    if isinstance(all_of, list) and len(all_of) == 1:
        return _resolve(schema, all_of[0])
    return None


def _kind_of(schema: dict[str, Any], spec: dict[str, Any], *, optional: bool) -> Kind:
    resolved = _resolve(schema, spec)
    if resolved is not None:
        return _kind_of(schema, resolved, optional=optional)

    any_of = spec.get("anyOf")
    if isinstance(any_of, list):
        arms = [a for a in any_of if a.get("type") != "null"]
        has_null = any(a.get("type") == "null" for a in any_of)
        # An optional field is ``anyOf: [ <inner>, {"type": "null"} ]``. Strip the
        # null arm and read the rest as if required, carrying that it was
        # optional so a blank one knows to submit ``null``.
        if has_null and len(arms) == 1:
            return _kind_of(schema, arms[0], optional=True)

    enum = spec.get("enum")
    if isinstance(enum, list):
        return Enum([str(v) for v in enum])
    if spec.get("type") == "string":
        return OptionalText() if optional else Text()

    raise FormError(f"no form field for {spec!r}")


def of_schema(schema: dict[str, Any]) -> list[Field]:
    """The fields to render, in the order the payload model declares them.

    A schema with no ``properties`` is a form with no fields, which is what an
    action taking no arguments advertises. A property whose type has no form field
    raises rather than being dropped.

    The order is the declaration order of the model's fields: Pydantic emits its
    ``properties`` in that order and a Python dict preserves it, so unlike the
    Rust spike there is no alphabetical-``BTreeMap`` hazard to defend against.
    """
    if schema.get("type") != "object":
        raise FormError(f"schema is not an object: {schema!r}")
    required = set(schema.get("required", []))
    properties = schema.get("properties")
    if properties is None:
        return []
    if not isinstance(properties, dict):
        raise FormError(f"properties is not an object: {properties!r}")

    return [
        Field(
            name=name,
            required=name in required,
            kind=_kind_of(schema, spec, optional=name not in required),
            description=spec.get("description"),
        )
        for name, spec in properties.items()
    ]


def payload(values: list[tuple[Field, str]]) -> dict[str, Any]:
    """What the typed-in values make, ready for ``wire.dispatch``.

    A blank ``OptionalText`` is sent as ``null``, which is what the schema
    advertises and the decoder accepts. A blank ``Text`` is sent as ``""`` rather
    than withheld: the action's ``execute`` is the enforcement point — ``editTitle``
    is what decides a blank title is refused — and a form that second-guessed it
    would be a second place that has to agree. An ``Enum`` is always sent, because
    a selector over a closed list has no blank state to mean "absent".
    """
    out: dict[str, Any] = {}
    for field, value in values:
        if isinstance(field.kind, OptionalText) and value == "":
            out[field.name] = None
        else:
            out[field.name] = value
    return out
