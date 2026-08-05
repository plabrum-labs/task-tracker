"""Reads against the comments table: one by id.

The read joins the issue and loads it onto the comment with ``contains_eager``,
and loads that issue's project so the issue's ``ref`` reads its slug once the
session closes — the ref is what ``comment show`` reports. Liveness is the
comment's own ``deleted_at``, and it gates on the issue being live too, one level
up, exactly as ``milestone/queries.py`` gates on the epic. There is no
``list_comments``: comments are listed by showing the issue that owns them, so
there is no global comment list to invent.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager

from tt.domains.comment.models import Comment
from tt.domains.issue.models import Issue

_loaded = (
    select(Comment)
    .join(Comment.issue)
    .options(
        contains_eager(Comment.issue).selectinload(Issue.project),
    )
    .order_by(Comment.created_at.asc())
)


def get_comment(db: Session, comment_id: int) -> Comment | None:
    """The one live comment of a live issue by global id, or ``None``."""
    stmt = _loaded.where(
        Comment.deleted_at.is_(None),
        Issue.deleted_at.is_(None),
        Comment.id == comment_id,
    )
    return db.scalars(stmt).first()
