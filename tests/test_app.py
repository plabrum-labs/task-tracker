"""The TUI driven through Textual's ``Pilot`` — a real app, a real event loop.

Each case builds ``TrackerApp`` over an in-memory database seeded through the domain
api, drives it with keystrokes, and asserts on the persisted rows and the mounted
widgets. These are the parity cases the reducer tests retired into: capture, a
refused edit that keeps its form, delete-and-reconcile, the width-gated board, a
greyed refusal, and the theme switch that persists.
"""

from pathlib import Path

import pytest
from sqlalchemy import Engine
from textual.pilot import Pilot
from textual.widgets import Input, OptionList, Static, TextArea

from tt.domains.issue import api as issue_api
from tt.domains.project import api as project_api
from tt.frontend.tui.app import TrackerApp
from tt.frontend.tui.domainview import (
    DETAIL_BESIDE_MIN_WIDTH,
    WAITING_GLYPH,
    CommentTarget,
    RunAction,
)
from tt.frontend.tui.screens.main import MainScreen
from tt.frontend.tui.screens.menu import MenuScreen
from tt.frontend.tui.widgets.body import BoardColumn, Card, IssueRow
from tt.frontend.tui.widgets.detail import DetailPane
from tt.frontend.tui.widgets.edit import EditPane
from tt.platform.config import ThemeName


def _main(app: TrackerApp) -> MainScreen:
    """The current screen, asserted to be the browse screen (no overlay on top)."""
    screen = app.screen
    assert isinstance(screen, MainScreen)
    return screen


def _ref(app: TrackerApp, issue_id: int) -> str:
    """The ref of a visible issue, looked up by the global id the cursor tracks — the
    address the api now takes, since the screen still tracks selection by id."""
    return next(i.ref for i in _main(app).issues if i.id == issue_id)


def _seed(engine: Engine) -> None:
    """One project ``tt`` with two issues, high before normal — the load order."""
    project_api.project_action(
        engine, "createProject", {"slug": "tt", "title": "task tracker", "path": "/repo/tt"}
    )
    project_api.project_action(
        engine, "addIssue", {"title": "ship the mvp", "priority": "high"}, "tt"
    )
    project_api.project_action(
        engine, "addIssue", {"title": "write readme", "priority": "medium"}, "tt"
    )


def _comment_on(engine: Engine, ref: str, body: str) -> int:
    """One comment on an issue, written through the action a frontend takes."""
    response = issue_api.issue_action(engine, "addComment", {"body": body}, ref)
    assert response.created_id is not None
    return response.created_id


def _rendered(pane: DetailPane) -> str:
    return " ".join(str(widget.render()) for widget in pane.query(Static))


