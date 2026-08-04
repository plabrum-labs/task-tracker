"""A project, and the issues under it.

The counts are the reason ``issues`` is a relationship and a read loads it
eagerly: ``editStatus`` and ``delete`` both refuse on them, and an availability
hook is a pure function of the object, so anything a hook reads has to be on the
object. Counting the loaded issues is what stops a list from offering a menu it
cannot justify.

The partial unique index on ``slug`` covers live rows only, which is what makes a
slug reusable once its project is deleted and the guarantee behind
``createProject``'s refusal. ``path`` is the working directory a project owns, so
bare ``tt`` can open the project the shell is standing in; its own live-only unique
index is what a duplicate-path refusal reads. A null ``path`` is unbound, and
SQLite treats nulls as distinct, so any number of projects may have none.
"""

from __future__ import annotations

from sqlalchemy import Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tt.domains.issue.enums import Status as IssueStatus
from tt.domains.issue.models import Issue
from tt.domains.project.enums import Status
from tt.platform.db import BaseDBModel
from tt.platform.enums import TextEnum


class Project(BaseDBModel):
    __tablename__ = "projects"
    __table_args__ = (
        Index("projects_slug_live", "slug", unique=True, sqlite_where=text("deleted_at IS NULL")),
        Index("projects_path_live", "path", unique=True, sqlite_where=text("deleted_at IS NULL")),
    )

    slug: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[Status] = mapped_column(TextEnum(Status))
    path: Mapped[str | None] = mapped_column(Text, default=None)

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
        return f"project {self.slug}"
