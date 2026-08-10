"""Migrating a real on-disk database, from wherever the frontend is invoked.

``upgrade`` runs Alembic's migrations against a file, and Alembic resolves its
``script_location`` against the current directory. The CLI and TUI run from the
user's working directory, not the repo root, so the case that matters is a
migration driven from somewhere else entirely.
"""

from pathlib import Path

import pytest
from sqlalchemy import inspect

import tt
from tt import schema
from tt.platform import db as platform_db


def test_migrations_ship_inside_the_package() -> None:
    # The wheel packages ``tt`` and nothing beside it, so a database opened from an
    # installed copy (the whole point of ``tt.api``) can only reach the migrations
    # if they live under the package. A tree resolved anywhere outside ``tt`` is one
    # the wheel drops and ``upgrade`` cannot find once installed.
    package_root = Path(tt.__file__).resolve().parent
    assert schema._MIGRATIONS_DIR.is_relative_to(package_root)
    assert (schema._MIGRATIONS_DIR / "env.py").is_file()
    assert list((schema._MIGRATIONS_DIR / "versions").glob("*.py"))


def test_upgrade_finds_its_migrations_from_an_unrelated_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # tmp_path holds no ``alembic`` scripts folder; a relative ``script_location``
    # would look for one here and fail.
    monkeypatch.chdir(tmp_path)
    url = f"sqlite:///{tmp_path / 'tt.db'}"

    schema.upgrade(url)

    engine = platform_db.connect(url)
    tables = set(inspect(engine).get_table_names())
    assert {"issues", "projects"} <= tables
