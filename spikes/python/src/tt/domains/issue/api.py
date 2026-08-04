"""The issue domain's one public surface.

A frontend imports this and nothing else of the domain: the reads return the
output schemas in ``schemas``, ``show`` says what an issue offers, ``trigger``
takes a key and a blob and writes, and ``action_schemas`` lists what the CLI
turns into subcommands. Each is a thin call onto ``issue_actions``, the group the
actions register with, so the routing lives in one place next to the actions it
drives.
"""

from typing import Any

from sqlalchemy.orm import Session

from tt.domains.issue import queries, schemas
from tt.domains.issue.actions import issue_actions
from tt.domains.issue.models import Issue
from tt.platform.actions import ActionDeps, ActionResponse, Field, Invalid, Offer, name_of

# --- reads ----------------------------------------------------------------


def _list_item(issue: Issue) -> schemas.IssueListItem:
    return schemas.IssueListItem(
        id=issue.id,
        project=issue.project.slug,
        title=issue.title,
        status=name_of(issue.status),
        priority=name_of(issue.priority),
    )


def _detail(issue: Issue) -> schemas.IssueDetail:
    return schemas.IssueDetail(
        id=issue.id,
        project=issue.project.slug,
        title=issue.title,
        body=issue.body,
        status=name_of(issue.status),
        priority=name_of(issue.priority),
        status_note=issue.status_note,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
    )


def list_issues(db: Session, project_slug: str) -> list[schemas.IssueListItem]:
    return [_list_item(issue) for issue in queries.for_project(db, project_slug)]


def get_issue(db: Session, issue_id: int) -> schemas.IssueDetail | None:
    issue = queries.get(db, issue_id)
    return _detail(issue) if issue is not None else None


def show(db: Session, issue_id: int) -> tuple[schemas.IssueDetail, list[Offer]]:
    """An issue and what it offers, for a detail view — the two reads a ``show``
    makes at once, so a caller does not load the row twice."""
    issue = queries.get(db, issue_id)
    if issue is None:
        raise Invalid(f"no issue {issue_id}")
    return _detail(issue), issue_actions.offers(issue)


# --- write ----------------------------------------------------------------


def trigger(tx: Session, key: str, payload: Any, issue_id: int) -> ActionResponse:
    """Run an action by key against the addressed issue. Availability is enforced
    against the live row, so what a ``show`` reported stays a snapshot."""
    return issue_actions.trigger(ActionDeps(tx), key, payload, issue_id)


# --- codegen --------------------------------------------------------------


def action_schemas() -> list[tuple[str, list[Field]]]:
    """Every key an issue answers to, with the fields its payload asks for, for the
    CLI to grow a subcommand per action."""
    return issue_actions.action_schemas()
