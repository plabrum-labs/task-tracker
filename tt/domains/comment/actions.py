"""Everything a comment can be asked.

A comment is the thinnest child, so its actions are two, both always available: a
whole-object ``edit`` over its body, and a ``delete``. A comment is created
through the issue it hangs off — ``issue.addComment`` — so there is no create
here. The flush is the group's, once. Each action registers by decorating itself
with ``comment_actions``.
"""

from datetime import UTC, datetime

from tt.domains.comment import queries, schemas
from tt.domains.comment.models import Comment
from tt.platform.actions import (
    ActionDeps,
    ActionGroup,
    ActionResponse,
    Empty,
    Invalid,
    ObjectAction,
)

comment_actions: ActionGroup[Comment] = ActionGroup("comment", locate=queries.get_comment)


@comment_actions
class EditComment(ObjectAction[Comment, schemas.EditCommentPayload]):
    # One whole-object edit: the payload carries the body and it is set. A blank
    # body is refused — a comment with nothing in it is not a note.
    KEY = "edit"
    LABEL = "Edit"
    Payload = schemas.EditCommentPayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Comment, payload: schemas.EditCommentPayload, deps: ActionDeps
    ) -> ActionResponse:
        body = payload.body.strip()
        if not body:
            raise Invalid("body is required")
        obj.body = body
        return ActionResponse(message=f"{obj.subject()}: saved")


@comment_actions
class Delete(ObjectAction[Comment, Empty]):
    # The soft delete, which stamps ``deleted_at`` like every other domain's. A
    # comment owns nothing, so there is nothing for it to refuse over.
    KEY = "delete"
    LABEL = "Delete"
    Payload = Empty

    @classmethod
    def execute(cls, obj: Comment, payload: Empty, deps: ActionDeps) -> ActionResponse:
        obj.deleted_at = datetime.now(UTC)
        return ActionResponse(message=f"{obj.subject()}: deleted")
