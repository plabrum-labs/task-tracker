"""Framework-free helpers over the domain — the pure core of the TUI.

Everything here is a pure function or a plain dataclass; nothing imports textual,
so it is driven directly in tests with no terminal. The screens in the package
above turn these values into widgets — the glyphs and colours a status reads as,
the tree one or two dimensions file issues under and which of its nodes are folded
shut, where the cursor lands after a move, and the commands a menu draws from an
object's offers. This module never touches a widget or the database.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal, Protocol

from tt.domains.issue.enums import Priority, Status
from tt.domains.issue.schemas import IssueListItem
from tt.domains.project.schemas import ProjectListItem
from tt.platform.actions import (
    Field,
    Offer,
    Refused,
)
from tt.platform.config import Layout

# --- the visual vocabulary ------------------------------------------------

STATUS_GLYPH = {"todo": "○", "doing": "◐", "done": "●"}

# The theme variable a status's glyph is drawn in, for the places that mix the
# glyph colour into a single markup string (the board header, the detail pane)
# rather than carrying it in a CSS component class. The flat row uses ``st-*``.
STATUS_VAR = {"todo": "$text-muted", "doing": "$warning", "done": "$success"}

# The status columns of the board, in the order they read left to right.
BOARD_STATUSES = ("todo", "doing", "done")


def next_status(current: str) -> str:
    """The status one step forward around the board cycle, wrapping ``done`` back to
    ``todo`` — the move the ``s`` accelerator writes. An unrecognised status starts
    the cycle at its head."""
    index = BOARD_STATUSES.index(current) if current in BOARD_STATUSES else -1
    return BOARD_STATUSES[(index + 1) % len(BOARD_STATUSES)]


HALF = 8  # rows a half-page jump moves; the terminal height is not pure.
FAR = 10**6  # a move large enough to reach either end after clamping.


def glyph(status: str) -> str:
    """The character a status draws as; its colour comes from the palette."""
    return STATUS_GLYPH.get(status, "·")


def status_var(status: str) -> str:
    """The theme variable a status's glyph is drawn in when its colour is mixed
    into one markup string; the muted colour for an unrecognised status."""
    return STATUS_VAR.get(status, "$text-muted")


@dataclass(frozen=True)
class PriorityMark:
    """How a priority draws in the margins: a glyph and the theme variable it takes
    its colour from. Only the levels that stand out carry one — the quiet middle
    (``medium``, the default) and an unset ``none`` show nothing."""

    glyph: str
    var: str


# Levels above and below the quiet middle get a mark; ``medium`` and ``none`` do
# not, so the list stays calm and only the exceptions draw the eye.
PRIORITY_MARK = {
    "urgent": PriorityMark("⇈", "$priority-urgent"),
    "high": PriorityMark("▲", "$priority-high"),
    "low": PriorityMark("▽", "$priority-low"),
}


def priority_mark(priority: str) -> PriorityMark | None:
    """The marker a priority draws, or ``None`` when the level shows nothing."""
    return PRIORITY_MARK.get(priority)


# The glyph a waiting issue carries in the margins, in the warning colour — an issue
# held back by an unfinished dependency is flagged the way a priority mark flags an
# exception, and the common case of nothing outstanding shows nothing.
WAITING_GLYPH = "⊘"
WAITING_VAR = "$warning"


def waiting_mark(waiting: bool) -> str | None:
    """The margin glyph a waiting issue draws, or ``None`` when nothing holds it."""
    return WAITING_GLYPH if waiting else None


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


# --- scope and render -----------------------------------------------------


@dataclass(frozen=True)
class ProjectScope:
    """The issues of one project, addressed by its slug."""

    slug: str


@dataclass(frozen=True)
class AllScope:
    """Every live project's issues at once."""


type Scope = ProjectScope | AllScope

type Split = Literal["beside", "below"]

# The list and detail pane split the row evenly; under this each half is too
# cramped to read, so the pane stacks below the list rather than to its right.
DETAIL_BESIDE_MIN_WIDTH = 120


def pane_split(width: int) -> Split:
    """How the detail pane sits against the list: ``beside`` it when the column is
    wide enough for both, stacked ``below`` it when the column is too thin."""
    return "beside" if width >= DETAIL_BESIDE_MIN_WIDTH else "below"


