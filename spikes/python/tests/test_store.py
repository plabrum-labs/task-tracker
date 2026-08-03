"""The SQL edge, against a real in-memory SQLite database.

No mocks and no seam invented for one: ``create_all`` builds the same schema the
migration does and every query below goes to sqlite. One ``connect`` is one
database held open by a ``StaticPool``, so the tests cannot see each other's rows.

The soft-delete cases are the point. Liveness is derived rather than stored, so
what has to be shown is that one row written on the way out is one row cleared on
the way back, and that nothing else changed in between. A write loads its row in
the transaction it writes, the way a frontend does, so an edit is a mutation the
session flushes.
"""

import pytest
from sqlalchemy import Engine

from conftest import a_project, an_issue
from tt.domains.issue import Issue, Priority, Status
from tt.domains.issue import services as issue_services
from tt.domains.project import Draft as ProjectDraft
from tt.domains.project import Status as ProjectStatus
from tt.domains.project import services as project_services
from tt.platform import db as platform_db
from tt.platform.error import Broken


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
    # The projection, not the draft: project_slug comes from the loaded project.
    assert made.project_slug == "tt"
    assert made.created_at == made.updated_at


def test_counts_are_part_of_the_projection(db: Engine) -> None:
    tt = a_project(db, "tt")
    for title in ["a", "b", "c"]:
        an_issue(db, tt, title)
    with platform_db.reading(db) as s:
        first = issue_services.list_issues(s, "tt")[0]
    with platform_db.transaction(db) as tx:
        issue = issue_services.get_issue(tx, first.id)
        assert issue is not None
        issue.status = Status.DOING

    with platform_db.reading(db) as s:
        loaded = project_services.get_project(s, "tt")
    assert loaded is not None
    assert (loaded.todo, loaded.doing, loaded.done) == (2, 1, 0)
    assert loaded.issue_count() == 3

    # A project with nothing under it still gets zeroes rather than nothing.
    empty = a_project(db, "empty")
    with platform_db.reading(db) as s:
        reloaded = project_services.get_project(s, empty.slug)
    assert reloaded is not None
    assert (reloaded.todo, reloaded.doing, reloaded.done) == (0, 0, 0)


# --- ordering -------------------------------------------------------------


def test_issues_come_back_high_priority_first_then_oldest_first(db: Engine) -> None:
    tt = a_project(db, "tt")
    # Inserted normal, high, high — so neither insertion order nor priority alone
    # produces the expected order.
    for title, priority in [
        ("first", Priority.NORMAL),
        ("second", Priority.HIGH),
        ("third", Priority.HIGH),
    ]:
        an_issue(db, tt, title, priority)
    with platform_db.reading(db) as s:
        issues = issue_services.list_issues(s, "tt")
    assert _titles(issues) == ["second", "third", "first"]


# --- updates --------------------------------------------------------------


def test_an_update_writes_the_editable_columns_and_leaves_the_rest(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "first")

    with platform_db.transaction(db) as tx:
        issue = issue_services.get_issue(tx, made.id)
        assert issue is not None
        issue.title = "renamed"
        issue.body = "why"
        issue.status = Status.DOING
        issue.priority = Priority.HIGH
        issue.status_note = "started"

    with platform_db.reading(db) as s:
        read = issue_services.get_issue(s, made.id)
    assert read is not None
    assert read.title == "renamed"
    assert read.status_note == "started"
    assert read.created_at == made.created_at
    assert read.project_id == made.project_id

    # A nullable column has one write path: clearing it writes NULL.
    with platform_db.transaction(db) as tx:
        issue = issue_services.get_issue(tx, made.id)
        assert issue is not None
        issue.status_note = None
    with platform_db.reading(db) as s:
        cleared = issue_services.get_issue(s, made.id)
    assert cleared is not None
    assert cleared.status_note is None


# --- soft deletes ---------------------------------------------------------


