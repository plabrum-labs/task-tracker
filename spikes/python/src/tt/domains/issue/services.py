"""The issues table, and the only file that names it.

The split is the same one ``wire`` makes for JSON: ``Issue`` names no column and
no query. The projections and the writes live here; the tables they run against
are ``domains.schema``'s. Every read carries its liveness predicate by
construction, because a frontend cannot write a query at all.

It names the projects table too, in the join and in the liveness predicate. A
join is a join; what the directory split buys is that no *project* query lives
here, not that the word never appears.

Every read is a fresh query. Nothing is cached, so what a frontend rendered is a
snapshot and a write is checked against the row as it is now.

The issues table is imported as ``_issues`` because ``issues`` names a public
read here — the Rust ``services::issues`` — and a module cannot bind both.
"""

from typing import Any, cast

from sqlalchemy import CursorResult, Row, func, insert, select, update
from sqlalchemy.orm import Session

from tt.domains.issue import Draft, Issue, Priority, Status
from tt.domains.schema import issues as _issues
from tt.domains.schema import projects as _projects
from tt.platform import clock
from tt.platform.db import MissingAfterInsert
from tt.platform.deleted import Deleted

# --- reading --------------------------------------------------------------

# The join is what puts ``project_slug`` on an ``Issue``: it is never a column,
# so every read selects the projects slug alongside the issue's own columns.
_projection = select(
    _issues.c.id,
    _issues.c.project_id,
    _projects.c.slug.label("project_slug"),
    _issues.c.title,
    _issues.c.body,
    _issues.c.status,
    _issues.c.priority,
    _issues.c.status_note,
    _issues.c.created_at,
    _issues.c.updated_at,
    _issues.c.deleted_at,
).join_from(_issues, _projects, _issues.c.project_id == _projects.c.id)

# High priority first, then oldest first. The priority column is an INTEGER
# storing the rank, so ``DESC`` on it sorts high above normal.
_ordered = _projection.order_by(_issues.c.priority.desc(), _issues.c.created_at.asc())


def _to_issue(row: Row[Any]) -> Issue:
    return Issue(
        id=row.id,
        project_id=row.project_id,
        project_slug=row.project_slug,
        title=row.title,
        body=row.body,
        status=Status(row.status),
        priority=Priority.from_rank(row.priority),
        status_note=row.status_note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def counts(session: Session) -> dict[int, tuple[int, int, int]]:
    """The counts, as one grouped query folded in Python.

    It reads the issues table, so it is the issues' to answer even though a
    project is what displays it — see ``domains.project.services`` for the caller.
    Live issues only.
    """
    rows = session.execute(
        select(_issues.c.project_id, _issues.c.status, func.count(_issues.c.id))
        .where(_issues.c.deleted_at.is_(None))
        .group_by(_issues.c.project_id, _issues.c.status)
    ).all()

    tally: dict[int, tuple[int, int, int]] = {}
    for project_id, status, n in rows:
        todo, doing, done = tally.get(project_id, (0, 0, 0))
        match Status(status):
            case Status.TODO:
                tally[project_id] = (todo + n, doing, done)
            case Status.DOING:
                tally[project_id] = (todo, doing + n, done)
            case Status.DONE:
                tally[project_id] = (todo, doing, done + n)
    return tally


def issues(session: Session, project_slug: str) -> list[Issue]:
    """Live issues of a live project.

    Liveness is derived rather than stored: both the issue's and the project's
    ``deleted_at`` must be null, so soft-deleting a project hides its issues with
    one row written and restoring it brings back exactly the issues that were not
    deleted in their own right.
    """
    rows = session.execute(
        _ordered.where(
            _issues.c.deleted_at.is_(None),
            _projects.c.deleted_at.is_(None),
            _projects.c.slug == project_slug,
        )
    ).all()
    return [_to_issue(row) for row in rows]


def issue(session: Session, id: int) -> Issue | None:
    """The same liveness rule as ``issues``: an issue of a deleted project is not
    found here either."""
    row = session.execute(
        _ordered.where(
            _issues.c.deleted_at.is_(None),
            _projects.c.deleted_at.is_(None),
            _issues.c.id == id,
        )
    ).first()
    return _to_issue(row) if row is not None else None


def _issue_by_id(session: Session, id: int) -> Issue | None:
    """Like ``issue`` but asking nothing of the project being live, so a create
    can read its row back through the same projection every read uses."""
    row = session.execute(
        _ordered.where(_issues.c.deleted_at.is_(None), _issues.c.id == id)
    ).first()
    return _to_issue(row) if row is not None else None


def trashed_issues(session: Session) -> list[Deleted[Issue]]:
    """An issue in the trash whose project also went is still worth printing, so
    this is the one read that asks nothing of the project but its slug."""
    rows = session.execute(_ordered.where(_issues.c.deleted_at.is_not(None))).all()
    return [Deleted(inner=_to_issue(row), deleted_at=row.deleted_at) for row in rows]


# --- writing --------------------------------------------------------------


def update_issue(session: Session, issue: Issue) -> None:
    """``project_id`` is not written: an issue does not move between projects.

    ``updated_at`` is stamped here rather than by the action, so an ``execute``
    states only the columns it changed and the clock stays in one place.
    """
    session.execute(
        update(_issues)
        .where(_issues.c.id == issue.id)
        .values(
            title=issue.title,
            body=issue.body,
            status=issue.status.value,
            priority=issue.priority.rank,
            status_note=issue.status_note,
            updated_at=clock.now(),
        )
    )


def delete_issue(session: Session, issue: Issue) -> None:
    """The soft delete, which is an update like any other — which is exactly why
    the type of what an action returned cannot say which of these to call."""
    stamp = clock.now()
    session.execute(
        update(_issues).where(_issues.c.id == issue.id).values(deleted_at=stamp, updated_at=stamp)
    )


def restore_issue(session: Session, deleted: Deleted[Issue]) -> None:
    """Writing NULL clears the stamp, the same path an assignment takes."""
    session.execute(
        update(_issues)
        .where(_issues.c.id == deleted.inner.id)
        .values(deleted_at=None, updated_at=clock.now())
    )


def create_issue(session: Session, draft: Draft) -> Issue:
    """Creation. The insert assigns the id; the load that follows is for the
    projection rather than the id — an ``Issue`` carries its project's slug, which
    is not on the row that was written. Reading it back through the same
    projection every read uses makes the returned value the stored object rather
    than a hopeful copy of the draft."""
    stamp = clock.now()
    # ``session.execute`` is typed to the base ``Result``; an INSERT returns a
    # ``CursorResult`` at run time, which is what carries the assigned id.
    result = cast(
        "CursorResult[Any]",
        session.execute(
            insert(_issues).values(
                project_id=draft.project_id,
                title=draft.title,
                body=draft.body,
                status=Status.TODO.value,
                priority=draft.priority.rank,
                status_note=None,
                created_at=stamp,
                updated_at=stamp,
                deleted_at=None,
            )
        ),
    )
    # A single-row insert always yields its primary key; ``None`` is the batched
    # case this never takes.
    primary_key = result.inserted_primary_key
    assert primary_key is not None
    new_id = primary_key[0]
    created = _issue_by_id(session, new_id)
    if created is None:
        raise MissingAfterInsert("the issue")
    return created
