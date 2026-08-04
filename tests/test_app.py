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
from textual.widgets import Input, OptionList, Static

from tt.domains.issue import api as issue_api
from tt.domains.project import api as project_api
from tt.frontend.tui.app import TrackerApp
from tt.frontend.tui.screens.form import FormScreen
from tt.frontend.tui.screens.main import MainScreen
from tt.frontend.tui.screens.menu import MenuScreen
from tt.frontend.tui.widgets.body import Card, IssueRow
from tt.frontend.tui.widgets.detail import DetailPane
from tt.platform.config import ThemeName


def _main(app: TrackerApp) -> MainScreen:
    """The current screen, asserted to be the browse screen (no overlay on top)."""
    screen = app.screen
    assert isinstance(screen, MainScreen)
    return screen


def _seed(engine: Engine) -> None:
    """One project ``tt`` with two issues, high before normal — the load order."""
    project_api.project_action(
        engine, "createProject", {"slug": "tt", "title": "task tracker", "path": "/repo/tt"}
    )
    project_api.project_action(
        engine, "addIssue", {"title": "ship the mvp", "priority": "high"}, "tt"
    )
    project_api.project_action(
        engine, "addIssue", {"title": "write readme", "priority": "normal"}, "tt"
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


async def test_capture_writes_a_row(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("]")  # scope onto the tt project so capture has a target
        await pilot.pause()
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


async def test_a_refused_edit_keeps_the_form_open(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("]")
        await pilot.pause()
        await pilot.press("x")  # issue menu
        await pilot.pause()
        await pilot.press("enter")  # pick Edit — the form opens pre-filled
        await pilot.pause()
        assert isinstance(app.screen, FormScreen)
        app.screen.query_one("#field-title", Input).value = ""  # clear the required title
        await pilot.press("enter")  # submit
        await pilot.pause()
        assert isinstance(app.screen, FormScreen), "the form stays up on a refusal"
        error = str(app.screen.query_one("#form-error").render())
        assert "title is required" in error


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
        before = issue_api.issue_get(seeded, selected)
        assert before is not None
        assert before.status == "todo"

        # The accelerator writes the next status at once — no form, unlike the menu's
        # pick-a-status. Three presses walk the whole cycle and wrap back to todo.
        for expected in ["doing", "done", "todo"]:
            await pilot.press("s")
            await pilot.pause()
            assert _main(app).selected_id == selected  # selection is retained
            moved = issue_api.issue_get(seeded, selected)
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
        },
        first.id,
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


async def test_board_only_appears_at_width_90(seeded: Engine) -> None:
    wide = _app(seeded)
    async with wide.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert _main(wide).view_layout == "board"
        assert len(_main(wide).query(Card)) > 0

    narrow = _app(seeded)
    async with narrow.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert _main(narrow).view_layout == "list"


async def test_project_menu_greys_a_refused_delete(seeded: Engine) -> None:
    app = _app(seeded)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("]")  # scope onto tt
        await pilot.pause()
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
