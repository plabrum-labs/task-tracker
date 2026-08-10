"""The action layer, over a real in-memory SQLite database.

Availability is a pure function of the object, so the menu cases run against
literals with no database in sight. ``execute`` mutates the loaded row, so the
cases that run an action load their object in the transaction they write — the way
a frontend does — then assert both the response and the row's new state.

Per the root ``CLAUDE.md`` every action key gets two cases: the menu withholds it
and the write refuses it. The issue edit refuses nothing at the menu, so for it the
honest pair is "always offered" and the blank-title refusal ``execute`` states.
"""

from datetime import date
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine

from conftest import a_project, an_issue
from tt import schema
from tt.domains.comment import Comment
from tt.domains.comment import actions as comment_actions
from tt.domains.comment import api as comment_api
from tt.domains.comment import queries as comment_queries
from tt.domains.comment import schemas as comment_schemas
from tt.domains.epic import Epic
from tt.domains.epic import Status as EpicStatus
from tt.domains.epic import actions as epic_actions
from tt.domains.epic import api as epic_api
from tt.domains.epic import queries as epic_queries
from tt.domains.epic import schemas as epic_schemas
from tt.domains.issue import Issue, Priority, Status
from tt.domains.issue import actions as issue_actions
from tt.domains.issue import api as issue_api
from tt.domains.issue import queries as issue_queries
from tt.domains.issue import schemas as issue_schemas
from tt.domains.issue.actions import issue_actions as issue_group
from tt.domains.milestone import Milestone
from tt.domains.milestone import actions as milestone_actions
from tt.domains.milestone import api as milestone_api
from tt.domains.milestone import queries as milestone_queries
from tt.domains.milestone import schemas as milestone_schemas
from tt.domains.project import Project
from tt.domains.project import Status as ProjectStatus
from tt.domains.project import actions as project_actions
from tt.domains.project import api as project_api
from tt.domains.project import queries as project_queries
from tt.domains.project import schemas as project_schemas
from tt.domains.project.actions import project_actions as project_group
from tt.domains.tag import Tag
from tt.domains.tag import actions as tag_actions
from tt.domains.tag import api as tag_api
from tt.domains.tag import queries as tag_queries
from tt.domains.tag.actions import tag_actions as tag_group
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
from tt.platform.refs import NamedRef

# --- run helpers ----------------------------------------------------------


def _fresh() -> Engine:
    engine = platform_db.connect("sqlite://")
    schema.create_all(engine)
    return engine


def run_issue[P: BaseModel](
    engine: Engine, action: type[ObjectAction[Issue, P]], issue_ref: str, payload: P
) -> ActionResponse:
    with platform_db.transaction(engine) as tx:
        obj = issue_queries.resolve_ref(tx, issue_ref)
        assert obj is not None
        return action.run(obj, payload, ActionDeps(tx))


def run_project[P: BaseModel](
    engine: Engine, action: type[ObjectAction[Project, P]], slug: str, payload: P
) -> ActionResponse:
    with platform_db.transaction(engine) as tx:
        obj = project_queries.get_project(tx, slug)
        assert obj is not None
        return action.run(obj, payload, ActionDeps(tx))


def run_epic[P: BaseModel](
    engine: Engine, action: type[ObjectAction[Epic, P]], address: NamedRef, payload: P
) -> ActionResponse:
    with platform_db.transaction(engine) as tx:
        obj = epic_queries.resolve_by_title(tx, address)
        assert obj is not None
        return action.run(obj, payload, ActionDeps(tx))


def run_milestone[P: BaseModel](
    engine: Engine, action: type[ObjectAction[Milestone, P]], address: NamedRef, payload: P
) -> ActionResponse:
    with platform_db.transaction(engine) as tx:
        obj = milestone_queries.resolve_by_title(tx, address)
        assert obj is not None
        return action.run(obj, payload, ActionDeps(tx))


def run_comment[P: BaseModel](
    engine: Engine, action: type[ObjectAction[Comment, P]], comment_id: int, payload: P
) -> ActionResponse:
    with platform_db.transaction(engine) as tx:
        obj = comment_queries.get_comment(tx, comment_id)
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
        priority=Priority.MEDIUM,
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
            priority=Priority.MEDIUM,
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
        "priority": Priority.MEDIUM,
        "due_date": None,
        "epic": None,
        "milestone": None,
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
        seeded.ref,
        _edit(
            title=" new ",
            body="details",
            status=Status.DOING,
            priority=Priority.HIGH,
        ),
    )
    assert response.message == "issue TT-1: saved"
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
        run_issue(db, issue_actions.EditIssue, seeded.ref, _edit(title="   "))


def test_set_status_is_always_offered() -> None:
    # No status machine: the focused move applies from and to every status.
    for status in Status:
        seeded = Issue(
            id=1, project_id=1, title="a title", body="", status=status, priority=Priority.MEDIUM
        )
        assert issue_actions.SetStatus.availability(seeded) == Runnable()


def test_set_status_moves_to_each_status(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "one")
    for status in [
        Status.BACKLOG,
        Status.BLOCKED,
        Status.DOING,
        Status.DONE,
        Status.TODO,
        Status.CANCELED,
    ]:
        response = run_issue(
            db,
            issue_actions.SetStatus,
            seeded.ref,
            issue_schemas.SetStatusPayload(status=status),
        )
        assert response.message == f"issue TT-1: {status}"
        with platform_db.reading(db) as s:
            read = issue_queries.get_issue(s, seeded.id)
        assert read is not None
        assert read.status == status


def test_set_status_refuses_an_unknown_status_or_an_extra_field(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "one")
    # A value outside the enum is Invalid, the schema having advertised the closed set.
    with pytest.raises(Invalid):
        issue_api.issue_action(db, "setStatus", {"status": "shipped"}, made.ref)
    # And ``extra="forbid"`` refuses a field the payload does not advertise.
    with pytest.raises(Invalid):
        issue_api.issue_action(db, "setStatus", {"status": "doing", "title": "x"}, made.ref)


def test_set_due_date_is_always_offered() -> None:
    for status in Status:
        seeded = Issue(
            id=1, project_id=1, title="a title", body="", status=status, priority=Priority.MEDIUM
        )
        assert issue_actions.SetDueDate.availability(seeded) == Runnable()


def test_set_due_date_sets_and_clears(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "one")

    response = run_issue(
        db,
        issue_actions.SetDueDate,
        seeded.ref,
        issue_schemas.SetDueDatePayload(due_date=date(2026, 9, 1)),
    )
    assert response.message == "issue TT-1: due 2026-09-01"
    with platform_db.reading(db) as s:
        read = issue_queries.get_issue(s, seeded.id)
    assert read is not None
    assert read.due_date == date(2026, 9, 1)

    # A null clears it — no date is a state, not a refusal.
    response = run_issue(
        db, issue_actions.SetDueDate, seeded.ref, issue_schemas.SetDueDatePayload(due_date=None)
    )
    assert response.message == "issue TT-1: due cleared"
    with platform_db.reading(db) as s:
        cleared = issue_queries.get_issue(s, seeded.id)
    assert cleared is not None
    assert cleared.due_date is None


