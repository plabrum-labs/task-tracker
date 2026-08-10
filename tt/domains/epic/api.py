"""The epic domain's one public surface.

As ``../issue/api.py``: a frontend hands it an engine, never a session, and every
database call is wrapped in ``@with_transaction`` so one public call is one
transaction. ``epic_list``/``epic_get``/``epic_detail`` read, ``epic_action``
writes, and ``action_schemas`` lists what the CLI turns into subcommands. Each is
a thin call onto ``epic_actions``, the group the actions register with.
"""

from typing import Any

from sqlalchemy.orm import Session

from tt.domains.epic import queries, schemas
from tt.domains.epic.actions import epic_actions
from tt.domains.epic.models import Epic
from tt.platform.actions import ActionDeps, ActionResponse, Field, Invalid, Offer, name_of
from tt.platform.db import with_transaction
from tt.platform.refs import NamedRef

# --- reads ----------------------------------------------------------------


def _list_item(epic: Epic) -> schemas.EpicListItem:
    return schemas.EpicListItem(
        id=epic.id,
        project=epic.project.slug,
        title=epic.title,
        status=name_of(epic.status),
        due_date=epic.due_date,
        counts=epic.counts,
    )


def _detail(epic: Epic) -> schemas.EpicDetail:
    return schemas.EpicDetail(
        id=epic.id,
        project=epic.project.slug,
        title=epic.title,
        body=epic.body,
        status=name_of(epic.status),
        due_date=epic.due_date,
        counts=epic.counts,
        created_at=epic.created_at,
        updated_at=epic.updated_at,
    )


@with_transaction
def epic_list(tx: Session, project_slug: str) -> list[schemas.EpicListItem]:
    return [_list_item(epic) for epic in queries.list_epics(tx, project_slug)]


@with_transaction
def epic_get(tx: Session, project_slug: str, title: str) -> schemas.EpicDetail | None:
    epic = queries.resolve_by_title(tx, NamedRef(project_slug, title))
    return _detail(epic) if epic is not None else None


@with_transaction
def epic_detail(
    tx: Session, project_slug: str, title: str
) -> tuple[schemas.EpicDetail, list[Offer]]:
    """An epic and what it offers, for a detail view."""
    address = NamedRef(project_slug, title)
    epic = queries.resolve_by_title(tx, address)
    if epic is None:
        raise Invalid(f"no epic {address!r}")
    return _detail(epic), epic_actions.offers(epic)


# --- write ----------------------------------------------------------------


@with_transaction
def epic_action(
    tx: Session, key: str, payload: Any, project_slug: str, title: str
) -> ActionResponse:
    """Run an action by key against the addressed epic. Availability is enforced
    against the live row, so what a detail view reported stays a snapshot."""
    return epic_actions.trigger(ActionDeps(tx), key, payload, NamedRef(project_slug, title))


# --- codegen --------------------------------------------------------------


def action_schemas() -> list[tuple[str, list[Field]]]:
    """Every key an epic answers to, with the fields its payload asks for, for the
    CLI to grow a subcommand per action."""
    return epic_actions.action_schemas()
