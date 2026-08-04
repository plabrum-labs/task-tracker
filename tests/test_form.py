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


def test_the_merged_edit_payload_derives_a_field_per_editable_column() -> None:
    fields = fields_of(issue_schemas.EditIssuePayload)
    assert fields == [
        Field(name="title", required=True, kind=Text(), description="What to call the issue."),
        Field(
            name="body",
            required=True,
            kind=Text(),
            description="The issue's description. Blank clears it.",
        ),
        Field(
            name="status",
            required=True,
            kind=Enum(["todo", "doing", "done"]),
            description="Where the issue is up to.",
        ),
        Field(
            name="status_note",
            required=False,
            kind=OptionalText(),
            description="What to record about the move.",
        ),
        Field(
            name="priority",
            required=True,
            kind=Enum(["normal", "high"]),
            description="How far up the list the issue sorts.",
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
    fields = fields_of(issue_schemas.EditIssuePayload)
    values = [
        (fields[0], "keep"),  # title
        (fields[1], ""),  # body
        (fields[2], "doing"),  # status
        (fields[3], ""),  # status_note — the blank optional
        (fields[4], "normal"),  # priority
    ]
    built = payload(values)
    assert built == {
        "title": "keep",
        "body": "",
        "status": "doing",
        "status_note": None,
        "priority": "normal",
    }

    # And the decoder accepts exactly that: the dispatch writes, so reading the
    # issue back shows the null cleared the note.
    _seeded(db)
    _dispatch_issue(db, "edit", built)
    issue = issue_api.issue_get(db, 1)
    assert issue is not None
    assert issue.status_note is None


def test_a_blank_required_text_field_is_submitted_and_refused(db: Engine) -> None:
    # The form does not second-guess ``execute``; a form that did would be a
    # second place that has to agree.
    fields = fields_of(issue_schemas.EditIssuePayload)
    built = payload(
        [
            (fields[0], ""),
            (fields[1], ""),
            (fields[2], "todo"),
            (fields[3], ""),
            (fields[4], "normal"),
        ]
    )
    assert built["title"] == ""
    _seeded(db)
    with pytest.raises(Invalid):
        _dispatch_issue(db, "edit", built)


def test_text_and_enum_are_the_two_controls_a_field_derives() -> None:
    # The kind is what ``tui`` reads to choose between a text box and a selector.
    fields = fields_of(issue_schemas.EditIssuePayload)
    assert fields[0].kind == Text()  # title
    assert fields[4].kind == Enum(["normal", "high"])  # priority