# How the body draws its groups: one under the next down the page, or fanned across
# the width as columns — the board is the status groups drawn that second way.
type GroupRender = Literal["stacked", "columns"]

# A column narrower than this cannot hold a card's ref and title, and under this
# height there is no room for cards beneath the headers.
COLUMN_MIN_WIDTH = 30
COLUMNS_MIN_HEIGHT = 10


def fits_columns(by: Grouping, count: int, width: int, height: int) -> bool:
    """Whether a grouping's ``count`` groups can fan across the terminal as columns.
    A single group is not a fan — one group is what the flat list already is — and a
    nested grouping is a tree, which only reads down the page."""
    return (
        len(by) == 1
        and count > 1
        and width >= COLUMN_MIN_WIDTH * count
        and height >= COLUMNS_MIN_HEIGHT
    )


def next_render(
    render: GroupRender, by: Grouping, count: int, width: int, height: int
) -> GroupRender:
    """The render the toggle lands on: back to stacked from columns, and out to
    columns when this many groups fit across the width — stacked when they do not."""
    if render == "columns":
        return "stacked"
    return "columns" if fits_columns(by, count, width, height) else "stacked"


# --- the rollup -----------------------------------------------------------

# The order a breakdown reads in: the workflow order ``Status`` declares, so the
# counts run backlog to done however the issues arrived.
ROLLUP_STATUSES: tuple[str, ...] = tuple(status.value for status in Status)


@dataclass(frozen=True)
class Rollup:
    """How a set of issues stands: how many sit at each status it uses, and how many
    of the total are done. It carries no identity — who the issues belong to is the
    caller's to say — so the detail pane's epic summary and a group header are the
    same value drawn twice."""

    counts: Mapping[str, int]
    done: int
    total: int


def rollup(issues: Sequence[IssueListItem]) -> Rollup:
    """The standing of a set of issues: a count per status that appears in it, in
    workflow order, and the done-of-total a progress bar draws from. Statuses nobody
    is at are left out rather than counted as zero."""
    tally = Counter(issue.status for issue in issues)
    done = tally[Status.DONE]
    ordered = {status: tally.pop(status) for status in ROLLUP_STATUSES if status in tally}
    # Anything left in the tally is a status outside the workflow order; it trails the
    # known ones rather than vanishing from the breakdown.
    return Rollup(counts=ordered | tally, done=done, total=len(issues))


# --- grouping -------------------------------------------------------------

# The dimension the body files issues under. ``none`` is the flat list: one group
# holding everything, which is why it needs no header.
type GroupBy = Literal["none", "status", "epic", "milestone", "tag", "priority", "due"]

# The dimensions in force, outermost first: one dimension is a flat run of groups, two
# nest the second inside the first, and ``("none",)`` is the ungrouped list.
type Grouping = tuple[GroupBy, ...]

# Two dimensions is the cap. A third level has no indent left to sit at in a list this
# dense, and the rollup at each level stops meaning anything that far in.
MAX_LEVELS = 2

# What the picker writes between the two dimensions of a nested grouping.
LEVEL_SEPARATOR = "›"

# The dimensions the picker offers, in the order it lists them.
GROUP_BYS: tuple[GroupBy, ...] = (
    "none",
    "status",
    "epic",
    "milestone",
    "tag",
    "priority",
    "due",
)

# What each dimension reads as in the picker.
GROUP_LABELS: Mapping[GroupBy, str] = {
    "none": "No grouping",
    "status": "Status",
    "epic": "Epic",
    "milestone": "Milestone",
    "tag": "Tag",
    "priority": "Priority",
    "due": "Due date",
}


def group_by(name: object) -> GroupBy | None:
    """The dimension a name picks out, or ``None`` when it names none. A picked row
    steers with its argument as a plain string, so this is what turns one back into a
    dimension."""
    for by in GROUP_BYS:
        if by == name:
            return by
    return None


def grouping(first: GroupBy, second: GroupBy = "none") -> Grouping:
    """The dimensions a pick puts in force, with the levels naming no dimension
    dropped: nothing to group by is the flat list, and a second level only nests when
    there is a first for it to nest under."""
    if first == "none":
        return ("none",)
    return (first,) if second == "none" else (first, second)


