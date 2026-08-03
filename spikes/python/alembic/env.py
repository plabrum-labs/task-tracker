"""The Alembic environment.

``target_metadata`` is the one ``BaseDBModel.metadata`` every table maps on, so a
``--autogenerate`` diff sees the whole schema. Importing ``tt.schema`` is what
pulls the domain models in and registers their tables before the diff runs.

``render_as_batch`` is on because SQLite cannot ``ALTER`` a column in place — batch
mode rewrites the table instead, which is what a later migration against this file
will need.
"""

from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool

import tt.schema  # noqa: F401  (registers every table on the shared metadata)
from tt.domains.issue.models import PriorityRank
from tt.platform.models import BaseDBModel
from tt.platform.types import EnumText

config = context.config
target_metadata = BaseDBModel.metadata


def render_item(type_: str, obj: Any, autogen_context: object) -> str | bool:
    """Render the domain's column types as the SQLite types they actually are.

    ``EnumText`` and ``PriorityRank`` are Python-side mappings — a migration only
    needs the ``TEXT``/``INTEGER`` the column stores, and rendering the decorator
    itself would need its enum imported and its argument reconstructed. Everything
    else falls through to Alembic's default rendering."""
    if type_ == "type":
        if isinstance(obj, EnumText):
            return "sa.Text()"
        if isinstance(obj, PriorityRank):
            return "sa.Integer()"
    return False


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        render_item=render_item,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
