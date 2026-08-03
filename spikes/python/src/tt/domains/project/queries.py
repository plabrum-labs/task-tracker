"""Reads and writes against the projects table.

Every read loads the issues with ``selectinload``, because a project's counts are
read straight off that collection and a hook that reads them runs after the
session is gone. Liveness is the row's own ``deleted_at``. Writes flush and never
commit — the transaction is the caller's.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from tt.domains.project.models import Draft, Project, Status

_loaded = select(Project).options(selectinload(Project.issues)).order_by(Project.created_at.asc())


def live(db: Session) -> list[Project]:
    """Live projects in creation order, each carrying its loaded issues."""
    return list(db.scalars(_loaded.where(Project.deleted_at.is_(None))))


def by_slug(db: Session, slug: str) -> Project | None:
    return db.scalars(_loaded.where(Project.deleted_at.is_(None), Project.slug == slug)).first()


def trashed(db: Session) -> list[Project]:
    return list(db.scalars(_loaded.where(Project.deleted_at.is_not(None))))


def add(db: Session, draft: Draft) -> Project:
    """Insert a project. ``issues`` is set empty so the fresh row's counts read as
    zero off a loaded collection, and a refresh reads its stamps back."""
    project = Project(
        slug=draft.slug,
        title=draft.title,
        body=draft.body,
        status=Status.ACTIVE,
        issues=[],
    )
    db.add(project)
    db.flush()
    db.refresh(project, ["created_at", "updated_at"])
    return project
