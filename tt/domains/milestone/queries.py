"""Reads against the milestones table: the live list for an epic, one by ref, and
one by id.

Every read joins the epic and loads it onto the milestone with ``contains_eager``,
loads the project with ``selectinload`` so the milestone's ``ref`` reads its slug
once the session closes, and loads the issues with ``selectinload``, because a
milestone's derived counts read straight off that collection and a hook that reads
them runs after the session is gone. Liveness is the milestone's own
``deleted_at``, and both list reads add that its epic is live too. ``resolve_ref``
addresses by the ``<slug>-<number>`` ref and is the ``locate`` the action dispatch
runs through; ``get_milestone`` still addresses by global id.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager, selectinload

from tt.domains.epic.models import Epic
from tt.domains.milestone.models import Milestone
from tt.domains.project.models import Project
from tt.platform.refs import parse_ref

_loaded = (
    select(Milestone)
    .join(Milestone.epic)
    .options(
        contains_eager(Milestone.epic),
        selectinload(Milestone.project),
        selectinload(Milestone.issues),
    )
    .order_by(Milestone.created_at.asc())
)


def list_milestones(db: Session, epic_id: int) -> list[Milestone]:
    """Live milestones of a live epic, oldest first, each carrying its counts."""
    stmt = _loaded.where(
        Milestone.deleted_at.is_(None),
        Epic.deleted_at.is_(None),
        Milestone.epic_id == epic_id,
    )
    return list(db.scalars(stmt))


def get_milestone(db: Session, milestone_id: int) -> Milestone | None:
    """The one live milestone of a live epic by global id, or ``None``."""
    stmt = _loaded.where(
        Milestone.deleted_at.is_(None),
        Epic.deleted_at.is_(None),
        Milestone.id == milestone_id,
    )
    return db.scalars(stmt).first()


def resolve_ref(db: Session, ref: str) -> Milestone | None:
    """The one live milestone a ``<slug>-<number>`` ref names, or ``None`` — for a
    ref that does not parse as much as for one that names no row. The number is
    scoped to the project, so the join filters on the project's slug."""
    parsed = parse_ref(ref)
    if parsed is None:
        return None
    slug, number = parsed
    stmt = _loaded.join(Milestone.project).where(
        Milestone.deleted_at.is_(None),
        Epic.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Project.slug == slug,
        Milestone.number == number,
    )
    return db.scalars(stmt).first()
