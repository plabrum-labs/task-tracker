"""The framework-free core of the TUI, asserted directly with no terminal.

Everything here is a pure function or a plain dataclass from
``tt.frontend.tui.domainview`` — the glyphs a status reads as, the groups a
dimension files issues under, where the cursor lands after a move, cwd matching,
and the command a menu draws from an offer. No database, no widget, no event loop.
"""

import os
from datetime import UTC, date, datetime

from tt.domains.comment.schemas import CommentView
from tt.domains.issue.schemas import IssueListItem
from tt.frontend.tui import domainview as dv
from tt.platform.actions import Offer, Refused, Runnable


def _item(
    issue_id: int,
    status: str = "todo",
    priority: str = "medium",
    waiting: bool = False,
    *,
    epic: str | None = None,
    milestone: str | None = None,
    tags: list[str] | None = None,
    due_date: date | None = None,
) -> IssueListItem:
    return IssueListItem(
        id=issue_id,
        ref=f"tt-{issue_id}",
        project="tt",
        title=f"issue {issue_id}",
        status=status,
        priority=priority,
        due_date=due_date,
        epic=epic,
        milestone=milestone,
        tags=tags if tags is not None else [],
        waiting=waiting,
    )


def _comment(comment_id: int) -> CommentView:
    written = datetime(2026, 8, comment_id, tzinfo=UTC)
    return CommentView(
        id=comment_id, body=f"comment {comment_id}", created_at=written, updated_at=written
    )


# --- grouping -------------------------------------------------------------

TODAY = date(2026, 8, 8)


def _shape(groups: list[dv.Group]) -> list[tuple[str, list[int]]]:
    """A grouping as what a reader sees: each header's label against the ids under it."""
    return [(one.key.label, [issue.id for issue in one.issues]) for one in groups]


def test_group_files_the_issues_under_each_dimension() -> None:
    cases: list[tuple[str, dv.GroupBy, list[IssueListItem], list[tuple[str, list[int]]]]] = [
        (
            "none is one group holding everything, and it has no header to draw",
            "none",
            [_item(1), _item(2)],
            [("", [1, 2])],
        ),
        (
            "status keeps the board's three, empty or not",
            "status",
            [_item(1, "todo"), _item(2, "done")],
            [("todo", [1]), ("doing", []), ("done", [2])],
        ),
        (
            "a status off the board only appears when something sits at it",
            "status",
            [_item(1, "backlog"), _item(2, "todo")],
            [("backlog", [1]), ("todo", [2]), ("doing", []), ("done", [])],
        ),
        (
            "epic, in the order the epics first appear, unfiled issues trailing",
            "epic",
            [_item(1, epic="tt-9"), _item(2), _item(3, epic="tt-4"), _item(4, epic="tt-9")],
            [("tt-9", [1, 4]), ("tt-4", [3]), ("(no epic)", [2])],
        ),
        (
            "milestone",
            "milestone",
            [_item(1, milestone="tt-5", epic="tt-4"), _item(2, epic="tt-4")],
            [("tt-5", [1]), ("(no milestone)", [2])],
        ),
        (
            "tag, with an issue wearing two filed under both",
            "tag",
            [_item(1, tags=["api", "ui"]), _item(2, tags=["ui"]), _item(3)],
            [("#api", [1]), ("#ui", [1, 2]), ("(untagged)", [3])],
        ),
        (
            "priority, highest level first",
            "priority",
            [_item(1, priority="low"), _item(2, priority="urgent"), _item(3, priority="low")],
            [("urgent", [2]), ("low", [1, 3])],
        ),
        (
            "due, bucketed against the day being read",
            "due",
            [
                _item(1, due_date=date(2026, 8, 1)),
                _item(2, due_date=date(2026, 8, 8)),
                _item(3, due_date=date(2026, 8, 12)),
                _item(4, due_date=date(2026, 9, 30)),
                _item(5),
            ],
            [
                ("overdue", [1]),
                ("today", [2]),
                ("this week", [3]),
                ("later", [4]),
                ("(no due date)", [5]),
            ],
        ),
    ]
    for name, by, issues, shape in cases:
        assert _shape(dv.group(issues, by, TODAY)) == shape, name


def test_a_group_carries_the_rollup_its_header_draws() -> None:
    issues = [_item(1, "todo", epic="tt-9"), _item(2, "done", epic="tt-9"), _item(3, "todo")]
    epic, unfiled = dv.group(issues, "epic")
    assert epic.rollup == dv.rollup([issues[0], issues[1]])
    assert (epic.rollup.done, epic.rollup.total) == (1, 2)
    assert unfiled.key.value is None  # the trailing group is the one with no value
    assert (unfiled.rollup.done, unfiled.rollup.total) == (0, 1)


