"""Reads against the issues table: the live list for a project, one by ref, and one
by id.

Every read joins the project and loads it onto the issue with ``contains_eager``,
so an issue's slug is there once the session closes and no read costs a query per
row, and loads the epic and milestone the issue links so their refs can be built
from the same slug. Liveness is carried by construction: an issue is live when its
own ``deleted_at`` is null, and both list reads add that its project is live too.

``get_issue`` addresses by the global id — the path a create's ``created_id`` and a
test's seed take. ``resolve_ref`` addresses by the ``<slug>-<number>`` ref, and is
the ``locate`` the action dispatch runs through.

``list_issues`` narrows and orders through an ``IssueFilter`` and an ``IssueSort``.
The filter's epic and milestone are ids, not refs: resolving a ref to the row it
names is a frontend's job, so this layer only names columns and never a ``<slug>-
<number>`` string.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Case, UnaryExpression, case, nulls_last, select
from sqlalchemy.orm import (
    InstrumentedAttribute,
    Session,
    aliased,
    contains_eager,
    selectinload,
)

from tt.domains.issue.enums import Direction, SortField, Status
from tt.domains.issue.models import Issue, issue_dependencies
from tt.domains.project.models import Project
from tt.domains.tag.models import Tag
from tt.platform.refs import parse_ref

# The eager loads every read shares. The list read adds the ordering; the two
# single-row reads below fetch one row, for which an ``ORDER BY`` would be dead
# weight.
_loaded = (
    select(Issue)
    .join(Issue.project)
    .options(
        contains_eager(Issue.project),
        selectinload(Issue.epic),
        selectinload(Issue.milestone),
        selectinload(Issue.tags),
        selectinload(Issue.comments),
        selectinload(Issue.depends_on),
        selectinload(Issue.dependents),
    )
)


@dataclass(frozen=True)
class IssueFilter:
    """What narrows a list of issues. Each axis is optional and they combine with
    AND, so an empty filter is every live issue of the project."""

    tag: str | None = None
    epic_id: int | None = None
    milestone_id: int | None = None
    statuses: frozenset[Status] | None = None


@dataclass(frozen=True)
class IssueSort:
    """How a list of issues is ordered. ``direction`` left at ``None`` takes the
    field's natural direction, so ``--sort due`` reads soonest-first without the
    caller spelling it out."""

    field: SortField = SortField.PRIORITY
    direction: Direction | None = None


# The direction a field sorts by when the caller names none: priority and how
# recently a row was touched read high-first, a date or the workflow order reads
# ascending.
_NATURAL: dict[SortField, Direction] = {
    SortField.PRIORITY: Direction.DESC,
    SortField.CREATED: Direction.ASC,
    SortField.UPDATED: Direction.DESC,
    SortField.DUE: Direction.ASC,
    SortField.STATUS: Direction.ASC,
}


def _status_rank() -> Case[int]:
    """The workflow position of an issue's status. The column stores the member
    name, whose alphabetical order (``backlog``, ``doing``, ``done`` …) is not the
    workflow order, so the sort ranks by the enum's declared order instead."""
    return case(
        *[(Issue.status == status, rank) for rank, status in enumerate(Status)],
        else_=len(Status),
    )


# The sort columns are a mapped attribute or the status ``CASE``. The ``Any`` is
# the element type, which ``InstrumentedAttribute`` is invariant in, so the four
# differently-typed columns and the int rank cannot share a narrower parameter.
def _column(field: SortField) -> InstrumentedAttribute[Any] | Case[Any]:
    match field:
        case SortField.PRIORITY:
            return Issue.priority
        case SortField.CREATED:
            return Issue.created_at
        case SortField.UPDATED:
            return Issue.updated_at
        case SortField.DUE:
            return Issue.due_date
        case SortField.STATUS:
            return _status_rank()


def _ordering(sort: IssueSort) -> list[UnaryExpression[Any]]:
    """The ``ORDER BY`` terms for a sort: the chosen key, then a stable tiebreak so
    equal keys keep a fixed order and an undated issue sorts last however the date
    axis runs."""
    direction = sort.direction or _NATURAL[sort.field]
    column = _column(sort.field)
    primary = column.desc() if direction is Direction.DESC else column.asc()
    if sort.field is SortField.DUE:
        primary = nulls_last(primary)
    return [primary, Issue.created_at.asc(), Issue.id.asc()]


