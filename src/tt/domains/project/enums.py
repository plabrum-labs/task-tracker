"""A project's closed vocabulary.

``Status`` is a ``StrEnum``, so its value is already the wire string a payload
serializes to and a frontend prints.
"""

from enum import StrEnum, auto


class Status(StrEnum):
    ACTIVE = auto()
    ARCHIVED = auto()
