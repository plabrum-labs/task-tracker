"""Reads against the milestones table: the live list for an epic, and one by id.

Every read joins the epic and loads it onto the milestone with ``contains_eager``,
so a milestone's epic is there once the session closes, and loads the issues with
``selectinload``, because a milestone's derived counts read straight off that
collection and a hook that reads them runs after the session is gone. Liveness is
the milestone's own ``deleted_at``, and both reads add that its epic is live too.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager, selectinload

from tt.domains.epic.models import Epic
from tt.domains.milestone.models import Milestone

_loaded = (
    select(Milestone)
    .join(Milestone.epic)
    .options(contains_eager(Milestone.epic), selectinload(Milestone.issues))
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
    """The one live milestone of a live epic by id, or ``None``."""
    stmt = _loaded.where(
        Milestone.deleted_at.is_(None),
        Epic.deleted_at.is_(None),
        Milestone.id == milestone_id,
    )
    return db.scalars(stmt).first()