def test_edit_carries_the_due_date(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "one")
    run_issue(db, issue_actions.EditIssue, seeded.ref, _edit(due_date=date(2026, 12, 25)))
    with platform_db.reading(db) as s:
        read = issue_queries.get_issue(s, seeded.id)
    assert read is not None
    assert read.due_date == date(2026, 12, 25)


def test_delete_hides_an_issue(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "here")

    response = run_issue(db, issue_actions.Delete, seeded.ref, Empty())
    assert response.message == "issue TT-1: deleted"
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
            project_schemas.EditProjectPayload(
                title="tt", body="", status=ProjectStatus.ARCHIVED, path=None
            ),
        )
    busier = project(issues=doing_issues(3))
    with pytest.raises(Conflict, match="finish or drop 3 issues first"):
        run_synthetic(
            db,
            project_actions.EditProject,
            busier,
            project_schemas.EditProjectPayload(
                title="tt", body="", status=ProjectStatus.ARCHIVED, path=None
            ),
        )


def test_edit_keeps_a_busy_project_editable_while_it_stays_active(db: Engine) -> None:
    # The regression the moved precondition buys: a title/body edit that keeps the
    # project active goes through even with an issue in progress.
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "wip")
    issue_api.issue_action(
        db,
        "edit",
        {
            "title": "wip",
            "body": "",
            "status": "doing",
            "priority": "medium",
            "due_date": None,
            "epic": None,
            "milestone": None,
        },
        made.ref,
    )
    response = project_api.project_action(
        db, "edit", {"title": "renamed", "body": "notes", "status": "active", "path": None}, "tt"
    )
    assert response.message == "project TT: saved"
    with platform_db.reading(db) as s:
        read = project_queries.get_project(s, "tt")
    assert read is not None
    assert read.title == "renamed"
    assert read.body == "notes"
    assert read.status == ProjectStatus.ACTIVE

    # But archiving it while it is still busy is refused.
    with pytest.raises(Conflict):
        project_api.project_action(
            db,
            "edit",
            {"title": "renamed", "body": "notes", "status": "archived", "path": None},
            "tt",
        )


def test_edit_archives_when_nothing_is_doing(db: Engine) -> None:
    a_project(db, "tt")
    response = project_api.project_action(
        db, "edit", {"title": "tt", "body": "", "status": "archived", "path": None}, "tt"
    )
    assert response.message == "project TT: saved"
    with platform_db.reading(db) as s:
        read = project_queries.get_project(s, "tt")
    assert read is not None
    assert read.status == ProjectStatus.ARCHIVED


def test_edit_sets_and_clears_the_path(db: Engine) -> None:
    # The whole-object edit owns ``path`` too, so it can bind one and, given a blank,
    # clear it back to unbound — the same null an OptionalText form sends.
    a_project(db, "tt")
    project_api.project_action(
        db, "edit", {"title": "tt", "body": "", "status": "active", "path": "/work/tt"}, "tt"
    )
    with platform_db.reading(db) as s:
        read = project_queries.get_project(s, "tt")
    assert read is not None
    assert read.path == "/work/tt"

    project_api.project_action(
        db, "edit", {"title": "tt", "body": "", "status": "active", "path": None}, "tt"
    )
    with platform_db.reading(db) as s:
        cleared = project_queries.get_project(s, "tt")
    assert cleared is not None
    assert cleared.path is None


def test_edit_refuses_a_path_another_live_project_owns(db: Engine) -> None:
    # The live-only unique index on ``path`` is read here as a sentence, the same
    # guard ``setPath`` carries, so the edit refuses instead of hitting the constraint.
    a_project(db, "tt")
    a_project(db, "other")
    project_api.project_action(db, "setPath", {"path": "/work/shared"}, "other")
    with pytest.raises(Conflict, match='path already belongs to "OTHER"'):
        project_api.project_action(
            db,
            "edit",
            {"title": "tt", "body": "", "status": "active", "path": "/work/shared"},
            "tt",
        )


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
    assert response.message == "project TT: deleted"
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
    assert response.message == "issue TT-1: created"
    assert response.created_id == 1
    with platform_db.reading(db) as s:
        created = issue_queries.list_issues(s, "tt")[0]
    assert created.title == "ship it"
    assert created.body == ""
    assert created.status == Status.TODO
    assert created.priority == Priority.MEDIUM

    with pytest.raises(Invalid):
        run_project(
            db,
            project_actions.AddIssue,
            "tt",
            project_schemas.AddIssuePayload(title="  ", body=None, priority=None),
        )


def test_issue_numbers_restart_per_project(db: Engine) -> None:
    # The regression the sequence buys: numbering is scoped to the project, not
    # global, so each project counts from 1 and two projects reuse the same numbers.
    a_project(db, "eng")
    a_project(db, "web")
    for title in ("first", "second"):
        project_api.project_action(db, "addIssue", {"title": title}, "eng")
    project_api.project_action(db, "addIssue", {"title": "only"}, "web")

    eng = [i.ref for i in issue_api.issue_list(db, "eng")]
    web = [i.ref for i in issue_api.issue_list(db, "web")]
    assert eng == ["ENG-1", "ENG-2"]
    assert web == ["WEB-1"]  # a fresh run, not eng-3


def test_a_ref_resolves_the_right_project_scoped_issue(db: Engine) -> None:
    # eng-1 and web-1 are distinct issues; the ref, not a global id, is what tells
    # them apart.
    a_project(db, "eng")
    a_project(db, "web")
    project_api.project_action(db, "addIssue", {"title": "in eng"}, "eng")
    project_api.project_action(db, "addIssue", {"title": "in web"}, "web")

    eng_one = issue_api.issue_get(db, "eng-1")
    web_one = issue_api.issue_get(db, "web-1")
    assert eng_one is not None and eng_one.title == "in eng"
    assert web_one is not None and web_one.title == "in web"


def test_a_deleted_number_is_not_reissued(db: Engine) -> None:
    # The sequence only rises, so deleting an issue does not free its number for the
    # next create — the unique-per-project constraint holds over the dead row.
    a_project(db, "eng")
    project_api.project_action(db, "addIssue", {"title": "one"}, "eng")
    issue_api.issue_action(db, "delete", {}, "eng-1")
    project_api.project_action(db, "addIssue", {"title": "two"}, "eng")
    live = [i.ref for i in issue_api.issue_list(db, "eng")]
    assert live == ["ENG-2"]  # eng-1 is gone and its number was not handed out again


