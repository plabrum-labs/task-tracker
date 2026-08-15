"""Migrating a database from wherever a frontend is invoked.

``upgrade`` runs Alembic's migrations, and Alembic resolves its ``script_location``
against the current directory. The CLI and TUI run from the user's working
directory, not the repo root, so the case that matters is a migration driven from
somewhere else entirely — and the proof it worked is an ``alembic_version`` row,
which ``create_all`` would never leave behind.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect, text
from tests import conftest

from tt import schema
from tt.platform import db as platform_db

# A throwaway database this test builds from empty, kept apart from the shared
# ``tt_test`` one so migrating it disturbs nothing another test relies on.
_SCHEMA_DB = "tt_schema_test"


@pytest.fixture
def migrated_url() -> Iterator[str]:
    """A freshly created, empty database and its url. Created and dropped on an
    autocommit connection to the server's ``tt`` maintenance database, since
    ``CREATE``/``DROP DATABASE`` cannot run inside a transaction."""
    base = sa.make_url(conftest.test_url())
    admin = sa.create_engine(base.set(database="tt"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{_SCHEMA_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{_SCHEMA_DB}"'))
    try:
        yield base.set(database=_SCHEMA_DB).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{_SCHEMA_DB}"'))
        admin.dispose()


def test_upgrade_finds_its_migrations_from_an_unrelated_directory(
    migrated_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # tmp_path holds no ``alembic`` scripts folder; a relative ``script_location``
    # would look for one here and fail.
    monkeypatch.chdir(tmp_path)

    schema.upgrade(migrated_url)

    engine = platform_db.connect(migrated_url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert {"issues", "projects"} <= tables
    # ``alembic_version`` is stamped by a migration run, never by ``create_all`` —
    # so its presence proves the upgrade path resolved and ran.
    assert "alembic_version" in tables
