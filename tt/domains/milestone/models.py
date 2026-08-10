"""A milestone, and the issues under it.

A milestone is a dated checkpoint on a project: a title and an optional
``due_date``, and nothing else it stores. It has no status column — its progress
is derived, read straight off the loaded issues the way an epic's and a project's
counts are, so a hook that reads them runs after the session is gone and anything
it reads has to be on the object.

A milestone belongs to a project, not an epic: ``epic_id`` is an optional label,
so a milestone can span several epics or none. It is addressed by its ``title``
within its project, not by a ref, so the title is unique among a project's live
milestones — the partial index below.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tt.domains.issue.models import IssueContainer
from tt.platform.db import BaseDBModel

if TYPE_CHECKING:
    from tt.domains.epic.models import Epic
    from tt.domains.issue.models import Issue
    from tt.domains.project.models import Project


class Milestone(IssueContainer, BaseDBModel):
    __tablename__ = "milestones"
    __table_args__ = (
        Index(
            "milestones_by_project",
            "project_id",
            sqlite_where=text("deleted_at IS NULL"),
        ),
        # A milestone is addressed by title within its project, so the title is unique
        # among a project's live milestones; a deleted title is free to reuse.
        Index(
            "milestones_title_live",
            "project_id",
            "title",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The optional epic this milestone is filed under. A milestone is project-level,
    # so the epic is a label, not a parent; it is left CASCADE because an epic only
    # ever soft-deletes (``deleted_at``), so the constraint never fires, and a reader
    # that surfaces the epic drops a soft-deleted one rather than the row cascading.
    epic_id: Mapped[int | None] = mapped_column(
        ForeignKey("epics.id", ondelete="CASCADE"), index=True, default=None
    )
    title: Mapped[str] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date, default=None)

    epic: Mapped[Epic | None] = relationship(lazy="raise")
    project: Mapped[Project] = relationship(lazy="raise")
    issues: Mapped[list[Issue]] = relationship(back_populates="milestone", lazy="raise")

    def subject(self) -> str:
        return f'milestone "{self.title}"'
