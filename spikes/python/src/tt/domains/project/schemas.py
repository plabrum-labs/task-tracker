"""The payload shapes a project's actions accept — the wire contract, in one place.

See ``../issue/schemas.py``: one model per action that takes arguments, each with
``extra="forbid"`` and a ``description`` per field. ``addIssue`` carries an issue's
priority, so this refers across to ``issue.Priority`` — the same direction
``services.py`` already depends in.
"""

from pydantic import BaseModel, ConfigDict, Field

from tt.domains.issue import Priority
from tt.domains.project import Status


class EditTitlePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="What to call the project.")


class EditBodyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(description="The project's description. Blank clears it.")


class EditStatusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status = Field(description="Whether the project is still being worked on.")


class AddIssuePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="What to call the issue.")
    body: str | None = Field(default=None, description="What the issue is about.")
    priority: Priority | None = Field(
        default=None, description="How far up the list it sorts. Defaults to normal."
    )


class CreateProjectPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(description="The short name the project is addressed by.")
    title: str | None = Field(default=None, description="What to call the project.")
    body: str | None = Field(default=None, description="What the project is about.")
