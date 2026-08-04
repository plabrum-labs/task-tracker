"""What an action is, and the small vocabulary around it.

An action is typed and knows nothing about JSON. It names a ``KEY``, a ``LABEL``
and a ``Payload`` model, overrides whichever availability hooks it needs, and
writes ``execute`` — an ``INSERT`` and an ``UPDATE`` are both just things a body
does with the deps it holds, so what tells them apart is the write each one
issues, not the type each one returns.

The split into two bases is the shape worth keeping: an ``ObjectAction`` runs
against a row a group loaded, so its hooks and ``execute`` are given the object; a
``TopLevelAction`` (create) has no object to address, so its hooks read
``deps`` and its ``execute`` queries for itself. Both derive ``availability`` and
``run`` here: ``run`` is the one path to ``execute``, and it enforces availability
first, so a caller holding a payload cannot skip the check.

Everything a frontend needs to drive an action without naming it lives here too:
the two refusals it can raise, the ``Empty`` payload, and the ``Offer`` an object
makes for an action it can — or cannot yet — run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict

from tt.platform.actions.deps import ActionDeps

if TYPE_CHECKING:
    from tt.platform.actions.form import Field


# --- refusals -------------------------------------------------------------


class Invalid(Exception):
    """The request does not make sense — a bad payload, an unknown key or id."""


class Conflict(Exception):
    """The request makes sense; the row says no."""


# The pair a frontend catches to turn a refusal into an exit code or a status
# line. A ``SQLAlchemyError`` is deliberately not in here: it is the machine
# failing, not the object refusing, and it propagates as a crash.
REFUSALS = (Invalid, Conflict)


# --- the response an action returns ---------------------------------------


@dataclass(frozen=True)
class ActionResponse:
    """What ``execute`` returns: the message a frontend reports, and the id of a
    row a creator made — ``None`` for an edit or a delete, which make nothing."""

    message: str
    created_id: int | None = None


# --- availability ---------------------------------------------------------


@dataclass(frozen=True)
class Runnable:
    """The action applies and can run now."""


@dataclass(frozen=True)
class Refused:
    """The action applies but is refused right now, and says why.

    The reason is per-object, not a fixed literal: ``editStatus`` says "finish or
    drop 3 issues first" and the 3 is this project's count.
    """

    reason: str


# What an object offers for one action. ``None`` is "does not apply to this object
# at all — not offered", which is what lets a group's ``offers`` drop the absent
# ones and keep the refused ones with their reason.
type Offered = Runnable | Refused
type Availability = Offered | None


# --- payload decoding -----------------------------------------------------


class Empty(BaseModel):
    """The arguments of an action that has none. ``extra="forbid"`` makes the
    decoder accept ``{}`` and refuse an unknown field; validating ``None`` fails,
    so ``null`` is refused where an empty object is not."""

    model_config = ConfigDict(extra="forbid")


# --- what an object offers -------------------------------------------------


@dataclass(frozen=True)
class Offer:
    """One action an object offers: its key, its label, whether it is runnable or
    refused, and the fields its payload asks for. The whole of what a person
    picking an action and an agent driving the CLI both need, with the payload
    type erased."""

    key: str
    label: str
    state: Offered
    fields: list[Field]


# --- the two actions ------------------------------------------------------


class ObjectAction[Obj, P: BaseModel]:
    """One action against a row a group loaded.

    A subclass sets ``KEY``, ``LABEL`` and ``Payload``, overrides whichever of the
    two hooks it needs, and writes ``execute``. ``availability`` and ``run`` come
    from here: ``run`` enforces availability against the object first, so a caller
    holding a payload cannot skip the check.
    """

    KEY: ClassVar[str]
    LABEL: ClassVar[str]
    Payload: ClassVar[type[BaseModel]]

    @classmethod
    def is_available(cls, obj: Obj) -> bool:
        """False when the action does not apply to this object at all."""
        return True

    @classmethod
    def is_disabled(cls, obj: Obj) -> str | None:
        """The reason it applies but cannot run right now, or ``None``."""
        return None

    @classmethod
    def execute(cls, obj: Obj, payload: P, deps: ActionDeps) -> ActionResponse:
        """Do the thing, on the deps it holds, and return what a frontend reports.
        An edit is an ``UPDATE``, a delete stamps ``deleted_at`` — and nothing
        outside the body says which."""
        raise NotImplementedError

    @classmethod
    def availability(cls, obj: Obj) -> Availability:
        if not cls.is_available(obj):
            return None
        disabled = cls.is_disabled(obj)
        return Runnable() if disabled is None else Refused(disabled)

    @classmethod
    def run(cls, obj: Obj, payload: P, deps: ActionDeps) -> ActionResponse:
        match cls.availability(obj):
            case None:
                raise Conflict(f"{cls.KEY} does not apply")
            case Refused(reason):
                raise Conflict(reason)
            case Runnable():
                return cls.execute(obj, payload, deps)


class TopLevelAction[P: BaseModel]:
    """One action with no object to address — a creator.

    Its hooks read ``deps`` rather than a row, because there is no row yet, and its
    ``execute`` queries through ``deps.tx`` for whatever it needs — the uniqueness
    a create refuses is read there, not off a parent it was handed.
    """

    KEY: ClassVar[str]
    LABEL: ClassVar[str]
    Payload: ClassVar[type[BaseModel]]

    @classmethod
    def is_available(cls, deps: ActionDeps) -> bool:
        return True

    @classmethod
    def is_disabled(cls, deps: ActionDeps) -> str | None:
        return None

    @classmethod
    def execute(cls, payload: P, deps: ActionDeps) -> ActionResponse:
        raise NotImplementedError

    @classmethod
    def availability(cls, deps: ActionDeps) -> Availability:
        if not cls.is_available(deps):
            return None
        disabled = cls.is_disabled(deps)
        return Runnable() if disabled is None else Refused(disabled)

    @classmethod
    def run(cls, payload: P, deps: ActionDeps) -> ActionResponse:
        match cls.availability(deps):
            case None:
                raise Conflict(f"{cls.KEY} does not apply")
            case Refused(reason):
                raise Conflict(reason)
            case Runnable():
                return cls.execute(payload, deps)
