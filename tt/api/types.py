"""The shapes the client returns: frozen dataclasses that are tt's public read
contract.

These are a deliberate translation of the domains' internal pydantic schemas,
not a re-export of them — a consumer depends on these, so an internal wire
change is absorbed by the adapter in ``_adapt`` rather than reaching the caller.
Refs (``<PROJECT>-<n>``) address issues; an ``Epic`` is a title within a project
and carries no ref of its own. Enums are already the public ones from ``enums``.

The module is ``types`` and not ``models`` on purpose: the schema bootstrap
discovers every ``models.py`` under ``tt`` as a table module, and these are plain
data, not mapped rows.
"""

from dataclasses import dataclass
from datetime import date, datetime

from tt.api.enums import EpicStatus, IssueStatus, Priority


@dataclass(frozen=True)
class Comment:
    id: int
    body: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class IssueSummary:
    """A list-view row: no body, no dependency graph."""

    id: int
    ref: str
    project: str
    title: str
    status: IssueStatus
    priority: Priority
    due_date: date | None
    epic: str | None
    milestone: str | None
    tags: tuple[str, ...]
    # Derived by tt: a live dependency is not yet done. Distinct from the
    # ``BLOCKED`` status a person sets.
    waiting: bool


@dataclass(frozen=True)
class Issue:
    """The full issue, with its body, comments, and dependency graph."""

    id: int
    ref: str
    project: str
    title: str
    body: str
    status: IssueStatus
    priority: Priority
    due_date: date | None
    epic: str | None
    milestone: str | None
    tags: tuple[str, ...]
    # The dependency graph as refs: ``blocked_by`` (tt's ``depends_on``) must
    # finish before this can start; ``blocks`` (tt's ``dependents``) waits on this.
    blocked_by: tuple[str, ...]
    blocks: tuple[str, ...]
    waiting: bool
    comments: tuple[Comment, ...]  # oldest first
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Epic:
    """An epic and the per-status rollup of its issues. The counts are tt's to
    compute — a consumer reads them, it does not recount."""

    id: int
    project: str
    title: str
    body: str
    status: EpicStatus
    due_date: date | None
    backlog: int
    blocked: int
    todo: int
    doing: int
    done: int
    canceled: int
    created_at: datetime
    updated_at: datetime
