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


def _shape(nodes: list[dv.GroupNode]) -> list[tuple[str, list[int]]]:
    """A grouping as what a reader sees: each header's label against the ids under it."""
    return [(one.key.label, [issue.id for issue in one.issues]) for one in nodes]


def _nested(nodes: list[dv.GroupNode]) -> list[tuple[str, list[tuple[str, list[int]]]]]:
    """A two-level grouping as what a reader sees: each outer header's label against
    the inner shape indented under it."""
    return [(one.key.label, _shape(one.children)) for one in nodes]


def test_group_tree_files_the_issues_under_each_dimension() -> None:
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
        assert _shape(dv.group_tree(issues, (by,), TODAY)) == shape, name


def test_a_group_carries_the_rollup_its_header_draws() -> None:
    issues = [_item(1, "todo", epic="tt-9"), _item(2, "done", epic="tt-9"), _item(3, "todo")]
    epic, unfiled = dv.group_tree(issues, ("epic",))
    assert epic.rollup == dv.rollup([issues[0], issues[1]])
    assert (epic.rollup.done, epic.rollup.total) == (1, 2)
    assert unfiled.key.value is None  # the trailing group is the one with no value
    assert (unfiled.rollup.done, unfiled.rollup.total) == (0, 1)


def test_a_milestone_group_carries_the_epic_it_sits_under() -> None:
    # The rollup row draws a second, related field beside the identity; for a
    # milestone that is the epic it belongs to.
    issues = [_item(1, milestone="tt-5", epic="tt-4")]
    milestone = dv.group_tree(issues, ("milestone",))[0]
    assert (milestone.key.value, milestone.key.related) == ("tt-5", "tt-4")


# --- the nested tree ------------------------------------------------------


def _epic_milestone() -> list[IssueListItem]:
    return [
        _item(1, "done", epic="tt-9", milestone="tt-5"),
        _item(2, "todo", epic="tt-9", milestone="tt-5"),
        _item(3, "todo", epic="tt-9"),
        _item(4, "doing", epic="tt-4", milestone="tt-7"),
        _item(5, "todo"),
    ]


def test_group_tree_nests_the_second_dimension_inside_the_first() -> None:
    nodes = dv.group_tree(_epic_milestone(), ("epic", "milestone"))
    assert _nested(nodes) == [
        ("tt-9", [("tt-5", [1, 2]), ("(no milestone)", [3])]),
        ("tt-4", [("tt-7", [4])]),
        # An issue filed under neither still heads a group at both levels, so it is
        # reachable rather than dropping out of the body.
        ("(no epic)", [("(no milestone)", [5])]),
    ]


def test_every_level_of_the_tree_carries_its_own_rollup() -> None:
    issues = _epic_milestone()
    epic, _, _ = dv.group_tree(issues, ("epic", "milestone"))
    # The outer node stands for everything beneath it, each inner node only for its own.
    assert (epic.rollup.done, epic.rollup.total) == (1, 3)
    milestone, unfiled = epic.children
    assert (milestone.rollup.done, milestone.rollup.total) == (1, 2)
    assert (unfiled.rollup.done, unfiled.rollup.total) == (0, 1)
    # A branch holds children, a leaf holds issues — never both.
    assert epic.issues == []
    assert milestone.children == []


def test_group_tree_nests_two_levels_and_no_more() -> None:
    issues = [_item(1, epic="tt-9", milestone="tt-5", priority="high")]
    nodes = dv.group_tree(issues, ("epic", "milestone", "priority"))
    assert _nested(nodes) == [("tt-9", [("tt-5", [1])])]
    assert dv.MAX_LEVELS == 2


def test_a_level_naming_no_dimension_drops_out_of_the_tree() -> None:
    issues = [_item(1, epic="tt-9")]
    # A second level of ``none`` is one level of grouping, and no level at all is the
    # flat list: one group holding everything, with no label and so no header.
    assert _shape(dv.group_tree(issues, ("epic", "none"))) == [("tt-9", [1])]
    assert _shape(dv.group_tree(issues, ("none",))) == [("", [1])]
    assert _shape(dv.group_tree(issues, ())) == [("", [1])]


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
    nodes = dv.group_tree(issues, ("status",))
    assert [one.key.value for one in nodes] == list(dv.BOARD_STATUSES)
    assert [[i.id for i in one.issues] for one in nodes] == [[1, 4], [2], [3]]


