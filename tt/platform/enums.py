"""Column types the domains store their enums through.

An enum has a wire name and a stored form, and the mapping between them belongs
next to neither the frontend nor the query. ``TextEnum`` stores an enum as the
text of its member name; ``IntEnum`` stores an ``IntEnum`` as its integer, so a
column can sort by importance while every other surface stays human-readable.
Both convert on the way in and out, so a column round-trips to the member without
a domain mapping the two by hand.
"""

import enum
from enum import Enum

from sqlalchemy import Dialect, Integer, Text
from sqlalchemy.types import TypeDecorator


class TextEnum[E: Enum](TypeDecorator[E]):
    """An enum stored as the text of its member name."""

    impl = Text
    cache_ok = True

    def __init__(self, enum_cls: type[E]) -> None:
        super().__init__()
        self._enum_cls = enum_cls

    def process_bind_param(self, value: E | None, dialect: Dialect) -> str | None:
        return None if value is None else value.name

    def process_result_value(self, value: str | None, dialect: Dialect) -> E | None:
        return None if value is None else self._enum_cls[value]


class IntEnum[E: enum.IntEnum](TypeDecorator[E]):
    """An ``IntEnum`` stored as its integer, so the column sorts by rank.

    ``Priority`` is the one that needs this: ``ORDER BY priority DESC`` sorts high
    above normal because the stored value is the member's own integer.
    """

    impl = Integer
    cache_ok = True

    def __init__(self, enum_cls: type[E]) -> None:
        super().__init__()
        self._enum_cls = enum_cls

    def process_bind_param(self, value: E | None, dialect: Dialect) -> int | None:
        return None if value is None else int(value)

    def process_result_value(self, value: int | None, dialect: Dialect) -> E | None:
        return None if value is None else self._enum_cls(value)
