"""Framework-free helpers over the domain — the pure core of the TUI.

Everything here is a pure function or a plain dataclass; nothing imports textual,
so it is driven directly in tests with no terminal. The screens in the package
above turn these values into widgets — the glyphs and colours a status reads as,
the columns a layout groups issues into, where the cursor lands after a move, and
the commands a menu draws from an object's offers. This module never touches a
widget or the database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

from tt.domains.issue.schemas import IssueListItem
from tt.domains.project.schemas import ProjectListItem
from tt.platform.actions import (
    Field,
    Offer,
    Refused,
)
from tt.platform.config import LAYOUTS, Layout

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


def marker(priority: str) -> str | None:
    """The priority marker, or ``None`` when there is nothing to show."""
    return "▲" if priority == "high" else None


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


# --- scope and layout -----------------------------------------------------


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


def fits(layout: Layout, width: int, height: int) -> bool:
    """Whether a layout has the room it needs. ``list`` always does; ``board`` wants
    width for three columns."""
    if layout == "board":
        return width >= 90 and height >= 10
    return True


def next_layout(layout: Layout, width: int, height: int) -> Layout:
    """The next layout in the cycle that fits, or the one we are on if none other
    does."""
    order = list(LAYOUTS)
    start = order.index(layout)
    for step in range(1, len(order) + 1):
        candidate = order[(start + step) % len(order)]
        if fits(candidate, width, height):
            return candidate
    return layout


# --- columns and selection ------------------------------------------------


@dataclass(frozen=True)
class Column:
    """One column of the body: a title, the status it groups (``None`` for the flat
    list), and the issues in it."""

    title: str
    status: str | None
    issues: list[IssueListItem]


def columns(issues: list[IssueListItem], layout: Layout) -> list[Column]:
    """The body as columns: one for ``list``, three by status for ``board``."""
    if layout == "board":
        return [
            Column(title=status, status=status, issues=[i for i in issues if i.status == status])
            for status in BOARD_STATUSES
        ]
    return [Column(title="", status=None, issues=list(issues))]


def _locate(cols: list[Column], selected_id: int | None) -> tuple[int, int] | None:
    for ci, col in enumerate(cols):
        for ri, issue in enumerate(col.issues):
            if issue.id == selected_id:
                return ci, ri
    return None


def _first_id(cols: list[Column]) -> int | None:
    for col in cols:
        if col.issues:
            return col.issues[0].id
    return None


def move_selection(
    issues: list[IssueListItem], layout: Layout, selected_id: int | None, dr: int, dc: int
) -> int | None:
    """The id the cursor lands on after moving ``dr`` rows and ``dc`` columns. Pure:
    the whole of list and board navigation, so a one-column layout is just the case
    where ``dc`` has nowhere to go."""
    cols = columns(issues, layout)
    here = _locate(cols, selected_id)
    if here is None:
        return _first_id(cols)
    ci, ri = here
    if dc != 0:
        step = 1 if dc > 0 else -1
        stop = len(cols) if step > 0 else -1
        for probe in range(ci + step, stop, step):
            if cols[probe].issues:
                ci = probe
                break
    column = cols[ci].issues
    if not column:
        return selected_id
    return column[_clamp(ri + dr, 0, len(column) - 1)].id


def surviving_id(
    issues: list[IssueListItem], selected_id: int | None, fallback_index: int
) -> int | None:
    """The selection after a reload. Keep the same issue if it is still here; else
    take whatever now sits where it was, so deleting the selected row lands on its
    neighbour rather than jumping to the top."""
    if not issues:
        return None
    if any(i.id == selected_id for i in issues):
        return selected_id
    return issues[_clamp(fallback_index, 0, len(issues) - 1)].id


def index_of(issues: list[IssueListItem], selected_id: int | None) -> int:
    """The flat position of the selected id, or ``0`` when it is gone."""
    for i, issue in enumerate(issues):
        if issue.id == selected_id:
            return i
    return 0


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
class RootTarget:
    """A creator with no object to address — ``createProject``."""


type Target = IssueTarget | ProjectTarget | RootTarget


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
