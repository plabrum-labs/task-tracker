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

import tt.domains.issue.models  # noqa: F401  (registers the issues table)
import tt.domains.project.models  # noqa: F401  (registers the projects table)
from tt.platform.db import BaseDBModel

# alembic.ini sits at the repo root, one level above the tt package.
_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def create_all(engine: Engine) -> None:
    """Create every table on a fresh, historyless database."""
    BaseDBModel.metadata.create_all(engine)


def upgrade(url: str) -> None:
    """Run the migrations forward to head. Idempotent: a database already at head
    is left untouched."""
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
