"""A comment, mapped to its table.

A comment is the thinnest owned child of an issue: a ``body`` and the timestamps
every row carries, and nothing else it stores. It has no status and no author —
this is a single-user tracker. ``issue`` is the relationship a read loads, one
level up, so a comment's liveness can be gated on its issue's the way a
milestone's is gated on its epic's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tt.platform.db import BaseDBModel

if TYPE_CHECKING:
    from tt.domains.issue.models import Issue


class Comment(BaseDBModel):
    __tablename__ = "comments"
    __table_args__ = (
        Index(
            "comments_by_issue",
            "issue_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text)

    issue: Mapped[Issue] = relationship(back_populates="comments", lazy="raise")

    def subject(self) -> str:
        return f"comment {self.id}"
