"""An epic's closed vocabulary.

``Status`` is a ``StrEnum``, so its value is already the wire string a payload
serializes to and a frontend prints. Unlike a project, an epic completes: ``DONE``
is the lifecycle a project can never reach, which is the reason an epic exists.
"""

from enum import StrEnum, auto


class Status(StrEnum):
    ACTIVE = auto()
    DONE = auto()