def test_a_milestone_group_carries_the_epic_it_sits_under() -> None:
    # The rollup row draws a second, related field beside the identity; for a
    # milestone that is the epic it belongs to.
    issues = [_item(1, milestone="tt-5", epic="tt-4")]
    milestone = dv.group(issues, "milestone")[0]
    assert (milestone.key.value, milestone.key.related) == ("tt-5", "tt-4")


def test_due_buckets_split_at_today_and_a_week_out() -> None:
    assert dv.due_bucket(date(2026, 8, 7), TODAY) == "overdue"
    assert dv.due_bucket(TODAY, TODAY) == "today"
    assert dv.due_bucket(date(2026, 8, 9), TODAY) == "this week"
    assert dv.due_bucket(date(2026, 8, 15), TODAY) == "this week"  # exactly a week out
    assert dv.due_bucket(date(2026, 8, 16), TODAY) == "later"  # one day past it


def test_grouping_by_status_reproduces_the_board_columns() -> None:
    # The board is nothing but the status groups: the same three columns, in the same
    # order, holding the same issues.
    issues = [_item(1, "todo"), _item(2, "doing"), _item(3, "done"), _item(4, "todo")]
    groups = dv.group(issues, "status")
    assert [one.key.value for one in groups] == list(dv.BOARD_STATUSES)
    assert [[i.id for i in one.issues] for one in groups] == [[1, 4], [2], [3]]


# --- selection ------------------------------------------------------------


def _flat(issues: list[IssueListItem]) -> list[dv.Group]:
    return dv.group(issues, "none")


def test_move_selection_walks_the_flat_list() -> None:
    groups = _flat([_item(1), _item(2), _item(3)])
    assert dv.move_selection(groups, "stacked", 1, 1, 0) == 2
    assert dv.move_selection(groups, "stacked", 3, 1, 0) == 3  # clamps at the end
    assert dv.move_selection(groups, "stacked", 2, -dv.FAR, 0) == 1  # < goes to the top
    assert dv.move_selection(groups, "stacked", 1, dv.FAR, 0) == 3  # G goes to the bottom
    # Nothing selected picks the first issue.
    assert dv.move_selection(groups, "stacked", None, 1, 0) == 1
    assert dv.move_selection(_flat([]), "stacked", None, 1, 0) is None


def test_move_selection_crosses_group_boundaries_when_stacked() -> None:
    # Stacked, the groups are one run of issues: a row move walks straight over a
    # header into the next section rather than stopping at it.
    groups = dv.group([_item(1, "todo"), _item(2, "doing"), _item(3, "done")], "status")
    assert dv.move_selection(groups, "stacked", 1, 1, 0) == 2  # last of todo into doing
    assert dv.move_selection(groups, "stacked", 3, -1, 0) == 2  # and back up over it
    # A column move has nowhere to go in a stack, so the selection stays.
    assert dv.move_selection(groups, "stacked", 1, 0, 1) == 1


def test_move_selection_moves_within_and_between_columns() -> None:
    issues = [_item(1, "todo"), _item(2, "todo"), _item(3, "doing"), _item(4, "done")]
    groups = dv.group(issues, "status")
    assert dv.move_selection(groups, "columns", 1, 1, 0) == 2  # down within todo
    assert dv.move_selection(groups, "columns", 2, 1, 0) == 2  # clamps at the column's end
    assert dv.move_selection(groups, "columns", 1, 0, 1) == 3  # right to doing
    assert dv.move_selection(groups, "columns", 3, 0, 1) == 4  # right to done
    assert dv.move_selection(groups, "columns", 1, 0, -1) == 1  # nothing to the left, stays


def test_move_selection_skips_an_empty_column() -> None:
    # Grouping by status always draws ``doing``; with nothing in it, moving right from
    # todo lands in done rather than on an empty column.
    groups = dv.group([_item(1, "todo"), _item(2, "done")], "status")
    assert dv.move_selection(groups, "columns", 1, 0, 1) == 2


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


# --- the comment thread's cursor ------------------------------------------


def test_move_comment_walks_the_thread_and_clamps_at_both_ends() -> None:
    comments = [_comment(1), _comment(2), _comment(3)]
    assert dv.move_comment(comments, 1, 1) == 2
    assert dv.move_comment(comments, 2, -1) == 1
    assert dv.move_comment(comments, 3, 1) == 3  # clamps at the end
    assert dv.move_comment(comments, 1, -1) == 1  # clamps at the start
    # Nothing selected takes the first comment, whichever way the cursor moved.
    assert dv.move_comment(comments, None, 1) == 1
    assert dv.move_comment(comments, None, -1) == 1


