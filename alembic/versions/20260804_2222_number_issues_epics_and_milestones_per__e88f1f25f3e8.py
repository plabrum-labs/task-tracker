"""number issues epics and milestones per project

Revision ID: e88f1f25f3e8
Revises: 762216d69769
Create Date: 2026-08-04 22:22:33.695732

Issues, epics and milestones each gain a project-scoped ``number`` — the identity
a frontend now shows and addresses as ``<slug>-<number>``. A milestone also gains
a denormalized ``project_id`` (its epic's, which never changes) so its number is
unique across the whole project, the way an issue's and an epic's are.

Existing rows are numbered in id order within their project, deleted rows
included, so no live row and a dead one ever collide on a number and the column
can be made NOT NULL. The per-project ``sequences`` counters are then seeded to
each ``MAX(number)`` so the next allocation continues the run rather than
restarting it. The columns are added nullable, backfilled, then tightened.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e88f1f25f3e8"
down_revision: str | None = "762216d69769"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add the columns nullable so existing rows survive the add; they are backfilled
    # and tightened to NOT NULL below.
    with op.batch_alter_table("issues", schema=None) as batch_op:
        batch_op.add_column(sa.Column("number", sa.Integer(), nullable=True))
    with op.batch_alter_table("epics", schema=None) as batch_op:
        batch_op.add_column(sa.Column("number", sa.Integer(), nullable=True))
    with op.batch_alter_table("milestones", schema=None) as batch_op:
        batch_op.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("number", sa.Integer(), nullable=True))

    # A milestone's project is its epic's — fixed, since an epic never moves project.
    op.execute(
        sa.text(
            "UPDATE milestones SET project_id = "
            "(SELECT epics.project_id FROM epics WHERE epics.id = milestones.epic_id)"
        )
    )

    # Number each table's rows in id (creation) order within their project. The
    # count of same-project rows up to and including this id is that rank; ids are
    # unique, so there are no ties. Deleted rows are numbered too, keeping the run
    # dense and the number permanent.
    for table in ("issues", "epics", "milestones"):
        # ``table`` is a fixed literal from the loop, never input.
        op.execute(
            sa.text(
                f"UPDATE {table} SET number = "
                f"(SELECT COUNT(*) FROM {table} AS t2 "
                f"WHERE t2.project_id = {table}.project_id AND t2.id <= {table}.id)"
            )
        )

    # Seed each project's counters to where the backfill left off, so the next
    # number continues the run. A project with no rows of a type gets no row here;
    # the first allocation upserts it.
    for table, seq_type in (("issues", "issue"), ("epics", "epic"), ("milestones", "milestone")):
        op.execute(
            sa.text(
                "INSERT INTO sequences (project_id, type, current_value) "
                f"SELECT project_id, '{seq_type}', MAX(number) FROM {table} "
                "GROUP BY project_id"
            )
        )

    # Now that every row has a number, tighten the columns and add the constraints.
    with op.batch_alter_table("issues", schema=None) as batch_op:
        batch_op.alter_column("number", existing_type=sa.Integer(), nullable=False)
        batch_op.create_unique_constraint("uq_issues_project_number", ["project_id", "number"])
    with op.batch_alter_table("epics", schema=None) as batch_op:
        batch_op.alter_column("number", existing_type=sa.Integer(), nullable=False)
        batch_op.create_unique_constraint("uq_epics_project_number", ["project_id", "number"])
    with op.batch_alter_table("milestones", schema=None) as batch_op:
        batch_op.alter_column("project_id", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("number", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index(batch_op.f("ix_milestones_project_id"), ["project_id"], unique=False)
        batch_op.create_unique_constraint("uq_milestones_project_number", ["project_id", "number"])
        batch_op.create_foreign_key(
            "fk_milestones_project_id_projects",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM sequences WHERE type IN ('issue', 'epic', 'milestone')"))
    with op.batch_alter_table("milestones", schema=None) as batch_op:
        batch_op.drop_constraint("fk_milestones_project_id_projects", type_="foreignkey")
        batch_op.drop_constraint("uq_milestones_project_number", type_="unique")
        batch_op.drop_index(batch_op.f("ix_milestones_project_id"))
        batch_op.drop_column("number")
        batch_op.drop_column("project_id")
    with op.batch_alter_table("issues", schema=None) as batch_op:
        batch_op.drop_constraint("uq_issues_project_number", type_="unique")
        batch_op.drop_column("number")
    with op.batch_alter_table("epics", schema=None) as batch_op:
        batch_op.drop_constraint("uq_epics_project_number", type_="unique")
        batch_op.drop_column("number")
