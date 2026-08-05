"""Bringing a database up to the current schema.

Two ways in, one metadata. ``create_all`` is for a database that lives and dies
with the process — the in-memory one the tests and ``show`` open — where there is
no history to migrate and Alembic cannot reach the connection a ``StaticPool``
holds anyway. ``upgrade`` runs the migrations against a file the CLI and TUI keep,
which is where a schema change is a new revision rather than a silent ``create``.

Importing the domain models is the point of this module living above them: it is
what registers every table on ``BaseDBModel.metadata`` before either call reads
it, and before Alembic's ``env`` diffs against it.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

import tt.domains.epic.models  # noqa: F401  (registers the epics table)
import tt.domains.issue.models  # noqa: F401  (registers the issues table)
import tt.domains.project.models  # noqa: F401  (registers the projects table)
from tt.platform import db
from tt.platform.db import BaseDBModel

# alembic.ini sits at the repo root, one level above the tt package.
_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
_REPO_ROOT = _ALEMBIC_INI.parent


def create_all(engine: Engine) -> None:
    """Create every table on a fresh, historyless database."""
    BaseDBModel.metadata.create_all(engine)


def upgrade(url: str) -> None:
    """Run the migrations forward to head. Idempotent: a database already at head
    is left untouched."""
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    # alembic.ini's ``script_location`` and ``prepend_sys_path`` are relative, so
    # Alembic would resolve them against the current directory. The CLI and TUI run
    # from wherever the user is, not the repo root, so anchor both to the repo here.
    config.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(_REPO_ROOT))
    command.upgrade(config, "head")


def bootstrap(override: str | None = None) -> Engine:
    """Resolve the database a frontend should open, bring it to head, and open it.

    ``override`` is a frontend's explicit ``--db``; without one the shared
    per-user file (or ``TT_DB``) is used."""
    url = override if override is not None else db.default_url()
    upgrade(url)
    return db.connect(url)