# --- folding --------------------------------------------------------------


def test_fold_paths_address_every_node_of_the_tree() -> None:
    nodes = dv.group_tree(_epic_milestone(), ("epic", "milestone"))
    assert dv.fold_paths(nodes) == [
        ("tt-9",),
        ("tt-9", "tt-5"),
        ("tt-9", None),  # the milestone-less group, addressed by the value it lacks
        ("tt-4",),
        ("tt-4", "tt-7"),
        (None,),
        (None, None),
    ]


def test_the_flat_list_has_no_header_to_fold_by() -> None:
    # The one group of the ungrouped list draws no header, so there is no caret on it
    # and ``zM`` would have nothing to shut.
    nodes = dv.group_tree([_item(1), _item(2)], ("none",))
    assert dv.fold_paths(nodes) == []
    assert dv.fold_all(nodes) == dv.EXPANDED
    assert dv.fold_target(nodes, 1) is None


def test_folding_a_node_takes_everything_under_it_off_the_page() -> None:
    nodes = dv.group_tree(_epic_milestone(), ("epic", "milestone"))
    assert [i.id for i in dv.visible_issues(nodes, dv.EXPANDED)] == [1, 2, 3, 4, 5]
    # An inner node hides its own issues; the outer one hides its whole subtree.
    inner = frozenset({("tt-9", "tt-5")})
    assert [i.id for i in dv.visible_issues(nodes, inner)] == [3, 4, 5]
    outer = frozenset({("tt-9",)})
    assert [i.id for i in dv.visible_issues(nodes, outer)] == [4, 5]
    # Whatever is folded, the tree still holds every issue — the headers stay drawn.
    assert [i.id for i in dv.all_issues(nodes)] == [1, 2, 3, 4, 5]
    assert dv.visible_issues(nodes, dv.fold_all(nodes)) == []


def test_toggle_fold_shuts_an_open_node_and_opens_a_shut_one() -> None:
    nodes = dv.group_tree(_epic_milestone(), ("epic", "milestone"))
    shut = dv.toggle_fold(dv.EXPANDED, ("tt-9", "tt-5"))
    assert shut == frozenset({("tt-9", "tt-5")})
    assert dv.toggle_fold(shut, ("tt-9", "tt-5")) == dv.EXPANDED
    # Fold-all shuts every node; expanding is the empty state it started from.
    everything = dv.fold_all(nodes)
    assert everything == frozenset(dv.fold_paths(nodes))
    assert dv.toggle_fold(everything, ("tt-4",)) == everything - {("tt-4",)}


def test_fold_target_is_the_innermost_node_holding_the_selection() -> None:
    nodes = dv.group_tree(_epic_milestone(), ("epic", "milestone"))
    assert dv.fold_target(nodes, 1) == ("tt-9", "tt-5")
    assert dv.fold_target(nodes, 3) == ("tt-9", None)
    assert dv.fold_target(nodes, 5) == (None, None)
    assert dv.fold_target(nodes, 99) is None  # nothing selected, nothing to fold
    # One dimension is one level deep, so the target is the group itself.
    assert dv.fold_target(dv.group_tree(_epic_milestone(), ("epic",)), 1) == ("tt-9",)


def test_folded_reads_the_state_a_header_draws_its_caret_from() -> None:
    nodes = dv.group_tree(_epic_milestone(), ("epic",))
    epic = nodes[0]
    path = dv.path_of((), epic)
    assert path == ("tt-9",)
    assert not dv.folded(epic, path, dv.EXPANDED)
    assert dv.folded(epic, path, frozenset({path}))


# --- selection ------------------------------------------------------------


def _flat(issues: list[IssueListItem]) -> list[dv.GroupNode]:
    return dv.group_tree(issues, ("none",))


def test_move_selection_walks_the_flat_list() -> None:
    nodes = _flat([_item(1), _item(2), _item(3)])
    assert dv.move_selection(nodes, "stacked", dv.EXPANDED, 1, 1, 0) == 2
    assert dv.move_selection(nodes, "stacked", dv.EXPANDED, 3, 1, 0) == 3  # clamps at the end
    assert dv.move_selection(nodes, "stacked", dv.EXPANDED, 2, -dv.FAR, 0) == 1  # < to the top
    assert dv.move_selection(nodes, "stacked", dv.EXPANDED, 1, dv.FAR, 0) == 3  # G to the bottom
    # Nothing selected picks the first issue.
    assert dv.move_selection(nodes, "stacked", dv.EXPANDED, None, 1, 0) == 1
    assert dv.move_selection(_flat([]), "stacked", dv.EXPANDED, None, 1, 0) is None


