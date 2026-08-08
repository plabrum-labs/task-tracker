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

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

from tt.domains.comment.schemas import CommentView
from tt.domains.issue.enums import Priority, Status
from tt.platform.actions import MULTILINE, REFERENCE


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
    body: Annotated[str, MULTILINE] = Field(description="The issue's description. Blank clears it.")
    status: Status = Field(description="Where the issue is up to.")
    priority: WirePriority = Field(description="How far up the list the issue sorts.")
    due_date: date | None = Field(
        description="When the issue is due, as YYYY-MM-DD. Blank clears it."
    )
    epic: Annotated[str | None, REFERENCE] = Field(
        description="The ref of the epic this issue belongs to, e.g. ENG-3. Blank clears it."
    )
    milestone: Annotated[str | None, REFERENCE] = Field(
        description=(
            "The ref of the milestone this issue belongs to, within its epic, e.g. ENG-5. "
            "Blank clears it."
        )
    )


class AddCommentPayload(BaseModel):
    # A comment is created through the issue it hangs off, so its create payload
    # lives here with the parent, as ``AddMilestonePayload`` lives with the epic.
    model_config = ConfigDict(extra="forbid")

    body: Annotated[str, MULTILINE] = Field(description="The comment's text.")


class SetStatusPayload(BaseModel):
    # The focused status move: the one field the whole-object edit also carries,
    # sent on its own for the frequent workflow action a frontend binds to a key.
    model_config = ConfigDict(extra="forbid")

    status: Status = Field(description="Where the issue is up to.")


class TagNamePayload(BaseModel):
    # Shared by ``tagIssue`` and ``untagIssue``: a tag is addressed by its name, not
    # its id, because a name is what a person types and what a global vocabulary is
    # keyed on. Tags stay off the whole-object edit — a many-to-many is not a scalar
    # column an edit sets — so attaching and detaching are their own focused verbs.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The name of the tag to attach or detach.")


class DependencyRefPayload(BaseModel):
    # Shared by ``addDependency`` and ``removeDependency``: the dependency is addressed
    # by ref, so the TUI renders a reference field rather than free text. From the
    # issue's own view "add a dependency" means "this issue depends on X", so the ref
    # names X, the issue this one depends on.
    model_config = ConfigDict(extra="forbid")

    dependency: Annotated[str, REFERENCE] = Field(
        description="The ref of the issue this one depends on, e.g. ENG-3."
    )


class SetDueDatePayload(BaseModel):
    # The focused due-date move, the mirror of ``SetStatus``: the one field the
    # whole-object edit also carries, sent on its own. A blank clears the date, the
    # null an optional-date form sends.
    model_config = ConfigDict(extra="forbid")

    due_date: date | None = Field(
        description="When the issue is due, as YYYY-MM-DD. Blank clears it."
    )


class IssueListItem(BaseModel):
    id: int
    ref: str
    project: str
    title: str
    status: str
    priority: str
    due_date: date | None
    epic: str | None
    milestone: str | None
    tags: list[str]
    # Derived, not stored: the issue depends on something that is not yet done. The
    # margin flags it so a waiting issue reads as waiting without opening it.
    waiting: bool


class IssueDetail(BaseModel):
    id: int
    ref: str
    project: str
    title: str
    body: str
    status: str
    priority: str
    due_date: date | None
    epic: str | None
    milestone: str | None
    tags: list[str]
    comments: list[CommentView]
    # The dependency graph, as refs: ``depends_on`` is what must finish before this
    # issue can start, ``dependents`` what waits on it. ``waiting`` is the derived
    # state, true when a live dependency is not yet done.
    depends_on: list[str]
    dependents: list[str]
    waiting: bool
    created_at: datetime
    updated_at: datetime
