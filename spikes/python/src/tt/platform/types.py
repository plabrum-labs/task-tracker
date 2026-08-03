"""Column types the domains store their enums through.

An enum has a wire name and a stored form, and the mapping between them belongs
next to neither the frontend nor the query. ``EnumText`` stores a ``StrEnum`` as
the text of its value — the same string the wire uses — so a column round-trips
to the member without a domain mapping the two by hand.
"""

from enum import StrEnum

from sqlalchemy import Dialect, Text
from sqlalchemy.types import TypeDecorator


class EnumText[E: StrEnum](TypeDecorator[E]):
    """A ``StrEnum`` stored as the text of its value.

    ``Priority`` is the exception a domain declares for itself: it sorts by rank,
    so its column is an integer, not this.
    """

    impl = Text
    cache_ok = True

    def __init__(self, enum_cls: type[E]) -> None:
        super().__init__()
        self._enum_cls = enum_cls

    def process_bind_param(self, value: E | None, dialect: Dialect) -> str | None:
        return None if value is None else value.value

    def process_result_value(self, value: str | None, dialect: Dialect) -> E | None:
        return None if value is None else self._enum_cls(value)
