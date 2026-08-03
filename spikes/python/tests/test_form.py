"""The form derived from an action's advertised schema, driven with no terminal.

``form.of_schema`` is pure and the schemas come from the real registries, so the
whole of what a frontend turns into controls is assertable without a person, a
sleep or a mock. Where the blank-optional case needs the decoder to agree, it
dispatches the payload against a live row the way a frontend's write does.
"""

from typing import Any

import pytest
from sqlalchemy import Engine

from tt.domains.issue import Draft as IssueDraft
from tt.domains.issue import Priority
from tt.domains.issue import actions as issue_actions
from tt.domains.issue import services as issue_services
from tt.domains.project import Draft as ProjectDraft
from tt.domains.project import actions as project_actions
from tt.domains.project import services as project_services
from tt.platform import db as platform_db
from tt.platform import form
from tt.platform.error import Error
from tt.platform.form import Enum, Field, FormError, OptionalText, Text
from tt.platform.wire import dispatch


def _issue_schema(key: str) -> dict[str, Any]:
    entry = next(entry for entry in issue_actions.group() if entry.key == key)
    return entry.schema


def _project_schema(key: str) -> dict[str, Any]:
    entry = next(entry for entry in project_actions.group() if entry.key == key)
    return entry.schema


def _seeded(engine: Engine) -> None:
    with platform_db.transaction(engine) as tx:
        project = project_services.create_project(
            tx, ProjectDraft(slug="tt", title="task tracker", body="")
        )
        draft = IssueDraft(title="ship the mvp", body="", priority=Priority.HIGH)
        issue_services.create_issue(tx, project, draft)


def _dispatch_issue(engine: Engine, key: str, payload: dict[str, Any]) -> str:
    with platform_db.transaction(engine) as tx:
        issue = issue_services.get_issue(tx, 1)
        assert issue is not None
        return dispatch(issue_actions.group(), issue, key, payload, tx)


def test_an_enum_field_becomes_a_selector_over_the_schema_values() -> None:
    fields = form.of_schema(_issue_schema("editStatus"))
    assert fields == [
        Field(
            name="status",
            required=True,
            kind=Enum(["todo", "doing", "done"]),
            description="Where the issue is up to.",
        ),
        Field(
            name="note",
            required=False,
            kind=OptionalText(),
            description="What to record about the move.",
        ),
    ]


def test_the_fields_are_in_the_order_the_payload_declares_them() -> None:
    # Alphabetically this would be body, priority, title. Pydantic emits its
    # ``properties`` in declaration order and a dict preserves it.
    fields = form.of_schema(_project_schema("addIssue"))
    assert [field.name for field in fields] == ["title", "body", "priority"]


def test_an_action_with_no_arguments_renders_a_form_with_no_fields() -> None:
    assert form.of_schema(_issue_schema("delete")) == []


def test_a_schema_the_form_cannot_render_fails_the_whole_form() -> None:
    # Dropping the field would render a form that cannot express the action, and
    # submitting it would produce a payload the decoder refuses for a reason
    # nothing on screen mentions.
    unrenderable: dict[str, Any] = {
        "type": "object",
        "properties": {"count": {"type": "integer"}},
        "required": ["count"],
    }
    with pytest.raises(FormError):
        form.of_schema(unrenderable)
    with pytest.raises(FormError):
        form.of_schema({"type": "null"})


def test_a_blank_optional_field_submits_the_null_its_schema_advertises(db: Engine) -> None:
    fields = form.of_schema(_issue_schema("editStatus"))
    values = [(fields[0], "doing"), (fields[1], "")]
    payload = form.payload(values)
    assert payload == {"status": "doing", "note": None}

    # And the decoder accepts exactly that: the dispatch writes, so reading the
    # issue back shows the null cleared the note.
    _seeded(db)
    _dispatch_issue(db, "editStatus", payload)
    with platform_db.reading(db) as session:
        issue = issue_services.get_issue(session, 1)
    assert issue is not None
    assert issue.status_note is None


def test_a_blank_required_text_field_is_submitted_and_refused(db: Engine) -> None:
    # The form does not second-guess ``execute``; a form that did would be a
    # second place that has to agree.
    fields = form.of_schema(_issue_schema("editTitle"))
    payload = form.payload([(fields[0], "")])
    assert payload == {"title": ""}
    _seeded(db)
    with pytest.raises(Error):
        _dispatch_issue(db, "editTitle", payload)


def test_text_and_enum_are_the_two_controls_a_field_derives() -> None:
    # The kind is what ``tui`` reads to choose between a text box and a selector.
    title = form.of_schema(_issue_schema("editTitle"))[0]
    assert title.kind == Text()
    priority = form.of_schema(_issue_schema("editPriority"))[0]
    assert priority.kind == Enum(["normal", "high"])
