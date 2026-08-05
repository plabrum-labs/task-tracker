"""The comment domain's one public surface.

As ``../milestone/api.py``: a frontend hands it an engine, never a session, and
every database call is wrapped in ``@with_transaction`` so one public call is one
transaction. ``comment_get``/``comment_detail`` read, ``comment_action`` writes,
and ``action_schemas`` lists what the CLI turns into subcommands. Each is a thin
call onto ``comment_actions``. A comment is addressed by its global id, not a ref
— it has no project-scoped number of its own.
"""

from typing import Any

from sqlalchemy.orm import Session

from tt.domains.comment import queries, schemas
from tt.domains.comment.actions import comment_actions
from tt.domains.comment.models import Comment
from tt.platform.actions import ActionDeps, ActionResponse, Field, Invalid, Offer
from tt.platform.db import with_transaction

# --- reads ----------------------------------------------------------------


def _detail(comment: Comment) -> schemas.CommentDetail:
    return schemas.CommentDetail(
        id=comment.id,
        body=comment.body,
        issue=comment.issue.ref,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )


@with_transaction
def comment_get(tx: Session, comment_id: int) -> schemas.CommentDetail | None:
    comment = queries.get_comment(tx, comment_id)
    return _detail(comment) if comment is not None else None


@with_transaction
def comment_detail(tx: Session, comment_id: int) -> tuple[schemas.CommentDetail, list[Offer]]:
    """A comment and what it offers, for a detail view."""
    comment = queries.get_comment(tx, comment_id)
    if comment is None:
        raise Invalid(f"no comment {comment_id}")
    return _detail(comment), comment_actions.offers(comment)


# --- write ----------------------------------------------------------------


@with_transaction
def comment_action(tx: Session, key: str, payload: Any, comment_id: int) -> ActionResponse:
    """Run an action by key against the addressed comment. Availability is enforced
    against the live row, so what a detail view reported stays a snapshot."""
    return comment_actions.trigger(ActionDeps(tx), key, payload, comment_id)


# --- codegen --------------------------------------------------------------


def action_schemas() -> list[tuple[str, list[Field]]]:
    """Every key a comment answers to, with the fields its payload asks for, for
    the CLI to grow a subcommand per action."""
    return comment_actions.action_schemas()
