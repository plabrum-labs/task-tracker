"""A project's wire contract: the payloads its actions accept, and the shapes its
reads return.

See ``../issue/schemas.py``: one input model per action that takes arguments,
each with ``extra="forbid"`` and a ``description`` per field. ``addIssue`` carries
an issue's priority, so it reaches across to ``issue``'s ``WirePriority`` — the
same direction the actions already depend in.
"""

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from tt.domains.issue.enums import Status as IssueStatus
from tt.domains.issue.enums import StatusCounts
from tt.domains.issue.schemas import WirePriority
from tt.domains.project.enums import Status
from tt.platform.actions import REFERENCE


class EditProjectPayload(BaseModel):
    # The whole editable project, sent at once: the caller reads the object,
    # changes what it likes, and sends it back.
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="What to call the project.")
    body: str = Field(description="The project's description. Blank clears it.")
    status: Status = Field(description="Whether the project is still being worked on.")
    path: str | None = Field(
        description="The directory bare tt opens this project in. Blank clears it."
    )


class AddIssuePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="What to call the issue.")
    body: str | None = Field(default=None, description="What the issue is about.")
    status: IssueStatus | None = Field(
        default=None, description="Where the issue is up to. Defaults to todo."
    )
    priority: WirePriority | None = Field(
        default=None, description="How far up the list it sorts. Defaults to medium."
    )


class AddEpicPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="What to call the epic.")
    body: str | None = Field(default=None, description="What the epic is about.")
    due_date: date | None = Field(default=None, description="When the epic is due, as YYYY-MM-DD.")


class AddMilestonePayload(BaseModel):
    # A milestone is project-level, so its create hangs off the project like
    # ``addEpic``; the epic is an optional label it may be filed under.
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="What to call the milestone.")
    due_date: date | None = Field(
        default=None, description="When the milestone is due, as YYYY-MM-DD."
    )
    epic: Annotated[str | None, REFERENCE] = Field(
        default=None,
        description='The title of the epic to file it under, e.g. "Payments". Optional.',
    )


class CreateProjectPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(description="The short name the project is addressed by.")
    title: str | None = Field(default=None, description="What to call the project.")
    body: str | None = Field(default=None, description="What the project is about.")
    path: str | None = Field(
        default=None, description="The directory bare tt opens this project in."
    )


class SetPathPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="The directory bare tt opens this project in.")


class ProjectListItem(BaseModel):
    slug: str
    title: str
    status: str
    path: str | None
    counts: StatusCounts


class ProjectDetail(BaseModel):
    slug: str
    title: str
    body: str
    status: str
    path: str | None
    counts: StatusCounts
    created_at: datetime
    updated_at: datetime
