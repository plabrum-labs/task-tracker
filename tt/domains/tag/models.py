"""A tag, and the table that links tags to issues.

A tag is global: it belongs to no project, and its name is unique across live
tags through the same partial index the project slug uses, so a name is reusable
once its tag is deleted and the guarantee behind ``createTag``'s refusal.
``issue_tags`` is the plain many-to-many link; it carries no lifecycle of its own,
so ``Tag.delete`` clears a tag's rows outright rather than soft-deleting them,
which is what keeps a deleted tag off every issue that wore it.
"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, Table, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from tt.platform.db import BaseDBModel

issue_tags = Table(
    "issue_tags",
    BaseDBModel.metadata,
    Column("issue_id", ForeignKey("issues.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(BaseDBModel):
    __tablename__ = "tags"
    __table_args__ = (
        Index("tags_name_live", "name", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )

    name: Mapped[str] = mapped_column(Text)

    def subject(self) -> str:
        return f'tag "{self.name}"'
