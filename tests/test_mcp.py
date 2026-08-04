"""The MCP server, driven with no event loop: the sync core the async shell wraps.

``build_tools`` and ``dispatch`` are pure functions of the registry and the
engine, so a test inspects the advertised tools and runs a dispatch against the
same in-memory ``db`` fixture the action tests use — no stdio, no subprocess, no
asyncio plugin. The tool list is derived from ``action_schemas`` the way the
server derives it, so a sixth action would show up here without this file naming
it.
"""

import json
from typing import Any

from sqlalchemy import Engine

from conftest import a_project, an_issue
from tt.domains.issue import api as issue_api
from tt.domains.issue import queries as issue_queries
from tt.domains.project import api as project_api
from tt.frontend import mcp
from tt.platform import db as platform_db


def _tool(name: str) -> Any:
    by_name = {tool.name: tool for tool in mcp.build_tools()}
    return by_name[name]


def _text(result: Any) -> str:
    return result.content[0].text


def test_the_tool_list_is_the_reads_plus_a_tool_per_registered_action() -> None:
    expected = {"project_list", "project_show", "issue_list", "issue_show"}
    expected |= {f"project_{key}" for key, _ in project_api.action_schemas()}
    expected |= {f"issue_{key}" for key, _ in issue_api.action_schemas()}
    # The one top-level action, named by the same ``{domain}_{KEY}`` rule.
    expected.add("project_createProject")
    assert {tool.name for tool in mcp.build_tools()} == expected


def test_an_enum_field_carries_its_wire_choices() -> None:
    status = _tool("issue_editStatus").input_schema["properties"]["status"]
    assert status == {
        "type": "string",
        "enum": ["todo", "doing", "done"],
        "description": "Where the issue is up to.",
    }


def test_an_object_tool_prepends_its_address_as_a_required_property() -> None:
    schema = _tool("issue_editStatus").input_schema
    assert schema["properties"]["id"] == {"type": "integer", "description": "The issue's id."}
    # The address and the required payload field are both demanded; the optional
    # note is not.
    assert set(schema["required"]) == {"id", "status"}
    assert "note" in schema["properties"]


def test_create_project_has_no_address_property() -> None:
    schema = _tool("project_createProject").input_schema
    assert "slug" in schema["properties"]
    # ``slug`` here is the payload's own field, not an address, so it is the create
    # payload's ``description`` rather than the address one.
    assert (
        schema["properties"]["slug"]["description"] == "The short name the project is addressed by."
    )


def test_a_runnable_write_persists(db: Engine) -> None:
    tt = a_project(db, "tt")
    seeded = an_issue(db, tt, "one")

    result = mcp.dispatch(db, "issue_editStatus", {"id": seeded.id, "status": "doing"})
    assert result.is_error is not True
    assert _text(result) == "issue 1: saved"

    with platform_db.reading(db) as session:
        read = issue_queries.get_issue(session, seeded.id)
    assert read is not None
    assert read.status.name.lower() == "doing"


def test_a_refused_write_is_a_tool_error_and_not_a_crash(db: Engine) -> None:
    a_project(db, "tt")
    # An active project refuses ``delete`` — the object's answer, mapped to an
    # error result rather than raised.
    result = mcp.dispatch(db, "project_delete", {"slug": "tt"})
    assert result.is_error is True
    assert "archive it first" in _text(result)


def test_a_top_level_create_routes_with_no_address(db: Engine) -> None:
    created = mcp.dispatch(db, "project_createProject", {"slug": "new", "title": "New"})
    assert created.is_error is not True
    assert "project new: created" in _text(created)
    assert project_api.project_get(db, "new") is not None

    # A duplicate slug is the object's refusal, not a crash.
    duplicate = mcp.dispatch(db, "project_createProject", {"slug": "new"})
    assert duplicate.is_error is True
    assert "already exists" in _text(duplicate)


def test_a_read_renders_the_offers_an_agent_picks_from(db: Engine) -> None:
    tt = a_project(db, "tt")
    an_issue(db, tt, "one")

    shown = json.loads(_text(mcp.dispatch(db, "issue_show", {"id": 1})))
    assert shown["issue"]["project"] == "tt"
    assert [action["key"] for action in shown["actions"]] == [
        "editTitle",
        "editBody",
        "editStatus",
        "editPriority",
        "delete",
    ]


def test_an_unknown_object_is_a_tool_error(db: Engine) -> None:
    missing = mcp.dispatch(db, "issue_show", {"id": 99})
    assert missing.is_error is True
    assert "no issue 99" in _text(missing)
