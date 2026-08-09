"""An issue's closed vocabularies.

``Status`` is a ``StrEnum``, so its value is already the wire string a payload
serializes to. ``Priority`` is an ``IntEnum`` because it sorts: the column stores
the member's own integer and ``ORDER BY priority DESC`` puts urgent above none,
while the wire keeps ``"medium"``/``"high"`` through the serializer in ``schemas``.
The members ascend, so a larger number is a higher priority.
"""

import enum
from enum import StrEnum, auto


class Status(StrEnum):
    BACKLOG = auto()
    BLOCKED = auto()
    TODO = auto()
    DOING = auto()
    DONE = auto()
    CANCELED = auto()


# The terminal statuses: work that needs no more doing, whether it finished
# (``DONE``) or was abandoned (``CANCELED``). A dependency at one of these no
# longer holds its dependents back. ``CANCELED`` trails ``DONE`` in the member
# order above, so it sorts last wherever status is the ordering key.
CLOSED: frozenset[Status] = frozenset({Status.DONE, Status.CANCELED})

# A per-status tally: how many issues sit at each status. The ``Status`` enum is
# the single source of the vocabulary, so a count-bearing surface names this one
# mapping rather than re-listing the members as parallel scalars. ``dict`` rather
# than ``Mapping`` because pydantic types a field from this alias invariantly — a
# ``Mapping`` return would not be assignable to the ``dict`` field.
type StatusCounts = dict[Status, int]


class Priority(enum.IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    URGENT = 4


class SortField(StrEnum):
    """The columns a list of issues can be ordered by. ``STATUS`` sorts by the
    workflow order of ``Status`` above, not the alphabetical order of the member
    names the column stores."""

    PRIORITY = auto()
    CREATED = auto()
    UPDATED = auto()
    DUE = auto()
    STATUS = auto()


class Direction(StrEnum):
    ASC = auto()
    DESC = auto()
