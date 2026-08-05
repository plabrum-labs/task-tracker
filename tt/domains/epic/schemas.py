"""An epic's wire contract: the payloads its actions accept, and the shapes its
reads return.

See ``../issue/schemas.py``: one input model per action that takes arguments,
each with ``extra="forbid"`` and a ``description`` per field. The whole-object
edit carries ``status`` and ``due_date`` too; the focused ``setStatus`` and
``setDueDate`` are the one-field verbs a frontend binds to a key. ``AddEpicPayload``
is a project's, in ``../project/schemas.py``, because ``addEpic`` hangs off a
project.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from tt.domains.epic.enums import Status


class EditEpicPayload(BaseModel):
    # The whole editable epic, sent at once: the caller reads the object, changes
    # what it likes, and sends it back.
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="What to call the epic.")
    body: str = Field(description="The epic's description. Blank clears it.")
    status: Status = Field(description="Whether the epic is still being worked on.")
    due_date: date | None = Field(
        description="When the epic is due, as YYYY-MM-DD. Blank clears it."
    )


class SetStatusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status = Field(description="Whether the epic is still being worked on.")


class SetDueDatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_date: date | None = Field(
        description="When the epic is due, as YYYY-MM-DD. Blank clears it."
    )


class AddMilestonePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="What to call the milestone.")
    due_date: date | None = Field(
        default=None, description="When the milestone is due, as YYYY-MM-DD."
    )


class EpicListItem(BaseModel):
    id: int
    ref: str
    project: str
    title: str
    status: str
    due_date: date | None
    backlog: int
    planning: int
    todo: int
    doing: int
    done: int


class EpicDetail(BaseModel):
    id: int
    ref: str
    project: str
    title: str
    body: str
    status: str
    due_date: date | None
    backlog: int
    planning: int
    todo: int
    doing: int
    done: int
    created_at: datetime
    updated_at: datetime
