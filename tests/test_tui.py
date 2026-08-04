"""The TUI state machine, driven with no terminal and no pty.

The pure helpers (``columns``, ``fits``, selection resolution, the command a menu
draws from an offer) are asserted directly. The reducer — ``on_key`` then the
``apply`` that may write — is driven against an in-memory database seeded through
the domain ``api``, exactly the two steps ``TrackerApp`` runs per keystroke minus
the terminal. Nothing here names a Textual widget.
"""

import pytest
from sqlalchemy import Engine

from tt.domains.issue import api as issue_api
from tt.domains.issue.schemas import IssueListItem
from tt.domains.project import api as project_api
from tt.frontend import tui
from tt.frontend.tui import (
    ACCELERATORS,
    AllScope,
    CaptureOverlay,
    Choosing,
    CloseOverlay,
    FormOverlay,
    Ignored,
    ListOverlay,
    MoveRow,
    OpenOverlay,
    OverlayMove,
    OverlayPick,
    OverlayType,
    ProjectScope,
    RunAccelerator,
    ShiftProject,
    StartFilter,
    State,
)
from tt.platform.actions import Offer, Refused, Runnable

# --- helpers --------------------------------------------------------------


def _item(issue_id: int, status: str = "todo", priority: str = "normal") -> IssueListItem:
    return IssueListItem(
        id=issue_id, project="tt", title=f"issue {issue_id}", status=status, priority=priority
    )


def _seed(engine: Engine) -> None:
    """One project ``tt`` at ``/repo/tt`` with two issues, high before normal — the
    order a scope loads them in."""
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
def seeded(db: Engine) -> Engine:
    _seed(db)
    return db


def press(engine: Engine, state: State, key: str) -> State:
    """One keystroke: the pure ``on_key`` then the ``apply`` that may write."""
    return tui.apply(engine, state, tui.on_key(state, key))


# --- pure: columns and selection ------------------------------------------


def test_columns_are_one_for_list_and_three_by_status_for_board() -> None:
    issues = [_item(1, "todo"), _item(2, "doing"), _item(3, "done"), _item(4, "todo")]
    flat = tui.columns(issues, "list")
    assert len(flat) == 1
    assert [i.id for i in flat[0].issues] == [1, 2, 3, 4]

    board = tui.columns(issues, "board")
    assert [c.status for c in board] == ["todo", "doing", "done"]
    assert [[i.id for i in c.issues] for c in board] == [[1, 4], [2], [3]]


def test_move_selection_walks_the_flat_list() -> None:
    issues = [_item(1), _item(2), _item(3)]
    assert tui.move_selection(issues, "list", 1, 1, 0) == 2
    assert tui.move_selection(issues, "list", 3, 1, 0) == 3  # clamps at the end
    assert tui.move_selection(issues, "list", 2, -tui.FAR, 0) == 1  # g goes to the top
    assert tui.move_selection(issues, "list", 1, tui.FAR, 0) == 3  # G goes to the bottom
    assert tui.move_selection(issues, "list", None, 1, 0) == 1  # nothing selected picks the first


def test_move_selection_moves_within_and_between_board_columns() -> None:
    issues = [_item(1, "todo"), _item(2, "todo"), _item(3, "doing"), _item(4, "done")]
    assert tui.move_selection(issues, "board", 1, 1, 0) == 2  # down within todo
    assert tui.move_selection(issues, "board", 1, 0, 1) == 3  # right to doing
    assert tui.move_selection(issues, "board", 3, 0, 1) == 4  # right to done
    assert tui.move_selection(issues, "board", 1, 0, -1) == 1  # nothing to the left, stays


def test_surviving_id_keeps_the_issue_or_takes_its_neighbour() -> None:
    issues = [_item(2), _item(3), _item(4)]
    assert tui.surviving_id(issues, 3, 1) == 3  # still here
    assert tui.surviving_id(issues, 1, 0) == 2  # gone: whatever now sits where it was
    assert tui.surviving_id(issues, 1, 9) == 4  # clamps the fallback
    assert tui.surviving_id([], 1, 0) is None


