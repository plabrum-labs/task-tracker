"""Reads against the epics table: the live list for a project, one by title, and
one by id.

Every read joins the project and loads it onto the epic with ``contains_eager``,
so an epic's slug is there once the session closes, and loads the issues with
``selectinload``, because an epic's derived counts read straight off that
collection and a hook that reads them runs after the session is gone. Liveness is
the epic's own ``deleted_at``, and both list reads add that its project is live
too. ``resolve_by_title`` addresses by a title within a project and is the
``locate`` the action dispatch runs through; ``get_epic`` still addresses by
global id.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager, selectinload

from tt.domains.epic.models import Epic
from tt.domains.project.models import Project
from tt.platform.refs import NamedRef

_loaded = (
    select(Epic)
    .join(Epic.project)
    .options(contains_eager(Epic.project), selectinload(Epic.issues))
    .order_by(Epic.created_at.asc())
)


def list_epics(db: Session, project_slug: str) -> list[Epic]:
    """Live epics of a live project, oldest first, each carrying its counts. Slugs
    are stored uppercase, so the argument is uppercased and may be given in any
    case."""
    stmt = _loaded.where(
        Epic.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Project.slug == project_slug.upper(),
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


def title_owner(db: Session, project_id: int, title: str, exclude_id: int | None) -> Epic | None:
    """The live epic that already carries ``title`` in the project, if any but
    ``exclude_id``. The partial unique index guarantees at most one; this is what
    turns its constraint into a sentence a refusal can carry."""
    stmt = select(Epic).where(
        Epic.project_id == project_id,
        Epic.title == title,
        Epic.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(Epic.id != exclude_id)
    return db.scalars(stmt).first()


def resolve_by_title(db: Session, address: NamedRef) -> Epic | None:
    """The one live epic a title names within its project, or ``None``. The title is
    unique among a project's live epics, so at most one comes back."""
    stmt = _loaded.where(
        Epic.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Project.slug == address.project_slug,
        Epic.title == address.title,
    )
    return db.scalars(stmt).first()