def test_add_issue_honors_an_explicit_status(db: Engine) -> None:
    a_project(db, "tt")
    run_project(
        db,
        project_actions.AddIssue,
        "tt",
        project_schemas.AddIssuePayload(title="plan it", body=None, status=Status.BACKLOG),
    )
    with platform_db.reading(db) as s:
        created = issue_queries.list_issues(s, "tt")[0]
    assert created.status == Status.BACKLOG


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
    assert response.message == "project OTHER: created"
    with platform_db.reading(db) as s:
        made = project_queries.get_project(s, "other")
    assert made is not None
    assert made.title == "another"


def test_create_project_uppercases_the_slug_and_addresses_case_insensitively(db: Engine) -> None:
    # A slug is stored uppercase so every ref reads ``ENG-12`` however it was typed;
    # the duplicate check folds case too, and a project is then addressable in any
    # case.
    with platform_db.transaction(db) as tx:
        response = project_group.trigger(
            ActionDeps(tx), "createProject", {"slug": "Eng", "title": "engineering"}
        )
    assert response.message == "project ENG: created"
    with platform_db.transaction(db) as tx, pytest.raises(Conflict):
        project_group.trigger(ActionDeps(tx), "createProject", {"slug": "eng", "title": None})
    with platform_db.reading(db) as s:
        assert project_queries.get_project(s, "eng") is not None
        assert project_queries.get_project(s, "ENG") is not None


def test_create_project_refuses_a_slug_over_the_length_cap(db: Engine) -> None:
    # The slug is uppercased into every issue id, so it is capped short; the
    # boundary length is accepted and one past it is refused.
    with platform_db.transaction(db) as tx, pytest.raises(Invalid):
        project_group.trigger(ActionDeps(tx), "createProject", {"slug": "toolong", "title": None})
    with platform_db.transaction(db) as tx:
        response = project_group.trigger(
            ActionDeps(tx), "createProject", {"slug": "abcde", "title": None}
        )
    assert response.message == "project ABCDE: created"


# --- epics -----------------------------------------------------------------


def an_epic(engine: Engine, project_slug: str, title: str) -> NamedRef:
    """Create an epic and return its address — a title within a project, the address
    every public call now takes."""
    response = project_api.project_action(engine, "addEpic", {"title": title}, project_slug)
    assert response.created_id is not None
    return NamedRef(project_slug, title)


def epic(*, issues: list[Issue] | None = None) -> Epic:
    return Epic(
        id=1,
        project_id=1,
        title="v1",
        body="",
        status=EpicStatus.ACTIVE,
        due_date=None,
        issues=list(issues or []),
    )


def test_add_epic_is_refused_while_the_project_is_archived(db: Engine) -> None:
    archived = project(status=ProjectStatus.ARCHIVED)
    assert project_actions.AddEpic.availability(archived) == Refused("project is archived")
    with pytest.raises(Conflict):
        run_synthetic(
            db,
            project_actions.AddEpic,
            archived,
            project_schemas.AddEpicPayload(title="nope", body=None, due_date=None),
        )


def test_add_epic_creates_an_epic_under_the_project(db: Engine) -> None:
    a_project(db, "tt")
    response = run_project(
        db,
        project_actions.AddEpic,
        "tt",
        project_schemas.AddEpicPayload(title=" v1 ", body=None, due_date=date(2026, 9, 1)),
    )
    assert response.message == 'epic "v1": created'
    assert response.created_id == 1
    with platform_db.reading(db) as s:
        made = epic_queries.get_epic(s, 1)
    assert made is not None
    assert made.title == "v1"  # trimmed
    assert made.status is EpicStatus.ACTIVE
    assert made.due_date == date(2026, 9, 1)

    # A blank title is the object's refusal.
    with pytest.raises(Invalid):
        run_project(
            db,
            project_actions.AddEpic,
            "tt",
            project_schemas.AddEpicPayload(title="  ", body=None, due_date=None),
        )


def test_add_epic_refuses_a_title_a_live_sibling_holds(db: Engine) -> None:
    # An epic is addressed by title within its project, so a title a live epic already
    # holds is refused — the partial unique index read as a sentence.
    a_project(db, "tt")
    an_epic(db, "tt", "v1")
    with pytest.raises(Conflict, match='an epic "v1" already exists'):
        project_api.project_action(db, "addEpic", {"title": "v1"}, "tt")


def _edit_epic(**over: Any) -> epic_schemas.EditEpicPayload:
    fields: dict[str, Any] = {
        "title": "v1",
        "body": "",
        "status": EpicStatus.ACTIVE,
        "due_date": None,
    }
    fields.update(over)
    return epic_schemas.EditEpicPayload(**fields)


def test_epic_edit_and_status_are_always_offered() -> None:
    for status in EpicStatus:
        seeded = Epic(id=1, project_id=1, title="v1", body="", status=status, due_date=None)
        assert epic_actions.EditEpic.availability(seeded) == Runnable()
        assert epic_actions.SetStatus.availability(seeded) == Runnable()
        assert epic_actions.SetDueDate.availability(seeded) == Runnable()


def test_epic_edit_sets_every_field_and_refuses_a_blank_title(db: Engine) -> None:
    a_project(db, "tt")
    address = an_epic(db, "tt", "old")
    response = run_epic(
        db,
        epic_actions.EditEpic,
        address,
        _edit_epic(
            title=" done ", body="notes", status=EpicStatus.DONE, due_date=date(2026, 12, 1)
        ),
    )
    assert response.message == 'epic "done": saved'
    # The edit renames the epic, so it is addressed by its new title from here on.
    renamed = NamedRef("tt", "done")
    with platform_db.reading(db) as s:
        read = epic_queries.resolve_by_title(s, renamed)
    assert read is not None
    assert (read.title, read.body, read.status, read.due_date) == (
        "done",
        "notes",
        EpicStatus.DONE,
        date(2026, 12, 1),
    )

    with pytest.raises(Invalid):
        run_epic(db, epic_actions.EditEpic, renamed, _edit_epic(title="   "))


def test_epic_edit_refuses_a_title_a_live_sibling_holds(db: Engine) -> None:
    # A rename onto a title another live epic already holds is refused, the same
    # partial unique index the create reads.
    a_project(db, "tt")
    an_epic(db, "tt", "v1")
    second = an_epic(db, "tt", "v2")
    with pytest.raises(Conflict, match='an epic "v1" already exists'):
        run_epic(db, epic_actions.EditEpic, second, _edit_epic(title="v1"))


def test_epic_set_status_and_due_date_move(db: Engine) -> None:
    a_project(db, "tt")
    address = an_epic(db, "tt", "v1")
    response = run_epic(
        db, epic_actions.SetStatus, address, epic_schemas.SetStatusPayload(status=EpicStatus.DONE)
    )
    assert response.message == 'epic "v1": done'

    run_epic(
        db,
        epic_actions.SetDueDate,
        address,
        epic_schemas.SetDueDatePayload(due_date=date(2026, 9, 1)),
    )
    with platform_db.reading(db) as s:
        read = epic_queries.resolve_by_title(s, address)
    assert read is not None
    assert read.status is EpicStatus.DONE
    assert read.due_date == date(2026, 9, 1)