def test_move_selection_crosses_group_boundaries_when_stacked() -> None:
    # Stacked, the groups are one run of issues: a row move walks straight over a
    # header into the next section rather than stopping at it.
    nodes = dv.group_tree([_item(1, "todo"), _item(2, "doing"), _item(3, "done")], ("status",))
    assert dv.move_selection(nodes, "stacked", dv.EXPANDED, 1, 1, 0) == 2  # todo into doing
    assert dv.move_selection(nodes, "stacked", dv.EXPANDED, 3, -1, 0) == 2  # and back up over it
    # A column move has nowhere to go in a stack, so the selection stays.
    assert dv.move_selection(nodes, "stacked", dv.EXPANDED, 1, 0, 1) == 1


def test_move_selection_steps_over_a_folded_subtree() -> None:
    nodes = dv.group_tree(_epic_milestone(), ("epic", "milestone"))
    shut = frozenset({("tt-9", "tt-5")})  # hides issues 1 and 2
    # Down out of the group before the folded one lands past it, not inside it.
    assert dv.move_selection(nodes, "stacked", shut, 3, -1, 0) == 3  # nothing above it now
    assert dv.move_selection(nodes, "stacked", shut, 4, -1, 0) == 3
    assert dv.move_selection(nodes, "stacked", shut, 3, 1, 0) == 4
    # The whole epic shut, and the walk skips it from either side.
    epic_shut = frozenset({("tt-9",)})
    assert dv.move_selection(nodes, "stacked", epic_shut, 4, -1, 0) == 4
    assert dv.move_selection(nodes, "stacked", epic_shut, 4, 1, 0) == 5


def test_a_selection_folded_out_of_sight_moves_to_the_edge_of_what_hid_it() -> None:
    # ``za`` leaves the cursor on the issue it shut away, so toggling back restores it.
    # Until then a move lands on the row after the hidden run, or the row before it.
    nodes = dv.group_tree(_epic_milestone(), ("epic", "milestone"))
    shut = frozenset({("tt-9", "tt-5")})
    assert dv.move_selection(nodes, "stacked", shut, 1, 1, 0) == 3  # first row after
    assert dv.move_selection(nodes, "stacked", shut, 2, -1, 0) == 3  # nothing before it
    epic_shut = frozenset({("tt-9",)})
    assert dv.move_selection(nodes, "stacked", epic_shut, 1, 1, 0) == 4
    assert dv.move_selection(nodes, "stacked", epic_shut, 4, -1, 0) == 4
    # Everything shut leaves no row to land on, so the selection waits where it is.
    assert dv.move_selection(nodes, "stacked", dv.fold_all(nodes), 1, 1, 0) == 1


def test_move_selection_moves_within_and_between_columns() -> None:
    issues = [_item(1, "todo"), _item(2, "todo"), _item(3, "doing"), _item(4, "done")]
    nodes = dv.group_tree(issues, ("status",))
    assert dv.move_selection(nodes, "columns", dv.EXPANDED, 1, 1, 0) == 2  # down within todo
    assert dv.move_selection(nodes, "columns", dv.EXPANDED, 2, 1, 0) == 2  # clamps at the end
    assert dv.move_selection(nodes, "columns", dv.EXPANDED, 1, 0, 1) == 3  # right to doing
    assert dv.move_selection(nodes, "columns", dv.EXPANDED, 3, 0, 1) == 4  # right to done
    assert dv.move_selection(nodes, "columns", dv.EXPANDED, 1, 0, -1) == 1  # nothing left, stays