def grouping_label(by: Grouping) -> str:
    """What a grouping reads as in the header: the flat list is a shape rather than a
    dimension, and a nested one names both levels, outermost first."""
    levels = [level for level in by if level != "none"]
    if not levels:
        return "flat"
    return "by " + f" {LEVEL_SEPARATOR} ".join(levels)


# The levels of a priority grouping, highest first — the order the list itself sorts
# in, so grouping by priority does not reverse what the eye already reads top down.
PRIORITY_ORDER: tuple[str, ...] = tuple(
    priority.name.lower() for priority in sorted(Priority, reverse=True)
)

# How far ahead "this week" reaches, in days.
DUE_WEEK = 7

# The due buckets in the order they read: what is late, what is now, what is coming,
# and what is far enough off to ignore.
DUE_BUCKETS: tuple[str, ...] = ("overdue", "today", "this week", "later")


@dataclass(frozen=True)
class GroupKey:
    """What a group is: the dimension's ``value`` (``None`` for the trailing group of
    issues carrying none), the ``label`` its header reads, and the second field it is
    filed under when the dimension has one — a milestone's epic."""

    value: str | None
    label: str
    related: str | None = None


@dataclass(frozen=True)
class GroupNode:
    """One node of the body's tree: what it is, how the issues beneath it stand, and
    what hangs off it — child nodes when a second dimension nests inside this one, the
    issues themselves when none does. A node carries one or the other, never both. The
    rollup is computed at every level and is what that level's header draws, so a node
    is complete without the widget needing to count anything itself."""

    key: GroupKey
    rollup: Rollup
    children: list[GroupNode] = field(default_factory=list)
    issues: list[IssueListItem] = field(default_factory=list)


type _Bucket = tuple[GroupKey, list[IssueListItem]]


def _by_value(
    issues: Sequence[IssueListItem], value_of: Callable[[IssueListItem], str | None]
) -> tuple[dict[str, list[IssueListItem]], list[IssueListItem]]:
    """The issues under each value the dimension takes, in first-appearance order, and
    separately the ones that carry no value at all."""
    named: dict[str, list[IssueListItem]] = {}
    missing: list[IssueListItem] = []
    for issue in issues:
        value = value_of(issue)
        if value is None:
            missing.append(issue)
        else:
            named.setdefault(value, []).append(issue)
    return named, missing


def _single(
    issues: Sequence[IssueListItem],
    value_of: Callable[[IssueListItem], str | None],
    missing_label: str,
    related_of: Callable[[IssueListItem], str | None] | None = None,
) -> list[_Bucket]:
    """A dimension whose value is one field of the issue: a group per value in the
    order the values first appear, and the issues without one in a trailing group."""
    named, missing = _by_value(issues, value_of)
    buckets = [
        (
            GroupKey(value, value, related_of(rows[0]) if related_of is not None else None),
            rows,
        )
        for value, rows in named.items()
    ]
    if missing:
        buckets.append((GroupKey(None, missing_label), missing))
    return buckets


def _by_status(issues: Sequence[IssueListItem]) -> list[_Bucket]:
    named, _ = _by_value(issues, lambda issue: issue.status)
    # The board's three statuses always draw, empty or not, so the columns keep their
    # shape; a status outside them heads a group only when something sits at it, and
    # one outside the workflow order trails the known ones.
    known = [s for s in ROLLUP_STATUSES if s in BOARD_STATUSES or s in named]
    unknown = [s for s in named if s not in ROLLUP_STATUSES]
    return [(GroupKey(status, status), named.get(status, [])) for status in known + unknown]


def _by_priority(issues: Sequence[IssueListItem]) -> list[_Bucket]:
    named, _ = _by_value(issues, lambda issue: issue.priority)
    known = [p for p in PRIORITY_ORDER if p in named]
    unknown = [p for p in named if p not in PRIORITY_ORDER]
    return [(GroupKey(level, level), named[level]) for level in known + unknown]


def _by_tag(issues: Sequence[IssueListItem]) -> list[_Bucket]:
    # A tag is many-to-many, so an issue wearing two tags is filed under both — the one
    # dimension where the groups do not partition the list.
    named: dict[str, list[IssueListItem]] = {}
    untagged: list[IssueListItem] = []
    for issue in issues:
        if not issue.tags:
            untagged.append(issue)
        for tag in issue.tags:
            named.setdefault(tag, []).append(issue)
    buckets = [(GroupKey(tag, f"#{tag}"), rows) for tag, rows in named.items()]
    if untagged:
        buckets.append((GroupKey(None, "(untagged)"), untagged))
    return buckets