def test_epic_delete_is_refused_while_it_holds_live_issues(db: Engine) -> None:
    # The menu refuses on the loaded count, and the write refuses against the live
    # row — so no live issue is left pointing at a deleted epic.
    busy = epic(issues=[issue()])
    assert epic_actions.Delete.availability(busy) == Refused("reassign or close 1 issue first")

    tt = a_project(db, "tt")
    address = an_epic(db, "tt", "v1")
    made = an_issue(db, tt, "under the epic")
    issue_api.issue_action(db, "edit", _wire_edit(epic="v1"), made.ref)
    with pytest.raises(Conflict):
        run_epic(db, epic_actions.Delete, address, Empty())

    # Clear the link, and the epic goes.
    issue_api.issue_action(db, "edit", _wire_edit(epic=None), made.ref)
    with platform_db.reading(db) as s:
        empty_epic = epic_queries.resolve_by_title(s, address)
    assert empty_epic is not None
    assert epic_actions.Delete.availability(empty_epic) == Runnable()
    assert run_epic(db, epic_actions.Delete, address, Empty()).message == 'epic "v1": deleted'
    with platform_db.reading(db) as s:
        assert epic_queries.resolve_by_title(s, address) is None


# --- an issue's epic link --------------------------------------------------


def _wire_edit(**over: Any) -> dict[str, Any]:
    """A whole-object issue-edit blob, defaulting to a no-op so a case names only
    the field it exercises."""
    wire: dict[str, Any] = {
        "title": "a title",
        "body": "",
        "status": "todo",
        "priority": "medium",
        "due_date": None,
        "epic": None,
        "milestone": None,
    }
    wire.update(over)
    return wire


def test_edit_assigns_and_clears_an_issues_epic(db: Engine) -> None:
    tt = a_project(db, "tt")
    an_epic(db, "tt", "v1")
    made = an_issue(db, tt, "wire it")

    issue_api.issue_action(db, "edit", _wire_edit(epic="v1"), made.ref)
    detail = issue_api.issue_get(db, made.ref)
    assert detail is not None
    assert detail.epic == "v1"

    issue_api.issue_action(db, "edit", _wire_edit(epic=None), made.ref)
    cleared = issue_api.issue_get(db, made.ref)
    assert cleared is not None
    assert cleared.epic is None


def test_edit_refuses_an_epic_only_another_project_holds(db: Engine) -> None:
    # An epic is resolved by title within the issue's own project, so a title only a
    # different project carries does not resolve here — the resolution is project-scoped.
    tt = a_project(db, "tt")
    a_project(db, "web")
    an_epic(db, "web", "web only")
    made = an_issue(db, tt, "wire it")
    with pytest.raises(Conflict, match='no epic "web only"'):
        issue_api.issue_action(db, "edit", _wire_edit(epic="web only"), made.ref)


def test_edit_refuses_an_unknown_epic(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "wire it")
    with pytest.raises(Conflict, match='no epic "ghost"'):
        issue_api.issue_action(db, "edit", _wire_edit(epic="ghost"), made.ref)


def test_epic_detail_refuses_an_unknown_title(db: Engine) -> None:
    a_project(db, "tt")
    with pytest.raises(Invalid, match="no epic"):
        epic_api.epic_detail(db, "tt", "does-not-exist")


# --- milestones ------------------------------------------------------------


def a_milestone(
    engine: Engine, project_slug: str, title: str, epic_title: str | None = None
) -> NamedRef:
    """Create a milestone on the project — optionally filed under an epic by its title
    — and return the milestone's address, a title within a project."""
    response = project_api.project_action(
        engine, "addMilestone", {"title": title, "epic": epic_title}, project_slug
    )
    assert response.created_id is not None
    return NamedRef(project_slug, title)


def milestone(*, issues: list[Issue] | None = None) -> Milestone:
    return Milestone(id=1, project_id=1, title="alpha", due_date=None, issues=list(issues or []))


def test_add_milestone_creates_one_under_the_project(db: Engine) -> None:
    # A milestone is project-level now, so its create hangs off the project; the epic
    # it is filed under, if any, is an optional label named by title within the project.
    a_project(db, "tt")
    an_epic(db, "tt", "v1")
    response = run_project(
        db,
        project_actions.AddMilestone,
        "tt",
        project_schemas.AddMilestonePayload(title=" alpha ", due_date=date(2026, 9, 1), epic="v1"),
    )
    assert response.message == 'milestone "alpha": created'
    assert response.created_id == 1
    with platform_db.reading(db) as s:
        made = milestone_queries.get_milestone(s, 1)
    assert made is not None
    assert made.title == "alpha"  # trimmed
    # Filed under the epic the title names.
    assert made.epic is not None
    assert made.epic.title == "v1"
    assert made.due_date == date(2026, 9, 1)

    # A blank title is the object's refusal.
    with pytest.raises(Invalid):
        run_project(
            db,
            project_actions.AddMilestone,
            "tt",
            project_schemas.AddMilestonePayload(title="  ", due_date=None, epic=None),
        )


def test_add_milestone_is_refused_while_the_project_is_archived(db: Engine) -> None:
    archived = project(status=ProjectStatus.ARCHIVED)
    assert project_actions.AddMilestone.availability(archived) == Refused("project is archived")
    with pytest.raises(Conflict):
        run_synthetic(
            db,
            project_actions.AddMilestone,
            archived,
            project_schemas.AddMilestonePayload(title="nope", due_date=None, epic=None),
        )


def test_add_milestone_files_under_an_epic_or_none(db: Engine) -> None:
    # A milestone may carry an epic label or none; either reads back off the project.
    a_project(db, "tt")
    an_epic(db, "tt", "v1")
    a_milestone(db, "tt", "unfiled")
    a_milestone(db, "tt", "filed", epic_title="v1")
    unfiled = milestone_api.milestone_get(db, "tt", "unfiled")
    filed = milestone_api.milestone_get(db, "tt", "filed")
    assert unfiled is not None and unfiled.epic is None
    assert filed is not None and filed.epic == "v1"


def test_add_milestone_refuses_a_title_a_live_sibling_holds(db: Engine) -> None:
    a_project(db, "tt")
    a_milestone(db, "tt", "alpha")
    with pytest.raises(Conflict, match='a milestone "alpha" already exists'):
        project_api.project_action(db, "addMilestone", {"title": "alpha"}, "tt")


