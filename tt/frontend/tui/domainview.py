"""Framework-free helpers over the domain — the pure core of the TUI.

Everything here is a pure function or a plain dataclass; nothing imports textual,
so it is driven directly in tests with no terminal. The screens in the package
above turn these values into widgets — the glyphs and colours a status reads as,
the facets a filter narrows on and the chips they draw as, the named slice a saved
view recalls, the tree one or two dimensions file issues under and which of its
nodes are folded shut, where the cursor lands after a move, and the commands a menu
draws from an object's offers.
This module never touches a widget or the database.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
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
from tt.platform.config import Layout, SavedFilter, SavedView

# --- the visual vocabulary ------------------------------------------------

STATUS_GLYPH = {"todo": "○", "doing": "◐", "done": "●", "canceled": "⊗"}

# The theme variable a status's glyph is drawn in, for the places that mix the
# glyph colour into a single markup string (the board header, the detail pane)
# rather than carrying it in a CSS component class. The flat row uses ``st-*``.
STATUS_VAR = {
    "todo": "$text-muted",
    "doing": "$warning",
    "done": "$success",
    "canceled": "$text-muted",
}

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


# The width above which a third column costs the list and the detail pane nothing.
RAIL_MIN_WIDTH = 160


def shows_rail(width: int) -> bool:
    """Whether the rail stands beside the list as a third column. It only does in the
    ultra-wide band; below it the grouping, the filters and the saved views stay modal
    behind ``g``/``f``/``v``, which is why collapsing the rail loses no capability."""
    return width >= RAIL_MIN_WIDTH


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
    is at are left out rather than counted as zero. Canceled issues still show as their
    own count but sit outside the progress total — done is measured against the work
    that was ever going to happen, not what was abandoned."""
    tally = Counter(issue.status for issue in issues)
    done = tally[Status.DONE]
    total = len(issues) - tally[Status.CANCELED]
    ordered = {status: tally.pop(status) for status in ROLLUP_STATUSES if status in tally}
    # Anything left in the tally is a status outside the workflow order; it trails the
    # known ones rather than vanishing from the breakdown.
    return Rollup(counts=ordered | tally, done=done, total=total)


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

# The dimensions whose values are objects of their own: an epic, a milestone, a tag are
# rows something can be done to, so their headers are what the cursor lands on and what
# a menu opens against. A status, a priority or a due bucket names no row, so a header
# of one stays display only.
OBJECT_DIMENSIONS: frozenset[GroupBy] = frozenset({"epic", "milestone", "tag"})

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
    """What a group is: the dimension ``by`` it splits on, that dimension's ``value``
    (``None`` for the trailing group of issues carrying none), the ``label`` its header
    reads, and the second field it is filed under when the dimension has one — a
    milestone's epic. The dimension rides here because a header is only selectable when
    it stands for a real object, and the value alone cannot say which dimension it came
    from."""

    by: GroupBy
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
    by: GroupBy,
    value_of: Callable[[IssueListItem], str | None],
    missing_label: str,
    related_of: Callable[[IssueListItem], str | None] | None = None,
) -> list[_Bucket]:
    """A dimension whose value is one field of the issue: a group per value in the
    order the values first appear, and the issues without one in a trailing group."""
    named, missing = _by_value(issues, value_of)
    buckets = [
        (
            GroupKey(by, value, value, related_of(rows[0]) if related_of is not None else None),
            rows,
        )
        for value, rows in named.items()
    ]
    if missing:
        buckets.append((GroupKey(by, None, missing_label), missing))
    return buckets


def _by_status(issues: Sequence[IssueListItem]) -> list[_Bucket]:
    named, _ = _by_value(issues, lambda issue: issue.status)
    # The board's three statuses always draw, empty or not, so the columns keep their
    # shape; a status outside them heads a group only when something sits at it, and
    # one outside the workflow order trails the known ones.
    known = [s for s in ROLLUP_STATUSES if s in BOARD_STATUSES or s in named]
    unknown = [s for s in named if s not in ROLLUP_STATUSES]
    return [
        (GroupKey("status", status, status), named.get(status, [])) for status in known + unknown
    ]


