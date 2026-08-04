"""The CLI, driven with no terminal: an argv in, an exit code and some text out.

``cli.app`` is a pure command tree over the registries, so a test can inspect what
subcommands it grew and invoke any argv against a seeded database through Typer's
``CliRunner``. An in-memory ``sqlite://`` does not survive across separate
invocations, so the fixture seeds a temp-file database and every invoke points
``--db`` at the same path.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tt import schema
from tt.domains.project import api as project_api
from tt.frontend import cli
from tt.platform import db

runner = CliRunner()

# A usage error is Click's own exit code, distinct from a refusal.
USAGE = 2


@pytest.fixture
def database(tmp_path: Path) -> str:
    """A temp-file database with one project and one high-priority issue under it,
    seeded through the same ``api`` the CLI itself writes through."""
    url = f"sqlite:///{tmp_path / 'tt.db'}"
    schema.upgrade(url)
    engine = db.connect(url)
    project_api.project_action(engine, "createProject", {"slug": "tt", "title": "task tracker"})
    project_api.project_action(
        engine, "addIssue", {"title": "ship the mvp", "priority": "high"}, "tt"
    )
    engine.dispose()
    return url


def invoke(database: str, *args: str):
    return runner.invoke(cli.app, ["--db", database, *args])


def _names(app: object) -> list[str]:
    # ``registered_commands`` is in registration order, which is the order the tree
    # offers them: the fixed commands, then a subcommand per registered action.
    return [command.name for command in app.registered_commands]  # type: ignore[attr-defined]


def test_every_registered_action_has_a_subcommand_and_nothing_else_does() -> None:
    assert _names(cli.issue_app) == ["ls", "show", "action", "edit", "delete"]
    # ``createProject`` is top-level — no address to hang a subcommand on — so it is
    # reached through the raw ``action`` path rather than generated here.
    assert _names(cli.project_app) == [
        "ls",
        "show",
        "action",
        "edit",
        "delete",
        "setPath",
        "addIssue",
    ]


def test_no_subcommand_opens_the_tui_on_the_chosen_database(
    database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bare ``tt`` is the TUI, not a help screen, and it opens the database the
    # callback bootstrapped rather than a fresh default.
    opened: list[object] = []
    monkeypatch.setattr("tt.frontend.tui.run", lambda engine: opened.append(engine))
    result = invoke(database)
    assert result.exit_code == 0
    assert len(opened) == 1


def test_a_subcommand_does_not_open_the_tui(database: str, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[object] = []
    monkeypatch.setattr("tt.frontend.tui.run", lambda engine: opened.append(engine))
    assert invoke(database, "project", "ls").exit_code == 0
    assert opened == []


def test_a_required_field_is_a_required_option_and_an_enum_is_a_closed_one(database: str) -> None:
    full = ["--title", "x", "--body", "", "--status", "todo", "--priority", "normal"]
    # The required options come from the schema, so an edit missing them is a usage
    # error rather than a payload the decoder refuses further in.
    assert invoke(database, "issue", "edit", "1").exit_code == USAGE
    # A value outside the enum is a usage error listing the alternatives.
    bad_enum = ["--title", "x", "--body", "", "--status", "shipped", "--priority", "normal"]
    assert invoke(database, "issue", "edit", "1", *bad_enum).exit_code == USAGE
    # A blank title gets past the parser — what a title has to contain is the
    # action's business — so this is a refusal (123), not a usage error (2).
    blank = invoke(
        database,
        "issue",
        "edit",
        "1",
        "--title",
        "",
        "--body",
        "",
        "--status",
        "todo",
        "--priority",
        "normal",
    )
    assert blank.exit_code == cli.REFUSED
    assert "title is required" in blank.output
    # An option the schema does not advertise is rejected.
    assert invoke(database, "issue", "edit", "1", *full, "--bogus", "x").exit_code == USAGE


def test_the_help_text_carries_the_field_doc_and_the_enum_values(database: str) -> None:
    result = invoke(database, "issue", "edit", "--help")
    assert result.exit_code == 0
    assert "Where the issue is up to." in result.output
    # Typer renders the closed choice inline rather than clap's "[possible values:
    # …]", but the alternatives are all there.
    for value in ("todo", "doing", "done"):
        assert value in result.output


def test_the_options_and_the_blob_reach_the_same_write(database: str) -> None:
    spelled = invoke(
        database,
        "issue",
        "edit",
        "1",
        "--title",
        "x",
        "--body",
        "",
        "--status",
        "doing",
        "--priority",
        "normal",
    )
    assert spelled.exit_code == 0
    blob = invoke(
        database,
        "issue",
        "action",
        "1",
        "edit",
        '{"title":"x","body":"","status":"doing","priority":"normal"}',
    )
    assert blob.exit_code == 0
    assert spelled.output == blob.output


def test_a_refusal_comes_back_from_the_live_row_and_not_from_the_parser(database: str) -> None:
    # ``delete`` is a subcommand on an active project, because the tree is built
    # before any row has been read; the refusal comes from the live object.
    result = invoke(database, "project", "delete", "tt")
    assert result.exit_code == cli.REFUSED
    assert "archive it first" in result.output


def test_show_hands_an_agent_the_offers_and_their_schemas(database: str) -> None:
    result = invoke(database, "issue", "show", "1")
    assert result.exit_code == 0
    value = json.loads(result.output)
    assert value["issue"]["project"] == "tt"
    assert value["issue"]["priority"] == "high"

    keys = [action["key"] for action in value["actions"]]
    assert keys == ["edit", "delete"]
    # Each offer carries a human label alongside its key.
    assert value["actions"][0]["label"] == "Edit"
    # Each action carries its fields, descriptions and all, for an agent to fill in.
    title = value["actions"][0]["arguments"][0]
    assert title == {
        "name": "title",
        "required": True,
        "description": "What to call the issue.",
        "type": "text",
    }


def test_ls_json_emits_a_structured_array_and_text_stays_the_default(database: str) -> None:
    projects = invoke(database, "project", "ls", "--json")
    assert projects.exit_code == 0
    assert [p["slug"] for p in json.loads(projects.output)] == ["tt"]

    issues = invoke(database, "issue", "ls", "--json")
    assert issues.exit_code == 0
    rows = json.loads(issues.output)
    assert rows[0]["project"] == "tt"
    assert rows[0]["priority"] == "high"

    # Without the flag the reads stay the human text table, not JSON, so a person's
    # ``ls`` is unchanged and only an agent that asks pays for the structure.
    text = invoke(database, "project", "ls")
    assert text.exit_code == 0
    with pytest.raises(json.JSONDecodeError):
        json.loads(text.output)


def test_create_project_is_reached_through_the_top_level_action_path(database: str) -> None:
    made = invoke(database, "project", "action", "createProject", '{"slug":"new","title":"New"}')
    assert made.exit_code == 0
    assert "project new: created" in made.output
    listed = invoke(database, "project", "ls")
    assert "new" in listed.output
    # A duplicate slug is the object's refusal, not a usage error.
    dup = invoke(database, "project", "action", "createProject", '{"slug":"new"}')
    assert dup.exit_code == cli.REFUSED
    assert "already exists" in dup.output


def test_an_unknown_object_or_malformed_json_is_invalid_rather_than_a_crash(database: str) -> None:
    assert invoke(database, "issue", "show", "99").exit_code == cli.REFUSED
    assert invoke(database, "project", "show", "nope").exit_code == cli.REFUSED
    malformed = invoke(database, "issue", "action", "1", "edit", "not json")
    assert malformed.exit_code == cli.REFUSED
