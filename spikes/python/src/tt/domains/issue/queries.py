"""Reads and writes against the issues table.

Every read joins the project and loads it onto the issue with ``contains_eager``,
so ``project_slug`` is there once the session closes and no read costs a query per
row. Liveness is carried by construction: an issue is live when its own
``deleted_at`` is null, and ``for_project`` and ``get`` add that its project is
live too. Writes flush and never commit — the transaction is the caller's.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager

from tt.domains.issue.models import Draft, Issue, Status
from tt.domains.project.models import Project

_loaded = (
    select(Issue)
    .join(Issue.project)
    .options(contains_eager(Issue.project))
    .order_by(Issue.priority.desc(), Issue.created_at.asc())
)


def for_project(db: Session, project_slug: str) -> list[Issue]:
    """Live issues of a live project, high priority first then oldest first."""
    stmt = _loaded.where(
        Issue.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Project.slug == project_slug,
    )
    return list(db.scalars(stmt))


def get(db: Session, issue_id: int) -> Issue | None:
    stmt = _loaded.where(
        Issue.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Issue.id == issue_id,
    )
    return db.scalars(stmt).first()


def trashed(db: Session) -> list[Issue]:
    """Issues in the trash by their own ``deleted_at``. An issue whose project also
    went is still printable, so this asks nothing of the project but its slug."""
    return list(db.scalars(_loaded.where(Issue.deleted_at.is_not(None))))


def add(db: Session, project: Project, draft: Draft) -> Issue:
    """Insert an issue under a loaded project. The relationship is assigned rather
    than the id, so the new issue carries its project and ``project_slug`` reads
    without a further query. The stamps are the database's, so a refresh reads them
    back onto the returned row."""
    issue = Issue(
        project=project,
        title=draft.title,
        body=draft.body,
        status=Status.TODO,
        priority=draft.priority,
        status_note=None,
    )
    db.add(issue)
    db.flush()
    db.refresh(issue, ["created_at", "updated_at"])
    return issue
