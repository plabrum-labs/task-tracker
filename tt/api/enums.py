"""The client contract's closed vocabularies.

These mirror the domains' internal enums by value but are owned here: the public
API versions its own vocabulary so an internal change — the sorting ``Priority``
is an ``IntEnum`` inside the domain, this one is not — cannot leak through the
contract. Every member's value is the wire string the domain already renders
(``member.name.lower()``), so mapping a read is a by-value construction.
"""

from enum import StrEnum, auto


class IssueStatus(StrEnum):
    BACKLOG = auto()
    BLOCKED = auto()
    TODO = auto()
    DOING = auto()
    DONE = auto()
    CANCELED = auto()


class EpicStatus(StrEnum):
    ACTIVE = auto()
    DONE = auto()


class Priority(StrEnum):
    NONE = auto()
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()
    URGENT = auto()
