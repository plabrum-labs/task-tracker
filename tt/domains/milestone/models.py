"""A milestone, and the issues under it.

A milestone is a dated checkpoint inside an epic: a title and an optional
``due_date``, and nothing else it stores. It has no status column — its progress
is derived, read straight off the loaded issues the way an epic's and a project's
counts are, so a hook that reads them runs after the session is gone and anything
it reads has to be on the object.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tt.domains.issue.enums import Status as IssueStatus
from tt.platform.db import BaseDBModel

if TYPE_CHECKING:
    from tt.domains.epic.models import Epic
    from tt.domains.issue.models import Issue


class Milestone(BaseDBModel):
    __tablename__ = "milestones"
    __table_args__ = (
        Index(
            "milestones_by_epic",
            "epic_id",
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    epic_id: Mapped[int] = mapped_column(ForeignKey("epics.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date, default=None)

    epic: Mapped[Epic] = relationship(lazy="raise")
    issues: Mapped[list[Issue]] = relationship(back_populates="milestone", lazy="raise")

    @property
    def live_issues(self) -> list[Issue]:
        """The loaded issues that are not themselves deleted — what the derived
        counts are over, so a soft-deleted issue drops out of its milestone's
        tally."""
        return [issue for issue in self.issues if issue.deleted_at is None]

    @property
    def backlog(self) -> int:
        return sum(1 for issue in self.live_issues if issue.status is IssueStatus.BACKLOG)

    @property
    def planning(self) -> int:
        return sum(1 for issue in self.live_issues if issue.status is IssueStatus.REQUIRES_PLANNING)

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
        return f"milestone {self.id}"