def test_move_comment_has_nothing_to_select_in_an_empty_thread() -> None:
    assert dv.move_comment([], None, 1) is None
    assert dv.move_comment([], 7, -1) is None


def test_the_comment_cursor_lands_on_the_survivor_after_a_delete() -> None:
    # The same reconciliation the issue list uses, over the thread: the deleted
    # comment's neighbour takes the cursor rather than it jumping to the top.
    remaining = [_comment(1), _comment(3), _comment(4)]
    assert dv.surviving_id(remaining, 3, 1) == 3  # still here
    assert dv.surviving_id(remaining, 2, 1) == 3  # gone: whatever now sits where it was
    assert dv.surviving_id([], 2, 0) is None  # the last comment deleted leaves none
    assert dv.index_of([_comment(1), _comment(2)], 2) == 1


# --- the rollup -----------------------------------------------------------


def test_rollup_counts_each_status_and_the_done_share() -> None:
    cases: list[tuple[str, list[IssueListItem], dict[str, int], int, int]] = [
        ("empty", [], {}, 0, 0),
        ("single status", [_item(1, "todo"), _item(2, "todo")], {"todo": 2}, 0, 2),
        ("all done", [_item(1, "done"), _item(2, "done")], {"done": 2}, 2, 2),
        (
            "mixed",
            [_item(1, "todo"), _item(2, "doing"), _item(3, "done"), _item(4, "todo")],
            {"todo": 2, "doing": 1, "done": 1},
            1,
            4,
        ),
        (
            "the statuses off the board count too",
            [_item(1, "backlog"), _item(2, "requires_planning"), _item(3, "done")],
            {"backlog": 1, "requires_planning": 1, "done": 1},
            1,
            3,
        ),
    ]
    for name, issues, counts, done, total in cases:
        standing = dv.rollup(issues)
        assert standing.counts == counts, name
        assert (standing.done, standing.total) == (done, total), name


def test_a_rollup_breakdown_reads_in_workflow_order() -> None:
    # However the issues arrive, the counts run backlog to done — the order the group
    # header and the detail pane's summary line both draw them in.
    issues = [_item(1, "done"), _item(2, "todo"), _item(3, "backlog"), _item(4, "doing")]
    assert list(dv.rollup(issues).counts) == ["backlog", "todo", "doing", "done"]


# --- the render toggle ----------------------------------------------------


def test_fits_columns_wants_width_for_every_group() -> None:
    assert dv.fits_columns(3, 100, 30)  # the board's three
    assert not dv.fits_columns(3, 80, 30)  # too narrow for three
    assert not dv.fits_columns(3, 100, 8)  # too short for cards under the headers
    assert not dv.fits_columns(1, 100, 30)  # one group is not a fan
    assert not dv.fits_columns(12, 100, 30)  # a dimension with too many values


def test_next_render_toggles_out_and_back_when_there_is_room() -> None:
    assert dv.next_render("stacked", 3, 100, 30) == "columns"
    assert dv.next_render("columns", 3, 100, 30) == "stacked"
    # No room to fan: the toggle leaves the stack where it is.
    assert dv.next_render("stacked", 3, 80, 30) == "stacked"


def test_the_saved_layout_is_the_status_board_and_nothing_else() -> None:
    # The preferences file speaks list and board, so the round trip carries only the
    # one view that vocabulary can name.
    assert dv.saved_layout("status", "columns") == "board"
    assert dv.saved_layout("status", "stacked") == "list"
    assert dv.saved_layout("epic", "columns") == "list"
    assert dv.opening_view("board") == ("status", "columns")
    assert dv.opening_view("list") == ("none", "stacked")


