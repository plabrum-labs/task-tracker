"""The action layer, over a real in-memory SQLite database.

Availability is a pure function of the object, so the menu cases run against
literals with no database in sight. ``execute`` holds the transaction and writes
for itself, so the cases that ``run`` an action seed a row, run, and assert both
the message and the row's new state.

Per the root ``CLAUDE.md`` every action key gets two cases: the menu withholds it
and the write refuses it. No issue action refuses anything, so for those the
honest pair is "always offered" and the refusal ``execute`` states.
"""

from dataclasses import replace
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine

from conftest import a_project, an_issue
from tt.domains import schema
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
    schema.initialise(engine)
    return engine


def run[O, P: BaseModel](engine: Engine, action: type[Action[O, P]], obj: O, payload: P) -> str:
    """``Action.run`` inside one transaction, committing on success so a later read
    sees what it wrote."""
    with platform_db.transaction(engine) as tx:
        return action.run(obj, payload, tx)


def dispatch[O](engine: Engine, group: wire.Group[O], obj: O, key: str, payload: Any) -> str:
    """The same, through the erased wire path: decode the blob against the group,
    then run."""
    with platform_db.transaction(engine) as tx:
        return wire.dispatch(group, obj, key, payload, tx)


# --- literals, for the availability cases ---------------------------------


