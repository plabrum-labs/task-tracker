"""The issue domain's one public surface.

As ``../project/api.py``: a frontend hands it an engine, never a session, and
every database call is wrapped in ``@with_transaction`` so one public call is one
transaction. ``issue_list``/``issue_get``/``issue_detail`` read, ``issue_action``
writes, and ``action_schemas`` lists what the CLI turns into subcommands. Each is
a thin call onto ``issue_actions``, the group the actions register with.
"""

from typing import Any

from sqlalchemy.orm import Session

from tt.domains.issue import queries, schemas
from tt.domains.issue.actions import issue_actions
from tt.domains.issue.models import Issue
from tt.platform.actions import ActionDeps, ActionResponse, Field, Invalid, Offer, name_of
from tt.platform.db import with_transaction

# --- reads ----------------------------------------------------------------


def _list_item(issue: Issue) -> schemas.IssueListItem:
    return schemas.IssueListItem(
        id=issue.id,
        project=issue.project.slug,
        title=issue.title,
        status=name_of(issue.status),
        priority=name_of(issue.priority),
        due_date=issue.due_date,
        epic=issue.epic_id,
        milestone=issue.milestone_id,
    )


def _detail(issue: Issue) -> schemas.IssueDetail:
    return schemas.IssueDetail(
        id=issue.id,
        project=issue.project.slug,
        title=issue.title,
        body=issue.body,
        status=name_of(issue.status),
        priority=name_of(issue.priority),
        due_date=issue.due_date,
        epic=issue.epic_id,
        milestone=issue.milestone_id,
        tags=sorted(tag.name for tag in issue.tags),
        created_at=issue.created_at,
        updated_at=issue.updated_at,
    )


@with_transaction
def issue_list(tx: Session, project_slug: str) -> list[schemas.IssueListItem]:
    return [_list_item(issue) for issue in queries.list_issues(tx, project_slug)]


@with_transaction
def issue_get(tx: Session, issue_id: int) -> schemas.IssueDetail | None:
    issue = queries.get_issue(tx, issue_id)
    return _detail(issue) if issue is not None else None


@with_transaction
def issue_detail(tx: Session, issue_id: int) -> tuple[schemas.IssueDetail, list[Offer]]:
    """An issue and what it offers, for a detail view — the two reads a detail view
    makes at once, so a caller does not load the row twice."""
    issue = queries.get_issue(tx, issue_id)
    if issue is None:
        raise Invalid(f"no issue {issue_id}")
    return _detail(issue), issue_actions.offers(issue)


# --- write ----------------------------------------------------------------


@with_transaction
def issue_action(tx: Session, key: str, payload: Any, issue_id: int) -> ActionResponse:
    """Run an action by key against the addressed issue. Availability is enforced
    against the live row, so what a detail view reported stays a snapshot."""
    return issue_actions.trigger(ActionDeps(tx), key, payload, issue_id)


# --- codegen --------------------------------------------------------------


def action_schemas() -> list[tuple[str, list[Field]]]:
    """Every key an issue answers to, with the fields its payload asks for, for the
    CLI to grow a subcommand per action."""
    return issue_actions.action_schemas()
