"""A comment's wire contract: the payload its edit accepts, and the shapes its
reads return.

A comment is thin: its whole-object edit carries only a body. ``AddCommentPayload``
is an issue's, in ``../issue/schemas.py``, because ``addComment`` hangs off an
issue. ``CommentView`` is what an issue's detail embeds for each of its comments;
``CommentDetail`` adds the issue it belongs to, for ``comment show``.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from tt.platform.actions import MULTILINE


class EditCommentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: Annotated[str, MULTILINE] = Field(description="The comment's text.")


class CommentView(BaseModel):
    id: int
    body: str
    created_at: datetime
    updated_at: datetime


class CommentDetail(BaseModel):
    id: int
    body: str
    issue: str
    created_at: datetime
    updated_at: datetime
