"""A milestone's wire contract: the payloads its actions accept, and the shapes
its reads return.

A milestone is thin: its whole-object edit carries only a title and an optional
due date, and the focused ``setDueDate`` is the one-field verb. ``AddMilestonePayload``
is an epic's, in ``../epic/schemas.py``, because ``addMilestone`` hangs off an epic.
The reads surface the epic the milestone belongs to and its derived issue counts.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class EditMilestonePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="What to call the milestone.")
    due_date: date | None = Field(
        description="When the milestone is due, as YYYY-MM-DD. Blank clears it."
    )


class SetDueDatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_date: date | None = Field(
        description="When the milestone is due, as YYYY-MM-DD. Blank clears it."
    )


class MilestoneListItem(BaseModel):
    id: int
    ref: str
    epic: str
    title: str
    due_date: date | None
    backlog: int
    blocked: int
    todo: int
    doing: int
    done: int


class MilestoneDetail(BaseModel):
    id: int
    ref: str
    epic: str
    title: str
    due_date: date | None
    backlog: int
    blocked: int
    todo: int
    doing: int
    done: int
    created_at: datetime
    updated_at: datetime
