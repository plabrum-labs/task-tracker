"""The milestone domain's one public surface.

As ``../epic/api.py``: a frontend hands it an engine, never a session, and every
database call is wrapped in ``@with_transaction`` so one public call is one
transaction. ``milestone_list``/``milestone_get``/``milestone_detail`` read,
``milestone_action`` writes, and ``action_schemas`` lists what the CLI turns into
subcommands. Each is a thin call onto ``milestone_actions``.
"""

from typing import Any

from sqlalchemy.orm import Session

from tt.domains.milestone import queries, schemas
from tt.domains.milestone.actions import milestone_actions
from tt.domains.milestone.models import Milestone
from tt.platform.actions import ActionDeps, ActionResponse, Field, Invalid, Offer
from tt.platform.db import with_transaction
from tt.platform.refs import NamedRef

# --- reads ----------------------------------------------------------------


def _epic_title(milestone: Milestone) -> str | None:
    """The title of the epic the milestone is filed under, or ``None`` when it is
    unfiled. The epic is an optional label on a project-level milestone, and a
    soft-deleted one reads as no epic — a milestone outlives the epic it was under."""
    epic = milestone.epic
    return epic.title if epic is not None and epic.deleted_at is None else None


def _list_item(milestone: Milestone) -> schemas.MilestoneListItem:
    return schemas.MilestoneListItem(
        id=milestone.id,
        project=milestone.project.slug,
        epic=_epic_title(milestone),
        title=milestone.title,
        due_date=milestone.due_date,
        counts=milestone.counts,
    )


def _detail(milestone: Milestone) -> schemas.MilestoneDetail:
    return schemas.MilestoneDetail(
        id=milestone.id,
        project=milestone.project.slug,
        epic=_epic_title(milestone),
        title=milestone.title,
        due_date=milestone.due_date,
        counts=milestone.counts,
        created_at=milestone.created_at,
        updated_at=milestone.updated_at,
    )


@with_transaction
def milestone_list(
    tx: Session, project_slug: str, epic_id: int | None = None
) -> list[schemas.MilestoneListItem]:
    return [_list_item(m) for m in queries.list_milestones(tx, project_slug, epic_id)]


@with_transaction
def milestone_get(tx: Session, project_slug: str, title: str) -> schemas.MilestoneDetail | None:
    milestone = queries.resolve_by_title(tx, NamedRef(project_slug, title))
    return _detail(milestone) if milestone is not None else None


@with_transaction
def milestone_detail(
    tx: Session, project_slug: str, title: str
) -> tuple[schemas.MilestoneDetail, list[Offer]]:
    """A milestone and what it offers, for a detail view."""
    address = NamedRef(project_slug, title)
    milestone = queries.resolve_by_title(tx, address)
    if milestone is None:
        raise Invalid(f"no milestone {address!r}")
    return _detail(milestone), milestone_actions.offers(milestone)


# --- write ----------------------------------------------------------------


@with_transaction
def milestone_action(
    tx: Session, key: str, payload: Any, project_slug: str, title: str
) -> ActionResponse:
    """Run an action by key against the addressed milestone. Availability is
    enforced against the live row, so what a detail view reported stays a
    snapshot."""
    return milestone_actions.trigger(ActionDeps(tx), key, payload, NamedRef(project_slug, title))


# --- codegen --------------------------------------------------------------


def action_schemas() -> list[tuple[str, list[Field]]]:
    """Every key a milestone answers to, with the fields its payload asks for, for
    the CLI to grow a subcommand per action."""
    return milestone_actions.action_schemas()
