"""An issue's wire contract: the payloads its actions accept, and the shapes its
reads return.

Each input model carries ``extra="forbid"`` so the decoder refuses an
unadvertised field, and every field's ``description`` is the ``--help`` text of a
CLI option and the label beside a TUI field. ``WirePriority`` is how a sorting
``IntEnum`` stays human-readable on the wire: it validates ``"high"`` into
``Priority.HIGH`` and serialises the member back to ``"high"``, while the column
and the sort stay integer.

The output schemas are the read contract: ``*ListItem`` for a row in a list and
``*Detail`` for one shown on its own, both plain data with the enums already
rendered to their wire strings.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

from tt.domains.issue.enums import Priority, Status


def _priority_by_name(value: object) -> object:
    """Accept the wire string a plain ``IntEnum`` field would reject. A member or
    an int passes through untouched for the enum's own validator to handle."""
    if isinstance(value, str):
        try:
            return Priority[value.upper()]
        except KeyError:
            return value
    return value


WirePriority = Annotated[
    Priority,
    BeforeValidator(_priority_by_name),
    PlainSerializer(lambda p: p.name.lower(), return_type=str),
]


class EditIssuePayload(BaseModel):
    # The whole editable issue, sent at once: the caller reads the object, changes
    # what it likes, and sends it back. Each field is named for its ``IssueDetail``
    # attribute so a form seeding from a detail is a uniform ``detail[field.name]``
    # lookup.
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="What to call the issue.")
    body: str = Field(description="The issue's description. Blank clears it.")
    status: Status = Field(description="Where the issue is up to.")
    priority: WirePriority = Field(description="How far up the list the issue sorts.")


class SetStatusPayload(BaseModel):
    # The focused status move: the one field the whole-object edit also carries,
    # sent on its own for the frequent workflow action a frontend binds to a key.
    model_config = ConfigDict(extra="forbid")

    status: Status = Field(description="Where the issue is up to.")


class IssueListItem(BaseModel):
    id: int
    project: str
    title: str
    status: str
    priority: str


class IssueDetail(BaseModel):
    id: int
    project: str
    title: str
    body: str
    status: str
    priority: str
    created_at: datetime
    updated_at: datetime
