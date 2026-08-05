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
from tt.domains.project import api as project_api
from tt.domains.project import schemas as project_schemas
from tt.platform.actions import (
    Date,
    Empty,
    Enum,
    Field,
    Invalid,
    OptionalText,
    Reference,
    Text,
    fields_of,
    payload,
)


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
            kind=Enum(["backlog", "requires_planning", "todo", "doing", "done"]),
            description="Where the issue is up to.",
        ),
        Field(
            name="priority",
            required=True,
            kind=Enum(["normal", "high"]),
            description="How far up the list the issue sorts.",
        ),
        Field(
            name="due_date",
            required=True,
            kind=Date(),
            description="When the issue is due, as YYYY-MM-DD. Blank clears it.",
        ),
        Field(
            name="epic",
            required=True,
            kind=Reference(),
            description="The id of the epic this issue belongs to. Blank clears it.",
        ),
    ]


def test_a_reference_field_derives_from_an_int_base() -> None:
    # An ``int`` base is a pointer at another row's id; a blank submits the null
    # that clears the link, the same rule ``OptionalText`` carries.
    fields = fields_of(issue_schemas.EditIssuePayload)
    epic = fields[-1]
    assert epic.name == "epic"
    assert epic.kind == Reference()
    assert payload([(epic, "")]) == {"epic": None}
    assert payload([(epic, "3")]) == {"epic": "3"}


def test_the_set_due_date_payload_derives_a_single_date_field() -> None:
    assert fields_of(issue_schemas.SetDueDatePayload) == [
        Field(
            name="due_date",
            required=True,
            kind=Date(),
            description="When the issue is due, as YYYY-MM-DD. Blank clears it.",
        ),
    ]


def test_a_blank_date_field_submits_the_null_its_schema_advertises() -> None:
    # A ``Date``'s blank is a real ``null``, the same rule ``OptionalText`` carries,
    # because every date column is nullable.
    fields = fields_of(issue_schemas.SetDueDatePayload)
    assert fields[0].kind == Date()
    assert payload([(fields[0], "")]) == {"due_date": None}
    assert payload([(fields[0], "2026-09-01")]) == {"due_date": "2026-09-01"}


def test_the_set_status_payload_derives_a_single_status_enum_field() -> None:
    assert fields_of(issue_schemas.SetStatusPayload) == [
        Field(
            name="status",
            required=True,
            kind=Enum(["backlog", "requires_planning", "todo", "doing", "done"]),
            description="Where the issue is up to.",
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
    # nothing on screen mentions. ``int`` is now a ``Reference`` control, so the
    # exemplar of an unrenderable kind is a ``float`` — no control derives from it.
    class Unrenderable(BaseModel):
        ratio: float

    with pytest.raises(TypeError):
        fields_of(Unrenderable)


def test_a_blank_optional_field_submits_the_null_its_schema_advertises(db: Engine) -> None:
    # ``addIssue``'s optional ``body`` is the ``OptionalText`` whose blank state is a
    # real ``null`` rather than a withheld field.
    fields = fields_of(project_schemas.AddIssuePayload)
    assert fields[1].kind == OptionalText()  # body
    built = payload(
        [
            (fields[0], "triage inbox"),  # title
            (fields[1], ""),  # body — the blank optional
            (fields[2], "normal"),  # priority
        ]
    )
    assert built == {"title": "triage inbox", "body": None, "priority": "normal"}

    # And the decoder accepts exactly that: the dispatch writes, so reading the
    # created issue back shows the null body defaulted to blank.
    a_project(db, "tt")
    response = project_api.project_action(db, "addIssue", built, "tt")
    assert response.created_id is not None
    issue = issue_api.issue_get(db, response.created_id)
    assert issue is not None
    assert issue.body == ""


def test_a_blank_required_text_field_is_submitted_and_refused(db: Engine) -> None:
    # The form does not second-guess ``execute``; a form that did would be a
    # second place that has to agree.
    fields = fields_of(issue_schemas.EditIssuePayload)
    built = payload(
        [
            (fields[0], ""),
            (fields[1], ""),
            (fields[2], "todo"),
            (fields[3], "normal"),
            (fields[4], ""),  # due_date — blank clears
            (fields[5], ""),  # epic — blank clears
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
    assert fields[3].kind == Enum(["normal", "high"])  # priority
