"""The projects table, and the only file that names it.

The split is the same one ``wire`` makes for JSON: ``Project`` names no column
and no query. The projections and the writes live here; the tables they run
against are ``domains.schema``'s.

The counts come from ``domains.issue.services.counts`` rather than from a query
here. They read the issues table, and the only rule that makes this split mean
anything is that a domain's services are the sole namer of its own table.

The projects table is imported as ``_projects`` because ``projects`` names a
public read here — the Rust ``services::projects`` — and a module cannot bind
both.
"""

from typing import Any, cast

from sqlalchemy import CursorResult, Row, insert, select, update
from sqlalchemy.orm import Session

from tt.domains.issue.services import counts
from tt.domains.project import Draft, Project, Status
from tt.domains.schema import projects as _projects
from tt.platform import clock
from tt.platform.db import MissingAfterInsert
from tt.platform.deleted import Deleted

# --- reading --------------------------------------------------------------

_ordered = select(_projects).order_by(_projects.c.created_at.asc())


def _to_project(row: Row[Any], tally: dict[int, tuple[int, int, int]]) -> Project:
    todo, doing, done = tally.get(row.id, (0, 0, 0))
    return Project(
        id=row.id,
        slug=row.slug,
        title=row.title,
        body=row.body,
        status=Status(row.status),
        todo=todo,
        doing=doing,
        done=done,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def projects(session: Session) -> list[Project]:
    """Live projects in creation order, each carrying its issue counts. The counts
    are what ``domains.project.actions``' refusals read, so a project loaded any
    other way would offer a menu it could not justify."""
    rows = session.execute(_ordered.where(_projects.c.deleted_at.is_(None))).all()
    tally = counts(session)
    return [_to_project(row, tally) for row in rows]


def project(session: Session, slug: str) -> Project | None:
    row = session.execute(
        _ordered.where(_projects.c.deleted_at.is_(None), _projects.c.slug == slug)
    ).first()
    if row is None:
        return None
    return _to_project(row, counts(session))


def _project_by_id(session: Session, id: int) -> Project | None:
    """Like ``project`` but keyed by id, so a create can read its row back through
    the same projection every read uses."""
    row = session.execute(
        _ordered.where(_projects.c.deleted_at.is_(None), _projects.c.id == id)
    ).first()
    if row is None:
        return None
    return _to_project(row, counts(session))


def trashed_projects(session: Session) -> list[Deleted[Project]]:
    """The trash is by the row's own ``deleted_at`` and nothing else.

    A project going does not put its issues here — it hides them, because
    ``issue.services.issues`` requires a live project. That is what makes restoring
    a project bring back exactly the issues that were not deleted in their own
    right: one row was written on the way out, so one row is cleared on the way
    back."""
    rows = session.execute(_ordered.where(_projects.c.deleted_at.is_not(None))).all()
    tally = counts(session)
    return [Deleted(inner=_to_project(row, tally), deleted_at=row.deleted_at) for row in rows]


# --- writing --------------------------------------------------------------


def update_project(session: Session, project: Project) -> None:
    """``updated_at`` is stamped here rather than by the action, so an ``execute``
    states only the columns it changed and the clock stays in one place.

    ``slug`` is not written by anything: no action changes it, and the partial
    unique index it sits under is the last thing an edit should be able to trip."""
    session.execute(
        update(_projects)
        .where(_projects.c.id == project.id)
        .values(
            title=project.title,
            body=project.body,
            status=project.status.value,
            updated_at=clock.now(),
        )
    )


def delete_project(session: Session, project: Project) -> None:
    """The soft delete, which is an update like any other — which is exactly why
    the type of what an action returned cannot say which of these to call."""
    stamp = clock.now()
    session.execute(
        update(_projects)
        .where(_projects.c.id == project.id)
        .values(deleted_at=stamp, updated_at=stamp)
    )


def restore_project(session: Session, deleted: Deleted[Project]) -> None:
    """Writing NULL clears the stamp, the same path an assignment takes."""
    session.execute(
        update(_projects)
        .where(_projects.c.id == deleted.inner.id)
        .values(deleted_at=None, updated_at=clock.now())
    )


def create_project(session: Session, draft: Draft) -> Project:
    """Creation. The insert assigns the id; the load that follows is for the
    projection rather than the id — a ``Project`` carries counts that are not on
    the row that was written. Reading it back through the same projection every
    read uses makes the returned value the stored object rather than a hopeful
    copy of the draft."""
    stamp = clock.now()
    # ``session.execute`` is typed to the base ``Result``; an INSERT returns a
    # ``CursorResult`` at run time, which is what carries the assigned id.
    result = cast(
        "CursorResult[Any]",
        session.execute(
            insert(_projects).values(
                slug=draft.slug,
                title=draft.title,
                body=draft.body,
                status=Status.ACTIVE.value,
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
    created = _project_by_id(session, new_id)
    if created is None:
        raise MissingAfterInsert("the project")
    return created
