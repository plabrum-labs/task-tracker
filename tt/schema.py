"""Bringing a database up to the current schema.

Two ways in, one metadata. ``create_all`` is for a database that lives and dies
with the process — the in-memory one the tests and ``show`` open — where there is
no history to migrate and Alembic cannot reach the connection a ``StaticPool``
holds anyway. ``upgrade`` runs the migrations against a file the CLI and TUI keep,
which is where a schema change is a new revision rather than a silent ``create``.

Discovering the domain models is the point of this module living above them: the
walk below imports every ``models.py`` under ``tt``, which is what registers each
table on ``BaseDBModel.metadata`` before either call reads it, and before
Alembic's ``env`` diffs against it. Adding a domain needs no edit here.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from tt.platform import db
from tt.platform.db import BaseDBModel
from tt.platform.discovery import discover_and_import

_PACKAGE_ROOT = Path(__file__).resolve().parent

# Import every domain's ``models`` for its table-registration side effect. Runs at
# import time so the metadata is populated before ``create_all``/``upgrade`` or
# Alembic's ``env`` reads it.
discover_and_import(["models.py"], search_root=_PACKAGE_ROOT)

# The migration tree ships inside the package (``tt/migrations``), so an installed
# wheel carries it and this resolves the same in a source checkout or a
# ``site-packages`` install — there is no repo root to reach for once installed.
# Named ``migrations`` rather than ``alembic`` so it does not shadow the real
# ``alembic`` package for tools resolving imports against the source tree.
_MIGRATIONS_DIR = _PACKAGE_ROOT / "migrations"


def create_all(engine: Engine) -> None:
    """Create every table on a fresh, historyless database."""
    BaseDBModel.metadata.create_all(engine)


def upgrade(url: str) -> None:
    """Run the migrations forward to head. Idempotent: a database already at head
    is left untouched."""
    # Built without an ``alembic.ini``: that file configures the authoring CLI and
    # is not shipped in the wheel. Runtime needs only an absolute ``script_location``
    # (the CLI and TUI run from wherever the user is, not from any fixed directory)
    # and the target database. ``env.py``'s ``import tt.schema`` resolves through the
    # already-imported package, so no ``prepend_sys_path`` is required.
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def bootstrap(override: str | None = None) -> Engine:
    """Resolve the database a frontend should open, bring it to head, and open it.

    ``override`` is a frontend's explicit ``--db``; without one the shared
    per-user file (or ``TT_DB``) is used."""
    url = override if override is not None else db.default_url()
    upgrade(url)
    return db.connect(url)
