"""The action layer, over a real in-memory SQLite database.

Availability is a pure function of the object, so the menu cases run against
literals with no database in sight. ``execute`` mutates the loaded row, so the
cases that run an action load their object in the transaction they write — the way
a frontend does — then assert both the response and the row's new state.

Per the root ``CLAUDE.md`` every action key gets two cases: the menu withholds it
and the write refuses it. The issue edit refuses nothing at the menu, so for it the
honest pair is "always offered" and the blank-title refusal ``execute`` states.
"""

from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine

from conftest import a_project, an_issue
from tt import schema
from tt.domains.issue import Issue, Priority, Status
from tt.domains.issue import actions as issue_actions
from tt.domains.issue import api as issue_api
from tt.domains.issue import queries as issue_queries
from tt.domains.issue import schemas as issue_schemas
from tt.domains.issue.actions import issue_actions as issue_group
from tt.domains.project import Project
from tt.domains.project import Status as ProjectStatus
from tt.domains.project import actions as project_actions
from tt.domains.project import api as project_api
from tt.domains.project import queries as project_queries
from tt.domains.project import schemas as project_schemas
from tt.domains.project.actions import project_actions as project_group
from tt.platform import db as platform_db
from tt.platform.actions import (
    ActionDeps,
    ActionResponse,
    Conflict,
    Empty,
    Invalid,
    ObjectAction,
    Refused,
    Runnable,
    decode,
)

# --- run helpers ----------------------------------------------------------


def _fresh() -> Engine:
    engine = platform_db.connect("sqlite://")
    schema.create_all(engine)
    return engine


def run_issue[P: BaseModel](
    engine: Engine, action: type[ObjectAction[Issue, P]], issue_id: int, payload: P
) -> ActionResponse:
    with platform_db.transaction(engine) as tx:
        obj = issue_queries.get_issue(tx, issue_id)
        assert obj is not None
        return action.run(obj, payload, ActionDeps(tx))


def run_project[P: BaseModel](
    engine: Engine, action: type[ObjectAction[Project, P]], slug: str, payload: P
) -> ActionResponse:
    with platform_db.transaction(engine) as tx:
        obj = project_queries.get_project(tx, slug)
        assert obj is not None
        return action.run(obj, payload, ActionDeps(tx))


def run_synthetic(
    engine: Engine, action: type[ObjectAction[Any, Any]], obj: Any, payload: Any
) -> ActionResponse:
    # A refusal is decided before ``execute``, so a synthetic object never has to be
    # attached to the session for these.
    with platform_db.transaction(engine) as tx:
        return action.run(obj, payload, ActionDeps(tx))


# --- literals, for the availability cases ---------------------------------


def issue() -> Issue:
    return Issue(
        id=1,
        project_id=1,
        title="a title",
        body="",
        status=Status.TODO,
        priority=Priority.NORMAL,
    )


def project(
    *, status: ProjectStatus = ProjectStatus.ACTIVE, issues: list[Issue] | None = None
) -> Project:
    return Project(
        id=1,
        slug="tt",
        title="task tracker",
        body="",
        status=status,
        issues=list(issues or []),
    )


def doing_issues(n: int) -> list[Issue]:
    """``n`` synthetic issues, all doing — what a project's counts read to refuse."""
    return [
        Issue(
            id=i + 1,
            project_id=1,
            title=f"i{i}",
            body="",
            status=Status.DOING,
            priority=Priority.NORMAL,
        )
        for i in range(n)
    ]


# --- issues: the menu never withholds anything ----------------------------


def _edit(**over: Any) -> issue_schemas.EditIssuePayload:
    """A whole-object edit payload, defaulting to a no-op so a case names only the
    field it exercises."""
    fields: dict[str, Any] = {
        "title": "a title",
        "body": "",
        "status": Status.TODO,
        "priority": Priority.NORMAL,
    }
    fields.update(over)
    return issue_schemas.EditIssuePayload(**fields)


