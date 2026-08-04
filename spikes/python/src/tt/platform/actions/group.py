"""A group of actions over one kind of object, and the dispatch through it.

A group is created with a name and a ``locate`` — the one callable that turns an
address into the live row an action runs against, ``queries.get_project`` for a
project or ``queries.get_issue`` for an issue, so slug-versus-int lives in one place and
not in every write. Each action registers by decorating itself with the group.

``trigger`` is the single write path: it finds the action by key, and for an
``ObjectAction`` requires an address and loads the row, for a ``TopLevelAction``
runs with no object. Decode-then-run is here, availability is enforced inside
``run``, and the flush is here, once. ``offers`` and ``top_level_offers`` are the
read side a menu draws, and ``action_schemas`` is what the CLI grows a subcommand
from.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from sqlalchemy.orm import Session

from tt.platform.actions.base import (
    ActionResponse,
    Invalid,
    ObjectAction,
    Offer,
    Runnable,
    TopLevelAction,
)
from tt.platform.actions.deps import ActionDeps
from tt.platform.actions.form import Field, decode, fields_of
from tt.platform.actions.registry import ActionRegistry


class ActionGroup[Obj]:
    def __init__(self, name: str, locate: Callable[[Session, Any], Obj | None]) -> None:
        self.name = name
        self.locate = locate
        self.registry = ActionRegistry()
        self._object_actions: list[type[ObjectAction[Obj, Any]]] = []
        self._top_level_actions: list[type[TopLevelAction[Any]]] = []

    def __call__[A: type[ObjectAction[Any, Any]] | type[TopLevelAction[Any]]](
        self, action_cls: A
    ) -> A:
        """Register an action by decorating its class with the group."""
        self.registry.register(self, action_cls)
        if issubclass(action_cls, ObjectAction):
            self._object_actions.append(action_cls)
        elif issubclass(action_cls, TopLevelAction):
            self._top_level_actions.append(action_cls)
        return action_cls

    def trigger(
        self, deps: ActionDeps, key: str, raw: Any, address: Any | None = None
    ) -> ActionResponse:
        """Decode against the action the key names, then run it. Availability is
        enforced inside ``run`` against the live row, so what a ``show`` reported
        stays a snapshot. The flush is here, once."""
        entry = self.registry.get(key)
        if entry is None:
            raise Invalid(f"no action {key!r}")
        _, action_cls = entry
        if issubclass(action_cls, ObjectAction):
            if address is None:
                raise Invalid(f"{key} needs {self.name} to run against")
            obj = self.locate(deps.tx, address)
            if obj is None:
                raise Invalid(f"no {self.name} {address!r}")
            object_action = cast("type[ObjectAction[Obj, Any]]", action_cls)
            response = object_action.run(obj, decode(object_action, raw), deps)
        else:
            top_level = cast("type[TopLevelAction[Any]]", action_cls)
            response = top_level.run(decode(top_level, raw), deps)
        deps.tx.flush()
        return response

    def offers(self, obj: Obj) -> list[Offer]:
        """What the object offers, in registration order. Refused actions are kept
        and absent ones dropped, so a caller is told both what it can do and what
        it could do but for a reason."""
        result: list[Offer] = []
        for action in self._object_actions:
            state = action.availability(obj)
            if state is not None:
                result.append(
                    Offer(
                        key=action.KEY,
                        label=action.LABEL,
                        state=state,
                        fields=fields_of(action.Payload),
                    )
                )
        return result

    def top_level_offers(self) -> list[Offer]:
        """What the group offers with no object — the creators. A create refuses
        nothing at menu time (its uniqueness is read in ``execute``), so each is
        runnable here."""
        return [
            Offer(
                key=action.KEY,
                label=action.LABEL,
                state=Runnable(),
                fields=fields_of(action.Payload),
            )
            for action in self._top_level_actions
        ]

    def action_schemas(self) -> list[tuple[str, list[Field]]]:
        """Every object-action key, with the fields its payload asks for, for the
        CLI to grow an address-taking subcommand per action. A top-level action has
        no address, so it is driven through the raw ``action`` path rather than
        generated here."""
        return [(action.KEY, fields_of(action.Payload)) for action in self._object_actions]
