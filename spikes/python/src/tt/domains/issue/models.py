"""An issue, mapped to its table.

``Status`` is stored as text (its member name), ``Priority`` as an integer, so
``ORDER BY priority DESC`` sorts high above normal. Both round-trip through the
column adapters in ``platform.enums``. ``project`` is the relationship a read
loads eagerly; a list row reads its slug off that loaded project rather than a
query per row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tt.domains.issue.enums import Priority, Status
from tt.platform.db import BaseDBModel
from tt.platform.enums import IntEnum, TextEnum

if TYPE_CHECKING:
    from tt.domains.project.models import Project


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
    status: Mapped[Status] = mapped_column(TextEnum(Status))
    priority: Mapped[Priority] = mapped_column(IntEnum(Priority))
    status_note: Mapped[str | None] = mapped_column(Text, default=None)

    project: Mapped[Project] = relationship(back_populates="issues", lazy="raise")

    def subject(self) -> str:
        return f"issue {self.id}"
