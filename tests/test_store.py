"""The SQL edge, against a real in-memory SQLite database.

No mocks and no seam invented for one: ``create_all`` builds the same schema the
migration does and every query below goes to sqlite. One ``connect`` is one
database held open by a ``StaticPool``, so the tests cannot see each other's rows.

The soft-delete cases are the point. Liveness is derived rather than stored, so
what has to be shown is that one row written on the way out is one row cleared on
the way back, and that nothing else changed in between. A write loads its row in
the transaction it writes, the way an action does, so an edit is a mutation the
session flushes and a delete stamps ``deleted_at`` inline.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from conftest import a_project, an_issue
from tt.domains.issue import Issue, Priority, Status
from tt.domains.issue import queries as issue_queries
from tt.domains.project import Project
from tt.domains.project import Status as ProjectStatus
from tt.domains.project import queries as project_queries
from tt.platform import db as platform_db


def _titles(issues: list[Issue]) -> list[str]:
    return [i.title for i in issues]


# --- creation -------------------------------------------------------------


def test_create_returns_the_stored_row_with_its_assigned_id(db: Engine) -> None:
    tt = a_project(db, "tt")
    assert tt.id > 0
    assert tt.status == ProjectStatus.ACTIVE
    assert (tt.todo, tt.doing, tt.done) == (0, 0, 0)
    # One instant in both stamps, because the row's two defaults resolve to one
    # ``CURRENT_TIMESTAMP`` in the insert.
    assert tt.created_at == tt.updated_at

    other = a_project(db, "other")
    assert other.id != tt.id

    made = an_issue(db, tt, "first", Priority.HIGH)
    assert made.id > 0
    assert made.status == Status.TODO
    # The slug comes from the loaded project, not a column on the issue.
    assert made.project.slug == "tt"
    assert made.created_at == made.updated_at


def test_counts_are_part_of_the_projection(db: Engine) -> None:
    tt = a_project(db, "tt")
    for title in ["a", "b", "c"]:
        an_issue(db, tt, title)
    with platform_db.reading(db) as s:
        first = issue_queries.list_issues(s, "tt")[0]
    with platform_db.transaction(db) as tx:
        issue = issue_queries.get_issue(tx, first.id)
        assert issue is not None
        issue.status = Status.DOING

    with platform_db.reading(db) as s:
        loaded = project_queries.get_project(s, "tt")
    assert loaded is not None
    assert (loaded.todo, loaded.doing, loaded.done) == (2, 1, 0)
    assert loaded.issue_count() == 3

    # A project with nothing under it still gets zeroes rather than nothing.
    empty = a_project(db, "empty")
    with platform_db.reading(db) as s:
        reloaded = project_queries.get_project(s, empty.slug)
    assert reloaded is not None
    assert (reloaded.todo, reloaded.doing, reloaded.done) == (0, 0, 0)


# --- ordering -------------------------------------------------------------


def test_issues_come_back_high_priority_first_then_oldest_first(db: Engine) -> None:
    tt = a_project(db, "tt")
    # Inserted normal, high, high — so neither insertion order nor priority alone
    # produces the expected order. The native integer sort on the stored priority
    # is what puts high first.
    for title, priority in [
        ("first", Priority.NORMAL),
        ("second", Priority.HIGH),
        ("third", Priority.HIGH),
    ]:
        an_issue(db, tt, title, priority)
    with platform_db.reading(db) as s:
        issues = issue_queries.list_issues(s, "tt")
    assert _titles(issues) == ["second", "third", "first"]


# --- updates --------------------------------------------------------------


def test_an_update_writes_the_editable_columns_and_leaves_the_rest(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "first")

    with platform_db.transaction(db) as tx:
        issue = issue_queries.get_issue(tx, made.id)
        assert issue is not None
        issue.title = "renamed"
        issue.body = "why"
        issue.status = Status.DOING
        issue.priority = Priority.HIGH
        issue.status_note = "started"

    with platform_db.reading(db) as s:
        read = issue_queries.get_issue(s, made.id)
    assert read is not None
    assert read.title == "renamed"
    assert read.status_note == "started"
    assert read.created_at == made.created_at
    assert read.project_id == made.project_id

    # A nullable column has one write path: clearing it writes NULL.
    with platform_db.transaction(db) as tx:
        issue = issue_queries.get_issue(tx, made.id)
        assert issue is not None
        issue.status_note = None
    with platform_db.reading(db) as s:
        cleared = issue_queries.get_issue(s, made.id)
    assert cleared is not None
    assert cleared.status_note is None


# --- soft deletes ---------------------------------------------------------


def _stamp(row: Issue | Project) -> None:
    row.deleted_at = datetime.now(UTC)


def test_deleting_a_project_hides_its_issues(db: Engine) -> None:
    tt = a_project(db, "tt")
    an_issue(db, tt, "kept")
    doomed = an_issue(db, tt, "doomed")

    # One issue deleted in its own right, before the project goes.
    with platform_db.transaction(db) as tx:
        issue = issue_queries.get_issue(tx, doomed.id)
        assert issue is not None
        _stamp(issue)
    with platform_db.reading(db) as s:
        assert _titles(issue_queries.list_issues(s, "tt")) == ["kept"]

    # Deleting the project hides it and every live issue under it — one soft delete
    # on the way out, and every read filters the stamp so the rows simply vanish.
    with platform_db.transaction(db) as tx:
        project = project_queries.get_project(tx, "tt")
        assert project is not None
        _stamp(project)
    with platform_db.reading(db) as s:
        assert project_queries.get_project(s, "tt") is None
        assert issue_queries.list_issues(s, "tt") == []
        assert issue_queries.get_issue(s, doomed.id) is None


def test_an_issue_can_be_deleted_on_its_own(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "gone")
    with platform_db.transaction(db) as tx:
        issue = issue_queries.get_issue(tx, made.id)
        assert issue is not None
        _stamp(issue)

    # The stamp is the issue's own, so it drops out of its live project's reads
    # while the project stays.
    with platform_db.reading(db) as s:
        assert project_queries.get_project(s, "tt") is not None
        assert issue_queries.list_issues(s, "tt") == []
        assert issue_queries.get_issue(s, made.id) is None


def test_a_slug_is_reusable_once_its_project_is_deleted(db: Engine) -> None:
    first = a_project(db, "tt")
    with platform_db.transaction(db) as tx:
        project = project_queries.get_project(tx, "tt")
        assert project is not None
        _stamp(project)

    second = a_project(db, "tt")
    assert second.id != first.id
    with platform_db.reading(db) as s:
        assert [p.slug for p in project_queries.list_projects(s)] == ["tt"]


# --- the assertion the OCaml side cannot make -----------------------------


def test_a_duplicate_live_slug_is_refused_by_the_constraint(db: Engine) -> None:
    # createProject's hook is what turns this into a sentence; the partial unique
    # index is what guarantees it. This inserts straight past the hook, so what
    # answers is the database — a driver failure, which the store no longer dresses
    # up as a refusal.
    a_project(db, "tt")
    with pytest.raises(SQLAlchemyError), platform_db.transaction(db) as tx:
        tx.add(Project(slug="tt", title="", body="", status=ProjectStatus.ACTIVE, issues=[]))
        tx.flush()


def test_the_enum_round_trips_through_the_column_it_is_stored_in(db: Engine) -> None:
    # The members go through columns whose custom types store a name and an int,
    # and come back as the same members.
    tt = a_project(db, "tt")
    for title, status, priority in [
        ("a", Status.TODO, Priority.NORMAL),
        ("b", Status.DOING, Priority.HIGH),
        ("c", Status.DONE, Priority.NORMAL),
    ]:
        made = an_issue(db, tt, title, priority)
        with platform_db.transaction(db) as tx:
            issue = issue_queries.get_issue(tx, made.id)
            assert issue is not None
            issue.status = status
        with platform_db.reading(db) as s:
            read = issue_queries.get_issue(s, made.id)
        assert read is not None
        assert read.status == status
        assert read.priority == priority

    with platform_db.transaction(db) as tx:
        project = project_queries.get_project(tx, "tt")
        assert project is not None
        project.status = ProjectStatus.ARCHIVED
    with platform_db.reading(db) as s:
        archived = project_queries.get_project(s, "tt")
    assert archived is not None
    assert archived.status == ProjectStatus.ARCHIVED