def test_every_issue_edit_is_always_offered() -> None:
    for status in Status:
        for priority in Priority:
            seeded = Issue(
                id=1,
                project_id=1,
                title="a title",
                body="",
                status=status,
                priority=priority,
            )
            assert issue_actions.EditIssue.availability(seeded) == Runnable()
            assert issue_actions.Delete.availability(seeded) == Runnable()


def test_edit_sets_every_field_at_once(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "old")

    response = run_issue(
        db,
        issue_actions.EditIssue,
        seeded.id,
        _edit(
            title=" new ",
            body="details",
            status=Status.DOING,
            priority=Priority.HIGH,
        ),
    )
    assert response.message == "issue 1: saved"
    with platform_db.reading(db) as s:
        read = issue_queries.get_issue(s, seeded.id)
    assert read is not None
    assert read.title == "new"  # trimmed
    assert read.body == "details"
    assert read.status == Status.DOING
    assert read.priority == Priority.HIGH


def test_edit_trims_and_refuses_a_blank_title(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "old")

    with pytest.raises(Invalid):
        run_issue(db, issue_actions.EditIssue, seeded.id, _edit(title="   "))


def test_delete_hides_an_issue(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "here")

    response = run_issue(db, issue_actions.Delete, seeded.id, Empty())
    assert response.message == "issue 1: deleted"
    with platform_db.reading(db) as s:
        assert issue_queries.get_issue(s, seeded.id) is None


# --- projects: the half with preconditions --------------------------------


def test_edit_refuses_archiving_while_anything_is_doing(db: Engine) -> None:
    # The precondition moved from the menu into execute: the edit is always offered
    # now, and only archiving a busy project is refused — against the object, so the
    # count in the reason is this project's.
    busy = project(issues=doing_issues(1))
    assert project_actions.EditProject.availability(busy) == Runnable()
    with pytest.raises(Conflict, match="finish or drop 1 issue first"):
        run_synthetic(
            db,
            project_actions.EditProject,
            busy,
            project_schemas.EditProjectPayload(title="tt", body="", status=ProjectStatus.ARCHIVED),
        )
    busier = project(issues=doing_issues(3))
    with pytest.raises(Conflict, match="finish or drop 3 issues first"):
        run_synthetic(
            db,
            project_actions.EditProject,
            busier,
            project_schemas.EditProjectPayload(title="tt", body="", status=ProjectStatus.ARCHIVED),
        )


def test_edit_keeps_a_busy_project_editable_while_it_stays_active(db: Engine) -> None:
    # The regression the moved precondition buys: a title/body edit that keeps the
    # project active goes through even with an issue in progress.
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "wip")
    issue_api.issue_action(
        db, "edit", {"title": "wip", "body": "", "status": "doing", "priority": "normal"}, made.id
    )
    response = project_api.project_action(
        db, "edit", {"title": "renamed", "body": "notes", "status": "active"}, "tt"
    )
    assert response.message == "project tt: saved"
    with platform_db.reading(db) as s:
        read = project_queries.get_project(s, "tt")
    assert read is not None
    assert read.title == "renamed"
    assert read.body == "notes"
    assert read.status == ProjectStatus.ACTIVE

    # But archiving it while it is still busy is refused.
    with pytest.raises(Conflict):
        project_api.project_action(
            db, "edit", {"title": "renamed", "body": "notes", "status": "archived"}, "tt"
        )


def test_edit_archives_when_nothing_is_doing(db: Engine) -> None:
    a_project(db, "tt")
    response = project_api.project_action(
        db, "edit", {"title": "tt", "body": "", "status": "archived"}, "tt"
    )
    assert response.message == "project tt: saved"
    with platform_db.reading(db) as s:
        read = project_queries.get_project(s, "tt")
    assert read is not None
    assert read.status == ProjectStatus.ARCHIVED


