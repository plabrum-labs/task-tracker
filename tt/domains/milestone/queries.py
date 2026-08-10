"""Reads against the milestones table: the live list for a project, one by title,
and one by id.

Every read joins the project and loads it onto the milestone with
``contains_eager`` so its slug is there once the session closes, loads the optional
epic with ``selectinload`` for the label a milestone reads, and loads the issues
with ``selectinload``, because a milestone's derived counts read straight off that
collection and a hook that reads them runs after the session is gone. Liveness is
the milestone's own ``deleted_at``, and the list read adds that its project is live
too. ``resolve_by_title`` addresses by a title within a project and is the
``locate`` the action dispatch runs through; ``get_milestone`` still addresses by
global id.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager, selectinload

from tt.domains.milestone.models import Milestone
from tt.domains.project.models import Project
from tt.platform.refs import NamedRef

_loaded = (
    select(Milestone)
    .join(Milestone.project)
    .options(
        contains_eager(Milestone.project),
        selectinload(Milestone.epic),
        selectinload(Milestone.issues),
    )
    .order_by(Milestone.created_at.asc())
)


def list_milestones(db: Session, project_slug: str, epic_id: int | None = None) -> list[Milestone]:
    """Live milestones of a live project, oldest first, each carrying its counts.
    Narrowed to one epic's when ``epic_id`` is given. Slugs are stored uppercase, so
    the argument is uppercased and may be given in any case."""
    stmt = _loaded.where(
        Milestone.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Project.slug == project_slug.upper(),
    )
    if epic_id is not None:
        stmt = stmt.where(Milestone.epic_id == epic_id)
    return list(db.scalars(stmt))


def get_milestone(db: Session, milestone_id: int) -> Milestone | None:
    """The one live milestone of a live project by global id, or ``None``."""
    stmt = _loaded.where(
        Milestone.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Milestone.id == milestone_id,
    )
    return db.scalars(stmt).first()


def title_owner(
    db: Session, project_id: int, title: str, exclude_id: int | None
) -> Milestone | None:
    """The live milestone that already carries ``title`` in the project, if any but
    ``exclude_id``. The partial unique index guarantees at most one; this is what
    turns its constraint into a sentence a refusal can carry."""
    stmt = select(Milestone).where(
        Milestone.project_id == project_id,
        Milestone.title == title,
        Milestone.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(Milestone.id != exclude_id)
    return db.scalars(stmt).first()


def resolve_by_title(db: Session, address: NamedRef) -> Milestone | None:
    """The one live milestone a title names within its project, or ``None``. The
    title is unique among a project's live milestones, so at most one comes back."""
    stmt = _loaded.where(
        Milestone.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Project.slug == address.project_slug,
        Milestone.title == address.title,
    )
    return db.scalars(stmt).first()