def test_move_selection_skips_an_empty_column() -> None:
    # Grouping by status always draws ``doing``; with nothing in it, moving right from
    # todo lands in done rather than on an empty column.
    nodes = dv.group_tree([_item(1, "todo"), _item(2, "done")], ("status",))
    assert dv.move_selection(nodes, "columns", dv.EXPANDED, 1, 0, 1) == 2
    # A folded column is an empty one to the cursor, so it is crossed the same way.
    shut = frozenset({("doing",)})
    nodes = dv.group_tree([_item(1, "todo"), _item(2, "doing"), _item(3, "done")], ("status",))
    assert dv.move_selection(nodes, "columns", shut, 1, 0, 1) == 3


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
    assert dv.fits_columns(("status",), 3, 100, 30)  # the board's three
    assert not dv.fits_columns(("status",), 3, 80, 30)  # too narrow for three
    assert not dv.fits_columns(("status",), 3, 100, 8)  # too short for cards under headers
    assert not dv.fits_columns(("status",), 1, 100, 30)  # one group is not a fan
    assert not dv.fits_columns(("status",), 12, 100, 30)  # a dimension with too many values
    # A nested grouping is a tree, and a tree only reads down the page.
    assert not dv.fits_columns(("epic", "status"), 3, 100, 30)


def test_next_render_toggles_out_and_back_when_there_is_room() -> None:
    assert dv.next_render("stacked", ("status",), 3, 100, 30) == "columns"
    assert dv.next_render("columns", ("status",), 3, 100, 30) == "stacked"
    # No room to fan: the toggle leaves the stack where it is.
    assert dv.next_render("stacked", ("status",), 3, 80, 30) == "stacked"
    # Neither does a second dimension, however much width there is.
    assert dv.next_render("stacked", ("epic", "status"), 3, 100, 30) == "stacked"


def test_the_saved_layout_is_the_status_board_and_nothing_else() -> None:
    # The preferences file speaks list and board, so the round trip carries only the
    # one view that vocabulary can name.
    assert dv.saved_layout(("status",), "columns") == "board"
    assert dv.saved_layout(("status",), "stacked") == "list"
    assert dv.saved_layout(("epic",), "columns") == "list"
    assert dv.saved_layout(("status", "epic"), "columns") == "list"
    assert dv.opening_view("board") == (("status",), "columns")
    assert dv.opening_view("list") == (("none",), "stacked")


def test_a_grouping_drops_the_levels_that_name_no_dimension() -> None:
    assert dv.grouping("epic") == ("epic",)
    assert dv.grouping("epic", "milestone") == ("epic", "milestone")
    assert dv.grouping("epic", "none") == ("epic",)
    # Nothing to group by is the flat list, with no inside for a second level to fill.
    assert dv.grouping("none") == ("none",)
    assert dv.grouping("none", "milestone") == ("none",)


def test_the_grouping_label_names_both_levels_outermost_first() -> None:
    assert dv.grouping_label(("none",)) == "flat"
    assert dv.grouping_label(("epic",)) == "by epic"
    assert dv.grouping_label(("epic", "milestone")) == "by epic › milestone"


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
    rows = dv.group_commands(("epic", "milestone"))
    assert [row.run for row in rows] == [dv.Navigate("group", by) for by in dv.GROUP_BYS]
    # The first step picks the outer level, so that is the one it marks.
    marked = [row.label for row in rows if row.label.endswith("✓")]
    assert marked == ["Epic  ✓"]
    # Steering the body is not a write, so no row carries a refusal or an accelerator.
    assert all(row.reason is None and row.hint is None for row in rows)


def test_the_second_step_offers_what_can_nest_inside_the_first() -> None:
    rows = dv.nest_commands("epic", ("epic", "milestone"))
    # No dimension nests inside itself, and the row that leaves the grouping one level
    # deep heads the list.
    assert [row.run for row in rows] == [
        dv.Navigate("nest", by) for by in dv.GROUP_BYS if by != "epic"
    ]
    assert rows[0].label == dv.NO_SECOND_LEVEL
    assert [row.label for row in rows if row.label.endswith("✓")] == ["Milestone  ✓"]


def test_the_second_step_marks_nothing_under_a_freshly_picked_first_level() -> None:
    # Re-picking the outer level is a fresh choice: the inner level of the grouping
    # being replaced is not what nests under this one.
    rows = dv.nest_commands("tag", ("epic", "milestone"))
    assert [row.label for row in rows if row.label.endswith("✓")] == []
    # With one dimension in force there is no second level, so that row is the marked one.
    rows = dv.nest_commands("epic", ("epic",))
    assert rows[0].label == f"{dv.NO_SECOND_LEVEL}  ✓"


def test_next_status_advances_one_step_and_wraps() -> None:
    assert dv.next_status("todo") == "doing"
    assert dv.next_status("doing") == "done"
    assert dv.next_status("done") == "todo"  # wraps
    # An unrecognised status starts the cycle at its head.
    assert dv.next_status("archived") == "todo"