def due_bucket(due: date, today: date) -> str:
    """The bucket a due date falls in, relative to the day being read: already late,
    due today, inside the coming week, or further off than that."""
    if due < today:
        return "overdue"
    if due == today:
        return "today"
    if due <= today + timedelta(days=DUE_WEEK):
        return "this week"
    return "later"


def _by_due(issues: Sequence[IssueListItem], today: date) -> list[_Bucket]:
    named, missing = _by_value(
        issues,
        lambda issue: due_bucket(issue.due_date, today) if issue.due_date is not None else None,
    )
    buckets = [(GroupKey(name, name), named[name]) for name in DUE_BUCKETS if name in named]
    if missing:
        buckets.append((GroupKey(None, "(no due date)"), missing))
    return buckets


def _buckets(issues: Sequence[IssueListItem], by: GroupBy, today: date | None) -> list[_Bucket]:
    """One level of grouping: the issues split by what the dimension reads off each."""
    match by:
        case "none":
            return [(GroupKey(None, ""), list(issues))]
        case "status":
            return _by_status(issues)
        case "epic":
            return _single(issues, lambda issue: issue.epic, "(no epic)")
        case "milestone":
            return _single(
                issues,
                lambda issue: issue.milestone,
                "(no milestone)",
                related_of=lambda issue: issue.epic,
            )
        case "tag":
            return _by_tag(issues)
        case "priority":
            return _by_priority(issues)
        case "due":
            return _by_due(issues, today if today is not None else date.today())


def _leaf(key: GroupKey, issues: list[IssueListItem]) -> GroupNode:
    return GroupNode(key=key, rollup=rollup(issues), issues=issues)


def group_tree(
    issues: Sequence[IssueListItem], bys: Sequence[GroupBy], today: date | None = None
) -> list[GroupNode]:
    """The issues as the tree of groups one dimension — or two nested — files them
    under, every node carrying how the issues beneath it stand. Issues with no value
    for a dimension fall in a trailing group of their own at that level rather than
    dropping out of the body. A level naming ``none`` contributes none, so no dimension
    at all is the flat list: a single group holding everything, with no label and so no
    header. At most ``MAX_LEVELS`` nest; anything past them is ignored. ``today`` is
    what the due buckets are read against; it defaults to the system date, and every
    other dimension ignores it."""
    named: list[GroupBy] = []
    for by in bys:
        if by != "none":
            named.append(by)
    levels = named[:MAX_LEVELS]
    if not levels:
        return [_leaf(GroupKey(None, ""), list(issues))]
    outer = _buckets(issues, levels[0], today)
    if len(levels) == 1:
        return [_leaf(key, rows) for key, rows in outer]
    return [
        GroupNode(
            key=key,
            rollup=rollup(rows),
            children=[_leaf(inner, under) for inner, under in _buckets(rows, levels[1], today)],
        )
        for key, rows in outer
    ]


def opening_view(layout: Layout) -> tuple[Grouping, GroupRender]:
    """The grouping and render a saved layout reopens on."""
    return (("status",), "columns") if layout == "board" else (("none",), "stacked")


def saved_layout(by: Grouping, render: GroupRender) -> Layout:
    """The layout a view is stored as. The preferences file's vocabulary is the list
    and the board, so the status groups drawn as columns are the board and every other
    view stores as the list."""
    return "board" if by == ("status",) and render == "columns" else "list"


# --- folding --------------------------------------------------------------

# What a node is addressed by while it is folded: the dimension values from the
# outermost level down to the node itself. Values are unique within a level, so a path
# names exactly one node of the tree.
type NodePath = tuple[str | None, ...]

# The nodes drawn shut. A grouping opens with none of them folded.
type Collapsed = frozenset[NodePath]

EXPANDED: Collapsed = frozenset()


def path_of(prefix: NodePath, node: GroupNode) -> NodePath:
    """Where a node sits, under the path of the parent it hangs off."""
    return (*prefix, node.key.value)


