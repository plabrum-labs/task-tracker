"""The fixtures the store and action tests share.

One ``connect`` is one in-memory database held open by a ``StaticPool``, so a test
cannot see another's rows. ``initialise`` runs the same DDL the binary runs, and
the helpers seed rows through the create services — the same path a frontend takes
— so a seeded object is the projection every read returns, not a hand-built one.
"""

import pytest
from sqlalchemy import Engine

from tt.domains import schema
from tt.domains.issue import Draft as IssueDraft
from tt.domains.issue import Issue, Priority
from tt.domains.issue import services as issue_services
from tt.domains.project import Draft as ProjectDraft
from tt.domains.project import Project
from tt.domains.project import services as project_services
from tt.platform import db as platform_db


@pytest.fixture
def db() -> Engine:
    engine = platform_db.connect("sqlite://")
    schema.initialise(engine)
    return engine


def a_project(engine: Engine, slug: str) -> Project:
    with platform_db.transaction(engine) as tx:
        return project_services.create_project(tx, ProjectDraft(slug=slug, title=slug, body=""))


def an_issue(
    engine: Engine, project: Project, title: str, priority: Priority = Priority.NORMAL
) -> Issue:
    with platform_db.transaction(engine) as tx:
        return issue_services.create_issue(
            tx, IssueDraft(project_id=project.id, title=title, body="", priority=priority)
        )
