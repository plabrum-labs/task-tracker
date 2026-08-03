"""The issue entrypoints a frontend and the actions call.

Business logic and naming, over the reads and writes in ``queries``: a ``list_``
returns the objects, a create inserts one, a soft delete stamps the row and a
restore clears it. Edits are the actions' own — they mutate the loaded row and
flush. The transaction is the caller's, so these flush and never commit.
"""

from sqlalchemy.orm import Session

from tt.domains.issue import queries
from tt.domains.issue.models import Draft, Issue
from tt.domains.project.models import Project
from tt.platform.deleted import Deleted


def list_issues(db: Session, project_slug: str) -> list[Issue]:
    return queries.for_project(db, project_slug)


def get_issue(db: Session, issue_id: int) -> Issue | None:
    return queries.get(db, issue_id)


def _as_deleted(issue: Issue) -> Deleted[Issue]:
    # ``queries.trashed`` returns only stamped rows, so the stamp is never null.
    assert issue.deleted_at is not None
    return Deleted(inner=issue, deleted_at=issue.deleted_at)


def trashed_issues(db: Session) -> list[Deleted[Issue]]:
    return [_as_deleted(issue) for issue in queries.trashed(db)]


def create_issue(db: Session, project: Project, draft: Draft) -> Issue:
    return queries.add(db, project, draft)


def delete_issue(db: Session, issue: Issue) -> None:
    issue.soft_delete()
    db.flush()


def restore_issue(db: Session, issue: Issue) -> None:
    issue.restore()
    db.flush()
