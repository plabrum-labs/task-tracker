"""Everything an epic can be asked.

One action is one class, as in ``../issue/actions.py``: a whole-object ``edit``,
the focused ``setStatus`` and ``setDueDate`` verbs it also carries, and a
``delete``. The one precondition an epic has is ``delete``: an epic still holding
live issues refuses, so its issues are reassigned or closed before it goes and no
live issue is left pointing at a deleted epic. The flush is the group's, once.
Each action registers by decorating itself with ``epic_actions``. Creating an
epic is a project's ``addEpic``, in ``../project/actions.py``, and so is creating a
milestone — a milestone is project-level, not an epic's child.
"""

from datetime import UTC, datetime

from tt.domains.epic import queries, schemas
from tt.domains.epic.models import Epic
from tt.platform.actions import (
    ActionDeps,
    ActionGroup,
    ActionResponse,
    Conflict,
    Empty,
    Invalid,
    ObjectAction,
)

epic_actions: ActionGroup[Epic] = ActionGroup("epic", locate=queries.resolve_by_title)


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
        # An epic is addressed by title within its project, so a rename onto one a
        # live sibling already holds is refused — the partial unique index guarantees
        # it; this is what turns that into a sentence.
        if queries.title_owner(deps.tx, obj.project_id, title, exclude_id=obj.id) is not None:
            raise Conflict(f'an epic "{title}" already exists')
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
