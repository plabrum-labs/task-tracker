"""Pure maps from the domains' internal pydantic schemas to the public dataclasses.

This module is the whole of the coupling to tt's internal wire shapes: when a
domain schema moves, it moves here, and the public contract in ``types`` does
not. Every function is pure — no database, no engine — so it is tested directly.
"""

from tt.api.enums import EpicStatus, IssueStatus, Priority
from tt.api.types import Comment, Epic, Issue, IssueSummary
from tt.domains.comment.schemas import CommentDetail, CommentView
from tt.domains.epic.schemas import EpicDetail
from tt.domains.issue.enums import Status
from tt.domains.issue.schemas import IssueDetail, IssueListItem


def comment_from(view: CommentView | CommentDetail) -> Comment:
    return Comment(
        id=view.id,
        body=view.body,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def summary_from(item: IssueListItem) -> IssueSummary:
    return IssueSummary(
        id=item.id,
        ref=item.ref,
        project=item.project,
        title=item.title,
        status=IssueStatus(item.status),
        priority=Priority(item.priority),
        due_date=item.due_date,
        epic=item.epic,
        milestone=item.milestone,
        tags=tuple(item.tags),
        waiting=item.waiting,
    )


def issue_from(detail: IssueDetail) -> Issue:
    return Issue(
        id=detail.id,
        ref=detail.ref,
        project=detail.project,
        title=detail.title,
        body=detail.body,
        status=IssueStatus(detail.status),
        priority=Priority(detail.priority),
        due_date=detail.due_date,
        epic=detail.epic,
        milestone=detail.milestone,
        tags=tuple(detail.tags),
        blocked_by=tuple(detail.depends_on),
        blocks=tuple(detail.dependents),
        waiting=detail.waiting,
        comments=tuple(comment_from(c) for c in detail.comments),
        created_at=detail.created_at,
        updated_at=detail.updated_at,
    )


def epic_from(detail: EpicDetail) -> Epic:
    # ``counts`` is densified to every ``Status`` member by the domain, but read
    # through ``.get`` so a future status the domain omits reads as zero rather
    # than raising here.
    counts = detail.counts
    return Epic(
        id=detail.id,
        project=detail.project,
        title=detail.title,
        body=detail.body,
        status=EpicStatus(detail.status),
        due_date=detail.due_date,
        backlog=counts.get(Status.BACKLOG, 0),
        blocked=counts.get(Status.BLOCKED, 0),
        todo=counts.get(Status.TODO, 0),
        doing=counts.get(Status.DOING, 0),
        done=counts.get(Status.DONE, 0),
        canceled=counts.get(Status.CANCELED, 0),
        created_at=detail.created_at,
        updated_at=detail.updated_at,
    )