def test_index_of_is_zero_when_the_selection_is_gone() -> None:
    issues = [_item(5), _item(6)]
    assert tui.index_of(issues, 6) == 1
    assert tui.index_of(issues, 99) == 0


# --- pure: layout fitting -------------------------------------------------


def test_fits_and_next_layout() -> None:
    assert tui.fits("list", 40, 10)
    assert not tui.fits("board", 80, 30)
    assert tui.fits("board", 100, 30)
    # tab lands on the widest that fits, skipping board when it doesn't.
    assert tui.next_layout("list", 100, 30) == "board"
    assert tui.next_layout("list", 80, 30) == "list"
    assert tui.next_layout("board", 100, 30) == "list"


# --- pure: cwd resolution -------------------------------------------------


def test_match_path_takes_the_longest_ancestor() -> None:
    candidates = [("tt", "/repo/tt"), ("web", "/repo/web"), ("unbound", None)]
    assert tui.match_path(candidates, "/repo/tt") == "tt"  # exact
    assert tui.match_path(candidates, "/repo/tt/sub/dir") == "tt"  # inside
    assert tui.match_path(candidates, "/elsewhere") is None  # outside every project
    # The deepest matching path wins, not the first.
    assert tui.match_path([("outer", "/repo"), ("inner", "/repo/tt")], "/repo/tt/x") == "inner"


# --- pure: the visual vocabulary ------------------------------------------


def test_glyph_marker_and_ref() -> None:
    assert tui.glyph("doing")[0] == "◐"
    assert tui.marker("high") == "▲"
    assert tui.marker("normal") is None
    assert tui.issue_ref("tt", 4) == "TT-4"


# --- pure: an offer becomes a command -------------------------------------


def test_a_runnable_offer_carries_its_accelerator_and_a_refused_one_its_reason() -> None:
    runnable = Offer(key="editStatus", label="Edit status", state=Runnable(), fields=[])
    refused = Offer(key="delete", label="Delete", state=Refused("archive it first"), fields=[])

    change = tui.issue_commands([runnable], 1)[0]
    assert change.reason is None
    assert change.hint == "s"  # editStatus is the s accelerator

    drop = tui.project_commands([refused], "tt")[0]
    assert drop.reason == "archive it first"


def test_the_accelerators_name_the_actions_they_run() -> None:
    assert ACCELERATORS == {"s": "editStatus", "p": "editPriority", "e": "editTitle", "d": "delete"}


# --- reducer: loading -----------------------------------------------------


