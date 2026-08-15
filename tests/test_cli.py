"""The CLI, driven with no terminal: an argv in, an exit code and some text out.

``cli.app`` is a pure command tree over the registries, so a test can inspect what
subcommands it grew and invoke any argv against a seeded database through Typer's
``CliRunner``. Each invocation opens its own engine, so they must share one
database: the fixture seeds the shared test database and every invoke points
``--db`` at its url.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import Engine
from typer.testing import CliRunner

from tt.domains.project import api as project_api
from tt.frontend import cli

runner = CliRunner()

# A usage error is Click's own exit code, distinct from a refusal.
USAGE = 2


@pytest.fixture
def database(db: Engine) -> str:
    """A clean database with one project and one high-priority issue under it, seeded
    through the same ``api`` the CLI itself writes through. Returns its url so every
    invoke can point ``--db`` at the one database each invocation reopens."""
    project_api.project_action(db, "createProject", {"slug": "tt", "title": "task tracker"})
    project_api.project_action(db, "addIssue", {"title": "ship the mvp", "priority": "high"}, "tt")
    return db.url.render_as_string(hide_password=False)


def invoke(database: str, *args: str):
    return runner.invoke(cli.app, ["--db", database, *args])


def _names(app: object) -> list[str]:
    # ``registered_commands`` is in registration order, which is the order the tree
    # offers them: the fixed commands, then a subcommand per registered action.
    return [command.name for command in app.registered_commands]  # type: ignore[attr-defined]


def test_every_registered_action_has_a_subcommand_and_nothing_else_does() -> None:
    assert _names(cli.issue_app) == [
        "ls",
        "show",
        "actions",
        "action",
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
    # ``createProject`` is top-level — no address to hang a subcommand on — so it is
    # reached through the raw ``action`` path, or the hand-written ``init`` convenience,
    # rather than generated here.
    assert _names(cli.project_app) == [
        "ls",
        "show",
        "actions",
        "action",
        "init",
        "edit",
        "delete",
        "setPath",
        "addIssue",
        "addEpic",
        "addMilestone",
    ]
    assert _names(cli.epic_app) == [
        "ls",
        "show",
        "actions",
        "action",
        "edit",
        "setStatus",
        "setDueDate",
        "delete",
    ]
    assert _names(cli.milestone_app) == [
        "ls",
        "show",
        "actions",
        "action",
        "edit",
        "setDueDate",
        "delete",
    ]
    # ``createTag`` is top-level, reached through the raw ``action`` path rather than
    # generated, so only ``rename`` and ``delete`` grow subcommands.
    assert _names(cli.tag_app) == ["ls", "action", "rename", "delete"]
    # A comment is created through its issue's ``addComment`` and has no global list,
    # so its own tree is ``show``/``action`` and the generated ``edit``/``delete``.
    assert _names(cli.comment_app) == ["show", "actions", "action", "edit", "delete"]


def test_no_subcommand_opens_the_tui_on_the_chosen_database(
    database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bare ``tt`` is the TUI, not a help screen, and it opens the database the
    # callback resolved rather than a fresh default.
    opened: list[object] = []
    monkeypatch.setattr("tt.frontend.tui.run", lambda engine: opened.append(engine))
    result = invoke(database)
    assert result.exit_code == 0
    assert len(opened) == 1


def test_version_reports_the_source_tree_without_a_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``--version`` is eager: it must print and exit before the callback opens a
    # database or the TUI, so it works even when no database is reachable.
    opened: list[object] = []
    monkeypatch.setattr("tt.frontend.tui.run", lambda engine: opened.append(engine))
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert opened == []
    assert result.stdout.startswith("tt ")
    assert str(cli._SOURCE_ROOT) in result.stdout


def test_a_subcommand_does_not_open_the_tui(database: str, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[object] = []
    monkeypatch.setattr("tt.frontend.tui.run", lambda engine: opened.append(engine))
    assert invoke(database, "project", "ls").exit_code == 0
    assert opened == []


def test_a_required_field_is_a_required_option_and_an_enum_is_a_closed_one(database: str) -> None:
    # setStatus is a focused verb, not a whole-object edit, so its required field is
    # a required option: missing it is a usage error, not a payload the action
    # refuses further in.
    assert invoke(database, "issue", "setStatus", "tt-1").exit_code == USAGE
    # A value outside the enum is a usage error listing the alternatives.
    assert invoke(database, "issue", "setStatus", "tt-1", "--status", "shipped").exit_code == USAGE
    # An option the schema does not advertise is rejected.
    bogus = invoke(database, "issue", "setStatus", "tt-1", "--status", "todo", "--bogus", "x")
    assert bogus.exit_code == USAGE


def test_edit_hydrates_omitted_fields_and_an_explicit_blank_still_clears(database: str) -> None:
    # A partial edit changes only the flags passed; every field omitted keeps its
    # current value, read from the row rather than demanded at the parser or blanked.
    before = json.loads(invoke(database, "issue", "show", "tt-1").output)["issue"]
    invoke(database, "issue", "setDueDate", "tt-1", "--due_date", "2026-09-01")
    moved = invoke(database, "issue", "edit", "tt-1", "--status", "doing")
    assert moved.exit_code == 0
    after = json.loads(invoke(database, "issue", "show", "tt-1").output)["issue"]
    assert after["status"] == "doing"
    # Untouched fields are preserved, not cleared.
    assert after["title"] == before["title"]
    assert after["priority"] == before["priority"]
    assert after["due_date"] == "2026-09-01"
    # An explicit blank is still a clear — distinct from an omitted flag.
    cleared = invoke(database, "issue", "edit", "tt-1", "--due_date", "")
    assert cleared.exit_code == 0
    final = json.loads(invoke(database, "issue", "show", "tt-1").output)["issue"]
    assert final["due_date"] is None
    assert final["status"] == "doing"  # still preserved across the second edit


def test_show_and_actions_accept_a_redundant_json_flag(database: str) -> None:
    # ``ls`` opts into JSON with ``--json``; ``show`` and ``actions`` are always
    # JSON. A caller that reaches for ``--json`` on them out of ``ls`` habit must
    # still get JSON, not a usage error — the flag is accepted and ignored.
    plain = invoke(database, "issue", "show", "tt-1")
    flagged = invoke(database, "issue", "show", "tt-1", "--json")
    assert flagged.exit_code == 0
    assert json.loads(flagged.output) == json.loads(plain.output)

    actions = invoke(database, "issue", "actions", "tt-1", "--json")
    assert actions.exit_code == 0
    assert json.loads(actions.output) == json.loads(
        invoke(database, "issue", "actions", "tt-1").output
    )


def test_a_blank_required_field_is_the_actions_refusal_not_a_usage_error(database: str) -> None:
    # A blank title gets past the parser — what a title has to contain is the
    # action's business — so clearing it is a refusal (123), not a usage error (2).
    blank = invoke(database, "issue", "edit", "tt-1", "--title", "")
    assert blank.exit_code == cli.REFUSED
    assert "title is required" in blank.output


def test_the_help_text_carries_the_field_doc_and_the_enum_values(
    database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A wide terminal so the closed choice renders on one line: the status metavar
    # wraps mid-word at 80 columns, which would split a value across lines.
    monkeypatch.setenv("COLUMNS", "200")
    result = invoke(database, "issue", "edit", "--help")
    assert result.exit_code == 0
    assert "Where the issue is up to." in result.output
    # Typer renders the closed choice inline rather than clap's "[possible values:
    # …]", but the alternatives are all there.
    for value in ("backlog", "blocked", "todo", "doing", "done", "canceled"):
        assert value in result.output


def test_the_options_and_the_blob_reach_the_same_write(database: str) -> None:
    spelled = invoke(
        database,
        "issue",
        "edit",
        "tt-1",
        "--title",
        "x",
        "--body",
        "",
        "--status",
        "doing",
        "--priority",
        "medium",
        "--due_date",
        "",
        "--epic",
        "",
        "--milestone",
        "",
    )
    assert spelled.exit_code == 0
    blob = invoke(
        database,
        "issue",
        "action",
        "tt-1",
        "edit",
        '{"title":"x","body":"","status":"doing","priority":"medium","due_date":null,"epic":null,"milestone":null}',
    )
    assert blob.exit_code == 0
    assert spelled.output == blob.output


def test_set_status_is_its_own_subcommand_taking_only_status(database: str) -> None:
    # The focused verb grows a subcommand like any other action; its one option is
    # the status, and it writes the move at once.
    moved = invoke(database, "issue", "setStatus", "tt-1", "--status", "doing")
    assert moved.exit_code == 0
    assert "issue TT-1: doing" in moved.output
    shown = invoke(database, "issue", "show", "tt-1")
    assert json.loads(shown.output)["issue"]["status"] == "doing"
    # A value outside the closed set is a usage error listing the alternatives.
    assert invoke(database, "issue", "setStatus", "tt-1", "--status", "shipped").exit_code == USAGE


def test_a_refusal_comes_back_from_the_live_row_and_not_from_the_parser(database: str) -> None:
    # ``delete`` is a subcommand on an active project, because the tree is built
    # before any row has been read; the refusal comes from the live object.
    result = invoke(database, "project", "delete", "tt")
    assert result.exit_code == cli.REFUSED
    assert "archive it first" in result.output


def test_show_carries_the_object_without_the_action_schemas(database: str) -> None:
    # ``show`` is the object as it stands, nothing more. The offered actions are a
    # separate read (``actions``), so flipping one field never drags the whole
    # action schema back through the agent as a payload to reconstruct.
    result = invoke(database, "issue", "show", "tt-1")
    assert result.exit_code == 0
    value = json.loads(result.output)
    assert value["issue"]["project"] == "TT"
    assert value["issue"]["priority"] == "high"
    assert "actions" not in value


def test_actions_lists_the_offers_compactly_with_refusal_reasons(database: str) -> None:
    # The offers an object presents now, each keyed, labelled, and either runnable or
    # refused with the live reason. The static argument schema is deliberately absent
    # — that belongs to the verb's ``--help``, not to a per-row read.
    result = invoke(database, "issue", "actions", "tt-1")
    assert result.exit_code == 0
    offers = json.loads(result.output)
    assert [offer["key"] for offer in offers] == [
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
    edit = offers[0]
    assert edit["label"] == "Edit"
    assert edit["state"] == "runnable"
    assert "arguments" not in edit

    # A refused action comes back as the object's own reason, surfaced up front rather
    # than only on a rejected write.
    project_offers = json.loads(invoke(database, "project", "actions", "tt").output)
    delete = next(offer for offer in project_offers if offer["key"] == "delete")
    assert delete["state"] == "refused"
    assert "archive it first" in delete["reason"]


def test_add_dependency_over_the_cli_and_a_cycle_is_a_refusal(database: str) -> None:
    # The generated subcommand takes the dependency as a ref option. tt-1 is the
    # fixture's issue; tt-2 is added here and depends on tt-1.
    invoke(database, "project", "action", "addIssue", '{"title":"b"}', "--slug", "tt")
    added = invoke(database, "issue", "addDependency", "tt-2", "--dependency", "tt-1")
    assert added.exit_code == 0
    assert "depends on TT-1" in added.output
    shown = json.loads(invoke(database, "issue", "show", "tt-2").output)
    assert shown["issue"]["depends_on"] == ["TT-1"]
    assert shown["issue"]["waiting"] is True
    # The waiting issue carries the marker in the text list.
    listed = invoke(database, "issue", "ls", "--project", "tt")
    assert "⊘" in listed.output
    # Closing the loop the other way is the object's refusal (123), not a usage error.
    cycle = invoke(database, "issue", "addDependency", "tt-1", "--dependency", "tt-2")
    assert cycle.exit_code == cli.REFUSED
    assert "cycle" in cycle.output


def test_ls_json_emits_a_structured_array_and_text_stays_the_default(database: str) -> None:
    projects = invoke(database, "project", "ls", "--json")
    assert projects.exit_code == 0
    assert [p["slug"] for p in json.loads(projects.output)] == ["TT"]

    issues = invoke(database, "issue", "ls", "--json")
    assert issues.exit_code == 0
    rows = json.loads(issues.output)
    assert rows[0]["project"] == "TT"
    assert rows[0]["priority"] == "high"

    # Without the flag the reads stay the human text table, not JSON, so a person's
    # ``ls`` is unchanged and only an agent that asks pays for the structure.
    text = invoke(database, "project", "ls")
    assert text.exit_code == 0
    with pytest.raises(json.JSONDecodeError):
        json.loads(text.output)


def test_issue_ls_hides_closed_work_until_all_is_asked(database: str) -> None:
    # A second issue, closed. A bare ``ls`` is the open work only; ``--all`` widens
    # it to include the done/canceled rows that would otherwise dominate the read.
    invoke(database, "project", "action", "addIssue", '{"title":"shipped"}', "--slug", "tt")
    invoke(database, "issue", "setStatus", "tt-2", "--status", "done")
    default_refs = [r["ref"] for r in json.loads(invoke(database, "issue", "ls", "--json").output)]
    assert "TT-2" not in default_refs
    assert "TT-1" in default_refs
    all_out = invoke(database, "issue", "ls", "--all", "--json").output
    assert "TT-2" in [r["ref"] for r in json.loads(all_out)]


def test_named_edit_hydrates_omitted_fields_too(database: str) -> None:
    # The epic/milestone edit path (addressed by title within a project) hydrates the
    # same way the ref-addressed one does: change the status, keep the title.
    invoke(database, "project", "addEpic", "tt", "--title", "Payments", "--due_date", "2026-09-01")
    moved = invoke(database, "epic", "edit", "Payments", "--project", "tt", "--status", "done")
    assert moved.exit_code == 0
    shown = json.loads(invoke(database, "epic", "show", "Payments", "--project", "tt").output)
    assert shown["epic"]["status"] == "done"
    assert shown["epic"]["due_date"] == "2026-09-01"  # preserved, not blanked


def test_counts_are_one_nested_object_keyed_by_status_name(database: str) -> None:
    # The per-status tally is a single ``counts`` object keyed by each status' own
    # name, not a spray of flat top-level count keys. The seeded issue defaults to
    # todo, and the tally is dense, so every status is present with todo at one.
    shown = json.loads(invoke(database, "project", "show", "tt").output)
    project = shown["project"]
    assert project["counts"] == {
        "backlog": 0,
        "blocked": 0,
        "todo": 1,
        "doing": 0,
        "done": 0,
        "canceled": 0,
    }
    # The old flat count keys are gone from the top level.
    assert "todo" not in project


def test_create_project_is_reached_through_the_top_level_action_path(database: str) -> None:
    made = invoke(database, "project", "action", "createProject", '{"slug":"new","title":"New"}')
    assert made.exit_code == 0
    assert "project NEW: created" in made.output
    listed = invoke(database, "project", "ls")
    assert "NEW" in listed.output
    # A duplicate slug is the object's refusal, not a usage error.
    dup = invoke(database, "project", "action", "createProject", '{"slug":"new"}')
    assert dup.exit_code == cli.REFUSED
    assert "already exists" in dup.output


def test_init_binds_a_project_to_the_directory_it_is_given(database: str, tmp_path: Path) -> None:
    here = str(tmp_path)
    made = invoke(database, "project", "init", "web", "--title", "Web", "--path", here)
    assert made.exit_code == 0
    assert "project WEB: created" in made.output
    shown = json.loads(invoke(database, "project", "show", "web").output)
    assert shown["project"]["path"] == here


def test_init_defaults_the_binding_to_the_current_directory(
    database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With no ``--path``, ``init`` binds the working directory, which is the whole
    # point of an ``init`` over the raw ``createProject`` path.
    here = str(tmp_path)
    monkeypatch.setattr("tt.frontend.cli.os.getcwd", lambda: here)
    made = invoke(database, "project", "init", "web")
    assert made.exit_code == 0
    shown = json.loads(invoke(database, "project", "show", "web").output)
    assert shown["project"]["path"] == here


def test_init_refuses_a_directory_another_project_already_owns(
    database: str, tmp_path: Path
) -> None:
    # Binding a directory a live project already owns is the object's refusal (123),
    # not a usage error.
    here = str(tmp_path)
    assert invoke(database, "project", "init", "web", "--path", here).exit_code == 0
    clash = invoke(database, "project", "init", "app", "--path", here)
    assert clash.exit_code == cli.REFUSED
    assert "already belongs" in clash.output


def test_an_unknown_object_or_malformed_json_is_invalid_rather_than_a_crash(database: str) -> None:
    assert invoke(database, "issue", "show", "tt-99").exit_code == cli.REFUSED
    assert invoke(database, "project", "show", "nope").exit_code == cli.REFUSED
    malformed = invoke(database, "issue", "action", "tt-1", "edit", "not json")
    assert malformed.exit_code == cli.REFUSED


def test_issue_ls_epic_and_milestone_filters_need_a_project(database: str) -> None:
    # An epic or milestone title is project-scoped, so it has nowhere to resolve
    # without a project; pairing one with no ``--project`` is a usage error, not a
    # refusal from a live row.
    assert invoke(database, "issue", "ls", "--epic", "v1").exit_code == USAGE
    assert invoke(database, "issue", "ls", "--milestone", "alpha").exit_code == USAGE


def test_issue_ls_rejects_an_unknown_sort_as_a_usage_error(database: str) -> None:
    bad_field = invoke(database, "issue", "ls", "--project", "tt", "--sort", "bogus")
    assert bad_field.exit_code == USAGE
    bad_direction = invoke(database, "issue", "ls", "--project", "tt", "--sort", "due:sideways")
    assert bad_direction.exit_code == USAGE


def test_issue_ls_unknown_or_foreign_epic_is_a_refusal(database: str) -> None:
    # An epic title that names no row, and one that only another project holds, are
    # both the object answer "no such epic here" (123) — resolution is scoped to the
    # named project — distinct from a malformed invocation.
    assert invoke(database, "issue", "ls", "--project", "tt", "--epic", "ghost").exit_code == (
        cli.REFUSED
    )
    invoke(database, "project", "action", "createProject", '{"slug":"web","title":"Web"}')
    invoke(database, "project", "action", "addEpic", '{"title":"v1"}', "--slug", "web")
    # "v1" is a live epic in web, but not in tt, so from tt it does not resolve.
    foreign = invoke(database, "issue", "ls", "--project", "tt", "--epic", "v1")
    assert foreign.exit_code == cli.REFUSED


def test_issue_ls_filters_by_tag_and_sorts_by_the_chosen_field(database: str) -> None:
    invoke(
        database,
        "project",
        "action",
        "addIssue",
        '{"title":"later","priority":"low"}',
        "--slug",
        "tt",
    )
    invoke(database, "tag", "action", "createTag", '{"name":"bug"}')
    invoke(database, "issue", "action", "tt-1", "tagIssue", '{"name":"bug"}')

    tagged = json.loads(
        invoke(database, "issue", "ls", "--project", "tt", "--tag", "bug", "--json").output
    )
    assert [row["ref"] for row in tagged] == ["TT-1"]

    ascending = json.loads(
        invoke(
            database, "issue", "ls", "--project", "tt", "--sort", "priority:asc", "--json"
        ).output
    )
    assert [row["title"] for row in ascending] == ["later", "ship the mvp"]
