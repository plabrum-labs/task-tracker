"""rename requires_planning status to blocked

Revision ID: a1f4c2d3e5b6
Revises: 63b5325a9ed0
Create Date: 2026-08-08 09:10:00.000000

The ``requires_planning`` status is renamed to ``blocked``. ``Status`` stores the
member name through ``platform.enums.TextEnum``, so existing rows hold the text
``REQUIRES_PLANNING`` and are rewritten to ``BLOCKED``. The column is plain text
with no constraint, so only the stored value moves. The downgrade is exact — no
other status folds into ``blocked`` — so it restores ``REQUIRES_PLANNING``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f4c2d3e5b6"
down_revision: str | None = "63b5325a9ed0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE issues SET status = 'BLOCKED' WHERE status = 'REQUIRES_PLANNING'"))


def downgrade() -> None:
    op.execute(sa.text("UPDATE issues SET status = 'REQUIRES_PLANNING' WHERE status = 'BLOCKED'"))
