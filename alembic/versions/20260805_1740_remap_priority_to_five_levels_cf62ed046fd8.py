"""remap priority to five levels

Revision ID: cf62ed046fd8
Revises: ad1a89982bc9
Create Date: 2026-08-05 17:40:25.823024

Priority grows from two levels (``normal``, ``high``) to five (``none``, ``low``,
``medium``, ``high``, ``urgent``), still stored as the ascending integer the sort
orders by. ``normal`` becomes the new middle ``medium`` and ``high`` keeps its
name but moves up to make room for ``urgent`` above it, so existing rows are
renumbered ``1 -> 2`` (normal to medium) and ``2 -> 3`` (high). The column type is
unchanged (a plain integer with no constraint), so only the stored values move.

Both directions renumber in a single ``CASE`` so every branch reads the original
value — sequential updates would collide as one value lands on another's number.
The downgrade is lossy: the three levels the old scheme cannot express collapse
into its two, ``high``/``urgent`` to ``high`` and ``none``/``low``/``medium`` to
``normal``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cf62ed046fd8"
down_revision: str | None = "ad1a89982bc9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE issues SET priority = CASE priority "
            "WHEN 2 THEN 3 "  # high moves up above the new urgent
            "WHEN 1 THEN 2 "  # normal becomes the new middle, medium
            "ELSE priority END"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE issues SET priority = CASE "
            "WHEN priority >= 3 THEN 2 "  # high and urgent both fold to high
            "ELSE 1 END"  # none, low and medium all fold to normal
        )
    )
