"""A flat key → action map, so a group can find an action by the key a blob names.

One registry per group. It holds the ``(group, action_cls)`` for every key the
group registers, which is what ``trigger`` looks up to decode against the right
payload and run against the right object. The group is carried alongside the
class so the map is enough on its own to route a key, without the group having to
re-scan its own lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tt.platform.actions.base import ObjectAction, TopLevelAction
    from tt.platform.actions.group import ActionGroup

type AnyAction = type[ObjectAction[Any, Any]] | type[TopLevelAction[Any]]


@dataclass
class ActionRegistry:
    """``key -> (group, action_cls)`` for every action a group registers."""

    _by_key: dict[str, tuple[ActionGroup[Any], AnyAction]] = field(default_factory=dict)

    def register(self, group: ActionGroup[Any], action_cls: AnyAction) -> None:
        self._by_key[action_cls.KEY] = (group, action_cls)

    def get(self, key: str) -> tuple[ActionGroup[Any], AnyAction] | None:
        return self._by_key.get(key)
