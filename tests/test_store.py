"""The SQL edge, against a real Postgres database.

No mocks and no seam invented for one: ``create_all`` builds the same schema the
migration does and every query below runs against Postgres. Each test starts from
truncated tables, so it cannot see another test's rows.

The soft-delete cases are the point. Liveness is derived rather than stored, so
what has to be shown is that one row written on the way out is one row cleared on
the way back, and that nothing else changed in between. A write loads its row in
the transaction it writes, the way an action does, so an edit is a mutation the
session flushes and a delete stamps ``deleted_at`` inline.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from conftest import a_project, an_issue
from tt.domains.issue import Issue, Priority, Status
from tt.domains.issue import queries as issue_queries
from tt.domains.issue.enums import Direction, SortField
from tt.domains.issue.queries import IssueFilter, IssueSort
from tt.domains.project import Project
from tt.domains.project import Status as ProjectStatus
from tt.domains.project import api as project_api
from tt.domains.project import queries as project_queries
from tt.domains.tag.models import Tag
from tt.platform import db as platform_db


def _titles(issues: list[Issue]) -> list[str]:
    return [i.title for i in issues]


# --- creation -------------------------------------------------------------


def test_create_returns_the_stored_row_with_its_assigned_id(db: Engine) -> None:
    tt = a_project(db, "tt")
    assert tt.id > 0
    assert tt.status == ProjectStatus.ACTIVE
    assert (tt.counts[Status.TODO], tt.counts[Status.DOING], tt.counts[Status.DONE]) == (0, 0, 0)
    # One instant in both stamps, because the row's two defaults resolve to one
    # ``CURRENT_TIMESTAMP`` in the insert.
    assert tt.created_at == tt.updated_at

    other = a_project(db, "other")
    assert other.id != tt.id

    made = an_issue(db, tt, "first", Priority.HIGH)
    assert made.id > 0
    assert made.status == Status.TODO
    # The slug comes from the loaded project, not a column on the issue.
    assert made.project.slug == "TT"
    assert made.created_at == made.updated_at


def test_counts_are_part_of_the_projection(db: Engine) -> None:
    tt = a_project(db, "tt")
    for title in ["a", "b", "c"]:
        an_issue(db, tt, title)
    with platform_db.reading(db) as s:
        first = issue_queries.list_issues(s, "tt")[0]
    with platform_db.reading(db) as s:
        rest = issue_queries.list_issues(s, "tt")
    with platform_db.transaction(db) as tx:
        doing = issue_queries.get_issue(tx, first.id)
        blocked = issue_queries.get_issue(tx, rest[1].id)
        backlog = issue_queries.get_issue(tx, rest[-1].id)
        assert doing is not None
        assert blocked is not None
        assert backlog is not None
        doing.status = Status.DOING
        blocked.status = Status.BLOCKED
        backlog.status = Status.BACKLOG

    with platform_db.reading(db) as s:
        loaded = project_queries.get_project(s, "tt")
    assert loaded is not None
    counts = (
        loaded.counts[Status.BACKLOG],
        loaded.counts[Status.BLOCKED],
        loaded.counts[Status.TODO],
        loaded.counts[Status.DOING],
        loaded.counts[Status.DONE],
    )
    assert counts == (1, 1, 0, 1, 0)
    assert loaded.issue_count() == 3

    # A project with nothing under it still names every status, at zero — the dense
    # tally the callers index without a missing-key guard.
    empty = a_project(db, "empty")
    with platform_db.reading(db) as s:
        reloaded = project_queries.get_project(s, empty.slug)
    assert reloaded is not None
    assert reloaded.counts == {status: 0 for status in Status}


# --- ordering -------------------------------------------------------------


def test_issues_come_back_high_priority_first_then_oldest_first(db: Engine) -> None:
    tt = a_project(db, "tt")
    # Inserted normal, high, high — so neither insertion order nor priority alone
    # produces the expected order. The native integer sort on the stored priority
    # is what puts high first.
    for title, priority in [
        ("first", Priority.MEDIUM),
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

    with platform_db.reading(db) as s:
        read = issue_queries.get_issue(s, made.id)
    assert read is not None
    assert read.title == "renamed"
    assert read.body == "why"
    assert read.status == Status.DOING
    assert read.priority == Priority.HIGH
    assert read.created_at == made.created_at
    assert read.project_id == made.project_id


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
        assert [p.slug for p in project_queries.list_projects(s)] == ["TT"]


# --- the assertion the OCaml side cannot make -----------------------------


def test_a_duplicate_live_slug_is_refused_by_the_constraint(db: Engine) -> None:
    # createProject's hook is what turns this into a sentence; the partial unique
    # index is what guarantees it. This inserts straight past the hook, so what
    # answers is the database — a driver failure, which the store no longer dresses
    # up as a refusal.
    a_project(db, "tt")
    with pytest.raises(SQLAlchemyError), platform_db.transaction(db) as tx:
        # A slug is stored uppercase, so the row that collides with the seeded "tt"
        # is the canonical "TT"; inserting straight past the hook is what leaves the
        # database to answer.
        tx.add(Project(slug="TT", title="", body="", status=ProjectStatus.ACTIVE, issues=[]))
        tx.flush()


def test_the_enum_round_trips_through_the_column_it_is_stored_in(db: Engine) -> None:
    # The members go through columns whose custom types store a name and an int,
    # and come back as the same members.
    tt = a_project(db, "tt")
    for title, status, priority in [
        ("a", Status.TODO, Priority.MEDIUM),
        ("b", Status.DOING, Priority.HIGH),
        ("c", Status.DONE, Priority.MEDIUM),
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


# --- filtering and sorting ------------------------------------------------


def _set(db: Engine, issue_id: int, **columns: object) -> None:
    """Set columns on a live issue in its own transaction — the way an action
    would, so a filter and sort test reads back what the database persisted."""
    with platform_db.transaction(db) as tx:
        issue = issue_queries.get_issue(tx, issue_id)
        assert issue is not None
        for name, value in columns.items():
            setattr(issue, name, value)


def test_sort_by_priority_takes_its_direction(db: Engine) -> None:
    tt = a_project(db, "tt")
    an_issue(db, tt, "lo", Priority.LOW)
    an_issue(db, tt, "hi", Priority.URGENT)
    an_issue(db, tt, "mid", Priority.MEDIUM)
    with platform_db.reading(db) as s:
        ascending = issue_queries.list_issues(
            s, "tt", sort=IssueSort(SortField.PRIORITY, Direction.ASC)
        )
    assert _titles(ascending) == ["lo", "mid", "hi"]


def test_sort_by_due_date_keeps_undated_last_either_way(db: Engine) -> None:
    tt = a_project(db, "tt")
    soon = an_issue(db, tt, "soon")
    later = an_issue(db, tt, "later")
    an_issue(db, tt, "undated")
    _set(db, soon.id, due_date=date(2026, 1, 1))
    _set(db, later.id, due_date=date(2026, 6, 1))

    with platform_db.reading(db) as s:
        ascending = issue_queries.list_issues(s, "tt", sort=IssueSort(SortField.DUE))
    assert _titles(ascending) == ["soon", "later", "undated"]

    # Reversing the date axis flips the two dated rows but the undated one, having
    # no date to place, stays at the end rather than jumping to the front.
    with platform_db.reading(db) as s:
        descending = issue_queries.list_issues(
            s, "tt", sort=IssueSort(SortField.DUE, Direction.DESC)
        )
    assert _titles(descending) == ["later", "soon", "undated"]


def test_sort_by_status_follows_the_workflow_not_the_member_name(db: Engine) -> None:
    # backlog < todo < doing by workflow order, but backlog < doing < todo by the
    # alphabetical order of the stored names, so the two orders disagree and the
    # result shows which one the sort uses.
    tt = a_project(db, "tt")
    doing = an_issue(db, tt, "doing_one")
    backlog = an_issue(db, tt, "backlog_one")
    todo = an_issue(db, tt, "todo_one")
    canceled = an_issue(db, tt, "canceled_one")
    _set(db, doing.id, status=Status.DOING)
    _set(db, backlog.id, status=Status.BACKLOG)
    _set(db, todo.id, status=Status.TODO)
    # Canceled trails the workflow, so it sorts last however early the row was made.
    _set(db, canceled.id, status=Status.CANCELED)
    with platform_db.reading(db) as s:
        rows = issue_queries.list_issues(s, "tt", sort=IssueSort(SortField.STATUS))
    assert _titles(rows) == ["backlog_one", "todo_one", "doing_one", "canceled_one"]


def test_filter_by_tag_keeps_only_the_tagged_issues(db: Engine) -> None:
    tt = a_project(db, "tt")
    tagged = an_issue(db, tt, "tagged")
    an_issue(db, tt, "untagged")
    with platform_db.transaction(db) as tx:
        issue = issue_queries.get_issue(tx, tagged.id)
        assert issue is not None
        bug = Tag(name="bug")
        tx.add(bug)
        issue.tags.append(bug)
    with platform_db.reading(db) as s:
        rows = issue_queries.list_issues(s, "tt", filter=IssueFilter(tag="bug"))
    assert _titles(rows) == ["tagged"]


def test_filter_by_epic_keeps_only_that_epics_issues(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = project_api.project_action(db, "addEpic", {"title": "v1"}, "tt")
    epic_id = made.created_id
    assert epic_id is not None
    inside = an_issue(db, tt, "inside")
    an_issue(db, tt, "outside")
    _set(db, inside.id, epic_id=epic_id)
    with platform_db.reading(db) as s:
        rows = issue_queries.list_issues(s, "tt", filter=IssueFilter(epic_id=epic_id))
    assert _titles(rows) == ["inside"]


def test_filter_by_milestone_keeps_only_that_milestones_issues(db: Engine) -> None:
    tt = a_project(db, "tt")
    epic = project_api.project_action(db, "addEpic", {"title": "v1"}, "tt")
    assert epic.created_id is not None
    milestone = project_api.project_action(db, "addMilestone", {"title": "m1", "epic": "v1"}, "tt")
    milestone_id = milestone.created_id
    assert milestone_id is not None
    inside = an_issue(db, tt, "inside")
    an_issue(db, tt, "outside")
    _set(db, inside.id, epic_id=epic.created_id, milestone_id=milestone_id)
    with platform_db.reading(db) as s:
        rows = issue_queries.list_issues(s, "tt", filter=IssueFilter(milestone_id=milestone_id))
    assert _titles(rows) == ["inside"]
