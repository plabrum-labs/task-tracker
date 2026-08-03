"""The action layer, over a real in-memory SQLite database.

Availability is a pure function of the object, so the menu cases run against
literals with no database in sight. ``execute`` mutates the loaded row and flushes,
so the cases that run an action load their object in the transaction they write —
the way a frontend does — then assert both the message and the row's new state.

Per the root ``CLAUDE.md`` every action key gets two cases: the menu withholds it
and the write refuses it. No issue action refuses anything, so for those the
honest pair is "always offered" and the refusal ``execute`` states.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine

from conftest import a_project, an_issue
from tt import schema
from tt.domains.issue import Issue, Priority, Status
from tt.domains.issue import actions as issue_actions
from tt.domains.issue import schemas as issue_schemas
from tt.domains.issue import services as issue_services
from tt.domains.project import Project, Restorable
from tt.domains.project import Status as ProjectStatus
from tt.domains.project import actions as project_actions
from tt.domains.project import schemas as project_schemas
from tt.domains.project import services as project_services
from tt.platform import db as platform_db
from tt.platform import wire
from tt.platform.action import Action, Refused, Runnable
from tt.platform.deleted import Deleted
from tt.platform.error import Conflict, Invalid

# --- run helpers ----------------------------------------------------------


def _fresh() -> Engine:
    engine = platform_db.connect("sqlite://")
    schema.create_all(engine)
    return engine


def run_issue[P: BaseModel](
    engine: Engine, action: type[Action[Issue, P]], issue_id: int, payload: P
) -> str:
    with platform_db.transaction(engine) as tx:
        obj = issue_services.get_issue(tx, issue_id)
        assert obj is not None
        return action.run(obj, payload, tx)


def run_project[P: BaseModel](
    engine: Engine, action: type[Action[Project, P]], slug: str, payload: P
) -> str:
    with platform_db.transaction(engine) as tx:
        obj = project_services.get_project(tx, slug)
        assert obj is not None
        return action.run(obj, payload, tx)


def run_projects[P: BaseModel](
    engine: Engine, action: type[Action[list[Project], P]], payload: P
) -> str:
    with platform_db.transaction(engine) as tx:
        return action.run(project_services.list_projects(tx), payload, tx)


def run_restore_issue(
    engine: Engine, action: type[Action[Deleted[Issue], wire.Empty]], issue_id: int
) -> str:
    with platform_db.transaction(engine) as tx:
        deleted = next(d for d in issue_services.trashed_issues(tx) if d.inner.id == issue_id)
        return action.run(deleted, wire.Empty(), tx)


def run_synthetic(engine: Engine, action: type[Action[Any, Any]], obj: Any, payload: Any) -> str:
    # A refusal is decided before ``execute``, so a synthetic object never has to be
    # attached to the session for these.
    with platform_db.transaction(engine) as tx:
        return action.run(obj, payload, tx)


def dispatch_issue(
    engine: Engine, group: wire.Group[Issue], issue_id: int, key: str, payload: Any
) -> str:
    with platform_db.transaction(engine) as tx:
        obj = issue_services.get_issue(tx, issue_id)
        assert obj is not None
        return wire.dispatch(group, obj, key, payload, tx)


def dispatch_project(
    engine: Engine, group: wire.Group[Project], slug: str, key: str, payload: Any
) -> str:
    with platform_db.transaction(engine) as tx:
        obj = project_services.get_project(tx, slug)
        assert obj is not None
        return wire.dispatch(group, obj, key, payload, tx)


def dispatch_synthetic(
    engine: Engine, group: wire.Group[Any], obj: Any, key: str, payload: Any
) -> str:
    with platform_db.transaction(engine) as tx:
        return wire.dispatch(group, obj, key, payload, tx)


# --- literals, for the availability cases ---------------------------------


def issue() -> Issue:
    return Issue(
        id=1,
        project_id=1,
        title="a title",
        body="",
        status=Status.TODO,
        priority=Priority.NORMAL,
        status_note=None,
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
            status_note=None,
        )
        for i in range(n)
    ]


# --- issues: the menu never withholds anything ----------------------------


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
                status_note=None,
            )
            assert issue_actions.EditTitle.availability(seeded) == Runnable()
            assert issue_actions.EditBody.availability(seeded) == Runnable()
            assert issue_actions.EditStatus.availability(seeded) == Runnable()
            assert issue_actions.EditPriority.availability(seeded) == Runnable()
            assert issue_actions.Delete.availability(seeded) == Runnable()


def test_edit_title_trims_saves_and_refuses_a_blank_title(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "old")

    message = run_issue(
        db, issue_actions.EditTitle, seeded.id, issue_schemas.EditTitlePayload(title=" new ")
    )
    assert message == "issue 1: saved"
    with platform_db.reading(db) as s:
        read = issue_services.get_issue(s, seeded.id)
    assert read is not None
    assert read.title == "new"

    with pytest.raises(Invalid):
        run_issue(
            db, issue_actions.EditTitle, seeded.id, issue_schemas.EditTitlePayload(title="   ")
        )


def test_edit_body_accepts_the_blank_that_edit_title_refuses(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "one")

    run_issue(db, issue_actions.EditBody, seeded.id, issue_schemas.EditBodyPayload(body=""))
    with platform_db.reading(db) as s:
        read = issue_services.get_issue(s, seeded.id)
    assert read is not None
    assert read.body == ""


def test_edit_status_replaces_the_note_it_arrived_with(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "one")

    run_issue(
        db,
        issue_actions.EditStatus,
        seeded.id,
        issue_schemas.EditStatusPayload(status=Status.DOING, note="started"),
    )
    with platform_db.reading(db) as s:
        noted = issue_services.get_issue(s, seeded.id)
    assert noted is not None
    assert noted.status == Status.DOING
    assert noted.status_note == "started"

    # Moving without one clears the old note.
    run_issue(
        db,
        issue_actions.EditStatus,
        seeded.id,
        issue_schemas.EditStatusPayload(status=Status.DONE, note=None),
    )
    with platform_db.reading(db) as s:
        cleared = issue_services.get_issue(s, seeded.id)
    assert cleared is not None
    assert cleared.status_note is None


def test_edit_priority_sets_the_priority(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "one")

    run_issue(
        db,
        issue_actions.EditPriority,
        seeded.id,
        issue_schemas.EditPriorityPayload(priority=Priority.HIGH),
    )
    with platform_db.reading(db) as s:
        read = issue_services.get_issue(s, seeded.id)
    assert read is not None
    assert read.priority == Priority.HIGH


def test_delete_hides_an_issue_and_restore_brings_it_back(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "here")

    message = run_issue(db, issue_actions.Delete, seeded.id, wire.Empty())
    assert message == "issue 1: deleted"
    with platform_db.reading(db) as s:
        assert issue_services.get_issue(s, seeded.id) is None

    with platform_db.reading(db) as s:
        deleted = issue_services.trashed_issues(s)[0]
    assert issue_actions.Restore.availability(deleted) == Runnable()
    message = run_restore_issue(db, issue_actions.Restore, seeded.id)
    assert message == "issue 1: restored"
    with platform_db.reading(db) as s:
        assert issue_services.get_issue(s, seeded.id) is not None


# --- projects: the half with preconditions --------------------------------


def test_edit_status_is_refused_while_anything_is_doing(db: Engine) -> None:
    busy = project(issues=doing_issues(1))
    assert project_actions.EditStatus.availability(busy) == Refused("finish or drop 1 issue first")
    busier = project(issues=doing_issues(3))
    assert project_actions.EditStatus.availability(busier) == Refused(
        "finish or drop 3 issues first"
    )
    # Stated against the object, so asking to stay active is refused too.
    with pytest.raises(Conflict):
        run_synthetic(
            db,
            project_actions.EditStatus,
            busy,
            project_schemas.EditStatusPayload(status=ProjectStatus.ACTIVE),
        )


def test_edit_status_is_runnable_once_nothing_is_doing(db: Engine) -> None:
    seeded = a_project(db, "tt")
    assert project_actions.EditStatus.availability(seeded) == Runnable()
    message = run_project(
        db,
        project_actions.EditStatus,
        "tt",
        project_schemas.EditStatusPayload(status=ProjectStatus.ARCHIVED),
    )
    assert message == "project tt: saved"
    with platform_db.reading(db) as s:
        read = project_services.get_project(s, "tt")
    assert read is not None
    assert read.status == ProjectStatus.ARCHIVED


def test_delete_is_refused_while_the_project_is_active(db: Engine) -> None:
    assert project_actions.Delete.availability(project()) == Refused("archive it first")
    a_project(db, "tt")
    with pytest.raises(Conflict):
        run_project(db, project_actions.Delete, "tt", wire.Empty())

    # Archive it, and it goes.
    with platform_db.transaction(db) as tx:
        current = project_services.get_project(tx, "tt")
        assert current is not None
        current.status = ProjectStatus.ARCHIVED
    with platform_db.reading(db) as s:
        archived = project_services.get_project(s, "tt")
    assert archived is not None
    assert project_actions.Delete.availability(archived) == Runnable()
    message = run_project(db, project_actions.Delete, "tt", wire.Empty())
    assert message == "project tt: deleted"
    with platform_db.reading(db) as s:
        assert project_services.get_project(s, "tt") is None


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
    message = run_project(
        db,
        project_actions.AddIssue,
        "tt",
        project_schemas.AddIssuePayload(title=" ship it ", body=None, priority=None),
    )
    assert message == "issue 1: created"
    with platform_db.reading(db) as s:
        created = issue_services.list_issues(s, "tt")[0]
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


def test_create_project_refuses_a_slug_the_list_already_holds(db: Engine) -> None:
    assert project_actions.CreateProject.availability([project()]) == Runnable()
    a_project(db, "tt")
    with pytest.raises(Conflict):
        run_projects(
            db,
            project_actions.CreateProject,
            project_schemas.CreateProjectPayload(slug="tt", title=None, body=None),
        )
    with pytest.raises(Invalid):
        run_projects(
            db,
            project_actions.CreateProject,
            project_schemas.CreateProjectPayload(slug="  ", title=None, body=None),
        )
    message = run_projects(
        db,
        project_actions.CreateProject,
        project_schemas.CreateProjectPayload(slug="other", title="another", body=None),
    )
    assert message == "project other: created"
    with platform_db.reading(db) as s:
        made = project_services.get_project(s, "other")
    assert made is not None
    assert made.title == "another"


def test_restore_is_refused_once_the_slug_is_taken_again(db: Engine) -> None:
    def restorable(live: list[Project]) -> Restorable:
        return Restorable(
            deleted=Deleted(inner=project(), deleted_at=datetime(2026, 1, 2, tzinfo=UTC)),
            live=live,
        )

    assert project_actions.Restore.availability(restorable([])) == Runnable()
    assert project_actions.Restore.availability(restorable([project()])) == Refused(
        'project "tt" exists again'
    )
    with pytest.raises(Conflict):
        run_synthetic(db, project_actions.Restore, restorable([project()]), wire.Empty())


# --- the wire -------------------------------------------------------------


def _keys[O](group: wire.Group[O], obj: O) -> list[str]:
    return [entry.key for entry, _ in wire.available(group, obj)]


def _schema_of[O](group: wire.Group[O], key: str) -> dict[str, Any]:
    return next(entry.schema for entry in group if entry.key == key)


def test_a_group_keeps_refused_actions_and_drops_absent_ones() -> None:
    assert _keys(issue_actions.group(), issue()) == [
        "editTitle",
        "editBody",
        "editStatus",
        "editPriority",
        "delete",
    ]
    # An active project with two issues doing: editStatus and delete come back
    # refused, addIssue is runnable — one group, told apart by what each execute
    # writes rather than by which list it sat in.
    busy = project(issues=doing_issues(2))
    offered = [(entry.key, state) for entry, state in wire.available(project_actions.group(), busy)]
    assert offered == [
        ("editTitle", Runnable()),
        ("editBody", Runnable()),
        ("editStatus", Refused("finish or drop 2 issues first")),
        ("delete", Refused("archive it first")),
        ("addIssue", Runnable()),
    ]


def test_dispatch_refuses_what_availability_refused(db: Engine) -> None:
    with pytest.raises(Conflict):
        dispatch_synthetic(db, project_actions.group(), project(), "delete", {})


def test_the_wire_agrees_with_the_typed_path() -> None:
    erased = _fresh()
    tt = a_project(erased, "tt")
    seeded = an_issue(erased, tt, "old")
    via_wire = dispatch_issue(
        erased, issue_actions.group(), seeded.id, "editTitle", {"title": " new "}
    )

    typed = _fresh()
    tt = a_project(typed, "tt")
    seeded = an_issue(typed, tt, "old")
    via_typed = run_issue(
        typed, issue_actions.EditTitle, seeded.id, issue_schemas.EditTitlePayload(title=" new ")
    )

    assert via_wire == via_typed == "issue 1: saved"

    # The same for a creator, whose execute writes a different table.
    erased = _fresh()
    a_project(erased, "tt")
    via_wire = dispatch_project(erased, project_actions.group(), "tt", "addIssue", {"title": "one"})

    typed = _fresh()
    a_project(typed, "tt")
    via_typed = run_project(
        typed,
        project_actions.AddIssue,
        "tt",
        project_schemas.AddIssuePayload(title="one", body=None, priority=None),
    )

    assert via_wire == via_typed == "issue 1: created"


def test_a_malformed_payload_is_invalid(db: Engine) -> None:
    for payload in [
        {},  # missing a required field
        {"title": 5},  # wrong type
        {"title": "x", "bogus": 1},  # not advertised
    ]:
        with pytest.raises(Invalid):
            dispatch_synthetic(db, issue_actions.group(), issue(), "editTitle", payload)
    # A value outside the enum, the one the schema could have told the caller
    # about in advance.
    with pytest.raises(Invalid):
        dispatch_synthetic(db, issue_actions.group(), issue(), "editStatus", {"status": "shipped"})


def test_an_action_with_no_arguments_takes_an_empty_object_and_not_null(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "here")
    assert dispatch_issue(db, issue_actions.group(), seeded.id, "delete", {}) == "issue 1: deleted"
    with pytest.raises(Invalid):
        dispatch_synthetic(db, issue_actions.group(), issue(), "delete", None)


def test_an_unknown_key_is_invalid(db: Engine) -> None:
    with pytest.raises(Invalid):
        dispatch_synthetic(db, issue_actions.group(), issue(), "explode", {})
    with pytest.raises(Invalid):
        dispatch_synthetic(db, project_actions.root(), [], "explode", {})


def test_one_key_in_two_groups_decodes_against_the_group_it_was_dispatched_on(db: Engine) -> None:
    issue_keys = _keys(issue_actions.group(), issue())
    project_keys = _keys(project_actions.group(), project())
    for key in ["editTitle", "editBody", "editStatus"]:
        assert key in issue_keys and key in project_keys

    # A project status through the issue's editStatus.
    with pytest.raises(Invalid):
        dispatch_synthetic(db, issue_actions.group(), issue(), "editStatus", {"status": "archived"})
    # An issue status, and an issue-only field, through the project's.
    with pytest.raises(Invalid):
        dispatch_synthetic(
            db, project_actions.group(), project(), "editStatus", {"status": "doing"}
        )
    with pytest.raises(Invalid):
        dispatch_synthetic(
            db,
            project_actions.group(),
            project(),
            "editStatus",
            {"status": "active", "note": "why"},
        )


# --- the derived schemas --------------------------------------------------
#
# Snapshots, so a change to a payload model shows up here as a diff.


def test_a_required_field_carries_its_doc_comment() -> None:
    schema_ = _schema_of(issue_actions.group(), "editTitle")
    assert schema_["type"] == "object"
    assert schema_["additionalProperties"] is False
    assert schema_["properties"]["title"] == {
        "type": "string",
        "title": "Title",
        "description": "What to call the issue.",
    }
    assert schema_["required"] == ["title"]


def test_an_enum_field_is_a_ref_into_defs() -> None:
    schema_ = _schema_of(issue_actions.group(), "editStatus")
    status = schema_["properties"]["status"]
    assert status["$ref"] == "#/$defs/Status"
    assert status["description"] == "Where the issue is up to."
    assert schema_["$defs"]["Status"]["enum"] == ["todo", "doing", "done"]
    assert schema_["required"] == ["status"]


def test_an_optional_enum_is_an_any_of_with_a_null_arm() -> None:
    schema_ = _schema_of(project_actions.group(), "addIssue")
    priority = schema_["properties"]["priority"]
    assert priority["anyOf"] == [{"$ref": "#/$defs/Priority"}, {"type": "null"}]
    assert priority["description"] == "How far up the list it sorts. Defaults to normal."
    assert schema_["required"] == ["title"]
    assert schema_["$defs"]["Priority"]["enum"] == ["normal", "high"]


def test_an_action_with_no_arguments_advertises_no_properties() -> None:
    # Pydantic emits an empty ``properties`` dict where Rust's schemars omits the
    # key outright; either way the form has no fields and an unknown one is
    # refused by ``additionalProperties: false``.
    schema_ = _schema_of(issue_actions.group(), "delete")
    assert schema_["type"] == "object"
    assert schema_["additionalProperties"] is False
    assert schema_["properties"] == {}
    assert "required" not in schema_


def test_the_advertised_schema_accepts_what_the_decoder_accepts(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "one")
    assert (
        dispatch_issue(
            db, issue_actions.group(), seeded.id, "editStatus", {"status": "doing", "note": None}
        )
        == "issue 1: saved"
    )
    assert dispatch_issue(
        db, issue_actions.group(), seeded.id, "editStatus", {"status": "doing"}
    ) == ("issue 1: saved")
