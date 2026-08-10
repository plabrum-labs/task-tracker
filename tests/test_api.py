"""The public client API: the adapters that map internal schemas to the contract,
and ``TtClient`` driving a real in-memory database.

The adapter maps are pure, so they are fed hand-built schemas and checked
directly. The client is exercised through ``from_engine`` over the same
``create_all`` engine the domain tests use, seeding rows through the create
actions — the path a frontend takes — so what a read returns is persisted.
"""

from datetime import date, datetime

import pytest
from sqlalchemy import Engine

from conftest import a_project, an_issue
from tt.api import _adapt
from tt.api.client import TtClient
from tt.api.enums import EpicStatus, IssueStatus, Priority
from tt.api.exceptions import TtNotFound, TtRefused
from tt.domains.comment.schemas import CommentView
from tt.domains.epic.schemas import EpicDetail
from tt.domains.issue import api as issue_api
from tt.domains.issue.enums import Status
from tt.domains.issue.schemas import IssueDetail, IssueListItem
from tt.domains.project import api as project_api

_WHEN = datetime(2026, 1, 2, 3, 4, 5)


# --- adapters (pure) ------------------------------------------------------


def test_summary_maps_wire_strings_to_public_enums() -> None:
    item = IssueListItem(
        id=7,
        ref="TT-3",
        project="TT",
        title="a row",
        status="doing",
        priority="high",
        due_date=date(2026, 6, 1),
        epic="Payments",
        milestone="Beta",
        tags=["b", "a"],
        waiting=True,
    )
    summary = _adapt.summary_from(item)
    assert summary.status is IssueStatus.DOING
    assert summary.priority is Priority.HIGH
    assert summary.tags == ("b", "a")
    assert summary.waiting is True


def test_issue_renames_the_dependency_graph_and_orders_comments() -> None:
    detail = IssueDetail(
        id=1,
        ref="TT-1",
        project="TT",
        title="parent",
        body="body",
        status="todo",
        priority="none",
        due_date=None,
        epic=None,
        milestone=None,
        tags=[],
        comments=[
            CommentView(id=10, body="first", created_at=_WHEN, updated_at=_WHEN),
            CommentView(id=11, body="second", created_at=_WHEN, updated_at=_WHEN),
        ],
        depends_on=["TT-2"],
        dependents=["TT-3", "TT-4"],
        waiting=True,
        created_at=_WHEN,
        updated_at=_WHEN,
    )
    issue = _adapt.issue_from(detail)
    assert issue.blocked_by == ("TT-2",)
    assert issue.blocks == ("TT-3", "TT-4")
    assert issue.priority is Priority.NONE
    assert tuple(c.body for c in issue.comments) == ("first", "second")


def test_epic_exposes_every_status_count_including_canceled() -> None:
    counts: dict[Status, int] = {
        Status.BACKLOG: 1,
        Status.BLOCKED: 2,
        Status.TODO: 3,
        Status.DOING: 4,
        Status.DONE: 5,
        Status.CANCELED: 6,
    }
    detail = EpicDetail(
        id=1,
        project="TT",
        title="Payments",
        body="",
        status="active",
        due_date=None,
        counts=counts,
        created_at=_WHEN,
        updated_at=_WHEN,
    )
    epic = _adapt.epic_from(detail)
    assert epic.status is EpicStatus.ACTIVE
    assert (epic.backlog, epic.blocked, epic.todo, epic.doing, epic.done, epic.canceled) == (
        1,
        2,
        3,
        4,
        5,
        6,
    )


# --- client over a real database ------------------------------------------


def test_list_issues_scopes_to_a_project_and_spans_all(db: Engine) -> None:
    tt = a_project(db, "TT")
    eng = a_project(db, "ENG")
    an_issue(db, tt, "tt one")
    an_issue(db, eng, "eng one")
    client = TtClient.from_engine(db)

    scoped = client.list_issues(project="TT")
    assert [row.ref for row in scoped] == ["TT-1"]

    everywhere = {row.ref for row in client.list_issues()}
    assert everywhere == {"TT-1", "ENG-1"}


def test_show_issue_returns_the_full_issue_or_raises(db: Engine) -> None:
    project = a_project(db, "TT")
    an_issue(db, project, "the issue")
    client = TtClient.from_engine(db)

    issue = client.show_issue("TT-1")
    assert issue.title == "the issue"
    assert issue.status is IssueStatus.TODO

    with pytest.raises(TtNotFound):
        client.show_issue("TT-99")


def test_show_issue_reports_the_dependency_graph_and_waiting(db: Engine) -> None:
    project = a_project(db, "TT")
    an_issue(db, project, "blocker")
    an_issue(db, project, "waiter")
    issue_api.issue_action(db, "addDependency", {"dependency": "TT-1"}, "TT-2")
    client = TtClient.from_engine(db)

    waiter = client.show_issue("TT-2")
    assert waiter.blocked_by == ("TT-1",)
    assert waiter.waiting is True
    blocker = client.show_issue("TT-1")
    assert blocker.blocks == ("TT-2",)


def test_show_epic_addresses_by_project_and_title(db: Engine) -> None:
    project = a_project(db, "TT")
    project_api.project_action(db, "addEpic", {"title": "Payments"}, project.slug)
    client = TtClient.from_engine(db)

    epic = client.show_epic("TT", "Payments")
    assert epic.title == "Payments"
    assert epic.status is EpicStatus.ACTIVE
    assert epic.done == 0

    with pytest.raises(TtNotFound):
        client.show_epic("TT", "Nope")


def test_set_status_moves_the_issue_and_returns_it(db: Engine) -> None:
    project = a_project(db, "TT")
    an_issue(db, project, "movable")
    client = TtClient.from_engine(db)

    updated = client.set_status("TT-1", IssueStatus.DOING)
    assert updated.status is IssueStatus.DOING
    assert client.show_issue("TT-1").status is IssueStatus.DOING


def test_add_comment_returns_the_created_comment(db: Engine) -> None:
    project = a_project(db, "TT")
    an_issue(db, project, "commentable")
    client = TtClient.from_engine(db)

    comment = client.add_comment("TT-1", "a note")
    assert comment.body == "a note"
    assert client.show_issue("TT-1").comments == (comment,)


def test_a_refused_write_raises_tt_refused(db: Engine) -> None:
    project = a_project(db, "TT")
    an_issue(db, project, "commentable")
    client = TtClient.from_engine(db)

    with pytest.raises(TtRefused, match="body is required"):
        client.add_comment("TT-1", "   ")