@pytest.fixture
def seeded(db: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    # Point the preferences file at a temp path so no test reads or writes the real
    # per-user config.
    monkeypatch.setenv("TT_CONFIG", str(tmp_path / "config.toml"))
    _seed(db)
    return db


def _app(engine: Engine, theme: ThemeName = ThemeName.DARK) -> TrackerApp:
    return TrackerApp(engine, theme=theme)


async def _scope_tt(pilot: Pilot[None]) -> None:
    """Scope onto the ``tt`` project the way a user now does: through the switcher
    (``P``), whose first row is the only seeded project."""
    await pilot.press("P")
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


async def test_capture_writes_a_row(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _scope_tt(pilot)  # scope onto the tt project so capture has a target
        await pilot.press("n")
        await pilot.pause()
        for char in "triage inbox":
            await pilot.press(char if char != " " else "space")
        await pilot.press("enter")
        await pilot.pause()
        assert "triage inbox" in [i.title for i in issue_api.issue_list(seeded, "tt")]
        # And the new row is on screen — reloaded, not just written.
        titles = [str(row.query_one(".title").render()) for row in _main(app).query(IssueRow)]
        assert "triage inbox" in titles


async def test_a_refused_edit_keeps_the_editor_open(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _scope_tt(pilot)
        await pilot.press("x")  # issue menu
        await pilot.pause()
        await pilot.press("enter")  # pick Edit — the form opens pre-filled in the pane
        await pilot.pause()
        # No modal: the browse screen stays, with the edit form in the right pane and
        # the read detail hidden behind it.
        main = _main(app)
        edit = main.query_one(EditPane)
        assert edit.display and not main.query_one(DetailPane).display
        edit.query_one("#field-title", Input).value = ""  # clear the required title
        await pilot.press("enter")  # submit
        await pilot.pause()
        assert edit.display, "the editor stays open on a refusal"
        error = str(edit.query_one("#edit-error").render())
        assert "title is required" in error


async def test_tab_moves_between_the_edit_pane_fields(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _scope_tt(pilot)
        await pilot.press("x")  # issue menu
        await pilot.pause()
        await pilot.press("enter")  # pick Edit — the form opens focused on the first field
        await pilot.pause()
        edit = _main(app).query_one(EditPane)
        assert app.focused is edit.query_one("#field-title", Input)
        # Tab is no longer the layout switch, so it steps through the form's controls.
        # The body is prose, so its control is the multi-line editor, not a one-liner.
        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is edit.query_one("#field-body", TextArea)
        await pilot.press("shift+tab")
        await pilot.pause()
        assert app.focused is edit.query_one("#field-title", Input)


async def test_editing_the_title_in_the_pane_persists_and_returns_to_read(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _scope_tt(pilot)
        selected = _main(app).selected_id
        assert selected is not None
        await pilot.press("x")  # issue menu
        await pilot.pause()
        await pilot.press("enter")  # pick Edit
        await pilot.pause()
        title = _main(app).query_one(EditPane).query_one("#field-title", Input)
        title.value = "renamed in the pane"
        await pilot.press("enter")  # save
        await pilot.pause()
        # The write persisted, and the pane is back to the read detail of the survivor.
        edited = issue_api.issue_get(seeded, _ref(app, selected))
        assert edited is not None and edited.title == "renamed in the pane"
        main = _main(app)
        assert main.query_one(DetailPane).display and not main.query_one(EditPane).display


async def test_escape_abandons_the_pane_editor_without_writing(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _scope_tt(pilot)
        selected = _main(app).selected_id
        assert selected is not None
        before = issue_api.issue_get(seeded, _ref(app, selected))
        assert before is not None
        await pilot.press("x")  # issue menu
        await pilot.pause()
        await pilot.press("enter")  # pick Edit
        await pilot.pause()
        _main(app).query_one(EditPane).query_one("#field-title", Input).value = "abandoned"
        await pilot.press("escape")  # cancel — no write
        await pilot.pause()
        main = _main(app)
        assert main.query_one(DetailPane).display and not main.query_one(EditPane).display
        after = issue_api.issue_get(seeded, _ref(app, selected))
        assert after is not None and after.title == before.title


async def test_d_deletes_and_selection_lands_on_neighbour(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        first = _main(app).selected_id
        assert first is not None
        await pilot.press("d")  # delete has no fields, so it runs at once
        await pilot.pause()
        remaining = [i.id for i in _main(app).issues]
        assert first not in remaining
        assert _main(app).selected_id in remaining


async def test_s_cycles_the_selected_issue_status_and_keeps_the_selection(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        selected = _main(app).selected_id
        assert selected is not None
        before = issue_api.issue_get(seeded, _ref(app, selected))
        assert before is not None
        assert before.status == "todo"

        # The accelerator writes the next status at once — no form, unlike the menu's
        # pick-a-status. Three presses walk the whole cycle and wrap back to todo.
        for expected in ["doing", "done", "todo"]:
            await pilot.press("s")
            await pilot.pause()
            assert _main(app).selected_id == selected  # selection is retained
            moved = issue_api.issue_get(seeded, _ref(app, selected))
            assert moved is not None
            assert moved.status == expected  # advanced one step and persisted


async def test_the_detail_pane_reads_the_selected_issue_body_and_tracks_the_cursor(
    seeded: Engine,
) -> None:
    # Give the first issue a body; the pane is the only place a body is shown.
    first = issue_api.issue_list(seeded, "tt")[0]
    issue_api.issue_action(
        seeded,
        "edit",
        {
            "title": first.title,
            "body": "the parser drops trailing commas",
            "status": first.status,
            "priority": first.priority,
            "due_date": first.due_date,
            "epic": first.epic,
            "milestone": first.milestone,
        },
        first.ref,
    )
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        pane = _main(app).query_one(DetailPane)
        # Browse mode holds no focus even though the pane is focusable, so the first
        # j/k moves the list rather than scrolling the pane.
        assert app.focused is None
        assert _main(app).selected_id == first.id
        assert pane.detail is not None and pane.detail.id == first.id
        rendered = " ".join(str(widget.render()) for widget in pane.query(Static))
        assert "the parser drops trailing commas" in rendered  # the body is on screen

        # Enter drills in: focus moves into the pane so a long body can be scrolled.
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused is pane
        # Escape backs out to browse, leaving no widget focused.
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is None

        # The pane tracks the cursor: moving down shows the next issue's detail.
        await pilot.press("j")
        await pilot.pause()
        second = issue_api.issue_list(seeded, "tt")[1]
        assert pane.detail is not None and pane.detail.id == second.id


async def test_the_detail_pane_shows_the_comment_thread_and_its_empty_state(seeded: Engine) -> None:
    issues = issue_api.issue_list(seeded, "tt")
    _comment_on(seeded, issues[0].ref, "the parser drops trailing commas")
    written = issue_api.issue_get(seeded, issues[0].ref)
    assert written is not None
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        pane = _main(app).query_one(DetailPane)
        rendered = _rendered(pane)
        assert "COMMENTS · 1" in rendered
        assert "the parser drops trailing commas" in rendered
        # The date column carries the day it was written, not the year.
        assert f"{written.comments[0].created_at:%m-%d}" in rendered

        # The next issue has none, so the thread reads its empty state rather than
        # disappearing — the same fallback the body has.
        await pilot.press("j")
        await pilot.pause()
        assert pane.detail is not None and pane.detail.id == issues[1].id
        rendered = _rendered(pane)
        assert "COMMENTS · 0" in rendered and "no comments" in rendered


async def test_the_detail_pane_and_row_flag_a_waiting_issue(seeded: Engine) -> None:
    # tt-1 (high) sorts first and depends on tt-2 (medium); the selection opens on it,
    # so the pane shows what it depends on and the margin flags the row.
    issues = issue_api.issue_list(seeded, "tt")
    dependency, dependent = issues[1].ref, issues[0].ref
    issue_api.issue_action(seeded, "addDependency", {"dependency": dependency}, dependent)
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        pane = _main(app).query_one(DetailPane)
        rendered = _rendered(pane)
        assert "DEPENDENCIES" in rendered
        assert "DEPENDS ON · 1" in rendered
        assert dependency in rendered
        assert "waiting" in rendered  # the status-line badge
        # The waiting row carries the glyph in its margin cell.
        cells = [str(row.query_one(".waiting").render()) for row in _main(app).query(IssueRow)]
        assert any(WAITING_GLYPH in cell for cell in cells)

        # Moving to the dependency shows the other end of the edge: tt-1 depends on it.
        await pilot.press("j")
        await pilot.pause()
        assert pane.detail is not None and pane.detail.ref == dependency
        moved = _rendered(pane)
        assert "DEPENDENTS · 1" in moved and dependent in moved


async def test_c_adds_a_comment_and_the_thread_reflects_it(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        selected = _main(app).selected_id
        assert selected is not None
        ref = _ref(app, selected)
        await pilot.press("c")
        await pilot.pause()
        main = _main(app)
        edit = main.query_one(EditPane)
        assert edit.display and not main.query_one(DetailPane).display
        body = edit.query_one("#field-body", TextArea)
        assert body.text == ""  # a create opens blank, not seeded from the issue
        body.text = "shipped behind a flag"
        await pilot.press("ctrl+s")  # the prose editor takes enter, so ctrl+s saves
        await pilot.pause()
        written = issue_api.issue_get(seeded, ref)
        assert written is not None
        assert [c.body for c in written.comments] == ["shipped behind a flag"]
        # The thread reloaded behind the form, so the new comment is on screen.
        assert "shipped behind a flag" in _rendered(_main(app).query_one(DetailPane))


async def test_focusing_the_pane_moves_a_comment_cursor_and_escape_drops_it(
    seeded: Engine,
) -> None:
    first = issue_api.issue_list(seeded, "tt")[0]
    one = _comment_on(seeded, first.ref, "first note")
    two = _comment_on(seeded, first.ref, "second note")
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        pane = _main(app).query_one(DetailPane)
        assert pane.selected_comment_id is None  # browse selects no comment

        await pilot.press("enter")  # focus the pane: j/k now walk the thread
        await pilot.pause()
        assert app.focused is pane
        await pilot.press("j")
        await pilot.pause()
        assert pane.selected_comment_id == one
        await pilot.press("j")
        await pilot.pause()
        assert pane.selected_comment_id == two
        await pilot.press("j")  # clamps at the end of the thread
        await pilot.pause()
        assert pane.selected_comment_id == two
        await pilot.press("k")
        await pilot.pause()
        assert pane.selected_comment_id == one

        # Escaping back to browse drops the cursor with the focus, so the browse keys
        # address the issue again.
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is None
        assert pane.selected_comment_id is None


async def test_e_edits_the_selected_comment_and_a_refusal_keeps_its_editor_open(
    seeded: Engine,
) -> None:
    first = issue_api.issue_list(seeded, "tt")[0]
    _comment_on(seeded, first.ref, "the parser drops trailing commas")
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("enter")  # into the thread
        await pilot.press("j")  # onto its one comment
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        edit = _main(app).query_one(EditPane)
        # Seeded from the comment, not from the issue it hangs off.
        body = edit.query_one("#field-body", TextArea)
        assert body.text == "the parser drops trailing commas"

        # A blank body is the comment's own refusal; it lands in the form, which stays.
        body.text = "   "
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert edit.display
        assert "body is required" in str(edit.query_one("#edit-error").render())

        body.text = "the parser drops trailing commas, still"
        await pilot.press("ctrl+s")
        await pilot.pause()
        written = issue_api.issue_get(seeded, first.ref)
        assert written is not None
        assert [c.body for c in written.comments] == ["the parser drops trailing commas, still"]
        assert "the parser drops trailing commas, still" in _rendered(
            _main(app).query_one(DetailPane)
        )


async def test_d_deletes_the_selected_comment_and_the_cursor_lands_on_its_neighbour(
    seeded: Engine,
) -> None:
    first = issue_api.issue_list(seeded, "tt")[0]
    one = _comment_on(seeded, first.ref, "first note")
    two = _comment_on(seeded, first.ref, "second note")
    three = _comment_on(seeded, first.ref, "third note")
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.press("j")
        await pilot.press("j")  # onto the middle comment
        await pilot.pause()
        pane = _main(app).query_one(DetailPane)
        assert pane.selected_comment_id == two

        await pilot.press("d")  # delete has no fields, so it runs at once
        await pilot.pause()
        written = issue_api.issue_get(seeded, first.ref)
        assert written is not None
        assert [c.id for c in written.comments] == [one, three]
        # Whatever now sits where the deleted comment was takes the cursor, and the
        # issue behind it is untouched.
        assert pane.selected_comment_id == three
        assert "second note" not in _rendered(pane)
        assert issue_api.issue_get(seeded, first.ref) is not None


async def test_x_opens_the_selected_comments_menu_rather_than_the_issues(seeded: Engine) -> None:
    first = issue_api.issue_list(seeded, "tt")[0]
    comment_id = _comment_on(seeded, first.ref, "the parser drops trailing commas")
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.press("j")  # onto the comment
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        menu = app.screen
        assert isinstance(menu, MenuScreen)
        # What a comment offers, not what an issue does.
        assert [c.label for c in menu._commands] == ["Edit", "Delete"]
        targets = [c.run.target for c in menu._commands if isinstance(c.run, RunAction)]
        assert targets == [CommentTarget(comment_id), CommentTarget(comment_id)]


async def test_detail_pane_stacks_below_the_list_when_narrow(seeded: Engine) -> None:
    wide = _app(seeded)
    async with wide.run_test(size=(DETAIL_BESIDE_MIN_WIDTH + 20, 30)) as pilot:
        await pilot.pause()
        assert not _main(wide).query_one("#content").has_class("below")  # beside: room for both

    narrow = _app(seeded)
    async with narrow.run_test(size=(DETAIL_BESIDE_MIN_WIDTH - 20, 30)) as pilot:
        await pilot.pause()
        assert _main(narrow).query_one("#content").has_class("below")  # stacked under the list


async def test_board_only_appears_at_width_90(seeded: Engine) -> None:
    wide = _app(seeded)
    async with wide.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("]")  # cycle to the board layout
        await pilot.pause()
        assert _main(wide).view_layout == "board"
        assert len(_main(wide).query(Card)) > 0

    narrow = _app(seeded)
    async with narrow.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await pilot.press("]")  # cycle to the board layout
        await pilot.pause()
        assert _main(narrow).view_layout == "list"


async def test_the_layout_choice_persists_across_restarts(seeded: Engine) -> None:
    first = _app(seeded)
    async with first.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("]")  # switch to the board
        await pilot.pause()
        assert _main(first).view_layout == "board"

    # A fresh app over the same config reopens on the board, not the default list.
    second = _app(seeded)
    async with second.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert _main(second).view_layout == "board"


async def test_a_saved_board_falls_back_to_the_list_when_it_no_longer_fits(seeded: Engine) -> None:
    wide = _app(seeded)
    async with wide.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("]")  # save the board as the preference
        await pilot.pause()
        assert _main(wide).view_layout == "board"

    # Reopening in a terminal too narrow for the board opens the list instead.
    narrow = _app(seeded)
    async with narrow.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        assert _main(narrow).view_layout == "list"


async def test_board_columns_sit_side_by_side(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("]")  # cycle to the board layout
        await pilot.pause()
        cols = list(app.screen.query(BoardColumn))
        assert len(cols) == 3
        # A kanban lays the status columns across a row: one shared top edge, each
        # starting to the right of the last — not stacked down the same left edge.
        assert len({c.region.y for c in cols}) == 1
        xs = [c.region.x for c in cols]
        assert xs == sorted(xs) and len(set(xs)) == 3


async def test_the_menu_opens_on_the_list_with_the_filter_hidden(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("x")  # issue menu
        await pilot.pause()
        menu = app.screen
        assert isinstance(menu, MenuScreen)
        # No search box up front, and the list — not the filter — holds focus, so the
        # arrow keys and accelerators land on it.
        assert not menu.query_one("#menu-filter", Input).display
        assert app.focused is menu.query_one(OptionList)


async def test_slash_reveals_the_menu_filter_and_narrows_the_list(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("x")  # issue menu
        await pilot.pause()
        menu = app.screen
        assert isinstance(menu, MenuScreen)
        await pilot.press("slash")  # reveal the filter
        await pilot.pause()
        filter_box = menu.query_one("#menu-filter", Input)
        assert filter_box.display and app.focused is filter_box
        for char in "delete":
            await pilot.press(char)
        await pilot.pause()
        shown = [menu._commands[ci].label for ci in menu._shown]
        assert shown == [label for label in shown if "delete" in label.lower()]
        assert any("Delete" in label for label in shown)


async def test_an_accelerator_in_the_menu_runs_its_command(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _scope_tt(pilot)  # scope onto tt
        await pilot.press("x")  # issue menu
        await pilot.pause()
        await pilot.press("e")  # the edit accelerator picks Edit from the list
        await pilot.pause()
        # The menu dismissed and its edit command opened the pane, pre-filled.
        main = _main(app)
        edit = main.query_one(EditPane)
        assert edit.display and not main.query_one(DetailPane).display
        assert edit.query_one("#field-title", Input).value != ""


async def test_e_opens_the_edit_form_for_the_selected_issue(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _scope_tt(pilot)  # scope onto tt
        selected = _main(app).selected_id
        assert selected is not None
        current = issue_api.issue_get(seeded, _ref(app, selected))
        assert current is not None
        await pilot.press("e")  # browse shortcut straight into the edit form
        await pilot.pause()
        main = _main(app)
        edit = main.query_one(EditPane)
        assert edit.display and not main.query_one(DetailPane).display
        # Seeded from the issue's current values, not blank.
        assert edit.query_one("#field-title", Input).value == current.title


async def test_project_menu_greys_a_refused_delete(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _scope_tt(pilot)  # scope onto tt
        await pilot.press("X")  # the project menu
        await pilot.pause()
        menu = app.screen
        assert isinstance(menu, MenuScreen)
        index = next(i for i, c in enumerate(menu._commands) if c.label == "Delete")
        option = menu.query_one(OptionList).get_option_at_index(index)
        assert option.disabled  # active projects cannot be deleted
        assert "archive it first" in str(option.prompt)
        # Picking a disabled option is a no-op, so the project is still here.
        assert project_api.project_get(seeded, "tt") is not None


async def test_comma_switches_theme_and_persists(
    db: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tt.platform import config

    monkeypatch.setenv("TT_CONFIG", str(tmp_path / "config.toml"))
    _seed(db)
    # No pinned theme: the app reads the (absent) preference and opens on the default.
    app = TrackerApp(db)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.theme == ThemeName.DARK.value
        await pilot.press(",")  # open settings, opened on Dark
        await pilot.pause()
        await pilot.press("down")  # move to Light
        await pilot.press("enter")  # commit
        await pilot.pause()
        assert app.theme == ThemeName.LIGHT.value
        assert config.load().theme == ThemeName.LIGHT
