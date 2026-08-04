"""The form derived from an action's payload model, driven with no terminal.

``fields_of`` is pure and reads the payload models directly, so the whole of what
a frontend turns into controls is assertable without a person, a sleep or a mock.
Where the blank-optional case needs the decoder to agree, it dispatches the
payload against a live row the way a frontend's write does.
"""

from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine

from conftest import a_project, an_issue
from tt.domains.issue import api as issue_api
from tt.domains.issue import schemas as issue_schemas
from tt.domains.project import schemas as project_schemas
from tt.platform.actions import Empty, Enum, Field, Invalid, OptionalText, Text, fields_of, payload


def _seeded(engine: Engine) -> None:
    tt = a_project(engine, "tt")
    an_issue(engine, tt, "ship the mvp")


def _dispatch_issue(engine: Engine, key: str, values: dict[str, Any]) -> str:
    return issue_api.issue_action(engine, key, values, 1).message


def test_an_enum_field_becomes_a_selector_over_the_member_names() -> None:
    fields = fields_of(issue_schemas.EditStatusPayload)
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
    # Alphabetically this would be body, priority, title. ``model_fields`` is in
    # declaration order and a dict preserves it.
    fields = fields_of(project_schemas.AddIssuePayload)
    assert [field.name for field in fields] == ["title", "body", "priority"]


def test_an_action_with_no_arguments_renders_a_form_with_no_fields() -> None:
    assert fields_of(Empty) == []


def test_a_field_the_form_cannot_render_fails_the_whole_form() -> None:
    # Dropping the field would render a form that cannot express the action, and
    # submitting it would produce a payload the decoder refuses for a reason
    # nothing on screen mentions. A non-string, non-enum field has no control.
    class Unrenderable(BaseModel):
        count: int

    with pytest.raises(TypeError):
        fields_of(Unrenderable)


def test_a_blank_optional_field_submits_the_null_its_schema_advertises(db: Engine) -> None:
    fields = fields_of(issue_schemas.EditStatusPayload)
    values = [(fields[0], "doing"), (fields[1], "")]
    built = payload(values)
    assert built == {"status": "doing", "note": None}

    # And the decoder accepts exactly that: the dispatch writes, so reading the
    # issue back shows the null cleared the note.
    _seeded(db)
    _dispatch_issue(db, "editStatus", built)
    issue = issue_api.issue_get(db, 1)
    assert issue is not None
    assert issue.status_note is None


def test_a_blank_required_text_field_is_submitted_and_refused(db: Engine) -> None:
    # The form does not second-guess ``execute``; a form that did would be a
    # second place that has to agree.
    fields = fields_of(issue_schemas.EditTitlePayload)
    built = payload([(fields[0], "")])
    assert built == {"title": ""}
    _seeded(db)
    with pytest.raises(Invalid):
        _dispatch_issue(db, "editTitle", built)


def test_text_and_enum_are_the_two_controls_a_field_derives() -> None:
    # The kind is what ``tui`` reads to choose between a text box and a selector.
    title = fields_of(issue_schemas.EditTitlePayload)[0]
    assert title.kind == Text()
    priority = fields_of(issue_schemas.EditPriorityPayload)[0]
    assert priority.kind == Enum(["normal", "high"])
