"""Reads against the projects table.

Every read loads the issues with ``selectinload``, because a project's counts are
read straight off that collection and a hook that reads them runs after the
session is gone. Liveness is the row's own ``deleted_at``.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from tt.domains.project.models import Project

_loaded = select(Project).options(selectinload(Project.issues)).order_by(Project.created_at.asc())


def live(db: Session) -> list[Project]:
    """Live projects in creation order, each carrying its loaded issues."""
    return list(db.scalars(_loaded.where(Project.deleted_at.is_(None))))


def by_slug(db: Session, slug: str) -> Project | None:
    return db.scalars(_loaded.where(Project.deleted_at.is_(None), Project.slug == slug)).first()
