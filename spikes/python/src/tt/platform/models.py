"""The declarative base every table is mapped on.

One base, so one ``metadata`` — which is what Alembic diffs and a test creates.
It carries the columns every row has: the id, the two stamps the database keeps
current, and the ``deleted_at`` a soft delete sets. A table adds its own columns
and inherits these, so no model spells them out and no write stamps them by hand.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class BaseDBModel(DeclarativeBase):
    """id, timestamps and soft-delete, shared by every mapped table."""

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )

    def soft_delete(self) -> None:
        self.deleted_at = datetime.now(UTC)

    def restore(self) -> None:
        self.deleted_at = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
