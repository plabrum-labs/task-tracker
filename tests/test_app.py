"""The TUI driven through Textual's ``Pilot`` — a real app, a real event loop.

Each case builds ``TrackerApp`` over an in-memory database seeded through the domain
api, drives it with keystrokes, and asserts on the persisted rows and the mounted
widgets. These are the parity cases the reducer tests retired into: capture, a
refused edit that keeps its form, delete-and-reconcile, the width-gated board, a
greyed refusal, and the theme switch that persists.
"""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from textual.pilot import Pilot
from textual.widgets import Input, OptionList, Static, TextArea

from tt.domains.epic import api as epic_api
from tt.domains.issue import api as issue_api
from tt.domains.issue.schemas import IssueDetail, IssueListItem
from tt.domains.project import api as project_api
from tt.frontend.tui.app import TrackerApp
from tt.frontend.tui.domainview import (
    DETAIL_BESIDE_MIN_WIDTH,
    FACETS,
    SAVE_VIEW,
    WAITING_GLYPH,
    AllScope,
    CommentTarget,
    EpicTarget,
    Filter,
    HeaderAt,
    Navigate,
    ProjectScope,
    RunAction,
    TagTarget,
)
from tt.frontend.tui.screens.main import MainScreen
from tt.frontend.tui.screens.menu import MenuScreen
from tt.frontend.tui.widgets.body import (
    CARET_OPEN,
    CARET_SHUT,
    Body,
    Card,
    GroupColumn,
    IssueRow,
)
from tt.frontend.tui.widgets.detail import DetailPane
from tt.frontend.tui.widgets.edit import EditPane
from tt.frontend.tui.widgets.rollup import RollupRow
from tt.frontend.tui.widgets.topbar import ChipsBar
from tt.platform import config
from tt.platform.config import SavedFilter, ThemeName


def _main(app: TrackerApp) -> MainScreen:
    """The current screen, asserted to be the browse screen (no overlay on top)."""
    screen = app.screen
    assert isinstance(screen, MainScreen)
    return screen


def _selected(app: TrackerApp) -> int:
    """The id of the issue the cursor is on, asserted to be on one — these cases browse
    the flat list, which has no object header for it to land on."""
    cursor = _main(app).cursor
    assert isinstance(cursor, int)
    return cursor


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


def _fabricated(
    *,
    due_date: date | None = None,
    epic: str | None = None,
    milestone: str | None = None,
    tags: list[str] | None = None,
) -> IssueDetail:
    """A detail built by hand, so the pane can be driven through relations without
    seeding the epic, milestone and tag rows behind them."""
    written = datetime(2026, 8, 1, tzinfo=UTC)
    return IssueDetail(
        id=1,
        ref="tt-1",
        project="tt",
        title="ship the mvp",
        body="the parser drops trailing commas",
        status="todo",
        priority="high",
        due_date=due_date,
        epic=epic,
        milestone=milestone,
        tags=tags if tags is not None else [],
        comments=[],
        depends_on=[],
        dependents=[],
        waiting=False,
        created_at=written,
        updated_at=written,
    )


def _peer(issue_id: int, status: str, epic: str | None) -> IssueListItem:
    """A row of the pane's ``peers`` — the loaded scope the rollup line stands over."""
    return IssueListItem(
        id=issue_id,
        ref=f"tt-{issue_id}",
        project="tt",
        title=f"issue {issue_id}",
        status=status,
        priority="medium",
        due_date=None,
        epic=epic,
        milestone=None,
        tags=[],
        waiting=False,
    )


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
        selected = _selected(app)
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
        selected = _selected(app)
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
        first = _selected(app)
        await pilot.press("d")  # delete has no fields, so it runs at once
        await pilot.pause()
        remaining = [i.id for i in _main(app).issues]
        assert first not in remaining
        assert _main(app).cursor in remaining


