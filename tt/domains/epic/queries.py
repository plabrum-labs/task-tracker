"""Reads against the epics table: the live list for a project, one by ref, and one
by id.

Every read joins the project and loads it onto the epic with ``contains_eager``,
so an epic's slug is there once the session closes, and loads the issues with
``selectinload``, because an epic's derived counts read straight off that
collection and a hook that reads them runs after the session is gone. Liveness is
the epic's own ``deleted_at``, and both list reads add that its project is live
too. ``resolve_ref`` addresses by the ``<slug>-<number>`` ref and is the ``locate``
the action dispatch runs through; ``get_epic`` still addresses by global id.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager, selectinload

from tt.domains.epic.models import Epic
from tt.domains.project.models import Project
from tt.platform.refs import parse_ref

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
    """The one live epic of a live project by global id, or ``None``."""
    stmt = _loaded.where(
        Epic.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Epic.id == epic_id,
    )
    return db.scalars(stmt).first()


def resolve_ref(db: Session, ref: str) -> Epic | None:
    """The one live epic a ``<slug>-<number>`` ref names, or ``None`` — for a ref
    that does not parse as much as for one that names no row."""
    parsed = parse_ref(ref)
    if parsed is None:
        return None
    slug, number = parsed
    stmt = _loaded.where(
        Epic.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Project.slug == slug,
        Epic.number == number,
    )
    return db.scalars(stmt).first()
