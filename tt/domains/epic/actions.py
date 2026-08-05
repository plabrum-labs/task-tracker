"""Everything an epic can be asked.

One action is one class, as in ``../issue/actions.py``: a whole-object ``edit``,
the focused ``setStatus`` and ``setDueDate`` verbs it also carries, and a
``delete``. The one precondition an epic has is ``delete``: an epic still holding
live issues refuses, so its issues are reassigned or closed before it goes and no
live issue is left pointing at a deleted epic. The flush is the group's, once.
Each action registers by decorating itself with ``epic_actions``.
"""

from datetime import UTC, datetime

from tt.domains.epic import queries, schemas
from tt.domains.epic.models import Epic
from tt.domains.milestone.models import Milestone
from tt.platform.actions import (
    ActionDeps,
    ActionGroup,
    ActionResponse,
    Empty,
    Invalid,
    ObjectAction,
)
from tt.platform.sequences import next_number

epic_actions: ActionGroup[Epic] = ActionGroup("epic", locate=queries.resolve_ref)


def _issues(n: int) -> str:
    return "1 issue" if n == 1 else f"{n} issues"


@epic_actions
class EditEpic(ObjectAction[Epic, schemas.EditEpicPayload]):
    # One whole-object edit: the payload carries every editable field and each is
    # set. Status and due date ride along here like any other field; the focused
    # moves are ``SetStatus`` and ``SetDueDate``.
    KEY = "edit"
    LABEL = "Edit"
    Payload = schemas.EditEpicPayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Epic, payload: schemas.EditEpicPayload, deps: ActionDeps
    ) -> ActionResponse:
        title = payload.title.strip()
        if not title:
            raise Invalid("title is required")
        obj.title = title
        obj.body = payload.body
        obj.status = payload.status
        obj.due_date = payload.due_date
        return ActionResponse(message=f"{obj.subject()}: saved")


@epic_actions
class SetStatus(ObjectAction[Epic, schemas.SetStatusPayload]):
    # The direct status move. No transition rules — any status may follow any other,
    # exactly as the edit allows.
    KEY = "setStatus"
    LABEL = "Set status"
    Payload = schemas.SetStatusPayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Epic, payload: schemas.SetStatusPayload, deps: ActionDeps
    ) -> ActionResponse:
        obj.status = payload.status
        return ActionResponse(message=f"{obj.subject()}: {payload.status}")


@epic_actions
class SetDueDate(ObjectAction[Epic, schemas.SetDueDatePayload]):
    # The direct due-date move. A null payload clears it — no date is a real state,
    # not a refusal.
    KEY = "setDueDate"
    LABEL = "Set due date"
    Payload = schemas.SetDueDatePayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Epic, payload: schemas.SetDueDatePayload, deps: ActionDeps
    ) -> ActionResponse:
        obj.due_date = payload.due_date
        when = payload.due_date.isoformat() if payload.due_date is not None else "cleared"
        return ActionResponse(message=f"{obj.subject()}: due {when}")


@epic_actions
class AddMilestone(ObjectAction[Epic, schemas.AddMilestonePayload]):
    # An action on an epic writing to the milestones table, the mirror of a
    # project's ``AddIssue``/``AddEpic``. The store assigns the id, so the message
    # reads the row it wrote.
    KEY = "addMilestone"
    LABEL = "Add milestone"
    Payload = schemas.AddMilestonePayload

    @classmethod
    def execute(
        cls, obj: Epic, payload: schemas.AddMilestonePayload, deps: ActionDeps
    ) -> ActionResponse:
        title = payload.title.strip()
        if not title:
            raise Invalid("title is required")
        milestone = Milestone(
            epic=obj,
            # The epic never moves projects, so its project is the milestone's for
            # good — set here so the number is drawn against it and the ref reads it.
            project=obj.project,
            number=next_number(deps.tx, obj.project_id, "milestone"),
            title=title,
            due_date=payload.due_date,
            issues=[],
        )
        deps.tx.add(milestone)
        deps.tx.flush()
        deps.tx.refresh(milestone, ["created_at", "updated_at"])
        return ActionResponse(message=f"{milestone.subject()}: created", created_id=milestone.id)


@epic_actions
class Delete(ObjectAction[Epic, Empty]):
    # The soft delete, refused while the epic still holds live issues so no live
    # issue is left pointing at a deleted epic. The count in the reason is this
    # epic's, read off the loaded rows.
    KEY = "delete"
    LABEL = "Delete"
    Payload = Empty

    @classmethod
    def is_disabled(cls, obj: Epic) -> str | None:
        open_issues = obj.issue_count()
        if open_issues > 0:
            return f"reassign or close {_issues(open_issues)} first"
        return None

    @classmethod
    def execute(cls, obj: Epic, payload: Empty, deps: ActionDeps) -> ActionResponse:
        obj.deleted_at = datetime.now(UTC)
        return ActionResponse(message=f"{obj.subject()}: deleted")