async def test_s_cycles_the_selected_issue_status_and_keeps_the_selection(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        selected = _selected(app)
        before = issue_api.issue_get(seeded, _ref(app, selected))
        assert before is not None
        assert before.status == "todo"

        # The accelerator writes the next status at once — no form, unlike the menu's
        # pick-a-status. Three presses walk the whole cycle and wrap back to todo.
        for expected in ["doing", "done", "todo"]:
            await pilot.press("s")
            await pilot.pause()
            assert _main(app).cursor == selected  # selection is retained
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
        assert _main(app).cursor == first.id
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


async def test_the_detail_pane_shows_the_relations_the_issue_carries(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        pane = _main(app).query_one(DetailPane)
        pane.detail = _fabricated(
            due_date=date(2026, 8, 9), epic="tt-3", milestone="tt-4", tags=["parser", "ux"]
        )
        await pilot.pause()
        rendered = _rendered(pane)
        assert "due" in rendered and "2026-08-09" in rendered
        assert "epic" in rendered and "tt-3" in rendered
        assert "milestone" in rendered and "tt-4" in rendered
        assert "tags" in rendered and "#parser" in rendered and "#ux" in rendered


async def test_the_detail_pane_omits_the_relations_the_issue_lacks(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        pane = _main(app).query_one(DetailPane)
        pane.detail = _fabricated()
        await pilot.pause()
        rendered = _rendered(pane)
        # No label without a value beside it: an absent relation drops its whole row.
        for label in ("due", "epic", "milestone", "tags"):
            assert label not in rendered
        # And with neither an epic nor a milestone there is nothing to roll up.
        assert not pane.query(RollupRow)


async def test_the_rollup_line_summarises_the_epic_the_issue_belongs_to(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        pane = _main(app).query_one(DetailPane)
        # Three issues in the epic and one outside it, so the line counts its own.
        pane.peers = [
            _peer(1, "todo", "tt-3"),
            _peer(2, "doing", "tt-3"),
            _peer(3, "done", "tt-3"),
            _peer(4, "done", None),
        ]
        pane.detail = _fabricated(epic="tt-3", milestone="tt-4")
        await pilot.pause()
        line = pane.query_one(RollupRow)
        rendered = " ".join(str(widget.render()) for widget in line.query(Static))
        assert "tt-3" in rendered  # the identity
        assert "tt-4" in rendered  # the related column
        assert "1/3" in rendered  # one of the epic's three is done
        # The breakdown carries a glyph and a count per status the epic is spread over.
        assert "○ 1" in rendered and "◐ 1" in rendered and "● 1" in rendered


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
        selected = _selected(app)
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


async def _group_by(pilot: Pilot[None], *steps: int) -> None:
    """Group the body the way a user does: ``g`` opens the picker, each step is how far
    down that step's list the dimension sits, and the second step leaves the grouping
    one level deep unless it is asked for one."""
    await pilot.press("g")
    await pilot.pause()
    for down in (*steps, 0)[:2]:
        for _ in range(down):
            await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()


async def _group_by_status(pilot: Pilot[None]) -> None:
    """Group the body by status: ``Status`` is the row under ``No grouping``, with no
    second level nested inside it."""
    await _group_by(pilot, 1)


async def test_the_group_picker_sets_the_grouping_and_heads_each_group(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        body = _main(app).query_one(Body)
        assert _main(app).view_group == ("none",)  # opens flat, no headers
        assert not body.query(RollupRow)

        await _group_by_status(pilot)
        assert _main(app).view_group == ("status",)
        # One header per group, and the rows are still the flat list's rows.
        assert len(body.query(RollupRow)) == 3
        assert len(body.query(IssueRow)) == 2


async def test_columns_only_appear_at_width_90(seeded: Engine) -> None:
    wide = _app(seeded)
    async with wide.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _group_by_status(pilot)
        await pilot.press("]")  # fan the groups across the width
        await pilot.pause()
        assert _main(wide).view_render == "columns"
        assert len(_main(wide).query(Card)) > 0

    narrow = _app(seeded)
    async with narrow.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await _group_by_status(pilot)
        await pilot.press("]")  # no room for three columns: stays stacked
        await pilot.pause()
        assert _main(narrow).view_render == "stacked"


async def test_the_board_persists_across_restarts(seeded: Engine) -> None:
    first = _app(seeded)
    async with first.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _group_by_status(pilot)
        await pilot.press("]")  # switch to the board
        await pilot.pause()
        assert (_main(first).view_group, _main(first).view_render) == (("status",), "columns")

    # A fresh app over the same config reopens on the board, not the default list.
    second = _app(seeded)
    async with second.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert (_main(second).view_group, _main(second).view_render) == (("status",), "columns")


async def test_a_saved_board_falls_back_to_the_list_when_it_no_longer_fits(seeded: Engine) -> None:
    wide = _app(seeded)
    async with wide.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _group_by_status(pilot)
        await pilot.press("]")  # save the board as the preference
        await pilot.pause()
        assert _main(wide).view_render == "columns"

    # Reopening in a terminal too narrow for the board opens the flat list instead.
    narrow = _app(seeded)
    async with narrow.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        assert (_main(narrow).view_group, _main(narrow).view_render) == (("none",), "stacked")


async def test_group_columns_sit_side_by_side(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await _group_by_status(pilot)
        await pilot.press("]")  # fan the groups across the width
        await pilot.pause()
        cols = list(app.screen.query(GroupColumn))
        assert len(cols) == 3
        # A kanban lays the status columns across a row: one shared top edge, each
        # starting to the right of the last — not stacked down the same left edge.
        assert len({c.region.y for c in cols}) == 1
        xs = [c.region.x for c in cols]
        assert xs == sorted(xs) and len(set(xs)) == 3


def _headers(app: TrackerApp) -> list[RollupRow]:
    """The group headers on screen, in reading order. The body's, not the detail
    pane's — that draws the selected issue's epic in the same widget."""
    return list(_main(app).query_one(Body).query(RollupRow))


def _caret(header: RollupRow) -> str:
    return str(header.query_one(".r-id").render())[0]


async def test_the_picker_nests_a_second_dimension_inside_the_first(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # Status, then Priority nested inside it. Both seeded issues are todo, one high
        # and one medium, so one status group splits into two priority groups.
        await _group_by(pilot, 1, 4)
        assert _main(app).view_group == ("status", "priority")
        headers = _headers(app)
        assert [str(h.query_one(".r-id").render())[2:] for h in headers] == [
            "todo",
            "high",
            "medium",
            "doing",
            "done",
        ]
        # The nested level draws indented; the outer one is the spine it hangs off.
        assert [h.has_class("lvl-1") for h in headers] == [False, True, True, False, False]


async def test_folding_takes_a_group_off_the_page_and_expanding_brings_it_back(
    seeded: Engine,
) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _group_by_status(pilot)
        body = _main(app).query_one(Body)
        assert len(body.query(IssueRow)) == 2
        assert all(_caret(h) == CARET_OPEN for h in _headers(app))

        await pilot.press("z", "M")
        await pilot.pause()
        # The rows go; the headers stay, turned shut, so the shape is still readable.
        assert len(body.query(IssueRow)) == 0
        assert len(_headers(app)) == 3
        assert all(_caret(h) == CARET_SHUT for h in _headers(app))

        # ``R`` on its own is refresh; as the second half of the chord it expands, which
        # it could not do if the binding had eaten the keystroke first.
        await pilot.press("z", "R")
        await pilot.pause()
        assert len(body.query(IssueRow)) == 2
        assert all(_caret(h) == CARET_OPEN for h in _headers(app))


async def test_za_folds_the_group_the_cursor_is_in(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _group_by_status(pilot)
        body = _main(app).query_one(Body)
        # The cursor opens on the first issue, which is in ``todo`` along with the other.
        await pilot.press("z", "a")
        await pilot.pause()
        assert [_caret(h) for h in _headers(app)] == [CARET_SHUT, CARET_OPEN, CARET_OPEN]
        assert len(body.query(IssueRow)) == 0
        # The selection stays on the issue it shut away, so the chord toggles back.
        await pilot.press("z", "a")
        await pilot.pause()
        assert len(body.query(IssueRow)) == 2


async def test_folds_are_remembered_per_grouping_for_the_session(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _group_by_status(pilot)
        await pilot.press("z", "M")
        await pilot.pause()
        body = _main(app).query_one(Body)
        assert len(body.query(IssueRow)) == 0

        await _group_by(pilot, 5)  # Priority — a grouping first reached opens expanded
        assert _main(app).view_group == ("priority",)
        assert len(body.query(IssueRow)) == 2

        await _group_by_status(pilot)  # and back: the same groups are still shut
        assert len(body.query(IssueRow)) == 0


async def _an_epic(engine: Engine) -> str:
    """One epic over the first seeded issue, so grouping by epic heads a group with it."""
    project_api.project_action(engine, "addEpic", {"title": "the parser"}, "tt")
    epic = epic_api.epic_list(engine, "tt")[0]
    first = issue_api.issue_list(engine, "tt")[0]
    issue_api.issue_action(
        engine,
        "edit",
        {
            "title": first.title,
            "body": "",
            "status": first.status,
            "priority": first.priority,
            "due_date": None,
            "epic": epic.ref,
            "milestone": None,
        },
        first.ref,
    )
    return epic.ref


async def test_x_on_an_epic_header_opens_the_epics_menu(seeded: Engine) -> None:
    ref = await _an_epic(seeded)
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _group_by(pilot, 2)  # Epic, two rows under No grouping
        assert _main(app).view_group == ("epic",)
        await pilot.press("k")  # up off the first issue onto the header of its epic
        await pilot.pause()
        assert _main(app).cursor == HeaderAt((ref,), "epic", ref)

        await pilot.press("x")
        await pilot.pause()
        menu = app.screen
        assert isinstance(menu, MenuScreen)
        # What the epic offers, addressed by its ref — not the issue under it.
        targets = {c.run.target for c in menu._commands if isinstance(c.run, RunAction)}
        assert targets == {EpicTarget(ref)}
        assert "Add milestone" in [c.label for c in menu._commands]
        # A live issue is still filed under it, so the delete reads its refusal.
        delete = next(c for c in menu._commands if c.label == "Delete")
        assert delete.reason is not None and "reassign or close" in delete.reason


async def test_the_palette_creates_a_tag_and_a_milestone_under_the_epic_in_focus(
    seeded: Engine,
) -> None:
    ref = await _an_epic(seeded)
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _scope_tt(pilot)
        await _group_by(pilot, 2)
        await pilot.press("k")  # onto the epic's header
        await pilot.pause()
        await pilot.press("colon")
        await pilot.pause()
        menu = app.screen
        assert isinstance(menu, MenuScreen)
        labels = [c.label for c in menu._commands]
        # The project's own create, the milestone against the epic in focus — labelled
        # with which one — and the global tag create.
        assert "Add epic" in labels
        assert f"Add milestone · {ref}" in labels
        assert "Create tag" in labels
        creates = {c.label: c.run.target for c in menu._commands if isinstance(c.run, RunAction)}
        assert creates[f"Add milestone · {ref}"] == EpicTarget(ref)
        assert creates["Create tag"] == TagTarget(None)


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
        selected = _selected(app)
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


# --- filtering ------------------------------------------------------------


def _titles(app: TrackerApp) -> list[str]:
    """The titles of the issue rows on screen, in reading order."""
    return [str(row.query_one(".title").render()) for row in _main(app).query(IssueRow)]


def _chips(app: TrackerApp) -> str:
    """What the chips bar reads, or ``""`` while nothing is filtered and it is hidden."""
    bar = _main(app).query_one(ChipsBar)
    return " ".join(str(chip.render()) for chip in bar.query(".chip")) if bar.display else ""


async def _filter_by(pilot: Pilot[None], facet_down: int, value_down: int) -> None:
    """Filter the way a user does: ``f`` opens the facet menu, the first step is how far
    down that list the facet sits and the second how far down its values the value is."""
    await pilot.press("f")
    await pilot.pause()
    for _ in range(facet_down):
        await pilot.press("down")
    await pilot.press("enter")
    await pilot.pause()
    for _ in range(value_down):
        await pilot.press("down")
    await pilot.press("enter")
    await pilot.pause()


async def test_a_picked_facet_narrows_the_list_and_draws_its_chip(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # Nothing filtered: both rows, and the bar gives its row back to the body.
        assert _titles(app) == ["ship the mvp", "write readme"]
        assert _chips(app) == ""

        await _filter_by(pilot, 1, 1)  # Priority › high, the level under urgent
        assert _main(app).view_filter == Filter(priority="high")
        assert _titles(app) == ["ship the mvp"]
        assert "priority high" in _chips(app)


async def test_chips_and_together_and_come_off_one_at_a_time(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _filter_by(pilot, 1, 1)  # Priority › high — one row survives
        assert _titles(app) == ["ship the mvp"]
        await _filter_by(pilot, 0, 3)  # Status › doing — both seeded issues are todo
        assert _main(app).view_filter == Filter(status="doing", priority="high")
        # The two AND, so nothing is left and the body says the filter is why.
        assert _titles(app) == []
        assert "no issues match the filter" in str(_main(app).query_one(".empty").render())

        # The removal rows sit under the facets, one per chip, in the chips' own order.
        await pilot.press("f")
        await pilot.pause()
        for _ in range(len(FACETS)):
            await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert _main(app).view_filter == Filter(priority="high")
        assert _titles(app) == ["ship the mvp"]

        # And ``F`` drops what is left in one go.
        await pilot.press("F")
        await pilot.pause()
        assert _main(app).view_filter == Filter()
        assert _titles(app) == ["ship the mvp", "write readme"]
        assert _chips(app) == ""


async def test_the_quick_line_is_the_text_facet_and_ands_with_the_rest(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _filter_by(pilot, 1, 1)  # Priority › high

        await pilot.press("slash")
        await pilot.pause()
        for char in "readme":
            await pilot.press(char)
        await pilot.pause()
        # What is typed is the text facet, not a filter beside the others.
        assert _main(app).view_filter == Filter(priority="high", text="readme")
        assert "text readme" in _chips(app)
        # It ANDs: the only readme issue is not the high one.
        assert _titles(app) == []

        # Escaping the quick line drops only its facet; the chip picked from the menu
        # is still in force.
        await pilot.press("escape")
        await pilot.pause()
        assert _main(app).view_filter == Filter(priority="high")
        assert _titles(app) == ["ship the mvp"]


async def test_the_due_facet_is_typed_as_a_date_and_refuses_one_that_does_not_parse(
    seeded: Engine,
) -> None:
    first = issue_api.issue_list(seeded, "tt")[0]
    issue_api.issue_action(seeded, "setDueDate", {"due_date": "2026-08-01"}, first.ref)
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()
        for _ in range(5):  # Due before, under the four facets picked off a list
            await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        # The one facet that is typed rather than picked opens the form pane.
        pane = _main(app).query_one(EditPane)
        assert pane.display
        box = pane.query_one("#field-due_before", Input)

        # A date that does not parse is refused the way an action refuses a payload: the
        # form stays open with the reason inline, and nothing is filtered.
        box.value = "next tuesday"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert pane.display
        assert "not a date" in str(pane.query_one("#edit-error", Static).render())
        assert _main(app).view_filter == Filter()

        box.value = "2026-08-15"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not pane.display
        assert _main(app).view_filter == Filter(due_before=date(2026, 8, 15))
        # An undated issue is never due before a cutoff, so only the dated one survives.
        assert _titles(app) == [first.title]
        assert "due before 2026-08-15" in _chips(app)


async def test_entering_an_epic_header_filters_to_that_epic(seeded: Engine) -> None:
    ref = await _an_epic(seeded)
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _group_by(pilot, 2)  # Epic, two rows under No grouping
        await pilot.press("k")  # up off the first issue onto the header of its epic
        await pilot.pause()
        assert _main(app).cursor == HeaderAt((ref,), "epic", ref)

        await pilot.press("enter")  # entering the epic is applying its facet
        await pilot.pause()
        assert _main(app).view_filter == Filter(epic=ref)
        assert _titles(app) == ["ship the mvp"]
        assert f"epic {ref}" in _chips(app)


async def test_the_palette_enters_what_the_selected_issue_is_filed_under(seeded: Engine) -> None:
    ref = await _an_epic(seeded)
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # The cursor opens on the high-priority issue, the one the epic was put on.
        await pilot.press("colon")
        await pilot.pause()
        menu = app.screen
        assert isinstance(menu, MenuScreen)
        row = next(c for c in menu._commands if c.label == f"Enter epic {ref}")
        assert row.run == Navigate("enter", "epic", ref)

        # Picking it applies the same facet the header does.
        await pilot.press("slash")
        await pilot.pause()
        for char in "epic":
            await pilot.press(char)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert _main(app).view_filter == Filter(epic=ref)
        assert _titles(app) == ["ship the mvp"]


async def _save_current_view(pilot: Pilot[None], name: str) -> None:
    """Save the live state the way a user does: ``v`` opens the overlay, the save row
    sits under whatever is already saved, and the prompt takes the name."""
    await pilot.press("v")
    await pilot.pause()
    menu = pilot.app.screen
    assert isinstance(menu, MenuScreen)
    save_row = next(i for i, c in enumerate(menu._commands) if c.label == SAVE_VIEW)
    for _ in range(save_row):
        await pilot.press("down")
    await pilot.press("enter")
    await pilot.pause()
    for char in name:
        await pilot.press(char if char != " " else "space")
    await pilot.press("enter")
    await pilot.pause()


async def test_v_saves_the_live_state_verbatim(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _scope_tt(pilot)
        await _filter_by(pilot, 1, 1)  # Priority › high
        await _group_by_status(pilot)
        await _save_current_view(pilot, "hot")

        stored = config.load().views
        assert [view.name for view in stored] == ["hot"]
        # The scope, the grouping and the filter as they stood, not as they default.
        assert stored[0].project == "tt"
        assert stored[0].group == ("status",)
        assert stored[0].filter == SavedFilter(priority="high")


async def test_a_saved_view_is_recalled_in_one_keystroke_across_sessions(seeded: Engine) -> None:
    first = _app(seeded)
    async with first.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _scope_tt(pilot)
        await _filter_by(pilot, 1, 1)  # Priority › high
        await _group_by_status(pilot)
        await _save_current_view(pilot, "hot")

    # A fresh app over the same config: the view is on the overlay, and picking it puts
    # the filter, the grouping and the scope back at once.
    second = _app(seeded)
    async with second.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert _main(second).scope == AllScope()
        assert _main(second).view_filter == Filter()

        await pilot.press("v")
        await pilot.pause()
        await pilot.press("enter")  # the saved view is the first row
        await pilot.pause()
        main = _main(second)
        assert main.scope == ProjectScope("tt")
        assert main.view_group == ("status",)
        assert main.view_filter == Filter(priority="high")
        assert _titles(second) == ["ship the mvp"]


async def test_a_recalled_view_spans_every_project(seeded: Engine) -> None:
    project_api.project_action(
        seeded, "createProject", {"slug": "web", "title": "the site", "path": "/repo/web"}
    )
    project_api.project_action(seeded, "addIssue", {"title": "fix the nav"}, "web")
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # Saved from all-projects, so recalling it after scoping onto one spans them
        # again rather than staying pinned.
        await _save_current_view(pilot, "everywhere")
        await _scope_tt(pilot)
        assert _titles(app) == ["ship the mvp", "write readme"]

        await pilot.press("v")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert _main(app).scope == AllScope()
        assert "fix the nav" in _titles(app)


async def test_the_overlay_deletes_a_view(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _save_current_view(pilot, "hot")
        assert [view.name for view in config.load().views] == ["hot"]

        await pilot.press("v")
        await pilot.pause()
        await pilot.press("down")  # past the view itself
        await pilot.press("down")  # past the save row, onto Delete hot
        await pilot.press("enter")
        await pilot.pause()
        assert config.load().views == ()


async def test_saving_over_a_name_replaces_that_view(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await _save_current_view(pilot, "hot")
        await _filter_by(pilot, 1, 1)  # Priority › high
        await _save_current_view(pilot, "hot")
        stored = config.load().views
        # One row under the name, holding what was live the second time.
        assert [view.name for view in stored] == ["hot"]
        assert stored[0].filter == SavedFilter(priority="high")


async def test_comma_switches_theme_and_persists(
    db: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
