"""The project entrypoints a frontend and the actions call.

Business logic and naming, over the reads and writes in ``queries``. The counts a
project carries come from its loaded issues, so nothing here reaches across to the
issue store the way the split projection once did. Edits are the actions' own —
they mutate the loaded row and flush. The transaction is the caller's, so these
flush and never commit.
"""

from sqlalchemy.orm import Session

from tt.domains.project import queries
from tt.domains.project.models import Draft, Project
from tt.platform.deleted import Deleted


def list_projects(db: Session) -> list[Project]:
    return queries.live(db)


def get_project(db: Session, slug: str) -> Project | None:
    return queries.by_slug(db, slug)


def _as_deleted(project: Project) -> Deleted[Project]:
    # ``queries.trashed`` returns only stamped rows, so the stamp is never null.
    assert project.deleted_at is not None
    return Deleted(inner=project, deleted_at=project.deleted_at)


def trashed_projects(db: Session) -> list[Deleted[Project]]:
    return [_as_deleted(project) for project in queries.trashed(db)]


def create_project(db: Session, draft: Draft) -> Project:
    return queries.add(db, draft)


def delete_project(db: Session, project: Project) -> None:
    project.soft_delete()
    db.flush()


def restore_project(db: Session, project: Project) -> None:
    project.restore()
    db.flush()
