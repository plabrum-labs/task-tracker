"""The fixtures the store and action tests share.

One ``connect`` is one in-memory database held open by a ``StaticPool``, so a test
cannot see another's rows. ``create_all`` builds the schema on it — Alembic cannot
reach a ``StaticPool``'s single connection, and a database that dies with the
process has no history to migrate. The helpers seed rows through the create
actions — the same path a frontend takes — inside their own transaction, then read
the stored row back, so what they return is persisted rather than hand-built.
"""

import pytest
from sqlalchemy import Engine

from tt import schema
from tt.domains.issue import Issue, Priority
from tt.domains.issue import queries as issue_queries
from tt.domains.project import Project
from tt.domains.project import api as project_api
from tt.domains.project import queries as project_queries
from tt.platform import db as platform_db


@pytest.fixture
def db() -> Engine:
    engine = platform_db.connect("sqlite://")
    schema.create_all(engine)
    return engine


def a_project(engine: Engine, slug: str) -> Project:
    project_api.project_action(engine, "createProject", {"slug": slug, "title": slug})
    with platform_db.reading(engine) as session:
        project = project_queries.get_project(session, slug)
        assert project is not None
        return project


def an_issue(
    engine: Engine, project: Project, title: str, priority: Priority = Priority.MEDIUM
) -> Issue:
    response = project_api.project_action(
        engine, "addIssue", {"title": title, "priority": priority.name.lower()}, project.slug
    )
    assert response.created_id is not None
    with platform_db.reading(engine) as session:
        issue = issue_queries.get_issue(session, response.created_id)
        assert issue is not None
        return issue
