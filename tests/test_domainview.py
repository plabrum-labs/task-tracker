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
from tt.platform.config import SavedFilter, SavedView


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
        project="TT",
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
            [_item(1, epic="auth"), _item(2), _item(3, epic="billing"), _item(4, epic="auth")],
            [("auth", [1, 4]), ("billing", [3]), ("(no epic)", [2])],
        ),
        (
            "milestone",
            "milestone",
            [_item(1, milestone="beta", epic="billing"), _item(2, epic="billing")],
            [("beta", [1]), ("(no milestone)", [2])],
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
    issues = [_item(1, "todo", epic="auth"), _item(2, "done", epic="auth"), _item(3, "todo")]
    epic, unfiled = dv.group_tree(issues, ("epic",))
    assert epic.rollup == dv.rollup([issues[0], issues[1]])
    assert (epic.rollup.done, epic.rollup.total) == (1, 2)
    assert unfiled.key.value is None  # the trailing group is the one with no value
    assert (unfiled.rollup.done, unfiled.rollup.total) == (0, 1)


def test_a_milestone_group_carries_the_epic_it_sits_under() -> None:
    # The rollup row draws a second, related field beside the identity; for a
    # milestone that is the epic it belongs to.
    issues = [_item(1, milestone="beta", epic="billing")]
    milestone = dv.group_tree(issues, ("milestone",))[0]
    assert (milestone.key.value, milestone.key.related) == ("beta", "billing")


# --- the nested tree ------------------------------------------------------


def _epic_milestone() -> list[IssueListItem]:
    return [
        _item(1, "done", epic="auth", milestone="beta"),
        _item(2, "todo", epic="auth", milestone="beta"),
        _item(3, "todo", epic="auth"),
        _item(4, "doing", epic="billing", milestone="ga"),
        _item(5, "todo"),
    ]


def _header(*path: str) -> dv.HeaderAt:
    """The cursor on a header of the epic › milestone tree, addressed by the values
    down to it: one level in is an epic's header, two a milestone's. Both dimensions
    name a per-project object, so the header carries the project its issues sit in."""
    return dv.HeaderAt(path, "epic" if len(path) == 1 else "milestone", path[-1], "TT")


def test_group_tree_nests_the_second_dimension_inside_the_first() -> None:
    nodes = dv.group_tree(_epic_milestone(), ("epic", "milestone"))
    assert _nested(nodes) == [
        ("auth", [("beta", [1, 2]), ("(no milestone)", [3])]),
        ("billing", [("ga", [4])]),
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
    issues = [_item(1, epic="auth", milestone="beta", priority="high")]
    nodes = dv.group_tree(issues, ("epic", "milestone", "priority"))
    assert _nested(nodes) == [("auth", [("beta", [1])])]
    assert dv.MAX_LEVELS == 2


def test_a_level_naming_no_dimension_drops_out_of_the_tree() -> None:
    issues = [_item(1, epic="auth")]
    # A second level of ``none`` is one level of grouping, and no level at all is the
    # flat list: one group holding everything, with no label and so no header.
    assert _shape(dv.group_tree(issues, ("epic", "none"))) == [("auth", [1])]
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


# --- filtering ------------------------------------------------------------


def _fleet() -> list[IssueListItem]:
    """One issue per shape the facets narrow on, so a facet case can name the ids it
    keeps and the ids it drops."""
    return [
        _item(1, "todo", "high", epic="auth", milestone="beta", tags=["api"], due_date=TODAY),
        _item(2, "doing", "low", epic="auth", tags=["api", "ui"], due_date=date(2026, 9, 1)),
        _item(3, "done", "high", epic="billing", milestone="ga", tags=["ui"]),
        _item(4),
    ]


def test_apply_narrows_on_each_facet() -> None:
    cases: list[tuple[str, dv.Filter, list[int]]] = [
        ("the empty filter is the identity", dv.NO_FILTER, [1, 2, 3, 4]),
        ("status", dv.Filter(status="doing"), [2]),
        ("priority", dv.Filter(priority="high"), [1, 3]),
        ("epic", dv.Filter(epic="auth"), [1, 2]),
        ("milestone", dv.Filter(milestone="ga"), [3]),
        ("tag, which an issue wears any number of", dv.Filter(tag="api"), [1, 2]),
        ("text, folded over the title", dv.Filter(text="ISSUE 3"), [3]),
        (
            "due before a cutoff, an undated issue never being due before one",
            dv.Filter(due_before=date(2026, 8, 20)),
            [1],
        ),
        ("a facet nothing carries admits nothing", dv.Filter(tag="parser"), []),
    ]
    for name, current, kept in cases:
        assert [issue.id for issue in dv.apply(_fleet(), current)] == kept, name


def test_apply_ands_its_facets_together() -> None:
    issues = _fleet()
    # Each facet alone keeps more than the two together do, and the order is the order
    # the issues arrived in.
    assert [i.id for i in dv.apply(issues, dv.Filter(epic="auth"))] == [1, 2]
    assert [i.id for i in dv.apply(issues, dv.Filter(priority="high"))] == [1, 3]
    assert [i.id for i in dv.apply(issues, dv.Filter(epic="auth", priority="high"))] == [1]
    # A third facet that agrees with neither takes the intersection to nothing.
    both = dv.Filter(epic="auth", priority="high", status="done")
    assert dv.apply(issues, both) == []


def test_a_facet_is_set_replaced_and_dropped() -> None:
    one = dv.with_facet(dv.NO_FILTER, "status", "doing")
    assert one.status == "doing"
    # A facet holds one value, so picking it again replaces rather than widens.
    assert dv.with_facet(one, "status", "done").status == "done"
    # Another facet joins it rather than displacing it.
    two = dv.with_facet(one, "tag", "api")
    assert (two.status, two.tag) == ("doing", "api")
    assert dv.without_facet(two, "status") == dv.Filter(tag="api")
    assert dv.without_facet(dv.without_facet(two, "status"), "tag") == dv.NO_FILTER
    # The due facet holds a date, so it is set and dropped on its own.
    cutoff = dv.with_due(dv.NO_FILTER, TODAY)
    assert cutoff.due_before == TODAY
    assert dv.with_due(cutoff, None) == dv.NO_FILTER
    # A blank text facet is no facet at all, which is what escaping the quick line does.
    assert dv.without_facet(dv.with_facet(dv.NO_FILTER, "text", "mvp"), "text") == dv.NO_FILTER


def test_chips_draw_the_facets_in_force_and_name_what_removes_each() -> None:
    assert dv.chips(dv.NO_FILTER) == []
    current = dv.Filter(status="doing", tag="api", due_before=TODAY, text="mvp")
    assert dv.chips(current) == [
        dv.Chip("status", "status doing"),
        dv.Chip("tag", "tag api"),
        dv.Chip("due", "due before 2026-08-08"),
        dv.Chip("text", "text mvp"),
    ]
    # Removing one chip leaves the rest standing.
    left = dv.without_facet(current, "tag")
    assert [chip.facet for chip in dv.chips(left)] == ["status", "due", "text"]


def test_facet_values_are_the_closed_lists_and_what_the_issues_carry() -> None:
    issues = _fleet()
    # Status and priority are vocabularies, so every level is offered whatever the
    # loaded issues use.
    assert dv.facet_values(issues, "status") == list(dv.ROLLUP_STATUSES)
    assert dv.facet_values(issues, "priority") == list(dv.PRIORITY_ORDER)
    # An epic, a milestone and a tag are objects, so only what is actually filed under
    # one is offered, sorted and without repeats.
    assert dv.facet_values(issues, "epic") == ["auth", "billing"]
    assert dv.facet_values(issues, "milestone") == ["beta", "ga"]
    assert dv.facet_values(issues, "tag") == ["api", "ui"]
    # Text is typed, not picked.
    assert dv.facet_values(issues, "text") == []


def test_entering_a_group_is_the_facet_it_stands_for() -> None:
    # The dimensions whose headers stand for a real object are the facets they filter to.
    assert [dv.drill_facet(by) for by in ("epic", "milestone", "tag")] == [
        "epic",
        "milestone",
        "tag",
    ]
    # A dimension standing for no object enters nothing.
    assert [dv.drill_facet(by) for by in ("none", "status", "priority", "due")] == [None] * 4


def test_the_filter_menu_marks_what_is_in_force_and_offers_to_remove_it() -> None:
    # Nothing filtered: the facets, and no removals to make.
    rows = dv.filter_commands(dv.NO_FILTER)
    assert [row.label for row in rows] == [dv.FACET_LABELS[facet] for facet in dv.FACETS]

    current = dv.Filter(status="doing", tag="api")
    rows = dv.filter_commands(current)
    marked = [row.label for row in rows if row.label.endswith("✓")]
    assert marked == ["Status  ✓", "Tag  ✓"]
    assert [row.label for row in rows[len(dv.FACETS) :]] == [
        "Remove status doing",
        "Remove tag api",
        dv.CLEAR_FILTERS,
    ]
    # Each removal row carries the facet it drops, so picking one is unambiguous.
    assert [row.run for row in rows[len(dv.FACETS) : -1]] == [
        dv.Navigate("unfacet", "status"),
        dv.Navigate("unfacet", "tag"),
    ]


def test_a_facets_value_rows_mark_the_one_in_force() -> None:
    rows = dv.facet_commands("status", ["todo", "doing"], dv.Filter(status="doing"))
    assert [row.label for row in rows] == ["todo", "doing  ✓"]
    assert [row.run for row in rows] == [
        dv.Navigate("facetValue", "todo"),
        dv.Navigate("facetValue", "doing"),
    ]


def test_drill_rows_name_the_facet_and_the_value_together() -> None:
    issue = _item(1, epic="auth", milestone="beta", tags=["api", "ui"])
    assert dv.drill_commands(issue) == [
        dv.Command("Enter epic auth", None, None, dv.Navigate("enter", "epic", "auth")),
        dv.Command("Enter milestone beta", None, None, dv.Navigate("enter", "milestone", "beta")),
        dv.Command("Enter tag api", None, None, dv.Navigate("enter", "tag", "api")),
        dv.Command("Enter tag ui", None, None, dv.Navigate("enter", "tag", "ui")),
    ]
    # An issue filed under nothing has nothing to enter.
    assert dv.drill_commands(_item(2)) == []


def test_filtering_composes_with_grouping() -> None:
    # Filter first, then group: the tree is built over what survives, so a group that
    # the filter emptied is not drawn at all.
    issues = _fleet()
    narrowed = dv.apply(issues, dv.Filter(epic="auth"))
    assert _shape(dv.group_tree(narrowed, ("tag",))) == [("#api", [1, 2]), ("#ui", [2])]


# --- saved views ----------------------------------------------------------


def test_a_view_round_trips_through_the_shape_the_file_stores() -> None:
    spanning = dv.View(
        name="bugs everywhere",
        scope=dv.AllScope(),
        by=("tag", "status"),
        filter=dv.Filter(tag="bug", due_before=date(2026, 8, 15), text="parser"),
    )
    assert dv.view_of(dv.saved_view(spanning)) == spanning
    # And the same for a view that pins a project and narrows on nothing.
    pinned = dv.View(name="tt", scope=dv.ProjectScope("tt"), by=("none",))
    assert dv.view_of(dv.saved_view(pinned)) == pinned


def test_a_spanning_view_stores_no_project_and_a_pinned_one_its_slug() -> None:
    spanning = dv.saved_view(dv.View("everywhere", dv.AllScope(), ("none",)))
    assert spanning.project is None
    pinned = dv.saved_view(dv.View("here", dv.ProjectScope("tt"), ("none",)))
    assert pinned.project == "tt"


def test_a_stored_view_drops_what_it_cannot_name() -> None:
    stored = SavedView(
        name="hand edited",
        group=("spreadsheet", "status", "epic", "tag"),
        filter=SavedFilter(due_before="next tuesday"),
    )
    view = dv.view_of(stored)
    # The level naming no dimension drops out, and two levels is the cap.
    assert view.by == ("status", "epic")
    assert view.filter.due_before is None
    assert view.scope == dv.AllScope()


def test_a_stored_view_naming_no_dimension_is_the_flat_list() -> None:
    assert dv.view_of(SavedView(name="flat")).by == ("none",)
    assert dv.view_of(SavedView(name="flat", group=("none",))).by == ("none",)


def test_the_view_overlay_recalls_saves_and_deletes() -> None:
    views = [dv.View("mine", dv.ProjectScope("tt"), ("status",))]
    rows = dv.view_commands(views)
    # The view's own row recalls it, and says where it lands.
    assert rows[0].run == dv.Navigate("view", "mine")
    assert rows[0].label == "mine   tt · by status"
    # Then the save row, then the deletes — what takes a view away sits under the rest.
    assert rows[1].label == dv.SAVE_VIEW and rows[1].run == dv.Navigate("saveView")
    assert rows[2].label == "Delete mine" and rows[2].run == dv.Navigate("deleteView", "mine")


def test_a_spanning_views_row_says_so() -> None:
    row = dv.view_commands([dv.View("hot", dv.AllScope(), ("none",))])[0]
    assert row.label == f"hot   {dv.EVERYWHERE} · flat"


def test_the_view_overlay_of_an_empty_file_only_offers_the_save_row() -> None:
    assert [row.run for row in dv.view_commands([])] == [dv.Navigate("saveView")]


# --- folding --------------------------------------------------------------


def test_fold_paths_address_every_node_of_the_tree() -> None:
    nodes = dv.group_tree(_epic_milestone(), ("epic", "milestone"))
    assert dv.fold_paths(nodes) == [
        ("auth",),
        ("auth", "beta"),
        ("auth", None),  # the milestone-less group, addressed by the value it lacks
        ("billing",),
        ("billing", "ga"),
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
    inner = frozenset({("auth", "beta")})
    assert [i.id for i in dv.visible_issues(nodes, inner)] == [3, 4, 5]
    outer = frozenset({("auth",)})
    assert [i.id for i in dv.visible_issues(nodes, outer)] == [4, 5]
    # Whatever is folded, the tree still holds every issue — the headers stay drawn.
    assert [i.id for i in dv.all_issues(nodes)] == [1, 2, 3, 4, 5]
    assert dv.visible_issues(nodes, dv.fold_all(nodes)) == []


def test_toggle_fold_shuts_an_open_node_and_opens_a_shut_one() -> None:
    nodes = dv.group_tree(_epic_milestone(), ("epic", "milestone"))
    shut = dv.toggle_fold(dv.EXPANDED, ("auth", "beta"))
    assert shut == frozenset({("auth", "beta")})
    assert dv.toggle_fold(shut, ("auth", "beta")) == dv.EXPANDED
    # Fold-all shuts every node; expanding is the empty state it started from.
    everything = dv.fold_all(nodes)
    assert everything == frozenset(dv.fold_paths(nodes))
    assert dv.toggle_fold(everything, ("billing",)) == everything - {("billing",)}


def test_fold_target_is_the_innermost_node_holding_the_selection() -> None:
    nodes = dv.group_tree(_epic_milestone(), ("epic", "milestone"))
    assert dv.fold_target(nodes, 1) == ("auth", "beta")
    assert dv.fold_target(nodes, 3) == ("auth", None)
    assert dv.fold_target(nodes, 5) == (None, None)
    assert dv.fold_target(nodes, 99) is None  # nothing selected, nothing to fold
    # One dimension is one level deep, so the target is the group itself.
    assert dv.fold_target(dv.group_tree(_epic_milestone(), ("epic",)), 1) == ("auth",)


def test_folded_reads_the_state_a_header_draws_its_caret_from() -> None:
    nodes = dv.group_tree(_epic_milestone(), ("epic",))
    epic = nodes[0]
    path = dv.path_of((), epic)
    assert path == ("auth",)
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
    shut = frozenset({("auth", "beta")})  # hides issues 1 and 2
    # Down out of the group before the folded one lands past it, not inside it. The
    # epic and milestone headers stand for objects, so they are stops of their own and
    # the walk crosses them on the way.
    assert dv.move_selection(nodes, "stacked", shut, 3, -1, 0) == _header("auth", "beta")
    assert dv.move_selection(nodes, "stacked", shut, 4, -1, 0) == _header("billing", "ga")
    assert dv.move_selection(nodes, "stacked", shut, 3, 1, 0) == _header("billing")
    # The whole epic shut, and the walk skips what it holds from either side.
    epic_shut = frozenset({("auth",)})
    assert dv.move_selection(nodes, "stacked", epic_shut, 4, -1, 0) == _header("billing", "ga")
    assert dv.move_selection(nodes, "stacked", epic_shut, 4, 1, 0) == 5


def test_a_selection_folded_out_of_sight_moves_to_the_edge_of_what_hid_it() -> None:
    # ``za`` leaves the cursor on the issue it shut away, so toggling back restores it.
    # Until then a move lands on the stop after the hidden run, or the one before it.
    nodes = dv.group_tree(_epic_milestone(), ("epic", "milestone"))
    shut = frozenset({("auth", "beta")})
    assert dv.move_selection(nodes, "stacked", shut, 1, 1, 0) == 3  # first row after
    # Nothing of the epic's is left before it but the header that shut it away.
    assert dv.move_selection(nodes, "stacked", shut, 2, -1, 0) == _header("auth", "beta")
    epic_shut = frozenset({("auth",)})
    assert dv.move_selection(nodes, "stacked", epic_shut, 1, 1, 0) == _header("billing")
    assert dv.move_selection(nodes, "stacked", epic_shut, 4, -1, 0) == _header("billing", "ga")
    # Everything shut leaves only the headers, and an epic's is a stop, so the cursor
    # lands on the one after the run it was hidden in.
    assert dv.move_selection(nodes, "stacked", dv.fold_all(nodes), 1, 1, 0) == _header("billing")
    # Where no header stands for an object there is nothing left to land on, and the
    # selection waits where it is until a node is opened again.
    statuses = dv.group_tree([_item(1, "todo"), _item(2, "done")], ("status",))
    assert dv.move_selection(statuses, "stacked", dv.fold_all(statuses), 1, 1, 0) == 1


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


def test_the_cursor_stops_on_the_headers_that_stand_for_an_object() -> None:
    # An epic, a milestone and a tag are rows something can be done to, so the cursor
    # lands on their headers; the group of issues carrying none of them is not one.
    epics = dv.group_tree(_epic_milestone(), ("epic", "milestone"))
    assert dv.visible_stops(epics, dv.EXPANDED) == [
        _header("auth"),
        _header("auth", "beta"),
        1,
        2,
        3,  # under "(no milestone)", whose header stands for nothing
        _header("billing"),
        _header("billing", "ga"),
        4,
        5,  # under "(no epic)" › "(no milestone)", neither of them a stop
    ]
    tags = dv.group_tree([_item(1, tags=["api"]), _item(2)], ("tag",))
    assert dv.visible_stops(tags, dv.EXPANDED) == [dv.HeaderAt(("api",), "tag", "api"), 1, 2]


def test_the_cursor_steps_over_a_header_that_stands_for_no_object() -> None:
    # A status, a priority and a due bucket are ways of reading the list, not rows, so
    # their headers are display only and the walk crosses them as it always did.
    for by in ("status", "priority", "due"):
        nodes = dv.group_tree(
            [_item(1, "todo", "high", due_date=TODAY), _item(2, "done", "low")],
            (dv.group_by(by) or "none",),
            TODAY,
        )
        assert dv.visible_stops(nodes, dv.EXPANDED) == [1, 2], by
    # And the flat list has no header at all to stop on.
    assert dv.visible_stops(_flat([_item(1), _item(2)]), dv.EXPANDED) == [1, 2]


def test_move_selection_walks_onto_and_off_an_object_header() -> None:
    nodes = dv.group_tree(_epic_milestone(), ("epic",))
    # Nothing selected takes the first stop, which is the first epic's header.
    assert dv.move_selection(nodes, "stacked", dv.EXPANDED, None, 1, 0) == _header("auth")
    # Down off the header onto the first issue it heads, and back up onto it.
    assert dv.move_selection(nodes, "stacked", dv.EXPANDED, _header("auth"), 1, 0) == 1
    assert dv.move_selection(nodes, "stacked", dv.EXPANDED, 1, -1, 0) == _header("auth")
    # A folded header keeps its stop: it is still drawn, so the cursor still reaches it.
    shut = frozenset({("auth",)})
    assert dv.move_selection(nodes, "stacked", shut, _header("auth"), 1, 0) == _header("billing")


def test_fold_target_is_the_node_whose_header_the_cursor_is_on() -> None:
    nodes = dv.group_tree(_epic_milestone(), ("epic", "milestone"))
    assert dv.fold_target(nodes, _header("auth")) == ("auth",)
    assert dv.fold_target(nodes, _header("auth", "beta")) == ("auth", "beta")


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
        (
            "canceled shows in the breakdown but sits outside the progress total",
            [_item(1, "done"), _item(2, "canceled"), _item(3, "todo")],
            {"todo": 1, "done": 1, "canceled": 1},
            1,
            2,
        ),
    ]
    for name, issues, counts, done, total in cases:
        standing = dv.rollup(issues)
        assert standing.counts == counts, name
        assert (standing.done, standing.total) == (done, total), name


def test_a_rollup_breakdown_reads_in_workflow_order() -> None:
    # However the issues arrive, the counts run backlog to done — the order the group
    # header and the detail pane's summary line both draw them in.
    issues = [
        _item(1, "done"),
        _item(2, "todo"),
        _item(3, "backlog"),
        _item(4, "doing"),
        _item(5, "canceled"),
    ]
    assert list(dv.rollup(issues).counts) == ["backlog", "todo", "doing", "done", "canceled"]


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


def test_the_rail_stands_only_in_the_ultra_wide_band() -> None:
    assert dv.shows_rail(dv.RAIL_MIN_WIDTH + 40)  # comfortably wide
    assert dv.shows_rail(dv.RAIL_MIN_WIDTH)  # exactly wide enough
    assert not dv.shows_rail(dv.RAIL_MIN_WIDTH - 1)  # one short collapses it
    # Wide enough for the detail pane beside the list is still not wide enough for a
    # third column: the rail's band sits above the pane's.
    assert not dv.shows_rail(dv.DETAIL_BESIDE_MIN_WIDTH)


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


def test_the_commands_of_a_header_address_the_object_it_stands_for() -> None:
    # Each builder addresses its object the way its own api takes one: an epic and a
    # milestone by a title within a project, a tag by the id behind the name issues wear.
    edit = Offer(key="edit", label="Edit", state=Runnable(), fields=[], seed=True)
    delete = Offer(key="delete", label="Delete", state=Runnable(), fields=[])
    rename = Offer(key="rename", label="Rename", state=Runnable(), fields=[], seed=True)

    epic_detail = {"project": "TT", "title": "the parser"}
    epic_edit, epic_delete = dv.epic_commands([edit, delete], "TT", "auth", epic_detail)
    assert isinstance(epic_edit.run, dv.RunAction)
    assert epic_edit.run.target == dv.EpicTarget("TT", "auth")
    assert epic_edit.run.seed == epic_detail  # seeds from the epic it opens on
    assert isinstance(epic_delete.run, dv.RunAction) and epic_delete.run.seed is None
    assert (epic_edit.hint, epic_delete.hint) == ("e", "d")  # the map already covers both

    milestone = dv.milestone_commands([edit], "TT", "beta", {"project": "TT", "title": "beta"})[0]
    assert isinstance(milestone.run, dv.RunAction)
    assert milestone.run.target == dv.MilestoneTarget("TT", "beta")

    tag = dv.tag_commands([rename], 7, {"id": 7, "name": "api"})[0]
    assert isinstance(tag.run, dv.RunAction) and tag.run.target == dv.TagTarget(7)
    assert tag.run.seed == {"id": 7, "name": "api"}
    # The top-level create addresses no tag at all.
    create = Offer(key="createTag", label="Create tag", state=Runnable(), fields=[])
    top = dv.tag_commands([create], None)[0]
    assert isinstance(top.run, dv.RunAction) and top.run.target == dv.TagTarget(None)


def test_a_refused_header_offer_carries_its_reason_rather_than_running() -> None:
    refused = Offer(
        key="delete", label="Delete", state=Refused("reassign 2 issues first"), fields=[]
    )
    assert dv.epic_commands([refused], "TT", "auth")[0].reason == "reassign 2 issues first"
    assert dv.milestone_commands([refused], "TT", "beta")[0].reason == "reassign 2 issues first"


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