def _foldable(node: GroupNode) -> bool:
    # A caret rides on a header, and the flat list's one group draws none — so that is
    # the one node with nothing to fold it by.
    return bool(node.key.label)


def _walk(
    nodes: Sequence[GroupNode], prefix: NodePath = ()
) -> Iterator[tuple[NodePath, GroupNode]]:
    """Every node of the tree with its path, in reading order, folds ignored."""
    for node in nodes:
        path = path_of(prefix, node)
        yield path, node
        yield from _walk(node.children, path)


def folded(node: GroupNode, path: NodePath, collapsed: Collapsed) -> bool:
    """Whether a node is drawn shut: it has a header to carry the caret, and its path
    is in the fold state."""
    return _foldable(node) and path in collapsed


def fold_paths(nodes: Sequence[GroupNode]) -> list[NodePath]:
    """Every foldable node's path, in reading order."""
    return [path for path, node in _walk(nodes) if _foldable(node)]


def fold_all(nodes: Sequence[GroupNode]) -> Collapsed:
    """The fold state with every node shut — what ``zM`` lands on."""
    return frozenset(fold_paths(nodes))


def toggle_fold(collapsed: Collapsed, path: NodePath) -> Collapsed:
    """The fold state with one node flipped: shut when it was open, open when shut."""
    return collapsed - {path} if path in collapsed else collapsed | {path}


def fold_target(nodes: Sequence[GroupNode], selected_id: int | None) -> NodePath | None:
    """The node ``za`` toggles: the innermost one holding the selected issue. ``None``
    when nothing is selected, or when the selection sits in the flat list's one
    headerless group — there is no caret there to toggle."""
    for path, node in _walk(nodes):
        if _foldable(node) and any(issue.id == selected_id for issue in node.issues):
            return path
    return None


def _rows(
    nodes: Sequence[GroupNode], collapsed: Collapsed, prefix: NodePath
) -> list[IssueListItem]:
    rows: list[IssueListItem] = []
    for node in nodes:
        path = path_of(prefix, node)
        if folded(node, path, collapsed):
            continue
        rows.extend(node.issues)
        rows.extend(_rows(node.children, collapsed, path))
    return rows


def all_issues(nodes: Sequence[GroupNode]) -> list[IssueListItem]:
    """Every issue of the tree, in reading order, whatever is folded."""
    return _rows(nodes, EXPANDED, ())


def visible_issues(nodes: Sequence[GroupNode], collapsed: Collapsed) -> list[IssueListItem]:
    """The issues on screen, in reading order: a folded node keeps everything under it
    off the page."""
    return _rows(nodes, collapsed, ())


# --- selection ------------------------------------------------------------


class Identified(Protocol):
    """A row a cursor addresses by id — an issue of the list, a comment of the
    thread. The reconciliation below is the same for both, so it names this rather
    than either row shape."""

    id: int


def _locate(
    columns: Sequence[list[IssueListItem]], selected_id: int | None
) -> tuple[int, int] | None:
    for gi, rows in enumerate(columns):
        for ri, issue in enumerate(rows):
            if issue.id == selected_id:
                return gi, ri
    return None


def _walk_columns(
    nodes: Sequence[GroupNode], collapsed: Collapsed, selected_id: int | None, dr: int, dc: int
) -> int | None:
    columns = [visible_issues([node], collapsed) for node in nodes]
    at = _locate(columns, selected_id)
    if at is None:
        return next((rows[0].id for rows in columns if rows), None)
    gi, ri = at
    if dc != 0:
        step = 1 if dc > 0 else -1
        stop = len(columns) if step > 0 else -1
        for probe in range(gi + step, stop, step):
            if columns[probe]:
                gi = probe
                break
    rows = columns[gi]
    return rows[_clamp(ri + dr, 0, len(rows) - 1)].id


def _walk_stack(
    nodes: Sequence[GroupNode], collapsed: Collapsed, selected_id: int | None, dr: int
) -> int | None:
    shown = visible_issues(nodes, collapsed)
    if not shown:
        # Everything is folded shut, so there is no row to land on; holding the
        # selection is what brings the cursor back when a node is opened again.
        return selected_id
    here = next((i for i, issue in enumerate(shown) if issue.id == selected_id), None)
    if here is None:
        # The selection is folded away, or gone altogether. The cursor counts as
        # sitting at the edge of the hidden run: a step down lands on the first row
        # after it, a step up on the last row before it.
        order = {issue.id: i for i, issue in enumerate(all_issues(nodes))}
        at = order.get(selected_id if selected_id is not None else -1, -1)
        after = next((i for i, issue in enumerate(shown) if order[issue.id] > at), len(shown))
        here = after if dr < 0 else after - 1
    return shown[_clamp(here + dr, 0, len(shown) - 1)].id


