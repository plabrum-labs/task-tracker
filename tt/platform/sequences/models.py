"""The per-project counter table a business sequence is drawn from.

A ``Sequence`` is one monotonic counter, keyed by the project it counts within and
a string ``type`` — only ``"issue"`` today, since an epic and a milestone are
addressed by title rather than a project-scoped number. The domains never key on
anything but their own type name, so this platform table stays string-keyed and
names no domain value, the same split snacks' ``business_sequences`` draws.

The counter is bookkeeping, not a soft-deletable row: it inherits the shared
columns like every table, but nothing ever stamps its ``deleted_at``. One row per
``(project_id, type)`` is guaranteed by the unique constraint, which is also the
target ``service.next_number`` upserts against.
"""

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tt.platform.db import BaseDBModel


class Sequence(BaseDBModel):
    __tablename__ = "sequences"
    __table_args__ = (UniqueConstraint("project_id", "type", name="uq_sequences_project_type"),)

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(Text)
    current_value: Mapped[int] = mapped_column(default=0)
