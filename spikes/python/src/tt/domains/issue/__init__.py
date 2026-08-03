"""An issue, always read through the project it belongs to.

``project_slug`` comes from the read rather than from a column — it is never
written, and it is what makes a list row printable without a second query per
row. ``services`` selects it alongside the issue's own columns.

``Status`` is a text column, so its members' values are what SQLite stores.
``Priority`` is not: the column is an ``INTEGER`` so ``ORDER BY priority DESC``
sorts by rank, while the wire and the schema use ``"normal"``/``"high"``. That
one type with two representations is where ``services`` has to map — a ``match``
that is exhaustive over the type, which is the cost the wire-name enums do not
have.
"""

from dataclasses import dataclass
from enum import StrEnum


class Status(StrEnum):
    TODO = "todo"
    DOING = "doing"
    DONE = "done"


class Priority(StrEnum):
    NORMAL = "normal"
    HIGH = "high"

    @property
    def rank(self) -> int:
        """The integer the column stores and ``ORDER BY`` sorts on."""
        match self:
            case Priority.NORMAL:
                return 0
            case Priority.HIGH:
                return 1

    @staticmethod
    def from_rank(rank: int) -> "Priority":
        match rank:
            case 0:
                return Priority.NORMAL
            case 1:
                return Priority.HIGH
            case _:
                raise ValueError(f"unknown priority rank {rank}")


@dataclass(frozen=True)
class Issue:
    id: int
    project_id: int
    #: From the join. No action writes it.
    project_slug: str
    title: str
    body: str
    status: Status
    priority: Priority
    status_note: str | None
    created_at: str
    updated_at: str

    def subject(self) -> str:
        return f"issue {self.id}"


@dataclass(frozen=True)
class Draft:
    """What a creator hands the store to turn into a row. No ``id`` and no stamps:
    those are the store's to assign."""

    project_id: int
    title: str
    body: str
    priority: Priority
