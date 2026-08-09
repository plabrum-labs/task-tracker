"""An epic, and the issues under it.

An epic hangs off a permanent project and carries its own ``status`` — the
completable lifecycle a project has no column for. Its progress is derived, never
stored: the counts read straight off the loaded issues the way a project's do, so
a hook that reads them runs after the session is gone and anything it reads has to
be on the object. ``due_date`` is the optional deadline the whole-object edit and
the focused ``setDueDate`` both carry.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tt.domains.epic.enums import Status
from tt.domains.issue.enums import Status as IssueStatus
from tt.platform.db import BaseDBModel
from tt.platform.enums import TextEnum

if TYPE_CHECKING:
    from tt.domains.issue.models import Issue
    from tt.domains.project.models import Project


class Epic(BaseDBModel):
    __tablename__ = "epics"
    __table_args__ = (
        Index(
            "epics_by_project",
            "project_id",
            "status",
            sqlite_where=text("deleted_at IS NULL"),
        ),
        # Permanent and never reissued, so unique over deleted rows too — see the
        # note on ``Issue``.
        UniqueConstraint("project_id", "number", name="uq_epics_project_number"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The project-scoped sequence number, drawn at creation. An epic is addressed
    # and shown as ``<project slug>-<number>`` — the ``ref`` below.
    number: Mapped[int] = mapped_column()
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[Status] = mapped_column(TextEnum(Status))
    due_date: Mapped[date | None] = mapped_column(Date, default=None)

    project: Mapped[Project] = relationship(lazy="raise")
    issues: Mapped[list[Issue]] = relationship(back_populates="epic", lazy="raise")

    @property
    def live_issues(self) -> list[Issue]:
        """The loaded issues that are not themselves deleted — what the derived
        counts are over, so a soft-deleted issue drops out of its epic's tally."""
        return [issue for issue in self.issues if issue.deleted_at is None]

    @property
    def backlog(self) -> int:
        return sum(1 for issue in self.live_issues if issue.status is IssueStatus.BACKLOG)

    @property
    def blocked(self) -> int:
        return sum(1 for issue in self.live_issues if issue.status is IssueStatus.BLOCKED)

    @property
    def todo(self) -> int:
        return sum(1 for issue in self.live_issues if issue.status is IssueStatus.TODO)

    @property
    def doing(self) -> int:
        return sum(1 for issue in self.live_issues if issue.status is IssueStatus.DOING)

    @property
    def done(self) -> int:
        return sum(1 for issue in self.live_issues if issue.status is IssueStatus.DONE)

    @property
    def canceled(self) -> int:
        return sum(1 for issue in self.live_issues if issue.status is IssueStatus.CANCELED)

    def issue_count(self) -> int:
        return len(self.live_issues)

    @property
    def ref(self) -> str:
        """How the epic is addressed and shown: its project's slug and its
        project-scoped number, ``ENG-3``. Reads the eagerly-loaded project."""
        return f"{self.project.slug}-{self.number}"

    def subject(self) -> str:
        return f"epic {self.ref}"
