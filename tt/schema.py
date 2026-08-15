"""Opening a database, and bringing one up to the current schema.

Opening and migrating are separate concerns. A frontend — the CLI, the TUI, or
the ``tt.api`` client — calls ``open_db``, which resolves the database and
connects to it and nothing more: it never migrates as a side effect of starting,
so a read path carries no schema-changing DDL. Bringing a database to head is
``upgrade``, an explicit backend step (the ``db-upgrade`` recipe, or a deploy),
run by whoever administers the database. ``create_all`` builds the whole schema
at once on a throwaway database that has no history to migrate — the disposable
Postgres schema a test fixture builds.

Discovering the domain models is the point of this module living above them: the
walk below imports every ``models.py`` under ``tt``, which is what registers each
table on ``BaseDBModel.metadata`` before any of the calls read it, and before
Alembic's ``env`` diffs against it. Adding a domain needs no edit here.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from tt.platform import db
from tt.platform.db import BaseDBModel
from tt.platform.discovery import discover_and_import

# Import every domain's ``models`` for its table-registration side effect. Runs at
# import time so the metadata is populated before ``create_all``/``upgrade`` or
# Alembic's ``env`` reads it.
discover_and_import(["models.py"], search_root=Path(__file__).resolve().parent)

# alembic.ini sits at the repo root, one level above the tt package.
_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
_REPO_ROOT = _ALEMBIC_INI.parent


def create_all(engine: Engine) -> None:
    """Create every table on a fresh, historyless database."""
    BaseDBModel.metadata.create_all(engine)


def upgrade(url: str) -> None:
    """Run the migrations forward to head. The explicit way a database is brought
    to the current schema — the ``db-upgrade`` recipe and the test fixtures call
    it, a frontend does not. Idempotent: a database already at head is untouched."""
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    # alembic.ini's ``script_location`` and ``prepend_sys_path`` are relative, so
    # Alembic would resolve them against the current directory. A caller may run
    # from anywhere, not the repo root, so anchor both to the repo here.
    config.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(_REPO_ROOT))
    command.upgrade(config, "head")


def open_db(override: str | None = None) -> Engine:
    """Resolve the database a frontend should use and connect to it.

    ``override`` is a frontend's explicit ``--db``; without one the configured
    server (``TT_DB``, or the local default) is used. This does not migrate — a
    schema change is applied out of band by ``upgrade`` — so opening a database
    never runs DDL as a side effect of starting."""
    url = override if override is not None else db.default_url()
    return db.connect(url)