def test_deleting_a_project_hides_its_issues_and_restoring_brings_them_back(db: Engine) -> None:
    tt = a_project(db, "tt")
    an_issue(db, tt, "kept")
    doomed = an_issue(db, tt, "doomed")

    # One issue deleted in its own right, before the project goes.
    with platform_db.transaction(db) as tx:
        issue = issue_services.get_issue(tx, doomed.id)
        assert issue is not None
        issue_services.delete_issue(tx, issue)
    with platform_db.reading(db) as s:
        assert _titles(issue_services.list_issues(s, "tt")) == ["kept"]

    with platform_db.transaction(db) as tx:
        project = project_services.get_project(tx, "tt")
        assert project is not None
        project_services.delete_project(tx, project)
    with platform_db.reading(db) as s:
        assert project_services.get_project(s, "tt") is None
        assert issue_services.list_issues(s, "tt") == []
        assert issue_services.get_issue(s, doomed.id) is None

    # The trash is by the row's own deleted_at, so the hidden issue is not in it —
    # one row was written on the way out, not two.
    with platform_db.reading(db) as s:
        trashed_projects = project_services.trashed_projects(s)
        trashed_issues = issue_services.trashed_issues(s)
    assert len(trashed_projects) == 1
    assert trashed_projects[0].inner.slug == "tt"
    assert [d.inner.title for d in trashed_issues] == ["doomed"]

    with platform_db.transaction(db) as tx:
        project = project_services.trashed_projects(tx)[0].inner
        project_services.restore_project(tx, project)

    # Exactly the issues that were not deleted in their own right.
    with platform_db.reading(db) as s:
        assert _titles(issue_services.list_issues(s, "tt")) == ["kept"]
        assert len(issue_services.trashed_issues(s)) == 1


def test_an_issue_can_be_restored_on_its_own(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "back")
    with platform_db.transaction(db) as tx:
        issue = issue_services.get_issue(tx, made.id)
        assert issue is not None
        issue_services.delete_issue(tx, issue)

    with platform_db.reading(db) as s:
        trashed = issue_services.trashed_issues(s)
    assert len(trashed) == 1
    # The trash row still knows its project, because that read asks nothing of the
    # project but its slug.
    assert trashed[0].inner.project_slug == "tt"

    with platform_db.transaction(db) as tx:
        deleted = issue_services.trashed_issues(tx)[0].inner
        issue_services.restore_issue(tx, deleted)
    with platform_db.reading(db) as s:
        assert _titles(issue_services.list_issues(s, "tt")) == ["back"]
        assert issue_services.trashed_issues(s) == []


def test_a_slug_is_reusable_once_its_project_is_deleted(db: Engine) -> None:
    first = a_project(db, "tt")
    with platform_db.transaction(db) as tx:
        project = project_services.get_project(tx, "tt")
        assert project is not None
        project_services.delete_project(tx, project)

    second = a_project(db, "tt")
    assert second.id != first.id
    with platform_db.reading(db) as s:
        assert [p.slug for p in project_services.list_projects(s)] == ["tt"]


# --- the assertion the OCaml side cannot make -----------------------------


def test_a_duplicate_live_slug_is_refused_by_the_constraint(db: Engine) -> None:
    # createProject's hook is what turns this into a sentence; the partial unique
    # index is what guarantees it. This bypasses the hook entirely, so what answers
    # is the database — surfacing as Broken because a driver failure is the machine
    # saying no, not the row.
    a_project(db, "tt")
    with pytest.raises(Broken), platform_db.transaction(db) as tx:
        project_services.create_project(tx, ProjectDraft(slug="tt", title="", body=""))


def test_the_enum_round_trips_through_the_column_it_is_stored_in(db: Engine) -> None:
    # The wire names go through a column whose custom type spells them out, and come
    # back as the same members.
    tt = a_project(db, "tt")
    for title, status, priority in [
        ("a", Status.TODO, Priority.NORMAL),
        ("b", Status.DOING, Priority.HIGH),
        ("c", Status.DONE, Priority.NORMAL),
    ]:
        made = an_issue(db, tt, title, priority)
        with platform_db.transaction(db) as tx:
            issue = issue_services.get_issue(tx, made.id)
            assert issue is not None
            issue.status = status
        with platform_db.reading(db) as s:
            read = issue_services.get_issue(s, made.id)
        assert read is not None
        assert read.status == status
        assert read.priority == priority

    with platform_db.transaction(db) as tx:
        project = project_services.get_project(tx, "tt")
        assert project is not None
        project.status = ProjectStatus.ARCHIVED
    with platform_db.reading(db) as s:
        archived = project_services.get_project(s, "tt")
    assert archived is not None
    assert archived.status == ProjectStatus.ARCHIVED
