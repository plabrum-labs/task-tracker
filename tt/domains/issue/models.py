"""An issue, mapped to its table.

``Status`` is stored as text (its member name), ``Priority`` as an integer, so
``ORDER BY priority DESC`` sorts high above normal. Both round-trip through the
column adapters in ``platform.enums``. ``project`` is the relationship a read
loads eagerly; a list row reads its slug off that loaded project rather than a
query per row.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    Date,
    ForeignKey,
    Index,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tt.domains.issue.enums import Priority, Status
from tt.domains.tag.models import Tag, issue_tags
from tt.platform.db import BaseDBModel
from tt.platform.enums import IntEnum, TextEnum

if TYPE_CHECKING:
    from tt.domains.comment.models import Comment
    from tt.domains.epic.models import Epic
    from tt.domains.milestone.models import Milestone
    from tt.domains.project.models import Project


# A blocking edge between two issues of the same project: ``blocker`` must be done
# before ``blocked`` can start. Issue-internal, so it lives here rather than in the
# tag module ``issue_tags`` sits in; a plain two-column link with no lifecycle of
# its own, cascaded away when either endpoint is hard-deleted.
issue_dependencies = Table(
    "issue_dependencies",
    BaseDBModel.metadata,
    Column("blocker_id", ForeignKey("issues.id", ondelete="CASCADE"), primary_key=True),
    Column("blocked_id", ForeignKey("issues.id", ondelete="CASCADE"), primary_key=True),
)


class Issue(BaseDBModel):
    __tablename__ = "issues"
    __table_args__ = (
        Index(
            "issues_by_project",
            "project_id",
            "status",
            sqlite_where=text("deleted_at IS NULL"),
        ),
        # A number is permanent and never reissued — the sequence only rises — so
        # its uniqueness within a project holds over deleted rows too, not just live
        # ones, and this is a plain constraint rather than a partial index.
        UniqueConstraint("project_id", "number", name="uq_issues_project_number"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The project-scoped sequence number, drawn from ``platform.sequences`` at
    # creation. An issue is addressed and shown as ``<project slug>-<number>``, the
    # ``ref`` below — not by the global ``id`` it happens to carry.
    number: Mapped[int] = mapped_column()
    # An epic is optional and lives under the same project; on an epic's hard delete
    # the link falls to null. A live issue never points at a deleted epic, though —
    # ``epic.delete`` refuses while it still holds live issues.
    epic_id: Mapped[int | None] = mapped_column(
        ForeignKey("epics.id", ondelete="SET NULL"), index=True, default=None
    )
    # A milestone is optional and lives under the issue's epic; the edit refuses one
    # from a different epic, and clears a now-stale one when the epic changes. On a
    # milestone's hard delete the link falls to null.
    milestone_id: Mapped[int | None] = mapped_column(
        ForeignKey("milestones.id", ondelete="SET NULL"), index=True, default=None
    )
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[Status] = mapped_column(TextEnum(Status))
    priority: Mapped[Priority] = mapped_column(IntEnum(Priority))
    due_date: Mapped[date | None] = mapped_column(Date, default=None)

    project: Mapped[Project] = relationship(back_populates="issues", lazy="raise")
    epic: Mapped[Epic | None] = relationship(back_populates="issues", lazy="raise")
    milestone: Mapped[Milestone | None] = relationship(back_populates="issues", lazy="raise")
    # Global, cross-cutting, and the one non-exclusive axis: an issue wears any
    # number of tags, and a tag any number of issues, through ``issue_tags``.
    tags: Mapped[list[Tag]] = relationship(secondary=issue_tags, lazy="raise")
    # The thread of dated notes under the issue. Owned children: a comment lives
    # and dies with its issue, and is created, edited and deleted through the
    # comment domain.
    comments: Mapped[list[Comment]] = relationship(back_populates="issue", lazy="raise")
    # The blocking graph, self-referential over ``issue_dependencies`` and split by
    # which end of the edge this issue sits on. ``blocked_by`` is the issues that
    # block this one (this row is ``blocked_id``); ``blocks`` is the issues this one
    # blocks (this row is ``blocker_id``). Same-project, no self-edge and no cycles
    # are the actions' rules, not the mapping's. The two map the same table from
    # opposite ends, so exactly one may own the writes: an edge is added and removed
    # through ``blocked_by``, and ``blocks`` is the read-only mirror.
    blocked_by: Mapped[list[Issue]] = relationship(
        secondary=issue_dependencies,
        primaryjoin=lambda: Issue.id == issue_dependencies.c.blocked_id,
        secondaryjoin=lambda: Issue.id == issue_dependencies.c.blocker_id,
        lazy="raise",
    )
    blocks: Mapped[list[Issue]] = relationship(
        secondary=issue_dependencies,
        primaryjoin=lambda: Issue.id == issue_dependencies.c.blocker_id,
        secondaryjoin=lambda: Issue.id == issue_dependencies.c.blocked_id,
        viewonly=True,
        lazy="raise",
    )

    @property
    def ref(self) -> str:
        """How the issue is addressed and shown: its project's slug and its
        project-scoped number, ``ENG-12``. Reads the eagerly-loaded project."""
        return f"{self.project.slug}-{self.number}"

    def subject(self) -> str:
        return f"issue {self.ref}"
