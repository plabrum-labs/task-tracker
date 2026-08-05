"""Everything an issue can be asked.

One action is one class: its object type, its key, its label, its two hooks and
its ``execute``, which holds the deps and mutates the loaded row — an edit sets
every editable column, a delete stamps ``deleted_at``. The flush is the group's,
once, so an ``execute`` only says what it changed. The payload shapes are in
``schemas``, one model per action.

Nothing an issue offers refuses anything. Many issues may be ``doing`` at once,
there is no WIP rule and no status machine, so every action's hooks are the
default and the only refusal is the blank title ``EditIssue.execute`` states. Each
action registers by decorating itself with ``issue_actions``.
"""

from datetime import UTC, datetime

from tt.domains.issue import queries, schemas
from tt.domains.issue.models import Issue
from tt.platform.actions import (
    ActionDeps,
    ActionGroup,
    ActionResponse,
    Empty,
    Invalid,
    ObjectAction,
)

issue_actions: ActionGroup[Issue] = ActionGroup("issue", locate=queries.get_issue)


@issue_actions
class EditIssue(ObjectAction[Issue, schemas.EditIssuePayload]):
    # One whole-object edit: the payload carries every editable field and each is
    # set. Status rides along here like any other field; the focused move is
    # ``SetStatus``.
    KEY = "edit"
    LABEL = "Edit"
    Payload = schemas.EditIssuePayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.EditIssuePayload, deps: ActionDeps
    ) -> ActionResponse:
        title = payload.title.strip()
        if not title:
            raise Invalid("title is required")
        obj.title = title
        obj.body = payload.body
        obj.status = payload.status
        obj.priority = payload.priority
        obj.due_date = payload.due_date
        return ActionResponse(message=f"{obj.subject()}: saved")


@issue_actions
class SetStatus(ObjectAction[Issue, schemas.SetStatusPayload]):
    # The direct status move. The whole-object edit still carries status; this is
    # the one-field verb a frontend binds to a quick key. No transition rules —
    # any status may follow any other, exactly as the edit allows.
    KEY = "setStatus"
    LABEL = "Set status"
    Payload = schemas.SetStatusPayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.SetStatusPayload, deps: ActionDeps
    ) -> ActionResponse:
        obj.status = payload.status
        return ActionResponse(message=f"{obj.subject()}: {payload.status}")


@issue_actions
class SetDueDate(ObjectAction[Issue, schemas.SetDueDatePayload]):
    # The direct due-date move, the mirror of ``SetStatus``. The whole-object edit
    # still carries the due date; this is the one-field verb. A null payload clears
    # it — no date is a real state, not a refusal.
    KEY = "setDueDate"
    LABEL = "Set due date"
    Payload = schemas.SetDueDatePayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.SetDueDatePayload, deps: ActionDeps
    ) -> ActionResponse:
        obj.due_date = payload.due_date
        when = payload.due_date.isoformat() if payload.due_date is not None else "cleared"
        return ActionResponse(message=f"{obj.subject()}: due {when}")


@issue_actions
class Delete(ObjectAction[Issue, Empty]):
    # The soft delete, which is an update like any other — the column it sets is
    # not one the edits touch, and every read filters it out, so the row vanishes.
    KEY = "delete"
    LABEL = "Delete"
    Payload = Empty

    @classmethod
    def execute(cls, obj: Issue, payload: Empty, deps: ActionDeps) -> ActionResponse:
        obj.deleted_at = datetime.now(UTC)
        return ActionResponse(message=f"{obj.subject()}: deleted")