def test_delete_is_refused_while_the_project_is_active(db: Engine) -> None:
    assert project_actions.Delete.availability(project()) == Refused("archive it first")
    a_project(db, "tt")
    with pytest.raises(Conflict):
        run_project(db, project_actions.Delete, "tt", Empty())

    # Archive it, and it goes.
    with platform_db.transaction(db) as tx:
        current = project_queries.get_project(tx, "tt")
        assert current is not None
        current.status = ProjectStatus.ARCHIVED
    with platform_db.reading(db) as s:
        archived = project_queries.get_project(s, "tt")
    assert archived is not None
    assert project_actions.Delete.availability(archived) == Runnable()
    response = run_project(db, project_actions.Delete, "tt", Empty())
    assert response.message == "project tt: deleted"
    with platform_db.reading(db) as s:
        assert project_queries.get_project(s, "tt") is None


def test_add_issue_is_refused_while_the_project_is_archived(db: Engine) -> None:
    archived = project(status=ProjectStatus.ARCHIVED)
    assert project_actions.AddIssue.availability(archived) == Refused("project is archived")
    with pytest.raises(Conflict):
        run_synthetic(
            db,
            project_actions.AddIssue,
            archived,
            project_schemas.AddIssuePayload(title="nope", body=None, priority=None),
        )


def test_add_issue_defaults_what_the_payload_leaves_out(db: Engine) -> None:
    a_project(db, "tt")
    response = run_project(
        db,
        project_actions.AddIssue,
        "tt",
        project_schemas.AddIssuePayload(title=" ship it ", body=None, priority=None),
    )
    assert response.message == "issue 1: created"
    assert response.created_id == 1
    with platform_db.reading(db) as s:
        created = issue_queries.list_issues(s, "tt")[0]
    assert created.title == "ship it"
    assert created.body == ""
    assert created.priority == Priority.NORMAL

    with pytest.raises(Invalid):
        run_project(
            db,
            project_actions.AddIssue,
            "tt",
            project_schemas.AddIssuePayload(title="  ", body=None, priority=None),
        )


def test_create_project_refuses_a_slug_the_live_list_already_holds(db: Engine) -> None:
    # A top-level action: no object, so the duplicate check queries through the
    # transaction the group hands ``execute``.
    a_project(db, "tt")
    with platform_db.transaction(db) as tx, pytest.raises(Conflict):
        project_group.trigger(ActionDeps(tx), "createProject", {"slug": "tt", "title": None})
    with platform_db.transaction(db) as tx, pytest.raises(Invalid):
        project_group.trigger(ActionDeps(tx), "createProject", {"slug": "  ", "title": None})
    with platform_db.transaction(db) as tx:
        response = project_group.trigger(
            ActionDeps(tx), "createProject", {"slug": "other", "title": "another"}
        )
    assert response.message == "project other: created"
    with platform_db.reading(db) as s:
        made = project_queries.get_project(s, "other")
    assert made is not None
    assert made.title == "another"


# --- dispatch through the group -------------------------------------------


def _keys(offered: list[Any]) -> list[str]:
    return [offer.key for offer in offered]


def test_offers_keep_refused_actions_and_drop_absent_ones() -> None:
    assert _keys(issue_group.offers(issue())) == ["edit", "delete"]
    # An active project with two issues doing: edit is runnable (its archive
    # precondition is in execute now), delete comes back refused, addIssue runnable
    # — one list, told apart by what each execute writes rather than by which
    # registration it sat in.
    busy = project(issues=doing_issues(2))
    offered = [(offer.key, offer.state) for offer in project_group.offers(busy)]
    assert offered == [
        ("edit", Runnable()),
        ("delete", Refused("archive it first")),
        ("setPath", Runnable()),
        ("addIssue", Runnable()),
    ]


def test_an_offer_carries_its_label() -> None:
    by_key = {offer.key: offer.label for offer in issue_group.offers(issue())}
    assert by_key["edit"] == "Edit"
    assert by_key["delete"] == "Delete"
    assert project_group.top_level_offers()[0].label == "Create project"


