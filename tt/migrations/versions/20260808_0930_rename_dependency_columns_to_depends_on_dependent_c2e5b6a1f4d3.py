"""rename dependency columns to depends_on/dependent

Revision ID: c2e5b6a1f4d3
Revises: a1f4c2d3e5b6
Create Date: 2026-08-08 09:30:00.000000

The ``issue_dependencies`` link table's columns move from ``blocker_id``/
``blocked_id`` to ``depends_on_id``/``dependent_id`` — the ``depends_on`` end must
finish before the ``dependent`` end can start, so the edge reads the way the domain
now names it. SQLite cannot rename a column in place, so batch mode rebuilds the
table; the composite primary key and both cascading foreign keys carry over unchanged.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c2e5b6a1f4d3"
down_revision: str | None = "a1f4c2d3e5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("issue_dependencies") as batch_op:
        batch_op.alter_column("blocker_id", new_column_name="depends_on_id")
        batch_op.alter_column("blocked_id", new_column_name="dependent_id")


def downgrade() -> None:
    with op.batch_alter_table("issue_dependencies") as batch_op:
        batch_op.alter_column("depends_on_id", new_column_name="blocker_id")
        batch_op.alter_column("dependent_id", new_column_name="blocked_id")