def test_add_milestone_refuses_an_unknown_epic(db: Engine) -> None:
    a_project(db, "tt")
    with pytest.raises(Conflict, match='no epic "ghost"'):
        project_api.project_action(db, "addMilestone", {"title": "alpha", "epic": "ghost"}, "tt")


def test_milestone_edit_and_set_due_date(db: Engine) -> None:
    a_project(db, "tt")
    a_milestone(db, "tt", "old")
    address = NamedRef("tt", "old")

    seeded = milestone()
    assert milestone_actions.EditMilestone.availability(seeded) == Runnable()
    assert milestone_actions.SetDueDate.availability(seeded) == Runnable()

    response = run_milestone(
        db,
        milestone_actions.EditMilestone,
        address,
        milestone_schemas.EditMilestonePayload(
            title=" alpha ", due_date=date(2026, 9, 1), epic=None
        ),
    )
    assert response.message == 'milestone "alpha": saved'
    # The edit renames it, so it is addressed by its new title from here on.
    renamed = NamedRef("tt", "alpha")
    with platform_db.reading(db) as s:
        read = milestone_queries.resolve_by_title(s, renamed)
    assert read is not None
    assert read.title == "alpha"  # trimmed
    assert read.due_date == date(2026, 9, 1)

    with pytest.raises(Invalid):
        run_milestone(
            db,
            milestone_actions.EditMilestone,
            renamed,
            milestone_schemas.EditMilestonePayload(title="   ", due_date=None, epic=None),
        )

    run_milestone(
        db,
        milestone_actions.SetDueDate,
        renamed,
        milestone_schemas.SetDueDatePayload(due_date=None),
    )
    with platform_db.reading(db) as s:
        cleared = milestone_queries.resolve_by_title(s, renamed)
    assert cleared is not None
    assert cleared.due_date is None


def test_milestone_edit_refuses_a_title_a_live_sibling_holds(db: Engine) -> None:
    a_project(db, "tt")
    a_milestone(db, "tt", "alpha")
    second = a_milestone(db, "tt", "beta")
    with pytest.raises(Conflict, match='a milestone "alpha" already exists'):
        run_milestone(
            db,
            milestone_actions.EditMilestone,
            second,
            milestone_schemas.EditMilestonePayload(title="alpha", due_date=None, epic=None),
        )


def test_milestone_edit_sets_and_clears_its_epic(db: Engine) -> None:
    # The whole-object milestone edit owns the optional epic label too: it can file
    # the milestone under an epic by title and, given a blank, clear it back to none.
    a_project(db, "tt")
    an_epic(db, "tt", "v1")
    a_milestone(db, "tt", "alpha")
    address = NamedRef("tt", "alpha")

    run_milestone(
        db,
        milestone_actions.EditMilestone,
        address,
        milestone_schemas.EditMilestonePayload(title="alpha", due_date=None, epic="v1"),
    )
    filed = milestone_api.milestone_get(db, "tt", "alpha")
    assert filed is not None and filed.epic == "v1"

    run_milestone(
        db,
        milestone_actions.EditMilestone,
        address,
        milestone_schemas.EditMilestonePayload(title="alpha", due_date=None, epic=None),
    )
    cleared = milestone_api.milestone_get(db, "tt", "alpha")
    assert cleared is not None and cleared.epic is None


def test_milestone_edit_refuses_an_unknown_epic(db: Engine) -> None:
    a_project(db, "tt")
    a_milestone(db, "tt", "alpha")
    with pytest.raises(Conflict, match='no epic "ghost"'):
        run_milestone(
            db,
            milestone_actions.EditMilestone,
            NamedRef("tt", "alpha"),
            milestone_schemas.EditMilestonePayload(title="alpha", due_date=None, epic="ghost"),
        )


def test_milestone_delete_is_refused_while_it_holds_live_issues(db: Engine) -> None:
    busy = milestone(issues=[issue()])
    assert milestone_actions.Delete.availability(busy) == Refused("reassign or close 1 issue first")

    tt = a_project(db, "tt")
    address = a_milestone(db, "tt", "alpha")
    made = an_issue(db, tt, "under the milestone")
    issue_api.issue_action(db, "edit", _wire_edit(milestone="alpha"), made.ref)
    with pytest.raises(Conflict):
        run_milestone(db, milestone_actions.Delete, address, Empty())

    issue_api.issue_action(db, "edit", _wire_edit(milestone=None), made.ref)
    with platform_db.reading(db) as s:
        empty_milestone = milestone_queries.resolve_by_title(s, address)
    assert empty_milestone is not None
    assert milestone_actions.Delete.availability(empty_milestone) == Runnable()
    assert run_milestone(db, milestone_actions.Delete, address, Empty()).message == (
        'milestone "alpha": deleted'
    )


# --- an issue's milestone link ---------------------------------------------


def test_edit_assigns_an_epic_and_milestone_independently(db: Engine) -> None:
    # An issue carries an epic and a milestone as two independent links, each resolved
    # by title within the issue's project.
    tt = a_project(db, "tt")
    an_epic(db, "tt", "v1")
    a_milestone(db, "tt", "alpha")
    made = an_issue(db, tt, "wire it")

    issue_api.issue_action(db, "edit", _wire_edit(epic="v1", milestone="alpha"), made.ref)
    detail = issue_api.issue_get(db, made.ref)
    assert detail is not None
    assert (detail.epic, detail.milestone) == ("v1", "alpha")


def test_edit_files_a_milestone_from_any_epic_in_the_project(db: Engine) -> None:
    # The old "milestone must match the issue's epic" rule is gone: a milestone need
    # only be in the issue's project, whatever epic it is itself filed under.
    tt = a_project(db, "tt")
    an_epic(db, "tt", "v1")
    an_epic(db, "tt", "v2")
    a_milestone(db, "tt", "beta", epic_title="v2")  # the milestone sits under v2
    made = an_issue(db, tt, "wire it")

    # The issue is under epic v1 yet takes milestone beta (which is under v2).
    issue_api.issue_action(db, "edit", _wire_edit(epic="v1", milestone="beta"), made.ref)
    detail = issue_api.issue_get(db, made.ref)
    assert detail is not None
    assert (detail.epic, detail.milestone) == ("v1", "beta")


def test_edit_assigns_a_milestone_when_the_issue_has_no_epic(db: Engine) -> None:
    # A milestone is project-level, so it can be set on an issue that carries no epic.
    tt = a_project(db, "tt")
    a_milestone(db, "tt", "alpha")
    made = an_issue(db, tt, "wire it")

    issue_api.issue_action(db, "edit", _wire_edit(epic=None, milestone="alpha"), made.ref)
    detail = issue_api.issue_get(db, made.ref)
    assert detail is not None
    assert detail.epic is None
    assert detail.milestone == "alpha"


