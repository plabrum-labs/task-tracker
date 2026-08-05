"""An issue's closed vocabularies.

``Status`` is a ``StrEnum``, so its value is already the wire string a payload
serializes to. ``Priority`` is an ``IntEnum`` because it sorts: the column stores
the member's own integer and ``ORDER BY priority DESC`` puts high above normal,
while the wire keeps ``"normal"``/``"high"`` through the serializer in ``schemas``.
"""

import enum
from enum import StrEnum, auto


class Status(StrEnum):
    BACKLOG = auto()
    REQUIRES_PLANNING = auto()
    TODO = auto()
    DOING = auto()
    DONE = auto()


class Priority(enum.IntEnum):
    NORMAL = 1
    HIGH = 2
