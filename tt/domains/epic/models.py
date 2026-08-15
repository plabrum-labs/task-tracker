"""An epic, and the issues under it.

An epic hangs off a permanent project and carries its own ``status`` — the
completable lifecycle a project has no column for. Its progress is derived, never
stored: the counts read straight off the loaded issues the way a project's do, so
a hook that reads them runs after the session is gone and anything it reads has to
be on the object. ``due_date`` is the optional deadline the whole-object edit and
the focused ``setDueDate`` both carry.

An epic is addressed by its ``title`` within its project, not by a ref, so the
title is unique among a project's live epics — the partial index below.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tt.domains.epic.enums import Status
from tt.domains.issue.models import IssueContainer
from tt.platform.db import BaseDBModel
from tt.platform.enums import TextEnum

if TYPE_CHECKING:
    from tt.domains.issue.models import Issue
    from tt.domains.project.models import Project


class Epic(IssueContainer, BaseDBModel):
    __tablename__ = "epics"
    __table_args__ = (
        Index(
            "epics_by_project",
            "project_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # An epic is addressed by title within its project, so the title is unique
        # among a project's live epics; a deleted title is free to reuse.
        Index(
            "epics_title_live",
            "project_id",
            "title",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[Status] = mapped_column(TextEnum(Status))
    due_date: Mapped[date | None] = mapped_column(Date, default=None)

    project: Mapped[Project] = relationship(lazy="raise")
    issues: Mapped[list[Issue]] = relationship(back_populates="epic", lazy="raise")

    def subject(self) -> str:
        return f'epic "{self.title}"'
