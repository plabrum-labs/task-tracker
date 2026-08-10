"""A milestone's wire contract: the payloads its actions accept, and the shapes
its reads return.

A milestone is thin: its whole-object edit carries a title, an optional due date,
and the optional epic it is filed under, and the focused ``setDueDate`` is the
one-field verb. ``AddMilestonePayload`` is a project's, in ``../project/schemas.py``,
because a milestone is project-level. The reads surface the optional epic and the
derived issue counts, and a milestone is addressed by its title within a project,
so they carry ``project`` and ``title`` and no ``ref``.
"""

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from tt.domains.issue.enums import StatusCounts
from tt.platform.actions import REFERENCE


class EditMilestonePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="What to call the milestone.")
    due_date: date | None = Field(
        description="When the milestone is due, as YYYY-MM-DD. Blank clears it."
    )
    epic: Annotated[str | None, REFERENCE] = Field(
        description='The title of the epic this milestone is filed under, e.g. "Payments". '
        "Blank clears it."
    )


class SetDueDatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_date: date | None = Field(
        description="When the milestone is due, as YYYY-MM-DD. Blank clears it."
    )


class MilestoneListItem(BaseModel):
    id: int
    project: str
    epic: str | None
    title: str
    due_date: date | None
    counts: StatusCounts


class MilestoneDetail(BaseModel):
    id: int
    project: str
    epic: str | None
    title: str
    due_date: date | None
    counts: StatusCounts
    created_at: datetime
    updated_at: datetime