def test_start_loads_the_scope_and_selects_the_first_issue(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    assert [i.title for i in state.issues] == ["ship the mvp", "write readme"]
    assert state.selected_id == state.issues[0].id


# --- reducer: keys mean different things in different contexts -------------


def test_a_key_means_a_move_while_browsing_and_a_letter_in_a_menu(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    assert tui.on_key(state, "j") == MoveRow(1)
    assert tui.on_key(state, "x") == OpenOverlay("issue")
    assert tui.on_key(state, "s") == RunAccelerator("editStatus")
    assert tui.on_key(state, "/") == StartFilter()
    assert tui.on_key(state, "]") == ShiftProject(1)
    assert tui.on_key(state, "z") == Ignored()

    menu = press(seeded, state, "x")
    assert isinstance(menu.overlay, ListOverlay)
    assert tui.on_key(menu, "j") == OverlayType("j")  # letters filter the menu
    assert tui.on_key(menu, "down") == OverlayMove(1)
    assert tui.on_key(menu, "enter") == OverlayPick()
    assert tui.on_key(menu, "escape") == CloseOverlay()


def test_the_issue_menu_lists_exactly_what_the_issue_offers(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state = press(seeded, state, "x")
    assert isinstance(state.overlay, ListOverlay)
    labels = [c.label for c in state.overlay.commands]
    assert labels == ["Edit title", "Edit body", "Edit status", "Edit priority", "Delete"]


# --- reducer: writes ------------------------------------------------------


def test_the_status_accelerator_opens_a_picker_and_the_submit_writes(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    issue_id = state.selected_id
    assert issue_id is not None

    state = press(seeded, state, "s")
    assert isinstance(state.overlay, FormOverlay)
    assert state.overlay.key == "editStatus"
    assert state.overlay.entries[0].control == Choosing(values=["todo", "doing", "done"], index=0)

    state = press(seeded, state, "right")  # todo -> doing
    state = press(seeded, state, "enter")  # submit, note left blank
    assert state.overlay is None

    detail = issue_api.issue_get(seeded, issue_id)
    assert detail is not None
    assert detail.status == "doing"


def test_capture_adds_an_issue_to_the_scoped_project(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state = press(seeded, state, "n")
    assert isinstance(state.overlay, CaptureOverlay)
    for char in "triage inbox":
        state = press(seeded, state, char)
    state = press(seeded, state, "enter")
    assert state.overlay is None
    assert any(i.title == "triage inbox" for i in issue_api.issue_list(seeded, "tt"))


def test_a_blank_edit_is_refused_and_leaves_the_form_up(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state = press(seeded, state, "e")  # editTitle
    assert isinstance(state.overlay, FormOverlay)
    state = press(seeded, state, "enter")  # submit with the title still blank
    assert isinstance(state.overlay, FormOverlay), "the form stays up on a refusal"
    assert "title is required" in state.status


def test_the_project_menu_greys_a_refused_action_and_picking_it_does_not_write(
    seeded: Engine,
) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state = press(seeded, state, "X")  # the project menu
    assert isinstance(state.overlay, ListOverlay)
    delete = next(c for c in state.overlay.commands if c.label == "Delete")
    assert delete.reason == "archive it first"  # active projects cannot be deleted

    # Move onto it and pick it: the reason lands in the status, and the project stays.
    index = [c.label for c in state.overlay.commands].index("Delete")
    for _ in range(index):
        state = press(seeded, state, "down")
    state = press(seeded, state, "enter")
    assert "archive it first" in state.status
    assert project_api.project_get(seeded, "tt") is not None


def test_deleting_the_selected_issue_moves_the_cursor_to_a_survivor(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    first = state.selected_id
    assert first is not None
    state = press(seeded, state, "d")  # delete has no fields, so it runs at once
    assert state.overlay is None
    remaining = [i.id for i in state.issues]
    assert first not in remaining
    assert state.selected_id in remaining


# --- reducer: browsing ----------------------------------------------------


def test_filter_narrows_the_visible_issues(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state = press(seeded, state, "/")
    assert state.filtering
    for char in "readme":
        state = press(seeded, state, char)
    assert [i.title for i in tui.visible_issues(state)] == ["write readme"]


def test_tab_cycles_to_the_board_only_when_it_fits(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state.size = (100, 30)
    state = press(seeded, state, "tab")
    assert state.layout == "board"

    state.layout = "list"
    state.size = (80, 30)  # too narrow for three columns
    state = press(seeded, state, "tab")
    assert state.layout == "list"


def test_shift_project_moves_the_scope_to_the_next_project(db: Engine) -> None:
    _seed(db)
    project_api.project_action(db, "createProject", {"slug": "web", "title": "website"})
    state = tui.start(db, ProjectScope("tt"))
    state = press(db, state, "]")
    assert state.scope == ProjectScope("web")


def test_switching_to_a_missing_project_falls_out_to_all_projects(db: Engine) -> None:
    _seed(db)
    state = tui.start(db, ProjectScope("tt"))
    # Archive then delete the project out from under the scope, then refresh.
    project_api.project_action(db, "editStatus", {"status": "archived"}, "tt")
    project_api.project_action(db, "delete", {}, "tt")
    state = press(db, state, "R")
    assert state.scope == AllScope()