def test_changing_the_epic_leaves_the_milestone_intact(db: Engine) -> None:
    # Epic and milestone are independent, so moving the issue to a different epic no
    # longer clears its milestone.
    tt = a_project(db, "tt")
    an_epic(db, "tt", "v1")
    an_epic(db, "tt", "v2")
    a_milestone(db, "tt", "alpha")
    made = an_issue(db, tt, "wire it")
    issue_api.issue_action(db, "edit", _wire_edit(epic="v1", milestone="alpha"), made.ref)

    # Re-submit with the epic changed to v2 and the milestone still named.
    issue_api.issue_action(db, "edit", _wire_edit(epic="v2", milestone="alpha"), made.ref)
    detail = issue_api.issue_get(db, made.ref)
    assert detail is not None
    assert detail.epic == "v2"
    assert detail.milestone == "alpha"


def test_edit_refuses_an_unknown_milestone(db: Engine) -> None:
    tt = a_project(db, "tt")
    an_epic(db, "tt", "v1")
    made = an_issue(db, tt, "wire it")
    with pytest.raises(Conflict, match='no milestone "ghost"'):
        issue_api.issue_action(db, "edit", _wire_edit(epic="v1", milestone="ghost"), made.ref)


# --- tags ------------------------------------------------------------------


def a_tag(engine: Engine, name: str) -> int:
    response = tag_api.tag_action(engine, "createTag", {"name": name})
    assert response.created_id is not None
    return response.created_id


def test_create_tag_refuses_a_blank_or_duplicate_live_name(db: Engine) -> None:
    with platform_db.transaction(db) as tx, pytest.raises(Invalid):
        tag_group.trigger(ActionDeps(tx), "createTag", {"name": "  "})
    a_tag(db, "bug")
    with platform_db.transaction(db) as tx, pytest.raises(Conflict, match="already exists"):
        tag_group.trigger(ActionDeps(tx), "createTag", {"name": "bug"})
    # A distinct name goes through.
    with platform_db.transaction(db) as tx:
        response = tag_group.trigger(ActionDeps(tx), "createTag", {"name": "ui"})
    assert response.message == 'tag "ui": created'


def test_tag_rename_refuses_a_duplicate_and_delete_frees_the_name(db: Engine) -> None:
    bug = a_tag(db, "bug")
    a_tag(db, "ui")
    # Renaming onto a live name another tag holds is refused.
    with pytest.raises(Conflict, match="already exists"):
        tag_api.tag_action(db, "rename", {"name": "ui"}, bug)
    assert tag_api.tag_action(db, "rename", {"name": "defect"}, bug).message == (
        'tag "defect": renamed'
    )
    with platform_db.reading(db) as s:
        renamed = tag_queries.get_tag(s, bug)
    assert renamed is not None
    assert renamed.name == "defect"

    # A delete frees the name for a new tag — the live-only unique index.
    tag_api.tag_action(db, "delete", {}, bug)
    with platform_db.reading(db) as s:
        assert tag_queries.get_tag(s, bug) is None
    assert (
        tag_api.tag_action(db, "createTag", {"name": "defect"}).message == 'tag "defect": created'
    )


def test_tag_and_untag_attach_detach_and_refuse(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "wire it")
    a_tag(db, "bug")

    # Attach, and attaching again is an idempotent success.
    assert issue_api.issue_action(db, "tagIssue", {"name": "bug"}, made.ref).message == (
        'issue TT-1: tagged "bug"'
    )
    issue_api.issue_action(db, "tagIssue", {"name": "bug"}, made.ref)
    detail = issue_api.issue_get(db, made.ref)
    assert detail is not None
    assert detail.tags == ["bug"]

    # An unknown name is refused on both verbs.
    with pytest.raises(Conflict, match='no tag "nope"'):
        issue_api.issue_action(db, "tagIssue", {"name": "nope"}, made.ref)
    with pytest.raises(Conflict, match='no tag "nope"'):
        issue_api.issue_action(db, "untagIssue", {"name": "nope"}, made.ref)

    # Detach, and detaching an absent tag is a refusal.
    assert issue_api.issue_action(db, "untagIssue", {"name": "bug"}, made.ref).message == (
        'issue TT-1: untagged "bug"'
    )
    with pytest.raises(Conflict, match='not tagged "bug"'):
        issue_api.issue_action(db, "untagIssue", {"name": "bug"}, made.ref)


def test_detail_surfaces_an_issues_tags_sorted(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "wire it")
    for name in ["ui", "bug", "perf"]:
        a_tag(db, name)
        issue_api.issue_action(db, "tagIssue", {"name": name}, made.ref)
    detail = issue_api.issue_get(db, made.ref)
    assert detail is not None
    assert detail.tags == ["bug", "perf", "ui"]


def test_the_list_row_carries_an_issues_tags_sorted(db: Engine) -> None:
    # The list read carries tags too, not just the detail: a frontend filtering by tag
    # works over the rows it already loaded rather than going back to the database.
    tt = a_project(db, "tt")
    tagged = an_issue(db, tt, "wire it")
    an_issue(db, tt, "untouched")
    for name in ["ui", "bug", "perf"]:
        a_tag(db, name)
        issue_api.issue_action(db, "tagIssue", {"name": name}, tagged.ref)

    rows = {row.ref: row.tags for row in issue_api.issue_list(db, "tt")}
    assert rows == {tagged.ref: ["bug", "perf", "ui"], "TT-2": []}

    # And an untagged issue reads back empty once its tags are detached again.
    issue_api.issue_action(db, "untagIssue", {"name": "bug"}, tagged.ref)
    row = next(r for r in issue_api.issue_list(db, "tt") if r.ref == tagged.ref)
    assert row.tags == ["perf", "ui"]


def test_deleting_a_tag_clears_it_from_every_issue(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "wire it")
    bug = a_tag(db, "bug")
    issue_api.issue_action(db, "tagIssue", {"name": "bug"}, made.ref)

    tag_api.tag_action(db, "delete", {}, bug)
    detail = issue_api.issue_get(db, made.ref)
    assert detail is not None
    assert detail.tags == []


def test_tag_offers_and_top_level_create() -> None:
    seeded = Tag(id=1, name="bug")
    assert _keys(tag_group.offers(seeded)) == ["rename", "delete"]
    assert tag_actions.tag_actions.top_level_offers()[0].key == "createTag"


# --- dependencies ----------------------------------------------------------


def test_add_dependency_records_a_dependency_and_surfaces_it(db: Engine) -> None:
    tt = a_project(db, "tt")
    a = an_issue(db, tt, "groundwork")
    b = an_issue(db, tt, "depends on a")

    # b depends on a: the ref names the dependency.
    assert issue_api.issue_action(db, "addDependency", {"dependency": a.ref}, b.ref).message == (
        f"issue {b.ref}: depends on {a.ref}"
    )
    b_detail = issue_api.issue_get(db, b.ref)
    assert b_detail is not None
    assert b_detail.depends_on == [a.ref]
    assert b_detail.dependents == []
    # The mirror: a has b as a dependent, read from a's own end.
    a_detail = issue_api.issue_get(db, a.ref)
    assert a_detail is not None
    assert a_detail.dependents == [b.ref]
    assert a_detail.depends_on == []