def list_issues(
    db: Session,
    project_slug: str,
    filter: IssueFilter | None = None,
    sort: IssueSort | None = None,
) -> list[Issue]:
    """Live issues of a live project the filter admits, in the sort's order.
    Defaults to every issue, high priority first then oldest first."""
    filter = filter or IssueFilter()
    sort = sort or IssueSort()
    stmt = _loaded.where(
        Issue.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Project.slug == project_slug.upper(),
    ).order_by(*_ordering(sort))
    if filter.tag is not None:
        # ``any`` rather than a join so an issue wearing the tag is returned once,
        # not once per matching link row.
        stmt = stmt.where(Issue.tags.any((Tag.name == filter.tag) & Tag.deleted_at.is_(None)))
    if filter.epic_id is not None:
        stmt = stmt.where(Issue.epic_id == filter.epic_id)
    if filter.milestone_id is not None:
        stmt = stmt.where(Issue.milestone_id == filter.milestone_id)
    if filter.statuses is not None:
        stmt = stmt.where(Issue.status.in_(filter.statuses))
    return list(db.scalars(stmt))


def get_issue(db: Session, issue_id: int) -> Issue | None:
    """The one live issue of a live project by global id, or ``None``."""
    stmt = _loaded.where(
        Issue.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Issue.id == issue_id,
    )
    return db.scalars(stmt).first()


def resolve_ref(db: Session, ref: str) -> Issue | None:
    """The one live issue a ``<slug>-<number>`` ref names, or ``None`` — for a ref
    that does not parse as much as for one that names no row."""
    parsed = parse_ref(ref)
    if parsed is None:
        return None
    slug, number = parsed
    stmt = _loaded.where(
        Issue.deleted_at.is_(None),
        Project.deleted_at.is_(None),
        Project.slug == slug,
        Issue.number == number,
    )
    return db.scalars(stmt).first()


def _live_edges(db: Session) -> list[tuple[int, int]]:
    """Every dependency edge whose two endpoints are both live, as ``(depends_on_id,
    dependent_id)`` pairs. A soft-deleted issue drops out of the graph — nothing
    depends on it and it depends on nothing — so a dead intermediary cannot carry a
    cycle."""
    depends_on = aliased(Issue)
    dependent = aliased(Issue)
    stmt = (
        select(issue_dependencies.c.depends_on_id, issue_dependencies.c.dependent_id)
        .join(depends_on, depends_on.id == issue_dependencies.c.depends_on_id)
        .join(dependent, dependent.id == issue_dependencies.c.dependent_id)
        .where(depends_on.deleted_at.is_(None), dependent.deleted_at.is_(None))
    )
    return [(int(a), int(b)) for a, b in db.execute(stmt).all()]


def dependents_downstream(edges: Iterable[tuple[int, int]], start: int) -> set[int]:
    """Every node reachable from ``start`` by following depends_on→dependent edges,
    ``start`` itself excluded. Pure over the edge list so the cycle guard's
    reachability is driven directly in tests, no database in sight."""
    forward: dict[int, set[int]] = {}
    for depends_on, dependent in edges:
        forward.setdefault(depends_on, set()).add(dependent)
    reached: set[int] = set()
    _reach(forward, start, reached)
    return reached


def _reach(forward: dict[int, set[int]], node: int, reached: set[int]) -> None:
    """Accumulate into ``reached`` every node forward-reachable from ``node``.
    Recursive rather than a work-list: a dependency graph is small and shallow, and
    ``reached`` guards against revisiting a node so a cycle already in the data
    terminates the walk."""
    for nxt in forward.get(node, frozenset()):
        if nxt not in reached:
            reached.add(nxt)
            _reach(forward, nxt, reached)


def dependents_closure(db: Session, issue_id: int) -> set[int]:
    """Every live issue that transitively depends on the given one. The engine behind
    the cycle guard: making ``obj`` depend on ``dependency`` would close a loop exactly
    when ``dependency`` already sits in the set of issues that depend on ``obj``."""
    return dependents_downstream(_live_edges(db), issue_id)
