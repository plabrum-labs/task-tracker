"""A project, and the issues under it.

The counts are the reason ``issues`` is a relationship and a read loads it
eagerly: ``editStatus`` and ``delete`` both refuse on them, and an availability
hook is a pure function of the object, so anything a hook reads has to be on the
object. Counting the loaded issues is what stops a list from offering a menu it
cannot justify — and it is the join the domain used to fake by importing the
issue store.

The partial unique index on ``slug`` covers live rows only, which is what makes a
slug reusable once its project is deleted and the guarantee behind
``createProject``'s refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tt.domains.issue.models import Issue
from tt.domains.issue.models import Status as IssueStatus
from tt.platform.deleted import Deleted
from tt.platform.models import BaseDBModel
from tt.platform.types import EnumText


class Status(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Project(BaseDBModel):
    __tablename__ = "projects"
    __table_args__ = (
        Index("projects_slug_live", "slug", unique=True, sqlite_where=text("deleted_at IS NULL")),
    )

    slug: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[Status] = mapped_column(EnumText(Status))

    issues: Mapped[list[Issue]] = relationship(
        back_populates="project",
        lazy="raise",
        cascade="all, delete-orphan",
        order_by="Issue.priority.desc(), Issue.created_at",
    )

    @property
    def live_issues(self) -> list[Issue]:
        """The loaded issues that are not themselves deleted — what the counts are
        over, so a soft-deleted issue drops out of its project's tally."""
        return [issue for issue in self.issues if issue.deleted_at is None]

    @property
    def todo(self) -> int:
        return sum(1 for issue in self.live_issues if issue.status is IssueStatus.TODO)

    @property
    def doing(self) -> int:
        return sum(1 for issue in self.live_issues if issue.status is IssueStatus.DOING)

    @property
    def done(self) -> int:
        return sum(1 for issue in self.live_issues if issue.status is IssueStatus.DONE)

    def issue_count(self) -> int:
        return len(self.live_issues)

    def subject(self) -> str:
        return f"project {self.slug}"


@dataclass(frozen=True)
class Draft:
    slug: str
    title: str
    body: str


@dataclass(frozen=True)
class Restorable:
    """What ``restore`` is offered against.

    The trash row alone is not enough: the partial unique index covers live rows
    only, so a slug freed by a delete can be taken again and bringing the old
    project back would then collide. A hook is a pure function of its object, so
    the live list it must read is carried on the object — the same reason a
    ``Project`` carries its counts. Without this the refusal is a UNIQUE
    constraint violation with no sentence in it.
    """

    deleted: Deleted[Project]
    live: list[Project]