def test_add_dependency_is_idempotent(db: Engine) -> None:
    tt = a_project(db, "tt")
    a = an_issue(db, tt, "groundwork")
    b = an_issue(db, tt, "depends on a")
    issue_api.issue_action(db, "addDependency", {"dependency": a.ref}, b.ref)
    # Adding the same edge again succeeds and does not double it.
    issue_api.issue_action(db, "addDependency", {"dependency": a.ref}, b.ref)
    detail = issue_api.issue_get(db, b.ref)
    assert detail is not None
    assert detail.depends_on == [a.ref]


def test_add_dependency_refuses_self_unknown_and_cross_project(db: Engine) -> None:
    tt = a_project(db, "tt")
    web = a_project(db, "web")
    a = an_issue(db, tt, "here")
    foreign = an_issue(db, web, "elsewhere")

    with pytest.raises(Conflict, match="cannot depend on itself"):
        issue_api.issue_action(db, "addDependency", {"dependency": a.ref}, a.ref)
    with pytest.raises(Conflict, match="no issue tt-999"):
        issue_api.issue_action(db, "addDependency", {"dependency": "tt-999"}, a.ref)
    with pytest.raises(Conflict, match="not in this project"):
        issue_api.issue_action(db, "addDependency", {"dependency": foreign.ref}, a.ref)


def test_remove_dependency_drops_an_edge_and_refuses_an_absent_one(db: Engine) -> None:
    tt = a_project(db, "tt")
    a = an_issue(db, tt, "groundwork")
    b = an_issue(db, tt, "depends on a")

    # Removing an edge that is not there is a refusal.
    with pytest.raises(Conflict, match=f"does not depend on {a.ref}"):
        issue_api.issue_action(db, "removeDependency", {"dependency": a.ref}, b.ref)

    issue_api.issue_action(db, "addDependency", {"dependency": a.ref}, b.ref)
    assert issue_api.issue_action(db, "removeDependency", {"dependency": a.ref}, b.ref).message == (
        f"issue {b.ref}: no longer depends on {a.ref}"
    )
    detail = issue_api.issue_get(db, b.ref)
    assert detail is not None
    assert detail.depends_on == []


def test_add_dependency_refuses_an_unknown_dependency_on_remove_too(db: Engine) -> None:
    tt = a_project(db, "tt")
    a = an_issue(db, tt, "here")
    with pytest.raises(Conflict, match="no issue tt-999"):
        issue_api.issue_action(db, "removeDependency", {"dependency": "tt-999"}, a.ref)


def test_add_dependency_refuses_a_direct_two_node_cycle(db: Engine) -> None:
    tt = a_project(db, "tt")
    a = an_issue(db, tt, "a")
    b = an_issue(db, tt, "b")
    # a depends on b; now making b depend on a would close a 2-node loop.
    issue_api.issue_action(db, "addDependency", {"dependency": b.ref}, a.ref)
    with pytest.raises(Conflict, match="would create a dependency cycle"):
        issue_api.issue_action(db, "addDependency", {"dependency": a.ref}, b.ref)


def test_add_dependency_refuses_a_transitive_cycle(db: Engine) -> None:
    # C depends on B depends on A; making A depend on C would close the loop.
    tt = a_project(db, "tt")
    a = an_issue(db, tt, "a")
    b = an_issue(db, tt, "b")
    c = an_issue(db, tt, "c")
    issue_api.issue_action(db, "addDependency", {"dependency": a.ref}, b.ref)  # B depends on A
    issue_api.issue_action(db, "addDependency", {"dependency": b.ref}, c.ref)  # C depends on B
    with pytest.raises(Conflict, match="would create a dependency cycle"):
        issue_api.issue_action(db, "addDependency", {"dependency": c.ref}, a.ref)  # A depends on C


def test_waiting_is_true_until_the_dependency_is_done_and_false_when_deleted(db: Engine) -> None:
    tt = a_project(db, "tt")
    a = an_issue(db, tt, "groundwork")
    b = an_issue(db, tt, "depends on a")
    issue_api.issue_action(db, "addDependency", {"dependency": a.ref}, b.ref)

    # An unfinished dependency makes b wait, in the detail and the list row alike.
    detail = issue_api.issue_get(db, b.ref)
    assert detail is not None and detail.waiting is True
    row = next(i for i in issue_api.issue_list(db, "tt") if i.ref == b.ref)
    assert row.waiting is True

    # Finishing the dependency clears it.
    issue_api.issue_action(db, "setStatus", {"status": "done"}, a.ref)
    detail = issue_api.issue_get(db, b.ref)
    assert detail is not None and detail.waiting is False

    # Reopening the dependency makes b wait again — so the delete below, not the
    # earlier done, is what clears it.
    issue_api.issue_action(db, "setStatus", {"status": "todo"}, a.ref)
    reopened = issue_api.issue_get(db, b.ref)
    assert reopened is not None and reopened.waiting is True

    # A deleted dependency does not count and drops out of the ref list.
    issue_api.issue_action(db, "delete", {}, a.ref)
    detail = issue_api.issue_get(db, b.ref)
    assert detail is not None
    assert detail.waiting is False
    assert detail.depends_on == []


def test_a_canceled_dependency_no_longer_holds_its_dependent(db: Engine) -> None:
    # Canceling a dependency closes it as surely as finishing it: the work will never
    # arrive, so it must not leave the dependent waiting forever.
    tt = a_project(db, "tt")
    a = an_issue(db, tt, "abandoned groundwork")
    b = an_issue(db, tt, "depends on a")
    issue_api.issue_action(db, "addDependency", {"dependency": a.ref}, b.ref)

    detail = issue_api.issue_get(db, b.ref)
    assert detail is not None and detail.waiting is True

    issue_api.issue_action(db, "setStatus", {"status": "canceled"}, a.ref)
    cleared = issue_api.issue_get(db, b.ref)
    assert cleared is not None and cleared.waiting is False
    row = next(i for i in issue_api.issue_list(db, "tt") if i.ref == b.ref)
    assert row.waiting is False


def test_dependents_downstream_is_the_forward_reachable_set() -> None:
    # A→B→C and A→D: from A the closure is B, C, D; from a leaf it is empty. Pure,
    # no database — the reachability the cycle guard rides on.
    edges = [(1, 2), (2, 3), (1, 4)]
    assert issue_queries.dependents_downstream(edges, 1) == {2, 3, 4}
    assert issue_queries.dependents_downstream(edges, 2) == {3}
    assert issue_queries.dependents_downstream(edges, 3) == set()
    # A cycle already in the data terminates rather than looping forever.
    assert issue_queries.dependents_downstream([(1, 2), (2, 1)], 1) == {1, 2}


