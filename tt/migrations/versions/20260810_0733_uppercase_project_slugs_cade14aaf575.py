"""uppercase project slugs

Revision ID: cade14aaf575
Revises: c2e5b6a1f4d3
Create Date: 2026-08-10 07:33:26.844555

A slug is stored uppercase so every ref reads ``TT-12`` however it was typed. This
canonicalises the slugs already on disk to that form. The partial unique index on
live slugs still holds afterwards — no two live projects uppercase to the same
slug — so this is a plain in-place rewrite of the column.

The original casing is not recorded anywhere, so the downgrade cannot restore it;
it is a no-op, and the uppercase form is the one the running code expects in any
case.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "cade14aaf575"
down_revision: str | None = "c2e5b6a1f4d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE projects SET slug = upper(slug)")


def downgrade() -> None:
    pass
