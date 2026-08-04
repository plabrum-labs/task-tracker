"""Reads against the issues table: the live list for a project, and one by id.

Every read joins the project and loads it onto the issue with ``contains_eager``,
so an issue's slug is there once the session closes and no read costs a query per
row. Liveness is carried by construction: an issue is live when its own
``deleted_at`` is null, and both reads add that its project is live too.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager

from tt.domains.issue.models import Issue
from tt.domains.project.models import Project

_loaded = (
    select(Issue)
    .join(Issue.project)
    .options(contains_eager(Issue.project))
    .order_by(Issue.priority.desc(), Issue.created_at.asc())
)


def list_issues(db: Session, project_slug: str) -> list[Issue]:
    """Live issues of a live project, high priority first then oldest first."""
    stmt = _loaded.where(
        Issue.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Project.slug == project_slug,
    )
    return list(db.scalars(stmt))


def get_issue(db: Session, issue_id: int) -> Issue | None:
    """The one live issue of a live project by id, or ``None``."""
    stmt = _loaded.where(
        Issue.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Issue.id == issue_id,
    )
    return db.scalars(stmt).first()
