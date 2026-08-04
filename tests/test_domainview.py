"""The framework-free core of the TUI, asserted directly with no terminal.

Everything here is a pure function or a plain dataclass from
``tt.frontend.tui.domainview`` — the glyphs and columns a status reads as, where
the cursor lands after a move, cwd matching, and the command a menu draws from an
offer. No database, no widget, no event loop.
"""

from tt.domains.issue.schemas import IssueListItem
from tt.frontend.tui import domainview as dv
from tt.platform.actions import Offer, Refused, Runnable


def _item(issue_id: int, status: str = "todo", priority: str = "normal") -> IssueListItem:
    return IssueListItem(
        id=issue_id, project="tt", title=f"issue {issue_id}", status=status, priority=priority
    )


# --- columns and selection ------------------------------------------------


def test_columns_are_one_for_list_and_three_by_status_for_board() -> None:
    issues = [_item(1, "todo"), _item(2, "doing"), _item(3, "done"), _item(4, "todo")]
    flat = dv.columns(issues, "list")
    assert len(flat) == 1
    assert [i.id for i in flat[0].issues] == [1, 2, 3, 4]

    board = dv.columns(issues, "board")
    assert [c.status for c in board] == ["todo", "doing", "done"]
    assert [[i.id for i in c.issues] for c in board] == [[1, 4], [2], [3]]


def test_move_selection_walks_the_flat_list() -> None:
    issues = [_item(1), _item(2), _item(3)]
    assert dv.move_selection(issues, "list", 1, 1, 0) == 2
    assert dv.move_selection(issues, "list", 3, 1, 0) == 3  # clamps at the end
    assert dv.move_selection(issues, "list", 2, -dv.FAR, 0) == 1  # g goes to the top
    assert dv.move_selection(issues, "list", 1, dv.FAR, 0) == 3  # G goes to the bottom
    assert dv.move_selection(issues, "list", None, 1, 0) == 1  # nothing selected picks the first


def test_move_selection_moves_within_and_between_board_columns() -> None:
    issues = [_item(1, "todo"), _item(2, "todo"), _item(3, "doing"), _item(4, "done")]
    assert dv.move_selection(issues, "board", 1, 1, 0) == 2  # down within todo
    assert dv.move_selection(issues, "board", 1, 0, 1) == 3  # right to doing
    assert dv.move_selection(issues, "board", 3, 0, 1) == 4  # right to done
    assert dv.move_selection(issues, "board", 1, 0, -1) == 1  # nothing to the left, stays


def test_surviving_id_keeps_the_issue_or_takes_its_neighbour() -> None:
    issues = [_item(2), _item(3), _item(4)]
    assert dv.surviving_id(issues, 3, 1) == 3  # still here
    assert dv.surviving_id(issues, 1, 0) == 2  # gone: whatever now sits where it was
    assert dv.surviving_id(issues, 1, 9) == 4  # clamps the fallback
    assert dv.surviving_id([], 1, 0) is None


def test_index_of_is_zero_when_the_selection_is_gone() -> None:
    issues = [_item(5), _item(6)]
    assert dv.index_of(issues, 6) == 1
    assert dv.index_of(issues, 99) == 0


# --- layout fitting -------------------------------------------------------


def test_fits_and_next_layout() -> None:
    assert dv.fits("list", 40, 10)
    assert not dv.fits("board", 80, 30)
    assert dv.fits("board", 100, 30)
    # tab lands on the widest that fits, skipping board when it doesn't.
    assert dv.next_layout("list", 100, 30) == "board"
    assert dv.next_layout("list", 80, 30) == "list"
    assert dv.next_layout("board", 100, 30) == "list"


# --- cwd resolution -------------------------------------------------------


def test_match_path_takes_the_longest_ancestor() -> None:
    candidates = [("tt", "/repo/tt"), ("web", "/repo/web"), ("unbound", None)]
    assert dv.match_path(candidates, "/repo/tt") == "tt"  # exact
    assert dv.match_path(candidates, "/repo/tt/sub/dir") == "tt"  # inside
    assert dv.match_path(candidates, "/elsewhere") is None  # outside every project
    # The deepest matching path wins, not the first.
    assert dv.match_path([("outer", "/repo"), ("inner", "/repo/tt")], "/repo/tt/x") == "inner"


# --- the visual vocabulary ------------------------------------------------


def test_glyph_marker_and_ref() -> None:
    assert dv.glyph("doing") == "◐"
    assert dv.marker("high") == "▲"
    assert dv.marker("normal") is None
    assert dv.issue_ref("tt", 4) == "TT-4"


# --- an offer becomes a command -------------------------------------------


def test_a_runnable_offer_carries_its_accelerator_and_a_refused_one_its_reason() -> None:
    runnable = Offer(key="delete", label="Delete", state=Runnable(), fields=[])
    refused = Offer(key="delete", label="Delete", state=Refused("archive it first"), fields=[])

    keep = dv.issue_commands([runnable], 1)[0]
    assert keep.reason is None
    assert keep.hint == "d"  # delete is the one remaining accelerator

    drop = dv.project_commands([refused], "tt")[0]
    assert drop.reason == "archive it first"


def test_the_accelerators_name_the_actions_they_run() -> None:
    # ``d`` runs delete straight; ``s`` drives the direct status cycle.
    assert dv.ACCELERATORS == {"d": "delete", "s": "setStatus"}


def test_next_status_advances_one_step_and_wraps() -> None:
    assert dv.next_status("todo") == "doing"
    assert dv.next_status("doing") == "done"
    assert dv.next_status("done") == "todo"  # wraps
    # An unrecognised status starts the cycle at its head.
    assert dv.next_status("archived") == "todo"
