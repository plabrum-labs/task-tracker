"""The payload shapes an issue's actions accept — the wire contract, in one place.

One model per action that takes arguments. Each carries ``extra="forbid"`` so the
decoder refuses an unadvertised field, and every field's ``description`` is what
``wire`` puts in the advertised schema — the ``--help`` text of a CLI option and
the label beside a TUI field. An action in ``actions.py`` names ``Payload =
schemas.<Name>`` and states nothing about how it is decoded.
"""

from pydantic import BaseModel, ConfigDict, Field

from tt.domains.issue import Priority, Status


class EditTitlePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="What to call the issue.")


class EditBodyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(description="The issue's description. Blank clears it.")


class EditStatusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status = Field(description="Where the issue is up to.")
    note: str | None = Field(default=None, description="What to record about the move.")


class EditPriorityPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: Priority = Field(description="How far up the list the issue sorts.")