def move_selection(
    nodes: Sequence[GroupNode],
    render: GroupRender,
    collapsed: Collapsed,
    selected_id: int | None,
    dr: int,
    dc: int,
) -> int | None:
    """The id the cursor lands on after moving ``dr`` rows and ``dc`` groups. Pure: the
    whole of the body's navigation, and it walks only what is on screen — a folded
    subtree is stepped over rather than through. Stacked, the visible rows are one run
    of issues and a row move crosses their headers; as columns each group is walked on
    its own and ``dc`` is what crosses between them. Headers are display only, so the
    cursor only ever lands on an issue."""
    if render == "columns":
        return _walk_columns(nodes, collapsed, selected_id, dr, dc)
    return _walk_stack(nodes, collapsed, selected_id, dr)


def surviving_id(
    rows: Sequence[Identified], selected_id: int | None, fallback_index: int
) -> int | None:
    """The selection after a reload. Keep the same row if it is still here; else take
    whatever now sits where it was, so deleting the selected row lands on its
    neighbour rather than jumping to the top."""
    if not rows:
        return None
    if any(row.id == selected_id for row in rows):
        return selected_id
    return rows[_clamp(fallback_index, 0, len(rows) - 1)].id


def index_of(rows: Sequence[Identified], selected_id: int | None) -> int:
    """The flat position of the selected id, or ``0`` when it is gone."""
    for i, row in enumerate(rows):
        if row.id == selected_id:
            return i
    return 0


def move_comment(comments: Sequence[Identified], selected_id: int | None, delta: int) -> int | None:
    """The comment id the cursor lands on after moving ``delta`` rows down the thread,
    clamped at both ends. Nothing selected takes the first comment — the thread has no
    columns to cross, so this is the whole of its navigation."""
    if not comments:
        return None
    here = next((i for i, comment in enumerate(comments) if comment.id == selected_id), None)
    if here is None:
        return comments[0].id
    return comments[_clamp(here + delta, 0, len(comments) - 1)].id


# --- cwd resolution -------------------------------------------------------


def match_path(candidates: list[tuple[str, str | None]], cwd: str) -> str | None:
    """The slug of the project whose path is the longest ancestor of ``cwd`` (or
    equal to it). ``None`` when the shell is not inside any project's directory."""
    here = os.path.normpath(os.path.expanduser(cwd))
    best: tuple[int, str] | None = None
    for slug, path in candidates:
        if path is None:
            continue
        root = os.path.normpath(os.path.expanduser(path))
        inside = here == root or here.startswith(root + os.sep)
        if inside and (best is None or len(root) > best[0]):
            best = (len(root), slug)
    return best[1] if best is not None else None


# --- commands (the rows of every list overlay) ----------------------------

# The single-key accelerators, each naming an action that runs against the selected
# issue. ``d`` runs the fieldless ``delete`` straight; ``e`` opens the edit form;
# ``s`` drives a direct status cycle — it dispatches ``setStatus`` on the next status
# rather than opening the menu's pick-a-status form, so it does not go through the
# generic offer path.
ACCELERATORS: dict[str, str] = {
    "d": "delete",
    "e": "edit",
    "s": "setStatus",
}
_HINT = {action_key: keystroke for keystroke, action_key in ACCELERATORS.items()}


@dataclass(frozen=True)
class IssueTarget:
    ref: str


@dataclass(frozen=True)
class ProjectTarget:
    slug: str


@dataclass(frozen=True)
class CommentTarget:
    """A comment of the thread. Addressed by its global id, not a ref: a comment has
    no project-scoped number of its own."""

    comment_id: int


@dataclass(frozen=True)
class RootTarget:
    """A creator with no object to address — ``createProject``."""


type Target = IssueTarget | ProjectTarget | CommentTarget | RootTarget


