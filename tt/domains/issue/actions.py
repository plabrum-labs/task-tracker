"""Everything an issue can be asked.

One action is one class: its object type, its key, its label, its two hooks and
its ``execute``, which holds the deps and mutates the loaded row — an edit sets
columns, a delete stamps ``deleted_at``. The flush is the group's, once, so an
``execute`` only says what it changed. The payload shapes are in ``schemas``, one
model per action.

None of the four edits refuses anything. Many issues may be ``doing`` at once,
there is no WIP rule, so both hooks are the default in every case and the refusals
are all ``execute``'s. Each action registers by decorating itself with
``issue_actions``.
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
class EditTitle(ObjectAction[Issue, schemas.EditTitlePayload]):
    KEY = "editTitle"
    LABEL = "Edit title"
    Payload = schemas.EditTitlePayload

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.EditTitlePayload, deps: ActionDeps
    ) -> ActionResponse:
        title = payload.title.strip()
        if not title:
            raise Invalid("title is required")
        obj.title = title
        return ActionResponse(message=f"{obj.subject()}: saved")


@issue_actions
class EditBody(ObjectAction[Issue, schemas.EditBodyPayload]):
    # The same payload shape as ``editTitle`` and a different rule: a blank body is
    # how you clear one. Two actions the schema cannot tell apart.
    KEY = "editBody"
    LABEL = "Edit body"
    Payload = schemas.EditBodyPayload

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.EditBodyPayload, deps: ActionDeps
    ) -> ActionResponse:
        obj.body = payload.body
        return ActionResponse(message=f"{obj.subject()}: saved")


@issue_actions
class EditStatus(ObjectAction[Issue, schemas.EditStatusPayload]):
    # The note describes the status it arrived with, so moving without one clears
    # the old note rather than leaving it to describe a state the issue is no
    # longer in.
    KEY = "editStatus"
    LABEL = "Edit status"
    Payload = schemas.EditStatusPayload

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.EditStatusPayload, deps: ActionDeps
    ) -> ActionResponse:
        obj.status = payload.status
        obj.status_note = payload.note
        return ActionResponse(message=f"{obj.subject()}: saved")


@issue_actions
class EditPriority(ObjectAction[Issue, schemas.EditPriorityPayload]):
    KEY = "editPriority"
    LABEL = "Edit priority"
    Payload = schemas.EditPriorityPayload

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.EditPriorityPayload, deps: ActionDeps
    ) -> ActionResponse:
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
