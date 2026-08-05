"""Reads against the issues table: the live list for a project, one by ref, and one
by id.

Every read joins the project and loads it onto the issue with ``contains_eager``,
so an issue's slug is there once the session closes and no read costs a query per
row, and loads the epic and milestone the issue links so their refs can be built
from the same slug. Liveness is carried by construction: an issue is live when its
own ``deleted_at`` is null, and both list reads add that its project is live too.

``get_issue`` addresses by the global id — the path a create's ``created_id`` and a
test's seed take. ``resolve_ref`` addresses by the ``<slug>-<number>`` ref, and is
the ``locate`` the action dispatch runs through.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager, selectinload

from tt.domains.issue.models import Issue
from tt.domains.project.models import Project
from tt.platform.refs import parse_ref

_loaded = (
    select(Issue)
    .join(Issue.project)
    .options(
        contains_eager(Issue.project),
        selectinload(Issue.epic),
        selectinload(Issue.milestone),
        selectinload(Issue.tags),
        selectinload(Issue.comments),
    )
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
    """The one live issue of a live project by global id, or ``None``."""
    stmt = _loaded.where(
        Issue.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Issue.id == issue_id,
    )
    return db.scalars(stmt).first()


def resolve_ref(db: Session, ref: str) -> Issue | None:
    """The one live issue a ``<slug>-<number>`` ref names, or ``None`` — for a ref
    that does not parse as much as for one that names no row."""
    parsed = parse_ref(ref)
    if parsed is None:
        return None
    slug, number = parsed
    stmt = _loaded.where(
        Issue.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Project.slug == slug,
        Issue.number == number,
    )
    return db.scalars(stmt).first()
