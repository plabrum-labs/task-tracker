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

# --- reads ----------------------------------------------------------------


def _epic_ref(milestone: Milestone) -> str:
    """The ref of the epic the milestone belongs to. The epic shares the milestone's
    project, so the ref is that slug and the epic's project-scoped number."""
    return f"{milestone.project.slug}-{milestone.epic.number}"


def _list_item(milestone: Milestone) -> schemas.MilestoneListItem:
    return schemas.MilestoneListItem(
        id=milestone.id,
        ref=milestone.ref,
        epic=_epic_ref(milestone),
        title=milestone.title,
        due_date=milestone.due_date,
        backlog=milestone.backlog,
        blocked=milestone.blocked,
        todo=milestone.todo,
        doing=milestone.doing,
        done=milestone.done,
        canceled=milestone.canceled,
    )


def _detail(milestone: Milestone) -> schemas.MilestoneDetail:
    return schemas.MilestoneDetail(
        id=milestone.id,
        ref=milestone.ref,
        epic=_epic_ref(milestone),
        title=milestone.title,
        due_date=milestone.due_date,
        backlog=milestone.backlog,
        blocked=milestone.blocked,
        todo=milestone.todo,
        doing=milestone.doing,
        done=milestone.done,
        canceled=milestone.canceled,
        created_at=milestone.created_at,
        updated_at=milestone.updated_at,
    )


@with_transaction
def milestone_list(tx: Session, epic_id: int) -> list[schemas.MilestoneListItem]:
    return [_list_item(m) for m in queries.list_milestones(tx, epic_id)]


@with_transaction
def milestone_get(tx: Session, ref: str) -> schemas.MilestoneDetail | None:
    milestone = queries.resolve_ref(tx, ref)
    return _detail(milestone) if milestone is not None else None


@with_transaction
def milestone_detail(tx: Session, ref: str) -> tuple[schemas.MilestoneDetail, list[Offer]]:
    """A milestone and what it offers, for a detail view."""
    milestone = queries.resolve_ref(tx, ref)
    if milestone is None:
        raise Invalid(f"no milestone {ref}")
    return _detail(milestone), milestone_actions.offers(milestone)


# --- write ----------------------------------------------------------------


@with_transaction
def milestone_action(tx: Session, key: str, payload: Any, ref: str) -> ActionResponse:
    """Run an action by key against the addressed milestone. Availability is
    enforced against the live row, so what a detail view reported stays a
    snapshot."""
    return milestone_actions.trigger(ActionDeps(tx), key, payload, ref)


# --- codegen --------------------------------------------------------------


def action_schemas() -> list[tuple[str, list[Field]]]:
    """Every key a milestone answers to, with the fields its payload asks for, for
    the CLI to grow a subcommand per action."""
    return milestone_actions.action_schemas()