def test_pane_split_stacks_below_when_thin() -> None:
    assert dv.pane_split(dv.DETAIL_BESIDE_MIN_WIDTH + 40) == "beside"  # comfortably wide
    assert dv.pane_split(dv.DETAIL_BESIDE_MIN_WIDTH) == "beside"  # exactly wide enough
    assert dv.pane_split(dv.DETAIL_BESIDE_MIN_WIDTH - 1) == "below"  # one short stacks
    assert dv.pane_split(dv.DETAIL_BESIDE_MIN_WIDTH // 2) == "below"  # clearly too thin


# --- cwd resolution -------------------------------------------------------


def test_match_path_takes_the_longest_ancestor() -> None:
    candidates = [("tt", "/repo/tt"), ("web", "/repo/web"), ("unbound", None)]
    assert dv.match_path(candidates, "/repo/tt") == "tt"  # exact
    assert dv.match_path(candidates, "/repo/tt/sub/dir") == "tt"  # inside
    assert dv.match_path(candidates, "/elsewhere") is None  # outside every project
    # The deepest matching path wins, not the first.
    assert dv.match_path([("outer", "/repo"), ("inner", "/repo/tt")], "/repo/tt/x") == "inner"


def test_match_path_expands_a_stored_tilde() -> None:
    home = os.path.expanduser("~")
    # A path stored with a literal ``~`` still matches the expanded working directory.
    assert dv.match_path([("tt", "~/repos/tt")], f"{home}/repos/tt") == "tt"
    assert dv.match_path([("tt", "~/repos/tt")], f"{home}/repos/tt/sub") == "tt"


# --- the visual vocabulary ------------------------------------------------


def test_glyph_and_marker() -> None:
    assert dv.glyph("doing") == "◐"
    high = dv.priority_mark("high")
    assert high is not None and high.glyph == "▲"
    urgent = dv.priority_mark("urgent")
    assert urgent is not None and urgent.var == "$priority-urgent"
    # The quiet middle and an unset priority draw nothing.
    assert dv.priority_mark("medium") is None
    assert dv.priority_mark("none") is None


def test_waiting_mark_flags_only_a_waiting_issue() -> None:
    assert dv.waiting_mark(True) == dv.WAITING_GLYPH
    assert dv.waiting_mark(False) is None


def test_status_var_names_the_theme_variable_and_falls_back_to_muted() -> None:
    assert dv.status_var("doing") == "$warning"
    assert dv.status_var("done") == "$success"
    assert dv.status_var("archived") == "$text-muted"  # unrecognised status


# --- an offer becomes a command -------------------------------------------


def test_a_runnable_offer_carries_its_accelerator_and_a_refused_one_its_reason() -> None:
    runnable = Offer(key="delete", label="Delete", state=Runnable(), fields=[])
    refused = Offer(key="delete", label="Delete", state=Refused("archive it first"), fields=[])

    keep = dv.issue_commands([runnable], "tt-1")[0]
    assert keep.reason is None
    assert keep.hint == "d"  # delete is the one remaining accelerator

    drop = dv.project_commands([refused], "tt")[0]
    assert drop.reason == "archive it first"


def test_the_accelerators_name_the_actions_they_run() -> None:
    # ``d`` runs delete straight; ``e`` opens the edit form; ``s`` drives the direct
    # status cycle.
    assert dv.ACCELERATORS == {"d": "delete", "e": "edit", "s": "setStatus"}


def test_comment_commands_address_the_comment_and_reuse_the_accelerators() -> None:
    offers = [
        Offer(key="edit", label="Edit", state=Runnable(), fields=[], seed=True),
        Offer(key="delete", label="Delete", state=Runnable(), fields=[]),
    ]
    detail = {"id": 7, "body": "the parser drops trailing commas"}
    edit, delete = dv.comment_commands(offers, 7, detail)

    # Every command addresses the comment by id, so ``dispatch`` routes it to the
    # comment api rather than to the issue the thread hangs off.
    assert isinstance(edit.run, dv.RunAction) and edit.run.target == dv.CommentTarget(7)
    assert isinstance(delete.run, dv.RunAction) and delete.run.target == dv.CommentTarget(7)
    assert (edit.hint, delete.hint) == ("e", "d")  # the map already covers both
    # The edit seeds from its target, so its form opens on the comment's body; the
    # fieldless delete carries none.
    assert edit.run.seed == detail
    assert delete.run.seed is None


def test_a_refused_comment_offer_carries_its_reason_rather_than_running() -> None:
    refused = Offer(key="delete", label="Delete", state=Refused("the issue is archived"), fields=[])
    assert dv.comment_commands([refused], 7)[0].reason == "the issue is archived"


def test_the_edit_offer_carries_the_e_accelerator() -> None:
    edit = Offer(key="edit", label="Edit", state=Runnable(), fields=[])
    assert dv.issue_commands([edit], "tt-1")[0].hint == "e"


def test_the_group_picker_offers_every_dimension_and_marks_the_one_in_force() -> None:
    rows = dv.group_commands("epic")
    assert [row.run for row in rows] == [dv.Navigate("group", by) for by in dv.GROUP_BYS]
    marked = [row.label for row in rows if row.label.endswith("✓")]
    assert marked == ["Epic  ✓"]
    # Steering the body is not a write, so no row carries a refusal or an accelerator.
    assert all(row.reason is None and row.hint is None for row in rows)


def test_next_status_advances_one_step_and_wraps() -> None:
    assert dv.next_status("todo") == "doing"
    assert dv.next_status("doing") == "done"
    assert dv.next_status("done") == "todo"  # wraps
    # An unrecognised status starts the cycle at its head.
    assert dv.next_status("archived") == "todo"
