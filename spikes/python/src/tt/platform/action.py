"""What an action is, with no transport in sight.

Nothing in this file mentions JSON. ``Payload`` is whatever Pydantic model the
action names; the JSON edge both frontends talk to is ``wire``, which adds the
schema and the decoder where they belong.

``execute`` is handed the open ``Session`` and writes for itself. There is no
second kind for creation: an ``INSERT`` and an ``UPDATE`` are both just things an
``execute`` body does with the session it holds, so what tells them apart is the
write each one issues, not the type each one returns.
"""

from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel
from sqlalchemy.orm import Session

from tt.platform.error import Conflict


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


# What an object offers for one action. ``None`` is "does not apply to this
# object at all — not offered", which is what lets ``wire.available`` drop the
# absent ones and keep the refused ones with their reason.
type Offered = Runnable | Refused
type Availability = Offered | None


class Checked:
    """Proof that availability was checked, threaded into ``execute``.

    In Rust this is a token whose constructor is private to the action module,
    and in OCaml an ``.mli`` hides ``execute`` outright — either way ``execute``
    is unreachable except through ``run``. Python can seal neither: this marker
    mirrors the shape, but nothing stops a caller building one and calling
    ``execute`` against an object that refused it. The enforcement point is a
    convention here rather than a type, and that gap is the Python finding.
    """

    __slots__ = ()


def _decide(available: bool, disabled: str | None) -> Availability:
    if not available:
        return None
    return Runnable() if disabled is None else Refused(disabled)


def _enforce(key: str, availability: Availability) -> Checked:
    """The enforcement point, as a value: a token comes back only when the object
    allows the write, and a refusal is raised otherwise."""
    match availability:
        case None:
            raise Conflict(f"{key} does not apply")
        case Refused(reason):
            raise Conflict(reason)
        case Runnable():
            return Checked()


class Action[Obj, P: BaseModel]:
    """One action, declared in one place.

    A subclass sets ``KEY`` and ``Payload``, overrides whichever of the two hooks
    it needs, and writes ``execute``. ``availability`` and ``run`` come from here:
    ``run`` is the one path to ``execute``, and it enforces availability against
    the object first.
    """

    KEY: ClassVar[str]
    # The Pydantic model of the arguments, from which ``wire`` derives both the
    # advertised schema and the decoder — so the two cannot name different types.
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
    def execute(cls, obj: Obj, payload: P, tx: Session, checked: Checked) -> str:
        """Do the thing, on the open session, and return the message a frontend
        reports. An edit is an ``UPDATE``, a delete stamps ``deleted_at``, a
        creator ``INSERT``s a different table — and nothing outside the body says
        which."""
        raise NotImplementedError

    @classmethod
    def availability(cls, obj: Obj) -> Availability:
        return _decide(cls.is_available(obj), cls.is_disabled(obj))

    @classmethod
    def run(cls, obj: Obj, payload: P, tx: Session) -> str:
        """Availability is enforced here rather than at the edge, so a caller that
        already holds a payload cannot skip it. ``wire.dispatch`` decodes and then
        comes through here."""
        checked = _enforce(cls.KEY, cls.availability(obj))
        return cls.execute(obj, payload, tx, checked)