def test_dispatch_refuses_what_availability_refused(db: Engine) -> None:
    a_project(db, "tt")
    with pytest.raises(Conflict):
        project_api.project_action(db, "delete", {}, "tt")


def test_the_api_agrees_with_the_typed_path() -> None:
    erased = _fresh()
    tt = a_project(erased, "tt")
    seeded = an_issue(erased, tt, "old")
    wire = {"title": " new ", "body": "", "status": "todo", "priority": "normal"}
    via_api = issue_api.issue_action(erased, "edit", wire, seeded.id).message

    typed = _fresh()
    tt = a_project(typed, "tt")
    seeded = an_issue(typed, tt, "old")
    via_typed = run_issue(typed, issue_actions.EditIssue, seeded.id, _edit(title=" new ")).message

    assert via_api == via_typed == "issue 1: saved"

    # The same for a creator, whose execute writes a different table.
    erased = _fresh()
    a_project(erased, "tt")
    via_api = project_api.project_action(erased, "addIssue", {"title": "one"}, "tt").message

    typed = _fresh()
    a_project(typed, "tt")
    via_typed = run_project(
        typed,
        project_actions.AddIssue,
        "tt",
        project_schemas.AddIssuePayload(title="one", body=None, priority=None),
    ).message

    assert via_api == via_typed == "issue 1: created"


def test_a_malformed_payload_is_invalid(db: Engine) -> None:
    tt = a_project(db, "tt")
    an_issue(db, tt, "one")
    for payload in [
        {},  # missing required fields
        {"title": 5, "body": "", "status": "todo", "priority": "normal"},  # wrong type
        {"title": "x", "body": "", "status": "todo", "priority": "normal", "bogus": 1},  # extra
    ]:
        with pytest.raises(Invalid):
            issue_api.issue_action(db, "edit", payload, 1)
    # A value outside the enum, the one the schema could have told the caller
    # about in advance.
    with pytest.raises(Invalid):
        issue_api.issue_action(
            db, "edit", {"title": "x", "body": "", "status": "shipped", "priority": "normal"}, 1
        )


def test_an_action_with_no_arguments_takes_an_empty_object_and_not_null(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "here")
    assert issue_api.issue_action(db, "delete", {}, seeded.id).message == "issue 1: deleted"
    # ``null`` is refused where an empty object is not — the decode is the same one
    # a dispatch runs.
    assert decode(issue_actions.Delete, {}) == Empty()
    with pytest.raises(Invalid):
        decode(issue_actions.Delete, None)


def test_an_unknown_key_is_invalid(db: Engine) -> None:
    tt = a_project(db, "tt")
    an_issue(db, tt, "here")
    with pytest.raises(Invalid):
        issue_api.issue_action(db, "explode", {}, 1)
    with pytest.raises(Invalid):
        project_api.project_action(db, "explode", {})


def test_one_key_in_two_groups_decodes_against_the_group_it_was_dispatched_on(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "one")
    issue_keys = _keys(issue_group.offers(issue()))
    project_keys = _keys(project_group.offers(project()))
    for key in ["edit", "delete"]:
        assert key in issue_keys and key in project_keys

    # A project status through the issue's edit — the issue enum has no ``archived``.
    with pytest.raises(Invalid):
        issue_api.issue_action(
            db,
            "edit",
            {"title": "x", "body": "", "status": "archived", "priority": "normal"},
            made.id,
        )
    # ``status_note`` is gone from the issue edit, so sending it is an extra field.
    with pytest.raises(Invalid):
        issue_api.issue_action(
            db,
            "edit",
            {"title": "x", "body": "", "status": "todo", "priority": "normal", "status_note": "x"},
            made.id,
        )
    # An issue status, and an issue-only field, through the project's edit.
    with pytest.raises(Invalid):
        project_api.project_action(db, "edit", {"title": "tt", "body": "", "status": "doing"}, "tt")
    with pytest.raises(Invalid):
        project_api.project_action(
            db, "edit", {"title": "tt", "body": "", "status": "active", "priority": "high"}, "tt"
        )