def _by_priority(issues: Sequence[IssueListItem]) -> list[_Bucket]:
    named, _ = _by_value(issues, lambda issue: issue.priority)
    known = [p for p in PRIORITY_ORDER if p in named]
    unknown = [p for p in named if p not in PRIORITY_ORDER]
    return [(GroupKey("priority", level, level), named[level]) for level in known + unknown]


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
    buckets = [(GroupKey("tag", tag, f"#{tag}"), rows) for tag, rows in named.items()]
    if untagged:
        buckets.append((GroupKey("tag", None, "(untagged)"), untagged))
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
    buckets = [(GroupKey("due", name, name), named[name]) for name in DUE_BUCKETS if name in named]
    if missing:
        buckets.append((GroupKey("due", None, "(no due date)"), missing))
    return buckets


def _buckets(issues: Sequence[IssueListItem], by: GroupBy, today: date | None) -> list[_Bucket]:
    """One level of grouping: the issues split by what the dimension reads off each."""
    match by:
        case "none":
            return [(GroupKey("none", None, ""), list(issues))]
        case "status":
            return _by_status(issues)
        case "epic":
            return _single(issues, "epic", lambda issue: issue.epic, "(no epic)")
        case "milestone":
            return _single(
                issues,
                "milestone",
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


def levels(bys: Sequence[GroupBy]) -> list[GroupBy]:
    """The dimensions a grouping actually files issues under, outermost first: the
    levels naming no dimension drop out, and at most ``MAX_LEVELS`` nest."""
    named: list[GroupBy] = [by for by in bys if by != "none"]
    return named[:MAX_LEVELS]


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
    named = levels(bys)
    if not named:
        return [_leaf(GroupKey("none", None, ""), list(issues))]
    outer = _buckets(issues, named[0], today)
    if len(named) == 1:
        return [_leaf(key, rows) for key, rows in outer]
    return [
        GroupNode(
            key=key,
            rollup=rollup(rows),
            children=[_leaf(inner, under) for inner, under in _buckets(rows, named[1], today)],
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


# --- filtering ------------------------------------------------------------

# A facet whose value is a plain string, so one menu pick sets it. ``text`` is here
# because the quick title search types a string like any other value.
type ValueFacet = Literal["status", "priority", "epic", "milestone", "tag", "text"]

# Every facet a filter narrows on. ``due`` stands apart because it holds a date, not a
# string, and so is typed rather than picked off a list.
type Facet = ValueFacet | Literal["due"]

VALUE_FACETS: tuple[ValueFacet, ...] = (
    "status",
    "priority",
    "epic",
    "milestone",
    "tag",
    "text",
)

# The facets in the order the menu lists them and the chips bar draws them.
FACETS: tuple[Facet, ...] = ("status", "priority", "epic", "milestone", "tag", "due", "text")

FACET_LABELS: Mapping[Facet, str] = {
    "status": "Status",
    "priority": "Priority",
    "epic": "Epic",
    "milestone": "Milestone",
    "tag": "Tag",
    "due": "Due before",
    "text": "Text",
}


@dataclass(frozen=True)
class Filter:
    """What narrows the list: the facets in force. Each holds at most one value, so
    picking a facet twice replaces rather than widens, and they AND together — the empty
    filter admits everything. ``text`` is the quick title search ``/`` types, folded in
    as a facet of its own so it composes with the rest instead of sitting beside them."""

    status: str | None = None
    priority: str | None = None
    epic: str | None = None
    milestone: str | None = None
    tag: str | None = None
    due_before: date | None = None
    text: str = ""


NO_FILTER = Filter()


def facet_of(name: object) -> Facet | None:
    """The facet a name picks out, or ``None`` when it names none. A picked row steers
    with its argument as a plain string, so this is what turns one back into a facet."""
    return next((facet for facet in FACETS if facet == name), None)


def facet_value(current: Filter, facet: ValueFacet) -> str | None:
    """The value a facet holds, or ``None`` when it is not in force. A blank ``text`` is
    no text facet at all, so it reads as not in force rather than as an empty needle."""
    match facet:
        case "status":
            return current.status
        case "priority":
            return current.priority
        case "epic":
            return current.epic
        case "milestone":
            return current.milestone
        case "tag":
            return current.tag
        case "text":
            return current.text or None


def with_facet(current: Filter, facet: ValueFacet, value: str) -> Filter:
    """The filter with one facet set, replacing whatever that facet held."""
    match facet:
        case "status":
            return replace(current, status=value)
        case "priority":
            return replace(current, priority=value)
        case "epic":
            return replace(current, epic=value)
        case "milestone":
            return replace(current, milestone=value)
        case "tag":
            return replace(current, tag=value)
        case "text":
            return replace(current, text=value)


def with_due(current: Filter, due: date | None) -> Filter:
    """The filter with the due cutoff set, or dropped by ``None``. The due facet holds a
    date rather than a string, so it is set on its own."""
    return replace(current, due_before=due)


def without_facet(current: Filter, facet: Facet) -> Filter:
    """The filter with one facet dropped — what removing its chip leaves."""
    match facet:
        case "due":
            return replace(current, due_before=None)
        case "status":
            return replace(current, status=None)
        case "priority":
            return replace(current, priority=None)
        case "epic":
            return replace(current, epic=None)
        case "milestone":
            return replace(current, milestone=None)
        case "tag":
            return replace(current, tag=None)
        case "text":
            return replace(current, text="")


def _admits(issue: IssueListItem, current: Filter) -> bool:
    return (
        (current.status is None or issue.status == current.status)
        and (current.priority is None or issue.priority == current.priority)
        and (current.epic is None or issue.epic == current.epic)
        and (current.milestone is None or issue.milestone == current.milestone)
        and (current.tag is None or current.tag in issue.tags)
        and (
            current.due_before is None
            or (issue.due_date is not None and issue.due_date < current.due_before)
        )
        and (not current.text or current.text.lower() in issue.title.lower())
    )


def apply(issues: Sequence[IssueListItem], current: Filter) -> list[IssueListItem]:
    """The issues every facet in force admits, in the order they arrived. The empty
    filter is the identity. An issue carrying no value for a facet that is in force is
    out — filtering to an epic is filtering to that epic, not to everything that could
    one day sit under it — and an undated issue is never due before a cutoff."""
    return [issue for issue in issues if _admits(issue, current)]


@dataclass(frozen=True)
class Chip:
    """One facet in force as the bar draws it: which facet it is, so removing the chip
    knows what to drop, and the line it reads."""

    facet: Facet
    label: str


def chips(current: Filter) -> list[Chip]:
    """The facets in force, in the order ``FACETS`` lists them. An empty filter has no
    chips, and the bar is then not drawn at all."""
    drawn: list[Chip] = []
    for facet in FACETS:
        if facet == "due":
            if current.due_before is not None:
                drawn.append(Chip(facet, f"due before {current.due_before:%Y-%m-%d}"))
            continue
        value = facet_value(current, facet)
        if value is not None:
            drawn.append(Chip(facet, f"{FACET_LABELS[facet].lower()} {value}"))
    return drawn


def facet_values(issues: Sequence[IssueListItem], facet: ValueFacet) -> list[str]:
    """The values a facet's menu offers. Status and priority are closed vocabularies, so
    every level is listed whether or not the loaded issues use it; an epic, a milestone
    and a tag are objects, so what is offered is what the loaded issues are actually
    filed under, sorted. ``text`` is typed rather than picked, so it offers none."""
    match facet:
        case "status":
            return list(ROLLUP_STATUSES)
        case "priority":
            return list(PRIORITY_ORDER)
        case "epic":
            return sorted({issue.epic for issue in issues if issue.epic is not None})
        case "milestone":
            return sorted({issue.milestone for issue in issues if issue.milestone is not None})
        case "tag":
            return sorted({tag for issue in issues for tag in issue.tags})
        case "text":
            return []


def drill_facet(by: GroupBy) -> ValueFacet | None:
    """The facet entering a group applies: drilling into an epic, a milestone or a tag is
    filtering to it, so a header is not a separate mechanism. A dimension standing for no
    object — a status, a priority, a due bucket — enters nothing."""
    match by:
        case "epic" | "milestone" | "tag":
            return by
        case "none" | "status" | "priority" | "due":
            return None


# --- saved views ----------------------------------------------------------


@dataclass(frozen=True)
class View:
    """A named slice of the tracker: the scope it is read over — one project it pins, or
    every project at once — the dimensions it groups by, and what it narrows to. The
    three go into force together, which is what makes one keystroke worth a name."""

    name: str
    scope: Scope
    by: Grouping
    filter: Filter = NO_FILTER


def _stored_grouping(stored: Sequence[str]) -> Grouping:
    """The grouping a stored view recalls: the levels naming a real dimension, outermost
    first and at most two, and the flat list when the file names none."""
    named: list[GroupBy] = []
    for level in stored:
        by = group_by(level)
        if by is not None:
            named.append(by)
    kept = levels(named)
    return grouping(
        kept[0] if kept else "none",
        kept[1] if len(kept) > 1 else "none",
    )


def _stored_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def view_of(stored: SavedView) -> View:
    """The view a stored entry recalls. Total the way ``config.load`` is: a level naming
    no dimension and a cutoff that is not a date drop out rather than raising, so a
    hand-edited file still opens."""
    narrowing = stored.filter
    return View(
        name=stored.name,
        scope=ProjectScope(stored.project) if stored.project is not None else AllScope(),
        by=_stored_grouping(stored.group),
        filter=Filter(
            status=narrowing.status,
            priority=narrowing.priority,
            epic=narrowing.epic,
            milestone=narrowing.milestone,
            tag=narrowing.tag,
            due_before=_stored_date(narrowing.due_before),
            text=narrowing.text,
        ),
    )


def saved_view(view: View) -> SavedView:
    """The view as the preferences file stores it — the inverse of ``view_of``. An
    all-projects view pins no project, which is how a slice cuts across them."""
    narrowing = view.filter
    return SavedView(
        name=view.name,
        project=view.scope.slug if isinstance(view.scope, ProjectScope) else None,
        group=tuple(view.by),
        filter=SavedFilter(
            status=narrowing.status,
            priority=narrowing.priority,
            epic=narrowing.epic,
            milestone=narrowing.milestone,
            tag=narrowing.tag,
            due_before=(
                f"{narrowing.due_before:%Y-%m-%d}" if narrowing.due_before is not None else None
            ),
            text=narrowing.text,
        ),
    )


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


def fold_target(nodes: Sequence[GroupNode], cursor: Cursor | None) -> NodePath | None:
    """The node ``za`` toggles: the one the cursor is the header of, or the innermost
    one holding the selected issue. ``None`` when nothing is selected, or when the
    selection sits in the flat list's one headerless group — there is no caret there to
    toggle."""
    if isinstance(cursor, HeaderAt):
        return cursor.path
    for path, node in _walk(nodes):
        if _foldable(node) and any(issue.id == cursor for issue in node.issues):
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


@dataclass(frozen=True)
class HeaderAt:
    """Where the cursor sits when it is on a group header: the ``path`` addressing the
    node in the tree, the dimension ``by`` its group splits on, and the ``value`` naming
    the object it heads — an epic or milestone ref, a tag's name."""

    path: NodePath
    by: GroupBy
    value: str


# What the cursor is on: an issue, by the id the list tracks it by, or the header of a
# group that stands for a real object.
type Cursor = int | HeaderAt


def header_at(node: GroupNode, path: NodePath) -> HeaderAt | None:
    """The stop a node's header offers the cursor, or ``None`` when the header stands
    for no object — a status, priority or due bucket, or the trailing group of issues
    carrying no value at all."""
    key = node.key
    if key.by not in OBJECT_DIMENSIONS or key.value is None:
        return None
    return HeaderAt(path, key.by, key.value)


def _stops(nodes: Sequence[GroupNode], collapsed: Collapsed, prefix: NodePath) -> list[Cursor]:
    stops: list[Cursor] = []
    for node in nodes:
        path = path_of(prefix, node)
        header = header_at(node, path)
        # A folded node still draws its header, so the stop it offers stands whether or
        # not what hangs off it is on the page.
        if header is not None:
            stops.append(header)
        if folded(node, path, collapsed):
            continue
        stops.extend(issue.id for issue in node.issues)
        stops.extend(_stops(node.children, collapsed, path))
    return stops


def all_stops(nodes: Sequence[GroupNode]) -> list[Cursor]:
    """Every stop of the tree, in reading order, whatever is folded."""
    return _stops(nodes, EXPANDED, ())


def visible_stops(nodes: Sequence[GroupNode], collapsed: Collapsed) -> list[Cursor]:
    """Where the cursor can land, in reading order: the issues on screen and the
    headers of the groups standing for an object, a folded subtree stepped over."""
    return _stops(nodes, collapsed, ())


class Identified(Protocol):
    """A row a cursor addresses by id — an issue of the list, a comment of the
    thread. The reconciliation below is the same for both, so it names this rather
    than either row shape."""

    id: int


def _locate(columns: Sequence[list[Cursor]], cursor: Cursor | None) -> tuple[int, int] | None:
    for gi, stops in enumerate(columns):
        for ri, stop in enumerate(stops):
            if stop == cursor:
                return gi, ri
    return None


def _walk_columns(
    nodes: Sequence[GroupNode], collapsed: Collapsed, cursor: Cursor | None, dr: int, dc: int
) -> Cursor | None:
    columns = [visible_stops([node], collapsed) for node in nodes]
    at = _locate(columns, cursor)
    if at is None:
        return next((stops[0] for stops in columns if stops), None)
    gi, ri = at
    if dc != 0:
        step = 1 if dc > 0 else -1
        stop = len(columns) if step > 0 else -1
        for probe in range(gi + step, stop, step):
            if columns[probe]:
                gi = probe
                break
    stops = columns[gi]
    return stops[_clamp(ri + dr, 0, len(stops) - 1)]


def _walk_stack(
    nodes: Sequence[GroupNode], collapsed: Collapsed, cursor: Cursor | None, dr: int
) -> Cursor | None:
    shown = visible_stops(nodes, collapsed)
    if not shown:
        # Everything is folded shut and no header stands for an object, so there is no
        # stop to land on; holding the selection is what brings the cursor back when a
        # node is opened again.
        return cursor
    here = next((i for i, stop in enumerate(shown) if stop == cursor), None)
    if here is None:
        # The selection is folded away, or gone altogether. The cursor counts as
        # sitting at the edge of the hidden run: a step down lands on the first stop
        # after it, a step up on the last stop before it.
        order = {stop: i for i, stop in enumerate(all_stops(nodes))}
        at = order.get(cursor, -1) if cursor is not None else -1
        after = next((i for i, stop in enumerate(shown) if order[stop] > at), len(shown))
        here = after if dr < 0 else after - 1
    return shown[_clamp(here + dr, 0, len(shown) - 1)]


def move_selection(
    nodes: Sequence[GroupNode],
    render: GroupRender,
    collapsed: Collapsed,
    cursor: Cursor | None,
    dr: int,
    dc: int,
) -> Cursor | None:
    """Where the cursor lands after moving ``dr`` rows and ``dc`` groups. Pure: the
    whole of the body's navigation, and it walks only what is on screen — a folded
    subtree is stepped over rather than through. Stacked, the visible stops are one run
    down the page; as columns each group is walked on its own and ``dc`` is what crosses
    between them. A header whose group stands for a real object is a stop like an issue;
    a status, priority or due header is display only and stepped over."""
    if render == "columns":
        return _walk_columns(nodes, collapsed, cursor, dr, dc)
    return _walk_stack(nodes, collapsed, cursor, dr)


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
class EpicTarget:
    """The epic a rollup header stands for, by the ref the header reads."""

    ref: str


@dataclass(frozen=True)
class MilestoneTarget:
    """The milestone a rollup header stands for, by the ref the header reads."""

    ref: str


@dataclass(frozen=True)
class TagTarget:
    """A tag, by its global id: a tag is a global label, so it has no project-scoped
    ref. ``None`` addresses no tag at all, which is what the top-level ``createTag``
    runs against."""

    tag_id: int | None


@dataclass(frozen=True)
class RootTarget:
    """A creator with no object to address — ``createProject``."""


type Target = (
    IssueTarget
    | ProjectTarget
    | CommentTarget
    | EpicTarget
    | MilestoneTarget
    | TagTarget
    | RootTarget
)


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
    move and ``arg`` carries its argument (a slug for ``switch``, a dimension for
    ``group``, a facet for ``facet``). ``value`` is the second argument the moves that
    address a facet *and* what to set it to need — entering an epic is both."""

    what: str
    arg: str | None = None
    value: str | None = None


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


def epic_commands(
    offers: list[Offer], ref: str, detail: dict[str, Any] | None = None
) -> list[Command]:
    return [_offer_command(offer, EpicTarget(ref), detail) for offer in offers]


def milestone_commands(
    offers: list[Offer], ref: str, detail: dict[str, Any] | None = None
) -> list[Command]:
    return [_offer_command(offer, MilestoneTarget(ref), detail) for offer in offers]


def tag_commands(
    offers: list[Offer], tag_id: int | None, detail: dict[str, Any] | None = None
) -> list[Command]:
    return [_offer_command(offer, TagTarget(tag_id), detail) for offer in offers]


def _pick(label: str, marked: bool, what: str, arg: str) -> Command:
    # Steering the body is not a write, so a picker row carries no refusal and no
    # accelerator — only where it sends the view.
    return Command(
        label=f"{label}  ✓" if marked else label,
        reason=None,
        hint=None,
        run=Navigate(what, arg),
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


# The row that drops every facet at once, at the foot of the filter menu.
CLEAR_FILTERS = "Clear all filters"


def filter_commands(current: Filter) -> list[Command]:
    """The rows of the filter menu's first step: one per facet, the ones in force
    marked, then a row per chip that removes just that facet and — while anything is in
    force — the row that drops the lot."""
    held = chips(current)
    rows = [
        _pick(FACET_LABELS[facet], any(chip.facet == facet for chip in held), "facet", facet)
        for facet in FACETS
    ]
    rows.extend(
        Command(f"Remove {chip.label}", None, None, Navigate("unfacet", chip.facet))
        for chip in held
    )
    if held:
        rows.append(Command(CLEAR_FILTERS, None, None, Navigate("clearFilters")))
    return rows


def facet_commands(facet: ValueFacet, values: Sequence[str], current: Filter) -> list[Command]:
    """The rows of the filter menu's second step: the values the facet can take, the one
    in force marked."""
    held = facet_value(current, facet)
    return [_pick(value, value == held, "facetValue", value) for value in values]


# The row of the view overlay that captures the live state under a name.
SAVE_VIEW = "Save current as…"

# What an all-projects view reads as where a pinned one names its project.
EVERYWHERE = "all projects"


def view_label(view: View) -> str:
    """What a saved view reads as in the overlay: its name, then the slice it stands
    for — recalling one is a jump, so the row says where it lands."""
    where = view.scope.slug if isinstance(view.scope, ProjectScope) else EVERYWHERE
    return f"{view.name}   {where} · {grouping_label(view.by)}"


def view_commands(views: Sequence[View]) -> list[Command]:
    """The rows of the view overlay: one per saved view, which recalls it, then the row
    that saves the live state under a name, then a delete row per view — the filter
    menu's shape, where what takes something away sits under what puts it in force."""
    rows = [Command(view_label(view), None, None, Navigate("view", view.name)) for view in views]
    rows.append(Command(SAVE_VIEW, None, None, Navigate("saveView")))
    rows.extend(
        Command(f"Delete {view.name}", None, None, Navigate("deleteView", view.name))
        for view in views
    )
    return rows


def drill_commands(issue: IssueListItem) -> list[Command]:
    """The rows that enter what an issue is filed under — its epic, its milestone, each
    of its tags. Picking one applies that facet, the same move entering a group header
    makes, so the detail's relations and the headers are one mechanism."""
    rows: list[Command] = []
    if issue.epic is not None:
        rows.append(_drill("epic", issue.epic))
    if issue.milestone is not None:
        rows.append(_drill("milestone", issue.milestone))
    rows.extend(_drill("tag", tag) for tag in issue.tags)
    return rows


def _drill(facet: ValueFacet, value: str) -> Command:
    return Command(
        label=f"Enter {FACET_LABELS[facet].lower()} {value}",
        reason=None,
        hint=None,
        run=Navigate("enter", facet, value),
    )


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
