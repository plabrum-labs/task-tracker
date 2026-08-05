"""Reads against the epics table: the live list for a project, and one by id.

Every read joins the project and loads it onto the epic with ``contains_eager``,
so an epic's slug is there once the session closes, and loads the issues with
``selectinload``, because an epic's derived counts read straight off that
collection and a hook that reads them runs after the session is gone. Liveness is
the epic's own ``deleted_at``, and both reads add that its project is live too.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager, selectinload

from tt.domains.epic.models import Epic
from tt.domains.project.models import Project

_loaded = (
    select(Epic)
    .join(Epic.project)
    .options(contains_eager(Epic.project), selectinload(Epic.issues))
    .order_by(Epic.created_at.asc())
)


def list_epics(db: Session, project_slug: str) -> list[Epic]:
    """Live epics of a live project, oldest first, each carrying its counts."""
    stmt = _loaded.where(
        Epic.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Project.slug == project_slug,
    )
    return list(db.scalars(stmt))


def get_epic(db: Session, epic_id: int) -> Epic | None:
    """The one live epic of a live project by id, or ``None``."""
    stmt = _loaded.where(
        Epic.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Epic.id == epic_id,
    )
    return db.scalars(stmt).first()
