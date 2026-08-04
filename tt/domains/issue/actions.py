"""Everything an issue can be asked.

One action is one class: its object type, its key, its label, its two hooks and
its ``execute``, which holds the deps and mutates the loaded row — an edit sets
every editable column, a delete stamps ``deleted_at``. The flush is the group's,
once, so an ``execute`` only says what it changed. The payload shapes are in
``schemas``, one model per action.

Neither the edit nor the delete refuses anything. Many issues may be ``doing`` at
once, there is no WIP rule, so both hooks are the default in every case and the
only refusal is the blank title ``execute`` states. Each action registers by
decorating itself with ``issue_actions``.
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
    # set. Status rides along here like any other field.
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
        return ActionResponse(message=f"{obj.subject()}: saved")


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
