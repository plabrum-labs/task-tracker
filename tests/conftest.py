"""The fixtures the store and action tests share.

Tests run against a real Postgres — the ``compose.yaml`` container by default,
overridable with ``TT_TEST_DB``. The schema is built once per session with
``create_all`` on a dedicated ``tt_test`` database, kept apart from the
development one so a test run never touches real rows. Each test then starts from
truncated tables with their identities reset, so a test cannot see another's rows
and the ids it reads back begin at 1.

The helpers seed rows through the create actions — the same path a frontend takes
— inside their own transaction, then read the stored row back, so what they
return is persisted rather than hand-built.
"""

import os
from collections.abc import Callable, Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, text

from tt import schema
from tt.domains.issue import Issue, Priority
from tt.domains.issue import queries as issue_queries
from tt.domains.project import Project
from tt.domains.project import api as project_api
from tt.domains.project import queries as project_queries
from tt.platform import db as platform_db
from tt.platform.db import BaseDBModel

# The disposable database a test run builds its schema on. A separate database
# from the development one on the same server, so ``TRUNCATE`` between tests never
# reaches rows a person is using.
_DEFAULT_TEST_URL = "postgresql+psycopg://tt:tt@localhost:54329/tt_test"


def test_url() -> str:
    """The Postgres a test run uses. ``TT_TEST_DB`` overrides the local default."""
    return os.environ.get("TT_TEST_DB") or _DEFAULT_TEST_URL


def _ensure_database(url: str) -> None:
    """Create the test database if it is absent, connecting to the server's default
    ``tt`` maintenance database. ``CREATE DATABASE`` cannot run inside a
    transaction, so it is issued on an autocommit connection."""
    target = sa.make_url(url)
    engine = sa.create_engine(target.set(database="tt"), isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": target.database}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    engine.dispose()


@pytest.fixture(scope="session")
def _engine() -> Iterator[Engine]:
    """One engine per session against a freshly-schema'd ``tt_test``. Dropping the
    schema first makes the session idempotent across runs; ``create_all`` builds
    every table at once, the way a throwaway database that has no history to
    migrate is set up."""
    url = test_url()
    _ensure_database(url)
    engine = platform_db.connect(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    schema.create_all(engine)
    yield engine
    engine.dispose()


def _truncate(engine: Engine) -> None:
    """Empty every table and reset its identity, so a database reads as an empty
    tracker whose next ids begin at 1. ``CASCADE`` lets one statement clear the
    whole graph regardless of foreign keys."""
    tables = ", ".join(f'"{table.name}"' for table in BaseDBModel.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db(_engine: Engine) -> Engine:
    """A clean database for one test: every table truncated with its identity reset,
    so each test starts from an empty tracker whose ids begin at 1."""
    _truncate(_engine)
    return _engine


@pytest.fixture
def fresh(_engine: Engine) -> Callable[[], Engine]:
    """A factory that hands back an emptied database on each call, for the rare test
    that needs several pristine states in one run — comparing two independent writes
    that must each land at id 1. Every call truncates and resets identities."""

    def make() -> Engine:
        _truncate(_engine)
        return _engine

    return make


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
