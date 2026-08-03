"""An issue, mapped to its table.

``Status`` is stored as text, so its members' values are what SQLite holds.
``Priority`` is not: the column is an ``INTEGER`` so ``ORDER BY priority DESC``
sorts high above normal, while the wire and every other surface use
``"normal"``/``"high"``. ``PriorityRank`` is the one place that mapping lives.

``project`` is the relationship a read loads eagerly; ``project_slug`` reads it,
so a list row is printable without a query per row. It is never a column and no
action writes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Dialect, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from tt.platform.models import BaseDBModel
from tt.platform.types import EnumText

if TYPE_CHECKING:
    from tt.domains.project.models import Project


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
    def from_rank(rank: int) -> Priority:
        match rank:
            case 0:
                return Priority.NORMAL
            case 1:
                return Priority.HIGH
            case _:
                raise ValueError(f"unknown priority rank {rank}")


class PriorityRank(TypeDecorator[Priority]):
    """``Priority`` stored as its rank, so the column sorts by importance."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: Priority | None, dialect: Dialect) -> int | None:
        return None if value is None else value.rank

    def process_result_value(self, value: int | None, dialect: Dialect) -> Priority | None:
        return None if value is None else Priority.from_rank(value)


class Issue(BaseDBModel):
    __tablename__ = "issues"
    __table_args__ = (
        Index(
            "issues_by_project",
            "project_id",
            "status",
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[Status] = mapped_column(EnumText(Status))
    priority: Mapped[Priority] = mapped_column(PriorityRank)
    status_note: Mapped[str | None] = mapped_column(Text, default=None)

    project: Mapped[Project] = relationship(back_populates="issues", lazy="raise")

    @property
    def project_slug(self) -> str:
        """From the loaded ``project``. Never a column; no action writes it."""
        return self.project.slug

    def subject(self) -> str:
        return f"issue {self.id}"


@dataclass(frozen=True)
class Draft:
    """What ``addIssue`` hands the store to turn into a row. No id and no stamps:
    those are the store's to assign, and the project comes from the action's own
    object rather than the draft."""

    title: str
    body: str
    priority: Priority
