"""address epics and milestones by title

Revision ID: 1d9729a70cbb
Revises: cade14aaf575
Create Date: 2026-08-10 08:26:04.227676

An epic and a milestone are no longer addressed by a ``<slug>-<number>`` ref: both
are named, project-scoped groupings, so each drops its ``number`` column and gains
a partial unique index on ``(project_id, title)`` over live rows — the title is how
one is addressed, unique among a project's live rows. The per-project ``sequences``
counters for ``epic`` and ``milestone`` are deleted; only ``issue`` still draws a
number.

A milestone is also decoupled from its epic: ``epic_id`` becomes nullable, so a
milestone is project-level and may sit under one epic, several, or none. Its
``project_id`` — already denormalized — is now the milestone's primary home. The
epic foreign key stays ``CASCADE``: an epic only ever soft-deletes, so it never
fires, and a reader drops a soft-deleted epic rather than the milestone cascading.

The downgrade renumbers as the original numbering migration did, but a milestone
created with no epic has no place in the epic-required schema it restores, so a
downgrade past that point fails until such a milestone is filed under an epic.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1d9729a70cbb"
down_revision: str | None = "cade14aaf575"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("epics", schema=None) as batch_op:
        batch_op.drop_constraint("uq_epics_project_number", type_="unique")
        batch_op.create_index(
            "epics_title_live",
            ["project_id", "title"],
            unique=True,
            sqlite_where=sa.text("deleted_at IS NULL"),
        )
        batch_op.drop_column("number")

    with op.batch_alter_table("milestones", schema=None) as batch_op:
        batch_op.alter_column("epic_id", existing_type=sa.INTEGER(), nullable=True)
        batch_op.drop_index("milestones_by_epic", sqlite_where=sa.text("deleted_at IS NULL"))
        batch_op.drop_constraint("uq_milestones_project_number", type_="unique")
        batch_op.create_index(
            "milestones_by_project",
            ["project_id"],
            unique=False,
            sqlite_where=sa.text("deleted_at IS NULL"),
        )
        batch_op.create_index(
            "milestones_title_live",
            ["project_id", "title"],
            unique=True,
            sqlite_where=sa.text("deleted_at IS NULL"),
        )
        batch_op.drop_column("number")

    # An epic and a milestone no longer draw a number, so their counters are dead.
    op.execute(sa.text("DELETE FROM sequences WHERE type IN ('epic', 'milestone')"))


def downgrade() -> None:
    # Re-add the number column nullable, renumber in id order within each project the
    # way the original numbering migration did, then tighten and re-constrain.
    with op.batch_alter_table("epics", schema=None) as batch_op:
        batch_op.add_column(sa.Column("number", sa.Integer(), nullable=True))
    with op.batch_alter_table("milestones", schema=None) as batch_op:
        batch_op.add_column(sa.Column("number", sa.Integer(), nullable=True))

    for table in ("epics", "milestones"):
        # ``table`` is a fixed literal from the loop, never input.
        op.execute(
            sa.text(
                f"UPDATE {table} SET number = "  # noqa: S608
                f"(SELECT COUNT(*) FROM {table} AS t2 "
                f"WHERE t2.project_id = {table}.project_id AND t2.id <= {table}.id)"
            )
        )

    for table, seq_type in (("epics", "epic"), ("milestones", "milestone")):
        op.execute(
            sa.text(
                "INSERT INTO sequences (project_id, type, current_value) "  # noqa: S608
                f"SELECT project_id, '{seq_type}', MAX(number) FROM {table} "
                "GROUP BY project_id"
            )
        )

    with op.batch_alter_table("epics", schema=None) as batch_op:
        batch_op.alter_column("number", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_index("epics_title_live", sqlite_where=sa.text("deleted_at IS NULL"))
        batch_op.create_unique_constraint("uq_epics_project_number", ["project_id", "number"])

    with op.batch_alter_table("milestones", schema=None) as batch_op:
        batch_op.alter_column("number", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("epic_id", existing_type=sa.INTEGER(), nullable=False)
        batch_op.drop_index("milestones_title_live", sqlite_where=sa.text("deleted_at IS NULL"))
        batch_op.drop_index("milestones_by_project", sqlite_where=sa.text("deleted_at IS NULL"))
        batch_op.create_unique_constraint("uq_milestones_project_number", ["project_id", "number"])
        batch_op.create_index(
            "milestones_by_epic", ["epic_id"], sqlite_where=sa.text("deleted_at IS NULL")
        )