def issue() -> Issue:
    return Issue(
        id=1,
        project_id=1,
        project_slug="tt",
        title="a title",
        body="",
        status=Status.TODO,
        priority=Priority.NORMAL,
        status_note=None,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def project() -> Project:
    return Project(
        id=1,
        slug="tt",
        title="task tracker",
        body="",
        status=ProjectStatus.ACTIVE,
        todo=0,
        doing=0,
        done=0,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


# --- issues: the menu never withholds anything ----------------------------


def test_every_issue_edit_is_always_offered() -> None:
    for status in Status:
        for priority in Priority:
            seeded = replace(issue(), status=status, priority=priority)
            assert issue_actions.EditTitle.availability(seeded) == Runnable()
            assert issue_actions.EditBody.availability(seeded) == Runnable()
            assert issue_actions.EditStatus.availability(seeded) == Runnable()
            assert issue_actions.EditPriority.availability(seeded) == Runnable()
            assert issue_actions.Delete.availability(seeded) == Runnable()


def test_edit_title_trims_saves_and_refuses_a_blank_title(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "old")

    message = run(
        db, issue_actions.EditTitle, seeded, issue_schemas.EditTitlePayload(title=" new ")
    )
    assert message == "issue 1: saved"
    with platform_db.reading(db) as s:
        read = issue_services.issue(s, seeded.id)
    assert read is not None
    assert read.title == "new"

    with pytest.raises(Invalid):
        run(db, issue_actions.EditTitle, seeded, issue_schemas.EditTitlePayload(title="   "))


def test_edit_body_accepts_the_blank_that_edit_title_refuses(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "one")

    run(db, issue_actions.EditBody, seeded, issue_schemas.EditBodyPayload(body=""))
    with platform_db.reading(db) as s:
        read = issue_services.issue(s, seeded.id)
    assert read is not None
    assert read.body == ""


def test_edit_status_replaces_the_note_it_arrived_with(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "one")

    run(
        db,
        issue_actions.EditStatus,
        seeded,
        issue_schemas.EditStatusPayload(status=Status.DOING, note="started"),
    )
    with platform_db.reading(db) as s:
        noted = issue_services.issue(s, seeded.id)
    assert noted is not None
    assert noted.status == Status.DOING
    assert noted.status_note == "started"

    # Moving without one clears the old note.
    run(
        db,
        issue_actions.EditStatus,
        noted,
        issue_schemas.EditStatusPayload(status=Status.DONE, note=None),
    )
    with platform_db.reading(db) as s:
        cleared = issue_services.issue(s, seeded.id)
    assert cleared is not None
    assert cleared.status_note is None


def test_edit_priority_sets_the_priority(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "one")

    run(
        db,
        issue_actions.EditPriority,
        seeded,
        issue_schemas.EditPriorityPayload(priority=Priority.HIGH),
    )
    with platform_db.reading(db) as s:
        read = issue_services.issue(s, seeded.id)
    assert read is not None
    assert read.priority == Priority.HIGH


def test_delete_hides_an_issue_and_restore_brings_it_back(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "here")

    message = run(db, issue_actions.Delete, seeded, wire.Empty())
    assert message == "issue 1: deleted"
    with platform_db.reading(db) as s:
        assert issue_services.issue(s, seeded.id) is None

    with platform_db.reading(db) as s:
        deleted = issue_services.trashed_issues(s)[0]
    assert issue_actions.Restore.availability(deleted) == Runnable()
    message = run(db, issue_actions.Restore, deleted, wire.Empty())
    assert message == "issue 1: restored"
    with platform_db.reading(db) as s:
        assert issue_services.issue(s, seeded.id) is not None


# --- projects: the half with preconditions --------------------------------


def test_edit_status_is_refused_while_anything_is_doing(db: Engine) -> None:
    busy = replace(project(), doing=1)
    assert project_actions.EditStatus.availability(busy) == Refused("finish or drop 1 issue first")
    busier = replace(project(), doing=3)
    assert project_actions.EditStatus.availability(busier) == Refused(
        "finish or drop 3 issues first"
    )
    # Stated against the object, so asking to stay active is refused too.
    with pytest.raises(Conflict):
        run(
            db,
            project_actions.EditStatus,
            busy,
            project_schemas.EditStatusPayload(status=ProjectStatus.ACTIVE),
        )


def test_edit_status_is_runnable_once_nothing_is_doing(db: Engine) -> None:
    seeded = a_project(db, "tt")
    assert project_actions.EditStatus.availability(seeded) == Runnable()
    message = run(
        db,
        project_actions.EditStatus,
        seeded,
        project_schemas.EditStatusPayload(status=ProjectStatus.ARCHIVED),
    )
    assert message == "project tt: saved"
    with platform_db.reading(db) as s:
        read = project_services.project(s, "tt")
    assert read is not None
    assert read.status == ProjectStatus.ARCHIVED


def test_delete_is_refused_while_the_project_is_active(db: Engine) -> None:
    assert project_actions.Delete.availability(project()) == Refused("archive it first")
    seeded = a_project(db, "tt")
    with pytest.raises(Conflict):
        run(db, project_actions.Delete, seeded, wire.Empty())

    # Archive it, and it goes.
    with platform_db.transaction(db) as tx:
        current = project_services.project(tx, "tt")
        assert current is not None
        project_services.update_project(tx, replace(current, status=ProjectStatus.ARCHIVED))
    with platform_db.reading(db) as s:
        archived = project_services.project(s, "tt")
    assert archived is not None
    assert project_actions.Delete.availability(archived) == Runnable()
    message = run(db, project_actions.Delete, archived, wire.Empty())
    assert message == "project tt: deleted"
    with platform_db.reading(db) as s:
        assert project_services.project(s, "tt") is None


def test_add_issue_is_refused_while_the_project_is_archived(db: Engine) -> None:
    archived = replace(project(), status=ProjectStatus.ARCHIVED)
    assert project_actions.AddIssue.availability(archived) == Refused("project is archived")
    with pytest.raises(Conflict):
        run(
            db,
            project_actions.AddIssue,
            archived,
            project_schemas.AddIssuePayload(title="nope", body=None, priority=None),
        )


def test_add_issue_defaults_what_the_payload_leaves_out(db: Engine) -> None:
    seeded = a_project(db, "tt")
    message = run(
        db,
        project_actions.AddIssue,
        seeded,
        project_schemas.AddIssuePayload(title=" ship it ", body=None, priority=None),
    )
    assert message == "issue 1: created"
    with platform_db.reading(db) as s:
        created = issue_services.issues(s, "tt")[0]
    assert created.title == "ship it"
    assert created.body == ""
    assert created.priority == Priority.NORMAL

    with platform_db.reading(db) as s:
        again = project_services.project(s, "tt")
    assert again is not None
    with pytest.raises(Invalid):
        run(
            db,
            project_actions.AddIssue,
            again,
            project_schemas.AddIssuePayload(title="  ", body=None, priority=None),
        )


def test_create_project_refuses_a_slug_the_list_already_holds(db: Engine) -> None:
    assert project_actions.CreateProject.availability([project()]) == Runnable()
    tt = a_project(db, "tt")
    with pytest.raises(Conflict):
        run(
            db,
            project_actions.CreateProject,
            [tt],
            project_schemas.CreateProjectPayload(slug="tt", title=None, body=None),
        )
    with pytest.raises(Invalid):
        run(
            db,
            project_actions.CreateProject,
            [],
            project_schemas.CreateProjectPayload(slug="  ", title=None, body=None),
        )
    message = run(
        db,
        project_actions.CreateProject,
        [],
        project_schemas.CreateProjectPayload(slug="other", title="another", body=None),
    )
    assert message == "project other: created"
    with platform_db.reading(db) as s:
        made = project_services.project(s, "other")
    assert made is not None
    assert made.title == "another"


def test_restore_is_refused_once_the_slug_is_taken_again(db: Engine) -> None:
    def restorable(live: list[Project]) -> Restorable:
        return Restorable(
            deleted=Deleted(inner=project(), deleted_at="2026-01-02T00:00:00Z"), live=live
        )

    assert project_actions.Restore.availability(restorable([])) == Runnable()
    assert project_actions.Restore.availability(restorable([project()])) == Refused(
        'project "tt" exists again'
    )
    with pytest.raises(Conflict):
        run(db, project_actions.Restore, restorable([project()]), wire.Empty())


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
    busy = replace(project(), doing=2)
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
        dispatch(db, project_actions.group(), project(), "delete", {})


def test_the_wire_agrees_with_the_typed_path() -> None:
    erased = _fresh()
    tt = a_project(erased, "tt")
    seeded = an_issue(erased, tt, "old")
    via_wire = dispatch(erased, issue_actions.group(), seeded, "editTitle", {"title": " new "})

    typed = _fresh()
    tt = a_project(typed, "tt")
    seeded = an_issue(typed, tt, "old")
    via_typed = run(
        typed, issue_actions.EditTitle, seeded, issue_schemas.EditTitlePayload(title=" new ")
    )

    assert via_wire == via_typed == "issue 1: saved"

    # The same for a creator, whose execute writes a different table.
    erased = _fresh()
    tt = a_project(erased, "tt")
    via_wire = dispatch(erased, project_actions.group(), tt, "addIssue", {"title": "one"})

    typed = _fresh()
    tt = a_project(typed, "tt")
    via_typed = run(
        typed,
        project_actions.AddIssue,
        tt,
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
            dispatch(db, issue_actions.group(), issue(), "editTitle", payload)
    # A value outside the enum, the one the schema could have told the caller
    # about in advance.
    with pytest.raises(Invalid):
        dispatch(db, issue_actions.group(), issue(), "editStatus", {"status": "shipped"})


def test_an_action_with_no_arguments_takes_an_empty_object_and_not_null(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "here")
    assert dispatch(db, issue_actions.group(), seeded, "delete", {}) == "issue 1: deleted"
    with pytest.raises(Invalid):
        dispatch(db, issue_actions.group(), issue(), "delete", None)


def test_an_unknown_key_is_invalid(db: Engine) -> None:
    with pytest.raises(Invalid):
        dispatch(db, issue_actions.group(), issue(), "explode", {})
    with pytest.raises(Invalid):
        dispatch(db, project_actions.root(), [], "explode", {})


def test_one_key_in_two_groups_decodes_against_the_group_it_was_dispatched_on(db: Engine) -> None:
    issue_keys = _keys(issue_actions.group(), issue())
    project_keys = _keys(project_actions.group(), project())
    for key in ["editTitle", "editBody", "editStatus"]:
        assert key in issue_keys and key in project_keys

    # A project status through the issue's editStatus.
    with pytest.raises(Invalid):
        dispatch(db, issue_actions.group(), issue(), "editStatus", {"status": "archived"})
    # An issue status, and an issue-only field, through the project's.
    with pytest.raises(Invalid):
        dispatch(db, project_actions.group(), project(), "editStatus", {"status": "doing"})
    with pytest.raises(Invalid):
        dispatch(
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
        dispatch(db, issue_actions.group(), seeded, "editStatus", {"status": "doing", "note": None})
        == "issue 1: saved"
    )
    with platform_db.reading(db) as s:
        reloaded = issue_services.issue(s, seeded.id)
    assert reloaded is not None
    assert dispatch(db, issue_actions.group(), reloaded, "editStatus", {"status": "doing"}) == (
        "issue 1: saved"
    )
