"""The JSON edge. Everything that knows about a transport format is here.

An ``Action`` is typed and knows nothing about JSON. ``entry_of`` pairs one with
the schema and decoder its ``Payload`` model carries, and erases the payload into
two callables — which is what lets actions with different payloads sit in one
group, and is the whole of what a TUI user picking an action and an agent driving
the CLI both need.

The key-to-payload-model mapping stays here, next to the action. It cannot move
out to a frontend: a caller holding the string ``"editTitle"`` and a blob has to
pick the decoder by key, and doing that anywhere else would let a frontend's idea
of an action's arguments drift from the action's own.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from tt.platform.action import Action, Availability, Offered
from tt.platform.error import Invalid


class Empty(BaseModel):
    """The arguments of an action that has none. ``extra="forbid"`` makes the
    decoder accept ``{}`` and refuse an unknown field; validating ``None`` fails,
    so ``null`` is refused where an empty object is not."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Entry[Obj]:
    """One action with its payload type erased into two callables."""

    key: str
    # The arguments, as the JSON Schema an agent consumes.
    schema: dict[str, Any]
    availability: Callable[[Obj], Availability]
    run: Callable[[Obj, Any, Session], str]


type Group[Obj] = list[Entry[Obj]]


def _decode(payload_type: type[BaseModel], raw: Any) -> BaseModel:
    """The one place a validation failure becomes an ``Invalid``. Pydantic's
    ``extra="forbid"`` rejects an unadvertised field, a wrong type or a value
    outside an enum here, so a bad blob is a refusal and not a crash."""
    try:
        return payload_type.model_validate(raw)
    except ValidationError as e:
        raise Invalid(str(e)) from e


def entry_of[Obj](action: type[Action[Obj, Any]]) -> Entry[Obj]:
    payload_type = action.Payload

    def run(obj: Obj, raw: Any, tx: Session) -> str:
        payload = _decode(payload_type, raw)
        return action.run(obj, payload, tx)

    return Entry(
        key=action.KEY,
        schema=payload_type.model_json_schema(),
        availability=action.availability,
        run=run,
    )


def available[Obj](group: Group[Obj], obj: Obj) -> list[tuple[Entry[Obj], Offered]]:
    """What the object offers, in registration order. Refused actions are kept and
    absent ones dropped, so a caller is told both what it can do and what it could
    do but for a reason."""
    offered: list[tuple[Entry[Obj], Offered]] = []
    for entry in group:
        state = entry.availability(obj)
        if state is not None:
            offered.append((entry, state))
    return offered


def dispatch[Obj](group: Group[Obj], obj: Obj, key: str, payload: Any, tx: Session) -> str:
    """The one entry point for a caller holding a key and a blob. Availability is
    not checked here — ``Action.run``, which ``entry_of`` wired in, does it against
    the live object, so what a frontend rendered stays a snapshot. The session is
    the frontend's open one, so a refusal rolls back anything an earlier
    ``execute`` already wrote."""
    for entry in group:
        if entry.key == key:
            return entry.run(obj, payload, tx)
    raise Invalid(f"no action {key!r}")


def name_of(value: object) -> str:
    """The wire name of a value. Our enums carry their wire string as ``.value``
    (``Priority.HIGH.value == "high"``), where ``str(Priority.HIGH)`` would give
    the member name — so a frontend printing a cell reads the value."""
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