# --- comments --------------------------------------------------------------


def a_comment(engine: Engine, issue_ref: str, body: str) -> int:
    """Add a comment under the issue named by its ref, and return the comment's id."""
    response = issue_api.issue_action(engine, "addComment", {"body": body}, issue_ref)
    assert response.created_id is not None
    return response.created_id


def test_add_comment_creates_one_under_the_issue(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "wire it")
    response = issue_api.issue_action(db, "addComment", {"body": " first note "}, made.ref)
    assert response.message == "comment 1: created"
    assert response.created_id == 1
    with platform_db.reading(db) as s:
        comment = comment_queries.get_comment(s, 1)
    assert comment is not None
    assert comment.body == "first note"  # trimmed
    # Filed under the issue the ref names.
    assert comment.issue.ref == made.ref

    # A blank body is the object's refusal.
    with pytest.raises(Invalid):
        issue_api.issue_action(db, "addComment", {"body": "   "}, made.ref)


def test_comment_edit_rewrites_and_refuses_a_blank_body(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "wire it")
    comment_id = a_comment(db, made.ref, "old note")

    seeded = Comment(id=1, issue_id=1, body="old note")
    assert comment_actions.EditComment.availability(seeded) == Runnable()
    assert comment_actions.Delete.availability(seeded) == Runnable()

    response = run_comment(
        db,
        comment_actions.EditComment,
        comment_id,
        comment_schemas.EditCommentPayload(body=" revised "),
    )
    assert response.message == "comment 1: saved"
    with platform_db.reading(db) as s:
        read = comment_queries.get_comment(s, comment_id)
    assert read is not None
    assert read.body == "revised"  # trimmed

    with pytest.raises(Invalid):
        run_comment(
            db,
            comment_actions.EditComment,
            comment_id,
            comment_schemas.EditCommentPayload(body="   "),
        )


def test_comment_delete_stamps_deleted_at(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "wire it")
    comment_id = a_comment(db, made.ref, "note")

    assert run_comment(db, comment_actions.Delete, comment_id, Empty()).message == (
        "comment 1: deleted"
    )
    # A deleted comment no longer resolves.
    with platform_db.reading(db) as s:
        assert comment_queries.get_comment(s, comment_id) is None


def test_issue_detail_embeds_live_comments_chronologically(db: Engine) -> None:
    tt = a_project(db, "tt")
    made = an_issue(db, tt, "wire it")
    a_comment(db, made.ref, "first")
    second = a_comment(db, made.ref, "second")
    a_comment(db, made.ref, "third")

    # A deleted comment drops out of the issue's thread; the live ones stay in
    # creation order.
    comment_api.comment_action(db, "delete", {}, second)

    detail = issue_api.issue_get(db, made.ref)
    assert detail is not None
    assert [c.body for c in detail.comments] == ["first", "third"]


# --- dispatch through the group -------------------------------------------


def _keys(offered: list[Any]) -> list[str]:
    return [offer.key for offer in offered]


def test_offers_keep_refused_actions_and_drop_absent_ones() -> None:
    assert _keys(issue_group.offers(issue())) == [
        "edit",
        "setStatus",
        "setDueDate",
        "tagIssue",
        "untagIssue",
        "addDependency",
        "removeDependency",
        "addComment",
        "delete",
    ]
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
        ("addEpic", Runnable()),
        ("addMilestone", Runnable()),
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
    wire = {
        "title": " new ",
        "body": "",
        "status": "todo",
        "priority": "medium",
        "due_date": None,
        "epic": None,
        "milestone": None,
    }
    via_api = issue_api.issue_action(erased, "edit", wire, seeded.ref).message

    typed = _fresh()
    tt = a_project(typed, "tt")
    seeded = an_issue(typed, tt, "old")
    via_typed = run_issue(typed, issue_actions.EditIssue, seeded.ref, _edit(title=" new ")).message

    assert via_api == via_typed == "issue TT-1: saved"

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

    assert via_api == via_typed == "issue TT-1: created"


def test_a_malformed_payload_is_invalid(db: Engine) -> None:
    tt = a_project(db, "tt")
    an_issue(db, tt, "one")
    for payload in [
        {},  # missing required fields
        # wrong type
        {
            "title": 5,
            "body": "",
            "status": "todo",
            "priority": "medium",
            "due_date": None,
            "epic": None,
            "milestone": None,
        },
        {
            "title": "x",
            "body": "",
            "status": "todo",
            "priority": "medium",
            "due_date": None,
            "epic": None,
            "milestone": None,
            "b": 1,
        },
    ]:
        with pytest.raises(Invalid):
            issue_api.issue_action(db, "edit", payload, "tt-1")
    # A value outside the enum, the one the schema could have told the caller
    # about in advance.
    with pytest.raises(Invalid):
        issue_api.issue_action(
            db,
            "edit",
            {
                "title": "x",
                "body": "",
                "status": "shipped",
                "priority": "medium",
                "due_date": None,
                "epic": None,
                "milestone": None,
            },
            "tt-1",
        )


def test_an_action_with_no_arguments_takes_an_empty_object_and_not_null(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "here")
    assert issue_api.issue_action(db, "delete", {}, seeded.ref).message == "issue TT-1: deleted"
    # ``null`` is refused where an empty object is not — the decode is the same one
    # a dispatch runs.
    assert decode(issue_actions.Delete, {}) == Empty()
    with pytest.raises(Invalid):
        decode(issue_actions.Delete, None)


def test_an_unknown_key_is_invalid(db: Engine) -> None:
    tt = a_project(db, "tt")
    an_issue(db, tt, "here")
    with pytest.raises(Invalid):
        issue_api.issue_action(db, "explode", {}, "tt-1")
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
            {
                "title": "x",
                "body": "",
                "status": "archived",
                "priority": "medium",
                "due_date": None,
                "epic": None,
                "milestone": None,
            },
            made.ref,
        )
    # ``status_note`` is gone from the issue edit, so sending it is an extra field.
    with pytest.raises(Invalid):
        issue_api.issue_action(
            db,
            "edit",
            {
                "title": "x",
                "body": "",
                "status": "todo",
                "priority": "medium",
                "due_date": None,
                "epic": None,
                "milestone": None,
                "status_note": "x",
            },
            made.ref,
        )
    # An issue status, and an issue-only field, through the project's edit.
    with pytest.raises(Invalid):
        project_api.project_action(
            db, "edit", {"title": "tt", "body": "", "status": "doing", "path": None}, "tt"
        )
    with pytest.raises(Invalid):
        project_api.project_action(
            db,
            "edit",
            {"title": "tt", "body": "", "status": "active", "priority": "high", "path": None},
            "tt",
        )