@dataclass(frozen=True)
class RunAction:
    """Picking this command runs an action against ``target``: straight away when it
    has no fields, through a form when it does. ``seed`` is the target's current
    values (the detail dumped to JSON) when the action edits its target, so the form
    opens pre-filled; ``None`` for a create, whose form opens blank."""

    target: Target
    key: str
    fields: list[Field]
    seed: dict[str, Any] | None = None


@dataclass(frozen=True)
class Navigate:
    """Picking this command steers the app rather than writing: ``what`` names the
    move and ``arg`` carries its argument (a slug for ``switch``)."""

    what: str
    arg: str | None = None


type CommandRun = RunAction | Navigate


@dataclass(frozen=True)
class Command:
    """One row of a command list: what it says, why it cannot run (``None`` when it
    can), the accelerator that also runs it, and what picking it does."""

    label: str
    reason: str | None
    hint: str | None
    run: CommandRun


def _offer_command(offer: Offer, target: Target, detail: dict[str, Any] | None = None) -> Command:
    # An offer that seeds from its target carries the target's current values into
    # its form; one that does not (a create) carries none, so its form opens blank.
    seed = detail if offer.seed else None
    reason = offer.state.reason if isinstance(offer.state, Refused) else None
    return Command(
        label=offer.label,
        reason=reason,
        hint=_HINT.get(offer.key),
        run=RunAction(target=target, key=offer.key, fields=offer.fields, seed=seed),
    )


def issue_commands(
    offers: list[Offer], issue_ref: str, detail: dict[str, Any] | None = None
) -> list[Command]:
    return [_offer_command(offer, IssueTarget(issue_ref), detail) for offer in offers]


def project_commands(
    offers: list[Offer], slug: str, detail: dict[str, Any] | None = None
) -> list[Command]:
    return [_offer_command(offer, ProjectTarget(slug), detail) for offer in offers]


def comment_commands(
    offers: list[Offer], comment_id: int, detail: dict[str, Any] | None = None
) -> list[Command]:
    return [_offer_command(offer, CommentTarget(comment_id), detail) for offer in offers]


def _pick(label: str, marked: bool, what: str, by: GroupBy) -> Command:
    # Steering the body is not a write, so a picker row carries no refusal and no
    # accelerator — only where it sends the view.
    return Command(
        label=f"{label}  ✓" if marked else label,
        reason=None,
        hint=None,
        run=Navigate(what, by),
    )


def group_commands(current: Grouping) -> list[Command]:
    """The rows of the grouping picker's first step: one per dimension, the outer one
    in force marked."""
    return [_pick(GROUP_LABELS[by], by == current[0], "group", by) for by in GROUP_BYS]


# The picker's second step opens on the row that leaves the grouping one level deep, so
# the two-step pick is one extra keystroke for a single dimension.
NO_SECOND_LEVEL = "No second level"


def nest_commands(first: GroupBy, current: Grouping) -> list[Command]:
    """The rows of the grouping picker's second step: the dimension to nest under
    ``first``, headed by the row that leaves the grouping one level deep. A dimension
    cannot nest inside itself, so ``first`` is not offered again."""
    # The mark rides on the grouping in force, and only while its outer level is the
    # dimension just picked — nesting under a different one is a fresh choice, with
    # nothing yet in force to mark.
    inner: GroupBy | None = None
    if current[:1] == (first,):
        inner = current[1] if len(current) > 1 else "none"
    return [
        _pick(
            NO_SECOND_LEVEL if by == "none" else GROUP_LABELS[by],
            by == inner,
            "nest",
            by,
        )
        for by in GROUP_BYS
        if by != first
    ]


def switcher_commands(projects: list[ProjectListItem], create: Offer | None) -> list[Command]:
    rows = [
        Command(
            label=f"{p.slug}   {p.path or ''}".rstrip(),
            reason=None,
            hint=None,
            run=Navigate("switch", p.slug),
        )
        for p in projects
    ]
    if create is not None:
        rows.append(_offer_command(create, RootTarget()))
    return rows


def visible(commands: list[Command], query: str) -> list[int]:
    """The indices of the commands whose label contains ``query`` (folded), in
    order — a plain substring filter for the overlay's search line."""
    needle = query.lower()
    return [i for i, command in enumerate(commands) if needle in command.label.lower()]
