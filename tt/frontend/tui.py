"""The domain with a person on the end of it — one keyboard-driven screen.

Bare ``tt`` opens on the project whose ``path`` is an ancestor of the working
directory, and shows its issues the way lazygit shows a repo: a dense list you
never leave. Status and priority read as glyphs and colour; everything you can do
to what the cursor is on lives in a command menu that is nothing but the object's
offered actions. Nothing here names an action key to build a menu — a row is a
row because the object offered it, and its form has the fields its payload
derived; only the handful of single-key accelerators name a key, and they run
through the same offers so availability can never diverge.

The state machine — ``State``, ``on_key``, ``apply`` and the pure helpers around
them — is framework-free Python, driven in ``tests/test_tui.py`` with an in-memory
database and no terminal. ``on_key`` is pure and ``apply`` is the only half that
touches the database. ``TrackerApp`` owns nothing but the terminal: it turns a key
event into one of our key strings, runs ``on_key`` then ``apply``, and rebuilds the
widget tree from the new ``State``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, assert_never

from rich.markup import escape as _esc
from sqlalchemy import Engine
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Static

from tt.domains.issue import api as issue_api
from tt.domains.issue.schemas import IssueListItem
from tt.domains.project import api as project_api
from tt.domains.project.schemas import ProjectListItem
from tt.platform.actions import (
    REFUSALS,
    Enum,
    Field,
    Offer,
    OptionalText,
    Refused,
    Text,
)
from tt.platform.actions import (
    payload as build_payload,
)

# --- the visual vocabulary ------------------------------------------------

ACCENT = "#8B8CF0"
TEXT = "#E7E9EE"
MUTED = "#8A8F99"
FAINT = "#565B66"
HIGH = "#F0883E"
STATUS_COLOR = {"todo": "#8A8F99", "doing": "#E3B341", "done": "#3FB950"}
STATUS_GLYPH = {"todo": "○", "doing": "◐", "done": "●"}

# The status columns of the board, in the order they read left to right.
BOARD_STATUSES = ("todo", "doing", "done")

HALF = 8  # rows a half-page jump moves; the terminal height is not pure.
FAR = 10**6  # a move large enough to reach either end after clamping.


def glyph(status: str) -> tuple[str, str]:
    """The character and colour a status draws as."""
    return STATUS_GLYPH.get(status, "·"), STATUS_COLOR.get(status, MUTED)


def marker(priority: str) -> str | None:
    """The priority marker, or ``None`` when there is nothing to show."""
    return "▲" if priority == "high" else None


def issue_ref(slug: str, issue_id: int) -> str:
    """The Linear-style id a row and a menu header show, e.g. ``TT-4``."""
    return f"{slug.upper()}-{issue_id}"


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
type Layout = Literal["list", "board"]
LAYOUTS: tuple[Layout, ...] = ("list", "board")


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
    here = os.path.normpath(cwd)
    best: tuple[int, str] | None = None
    for slug, path in candidates:
        if path is None:
            continue
        root = os.path.normpath(path)
        inside = here == root or here.startswith(root + os.sep)
        if inside and (best is None or len(root) > best[0]):
            best = (len(root), slug)
    return best[1] if best is not None else None


# --- commands (the rows of every list overlay) ----------------------------

ACCELERATORS: dict[str, str] = {
    "s": "editStatus",
    "p": "editPriority",
    "e": "editTitle",
    "d": "delete",
}
_HINT = {action_key: keystroke for keystroke, action_key in ACCELERATORS.items()}


@dataclass(frozen=True)
class IssueTarget:
    id: int


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
    has no fields, through a form when it does."""

    target: Target
    key: str
    fields: list[Field]


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


def _offer_command(offer: Offer, target: Target) -> Command:
    reason = offer.state.reason if isinstance(offer.state, Refused) else None
    return Command(
        label=offer.label,
        reason=reason,
        hint=_HINT.get(offer.key),
        run=RunAction(target=target, key=offer.key, fields=offer.fields),
    )


def issue_commands(offers: list[Offer], issue_id: int) -> list[Command]:
    return [_offer_command(offer, IssueTarget(issue_id)) for offer in offers]


def project_commands(offers: list[Offer], slug: str) -> list[Command]:
    return [_offer_command(offer, ProjectTarget(slug)) for offer in offers]


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


# --- form controls --------------------------------------------------------


@dataclass(frozen=True)
class Editing:
    text: str


@dataclass(frozen=True)
class Choosing:
    values: list[str]
    index: int


type Control = Editing | Choosing


def control_value(control: Control) -> str:
    match control:
        case Editing(text=text):
            return text
        case Choosing(values=values, index=index):
            return values[index] if 0 <= index < len(values) else ""
    assert_never(control)


def _control_of(kind: Text | OptionalText | Enum) -> Control:
    match kind:
        case Text() | OptionalText():
            return Editing("")
        case Enum(values=values):
            return Choosing(list(values), 0)
    assert_never(kind)


@dataclass
class Entered:
    field: Field
    control: Control


# --- overlays -------------------------------------------------------------


@dataclass
class ListOverlay:
    """A command list floating over the screen: the issue menu, the project menu,
    the palette and the switcher are all this, told apart only by ``header`` and the
    commands they carry."""

    header: str
    commands: list[Command]
    query: str
    index: int


@dataclass
class FormOverlay:
    target: Target
    key: str
    label: str
    entries: list[Entered]
    focus: int


@dataclass
class CaptureOverlay:
    slug: str
    text: str


@dataclass(frozen=True)
class CheatsheetOverlay:
    pass


type Overlay = ListOverlay | FormOverlay | CaptureOverlay | CheatsheetOverlay | None


# --- state ----------------------------------------------------------------

BROWSING = "j/k move · x actions · / filter · ? keys · q quit"


@dataclass
class State:
    scope: Scope
    layout: Layout
    projects: list[ProjectListItem]
    issues: list[IssueListItem]
    selected_id: int | None
    overlay: Overlay
    filter: str
    filtering: bool
    status: str
    size: tuple[int, int]
    quit: bool


def _empty() -> State:
    return State(
        scope=AllScope(),
        layout="list",
        projects=[],
        issues=[],
        selected_id=None,
        overlay=None,
        filter="",
        filtering=False,
        status=BROWSING,
        size=(80, 24),
        quit=False,
    )


def visible_issues(state: State) -> list[IssueListItem]:
    """The issues the filter leaves on screen — everything when it is blank."""
    if not state.filter:
        return state.issues
    needle = state.filter.lower()
    return [i for i in state.issues if needle in i.title.lower()]


def _slug_of(state: State) -> str | None:
    return state.scope.slug if isinstance(state.scope, ProjectScope) else None


def _project(state: State, slug: str) -> ProjectListItem | None:
    return next((p for p in state.projects if p.slug == slug), None)


# --- intents --------------------------------------------------------------


@dataclass(frozen=True)
class Ignored:
    pass


@dataclass(frozen=True)
class Quit:
    pass


@dataclass(frozen=True)
class Refresh:
    pass


@dataclass(frozen=True)
class MoveRow:
    n: int


@dataclass(frozen=True)
class MoveCol:
    n: int


@dataclass(frozen=True)
class CycleLayout:
    pass


@dataclass(frozen=True)
class ShiftProject:
    n: int


@dataclass(frozen=True)
class OpenOverlay:
    kind: Literal["issue", "project", "palette", "switcher", "cheatsheet"]


@dataclass(frozen=True)
class CloseOverlay:
    pass


@dataclass(frozen=True)
class OverlayMove:
    n: int


@dataclass(frozen=True)
class OverlayPick:
    pass


@dataclass(frozen=True)
class OverlayType:
    char: str


@dataclass(frozen=True)
class OverlayRub:
    pass


@dataclass(frozen=True)
class RunAccelerator:
    key: str


@dataclass(frozen=True)
class StartCapture:
    pass


@dataclass(frozen=True)
class CaptureType:
    char: str


@dataclass(frozen=True)
class CaptureRub:
    pass


@dataclass(frozen=True)
class CaptureSubmit:
    pass


@dataclass(frozen=True)
class FormMove:
    n: int


@dataclass(frozen=True)
class FormCycle:
    n: int


@dataclass(frozen=True)
class FormInsert:
    char: str


@dataclass(frozen=True)
class FormRub:
    pass


@dataclass(frozen=True)
class FormSubmit:
    pass


@dataclass(frozen=True)
class StartFilter:
    pass


@dataclass(frozen=True)
class FilterType:
    char: str


@dataclass(frozen=True)
class FilterRub:
    pass


@dataclass(frozen=True)
class FilterClear:
    pass


type Intent = (
    Ignored
    | Quit
    | Refresh
    | MoveRow
    | MoveCol
    | CycleLayout
    | ShiftProject
    | OpenOverlay
    | CloseOverlay
    | OverlayMove
    | OverlayPick
    | OverlayType
    | OverlayRub
    | RunAccelerator
    | StartCapture
    | CaptureType
    | CaptureRub
    | CaptureSubmit
    | FormMove
    | FormCycle
    | FormInsert
    | FormRub
    | FormSubmit
    | StartFilter
    | FilterType
    | FilterRub
    | FilterClear
)


def _list_key(key: str) -> Intent:
    match key:
        case "escape":
            return CloseOverlay()
        case "down":
            return OverlayMove(1)
        case "up":
            return OverlayMove(-1)
        case "enter":
            return OverlayPick()
        case "backspace":
            return OverlayRub()
        case _ if len(key) == 1 and key.isprintable():
            return OverlayType(key)
        case _:
            return Ignored()


def _form_key(key: str) -> Intent:
    match key:
        case "escape":
            return CloseOverlay()
        case "tab" | "down":
            return FormMove(1)
        case "shift+tab" | "up":
            return FormMove(-1)
        case "enter":
            return FormSubmit()
        case "backspace":
            return FormRub()
        case "left":
            return FormCycle(-1)
        case "right":
            return FormCycle(1)
        case _ if len(key) == 1 and key.isprintable():
            return FormInsert(key)
        case _:
            return Ignored()


def _capture_key(key: str) -> Intent:
    match key:
        case "escape":
            return CloseOverlay()
        case "enter":
            return CaptureSubmit()
        case "backspace":
            return CaptureRub()
        case _ if len(key) == 1 and key.isprintable():
            return CaptureType(key)
        case _:
            return Ignored()


def _filter_key(key: str) -> Intent:
    match key:
        case "escape":
            return FilterClear()
        case "enter":
            return StartFilter()  # toggles typing off, keeps the filter
        case "backspace":
            return FilterRub()
        case _ if len(key) == 1 and key.isprintable():
            return FilterType(key)
        case _:
            return Ignored()


def _browse_key(state: State, key: str) -> Intent:
    if state.filtering:
        return _filter_key(key)
    match key:
        case "j" | "down":
            return MoveRow(1)
        case "k" | "up":
            return MoveRow(-1)
        case "J" | "ctrl+d":
            return MoveRow(HALF)
        case "K" | "ctrl+u":
            return MoveRow(-HALF)
        case "g" | "<":
            return MoveRow(-FAR)
        case "G" | ">":
            return MoveRow(FAR)
        case "h" | "left":
            return MoveCol(-1)
        case "l" | "right":
            return MoveCol(1)
        case "tab":
            return CycleLayout()
        case "[":
            return ShiftProject(-1)
        case "]":
            return ShiftProject(1)
        case "enter" | "x" | "space":
            return OpenOverlay("issue")
        case "X":
            return OpenOverlay("project")
        case ":":
            return OpenOverlay("palette")
        case "P":
            return OpenOverlay("switcher")
        case "?":
            return OpenOverlay("cheatsheet")
        case "/":
            return StartFilter()
        case "R":
            return Refresh()
        case "q":
            return Quit()
        case "n":
            return StartCapture()
        case _ if key in ACCELERATORS:
            return RunAccelerator(ACCELERATORS[key])
        case "escape" if state.filter:
            return FilterClear()
        case _:
            return Ignored()


def on_key(state: State, key: str) -> Intent:
    """Pure. Which context is up decides what a key means; nothing here decides what
    it does. Keys are the plain names ``"up"``…``"backspace"``, ``"space"``,
    ``"ctrl+d"``, ``"ctrl+u"``, ``"shift+tab"`` or the printable character itself."""
    match state.overlay:
        case ListOverlay():
            return _list_key(key)
        case FormOverlay():
            return _form_key(key)
        case CaptureOverlay():
            return _capture_key(key)
        case CheatsheetOverlay():
            return CloseOverlay()
        case None:
            return _browse_key(state, key)
    assert_never(state.overlay)


# --- loading --------------------------------------------------------------


def _load(engine: Engine, scope: Scope) -> tuple[list[ProjectListItem], list[IssueListItem]]:
    projects = project_api.project_list(engine)
    if isinstance(scope, ProjectScope):
        issues = issue_api.issue_list(engine, scope.slug)
    else:
        issues = [issue for p in projects for issue in issue_api.issue_list(engine, p.slug)]
    return projects, issues


def _reload(engine: Engine, state: State) -> State:
    """Read the scope back and reconcile the selection. Falls out to all-projects
    when the scoped project has gone, which is how deleting it navigates."""
    fallback = index_of(visible_issues(state), state.selected_id)
    projects, issues = _load(engine, state.scope)
    if isinstance(state.scope, ProjectScope) and all(p.slug != state.scope.slug for p in projects):
        state.scope = AllScope()
        projects, issues = _load(engine, state.scope)
    state.projects = projects
    state.issues = issues
    state.selected_id = surviving_id(visible_issues(state), state.selected_id, fallback)
    return state


def start(engine: Engine, scope: Scope) -> State:
    state = _empty()
    state.scope = scope
    state.status = BROWSING
    return _reload(engine, state)


# --- applying -------------------------------------------------------------


def _dispatch(engine: Engine, target: Target, key: str, payload: dict[str, object]) -> str:
    match target:
        case IssueTarget(id=issue_id):
            return issue_api.issue_action(engine, key, payload, issue_id).message
        case ProjectTarget(slug=slug):
            return project_api.project_action(engine, key, payload, slug).message
        case RootTarget():
            return project_api.project_action(engine, key, payload, None).message
    assert_never(target)


def _run_or_form(engine: Engine, state: State, run: RunAction, label: str) -> State:
    if run.fields:
        state.overlay = FormOverlay(
            target=run.target,
            key=run.key,
            label=label,
            entries=[Entered(field=f, control=_control_of(f.kind)) for f in run.fields],
            focus=0,
        )
        return state
    try:
        message = _dispatch(engine, run.target, run.key, {})
    except REFUSALS as error:
        state.status = f"{run.key}: {error}"
        return state
    state.overlay = None
    state.status = message
    return _reload(engine, state)


def _navigate(engine: Engine, state: State, nav: Navigate) -> State:
    match nav.what:
        case "switch" if nav.arg is not None:
            state.scope = ProjectScope(nav.arg)
            state.overlay = None
            state.selected_id = None
            state.status = BROWSING
            return _reload(engine, state)
        case "layout":
            state.overlay = None
            return _apply(engine, state, CycleLayout())
        case "switcher":
            return _apply(engine, state, OpenOverlay("switcher"))
        case "refresh":
            state.overlay = None
            return _reload(engine, state)
        case _:
            return state


def _selected_issue(state: State) -> IssueListItem | None:
    return next((i for i in visible_issues(state) if i.id == state.selected_id), None)


def _open_overlay(engine: Engine, state: State, kind: str) -> State:
    match kind:
        case "cheatsheet":
            state.overlay = CheatsheetOverlay()
            return state
        case "switcher":
            offers = project_api.top_level_offers()
            create = next((o for o in offers if o.key == "createProject"), None)
            state.overlay = ListOverlay(
                header="Switch project",
                commands=switcher_commands(state.projects, create),
                query="",
                index=0,
            )
            return state
        case "issue":
            return _open_issue_menu(engine, state)
        case "project":
            return _open_project_menu(engine, state)
        case "palette":
            return _open_palette(engine, state)
        case _:
            return state


def _open_issue_menu(engine: Engine, state: State) -> State:
    issue = _selected_issue(state)
    if issue is None:
        state.status = "no issue selected"
        return state
    _, offers = issue_api.issue_detail(engine, issue.id)
    state.overlay = ListOverlay(
        header=f"{issue_ref(issue.project, issue.id)} · {issue.title}",
        commands=issue_commands(offers, issue.id),
        query="",
        index=0,
    )
    return state


def _open_project_menu(engine: Engine, state: State) -> State:
    slug = _slug_of(state)
    if slug is None:
        state.status = "pick a project first (P)"
        return state
    _, offers = project_api.project_detail(engine, slug)
    state.overlay = ListOverlay(
        header=f"Project · {slug}",
        commands=project_commands(offers, slug),
        query="",
        index=0,
    )
    return state


def _open_palette(engine: Engine, state: State) -> State:
    commands: list[Command] = []
    issue = _selected_issue(state)
    if issue is not None:
        _, offers = issue_api.issue_detail(engine, issue.id)
        commands.extend(issue_commands(offers, issue.id))
    slug = _slug_of(state)
    if slug is not None:
        _, offers = project_api.project_detail(engine, slug)
        commands.extend(project_commands(offers, slug))
    commands.extend(
        [
            Command("Switch project", None, "P", Navigate("switcher")),
            Command("Toggle layout", None, "tab", Navigate("layout")),
            Command("Refresh", None, "R", Navigate("refresh")),
        ]
    )
    state.overlay = ListOverlay(header="Commands", commands=commands, query="", index=0)
    return state


def _overlay_pick(engine: Engine, state: State) -> State:
    overlay = state.overlay
    if not isinstance(overlay, ListOverlay):
        return state
    shown = visible(overlay.commands, overlay.query)
    if not shown:
        return state
    command = overlay.commands[shown[_clamp(overlay.index, 0, len(shown) - 1)]]
    if command.reason is not None:
        state.status = f"{command.label}: {command.reason}"
        return state
    match command.run:
        case RunAction():
            return _run_or_form(engine, state, command.run, command.label)
        case Navigate():
            return _navigate(engine, state, command.run)
    assert_never(command.run)


def _submit_form(engine: Engine, state: State) -> State:
    form = state.overlay
    if not isinstance(form, FormOverlay):
        return state
    values = [(entry.field, control_value(entry.control)) for entry in form.entries]
    try:
        message = _dispatch(engine, form.target, form.key, build_payload(values))
    except REFUSALS as error:
        # A refusal leaves the form up with its values intact — the fix is usually
        # right there in them.
        state.status = f"{form.key}: {error}"
        return state
    state.overlay = None
    state.status = message
    return _reload(engine, state)


def _accelerate(engine: Engine, state: State, action_key: str) -> State:
    issue = _selected_issue(state)
    if issue is None:
        state.status = "no issue selected"
        return state
    _, offers = issue_api.issue_detail(engine, issue.id)
    offer = next((o for o in offers if o.key == action_key), None)
    if offer is None:
        state.status = f"{action_key}: not available"
        return state
    if isinstance(offer.state, Refused):
        state.status = f"{action_key}: {offer.state.reason}"
        return state
    run = RunAction(IssueTarget(issue.id), action_key, offer.fields)
    return _run_or_form(engine, state, run, offer.label)


def _submit_capture(engine: Engine, state: State) -> State:
    capture = state.overlay
    if not isinstance(capture, CaptureOverlay):
        return state
    title = capture.text.strip()
    if not title:
        state.overlay = None
        return state
    try:
        response = project_api.project_action(engine, "addIssue", {"title": title}, capture.slug)
    except REFUSALS as error:
        state.status = f"addIssue: {error}"
        return state
    state.overlay = None
    state.status = response.message
    return _reload(engine, state)


def _shift_project(engine: Engine, state: State, n: int) -> State:
    if not state.projects:
        return state
    slugs = [p.slug for p in state.projects]
    current = _slug_of(state)
    base = slugs.index(current) if current in slugs else 0
    state.scope = ProjectScope(slugs[(base + n) % len(slugs)])
    state.selected_id = None
    state.status = BROWSING
    return _reload(engine, state)


def _overlay_move(state: State, n: int) -> State:
    if isinstance(state.overlay, ListOverlay):
        shown = visible(state.overlay.commands, state.overlay.query)
        if shown:
            state.overlay.index = _clamp(state.overlay.index + n, 0, len(shown) - 1)
    return state


def _overlay_type(state: State, char: str) -> State:
    if isinstance(state.overlay, ListOverlay):
        state.overlay.query += char
        state.overlay.index = 0
    return state


def _overlay_rub(state: State) -> State:
    if isinstance(state.overlay, ListOverlay):
        state.overlay.query = state.overlay.query[:-1]
        state.overlay.index = 0
    return state


def _start_capture(state: State) -> State:
    slug = _slug_of(state)
    if slug is None:
        state.status = "pick a project first (P)"
        return state
    state.overlay = CaptureOverlay(slug=slug, text="")
    state.status = "new issue — type a title, enter to add, esc to cancel"
    return state


def _capture_type(state: State, char: str) -> State:
    if isinstance(state.overlay, CaptureOverlay):
        state.overlay.text += char
    return state


def _capture_rub(state: State) -> State:
    if isinstance(state.overlay, CaptureOverlay):
        state.overlay.text = state.overlay.text[:-1]
    return state


def _form_move(state: State, n: int) -> State:
    if isinstance(state.overlay, FormOverlay) and state.overlay.entries:
        state.overlay.focus = (state.overlay.focus + n) % len(state.overlay.entries)
    return state


def _form_cycle(state: State, n: int) -> State:
    if not isinstance(state.overlay, FormOverlay):
        return state
    entry = state.overlay.entries[state.overlay.focus]
    if isinstance(entry.control, Choosing) and entry.control.values:
        values = entry.control.values
        entry.control = Choosing(values, (entry.control.index + n) % len(values))
    return state


def _form_insert(state: State, char: str) -> State:
    if not isinstance(state.overlay, FormOverlay):
        return state
    entry = state.overlay.entries[state.overlay.focus]
    if isinstance(entry.control, Editing):
        entry.control = Editing(entry.control.text + char)
    return state


def _form_rub(state: State) -> State:
    if not isinstance(state.overlay, FormOverlay):
        return state
    entry = state.overlay.entries[state.overlay.focus]
    if isinstance(entry.control, Editing):
        entry.control = Editing(entry.control.text[:-1])
    return state


def _start_filter(state: State) -> State:
    state.filtering = not state.filtering
    state.status = f"/{state.filter}" if state.filtering else BROWSING
    return state


def _filtered_first(state: State) -> int | None:
    return _first_id(columns(visible_issues(state), state.layout))


def _apply(engine: Engine, state: State, intent: Intent) -> State:
    match intent:
        case Ignored():
            return state
        case Quit():
            state.quit = True
            return state
        case Refresh():
            return _reload(engine, state)
        case MoveRow(n=n):
            state.selected_id = move_selection(
                visible_issues(state), state.layout, state.selected_id, n, 0
            )
            return state
        case MoveCol(n=n):
            state.selected_id = move_selection(
                visible_issues(state), state.layout, state.selected_id, 0, n
            )
            return state
        case CycleLayout():
            state.layout = next_layout(state.layout, state.size[0], state.size[1])
            return state
        case ShiftProject(n=n):
            return _shift_project(engine, state, n)
        case OpenOverlay(kind=kind):
            return _open_overlay(engine, state, kind)
        case CloseOverlay():
            state.overlay = None
            state.status = BROWSING
            return state
        case OverlayMove(n=n):
            return _overlay_move(state, n)
        case OverlayPick():
            return _overlay_pick(engine, state)
        case OverlayType(char=char):
            return _overlay_type(state, char)
        case OverlayRub():
            return _overlay_rub(state)
        case RunAccelerator(key=action_key):
            return _accelerate(engine, state, action_key)
        case StartCapture():
            return _start_capture(state)
        case CaptureType(char=char):
            return _capture_type(state, char)
        case CaptureRub():
            return _capture_rub(state)
        case CaptureSubmit():
            return _submit_capture(engine, state)
        case FormMove(n=n):
            return _form_move(state, n)
        case FormCycle(n=n):
            return _form_cycle(state, n)
        case FormInsert(char=char):
            return _form_insert(state, char)
        case FormRub():
            return _form_rub(state)
        case FormSubmit():
            return _submit_form(engine, state)
        case StartFilter():
            return _start_filter(state)
        case FilterType(char=char):
            state.filter += char
            state.selected_id = _filtered_first(state)
            return state
        case FilterRub():
            state.filter = state.filter[:-1]
            state.selected_id = _filtered_first(state)
            return state
        case FilterClear():
            state.filter = ""
            state.filtering = False
            state.status = BROWSING
            state.selected_id = _filtered_first(state)
            return state
    assert_never(intent)


def apply(engine: Engine, state: State, intent: Intent) -> State:
    """The half with the database in it."""
    return _apply(engine, state, intent)


# --- rendering ------------------------------------------------------------


def _topbar(state: State) -> Static:
    slug = _slug_of(state)
    if slug is None:
        name = f"[{ACCENT}]◆[/] [b {TEXT}]all projects[/]"
    else:
        project = _project(state, slug)
        title = f" [{MUTED}]{_esc(project.title)}[/]" if project and project.title else ""
        name = f"[{ACCENT}]◆[/] [b {TEXT}]{_esc(slug)}[/]{title}"
    count = f"[{MUTED}]{len(visible_issues(state))} issues[/]"
    tabs = " ".join(
        f"[b {ACCENT}]{name_}[/]" if name_ == state.layout else f"[{FAINT}]{name_}[/]"
        for name_ in LAYOUTS
    )
    return Static(f"  {name}   {count}    {tabs}", id="topbar")


def _row(issue: IssueListItem, selected: bool) -> Horizontal:
    char, color = glyph(issue.status)
    bar = f"[{ACCENT}]▌[/]" if selected else " "
    title_color = TEXT if issue.status != "done" else MUTED
    pri = marker(issue.priority)
    pri_markup = f"[{HIGH}]{pri} high[/]" if pri else ""
    return Horizontal(
        Static(bar, classes="c-bar"),
        Static(f"[{color}]{char}[/]", classes="c-glyph"),
        Static(f"[{MUTED}]{_esc(issue_ref(issue.project, issue.id))}[/]", classes="c-id"),
        Static(f"[{title_color}]{_esc(issue.title)}[/]", classes="c-title"),
        Static(pri_markup, classes="c-pri"),
        classes="row sel" if selected else "row",
    )


def _card(issue: IssueListItem, selected: bool) -> Static:
    pri = marker(issue.priority)
    tag = f"[{HIGH}]{pri}[/] " if pri else ""
    ref = f"[{FAINT}]{_esc(issue_ref(issue.project, issue.id))}[/]"
    body = f"{tag}{ref}\n[{TEXT}]{_esc(issue.title)}[/]"
    return Static(body, classes="card sel" if selected else "card")


def _board(state: State, shown: list[IssueListItem]) -> Horizontal:
    panels: list[Vertical] = []
    for col in columns(shown, "board"):
        char, color = glyph(col.status or "")
        head = Static(
            f"[{color}]{char}[/] [{MUTED}]{col.title}[/]  [{FAINT}]{len(col.issues)}[/]",
            classes="col-h",
        )
        cards = [_card(issue, issue.id == state.selected_id) for issue in col.issues]
        panels.append(Vertical(head, *cards, classes="col"))
    return Horizontal(*panels, id="body")


def _body(state: State) -> VerticalScroll | Horizontal:
    shown = visible_issues(state)
    if not shown:
        empty = "no issues — n to add one" if _slug_of(state) else "no issues"
        return VerticalScroll(Static(f"  [{FAINT}]{empty}[/]"), id="body")
    if state.layout == "board":
        return _board(state, shown)
    rows = [_row(issue, issue.id == state.selected_id) for issue in shown]
    return VerticalScroll(*rows, id="body")


def _footer(state: State) -> Static:
    if state.filtering:
        return Static(f"  [{ACCENT}]/[/]{_esc(state.filter)}[{FAINT}]▎[/]", id="footer")
    return Static(f"  [{FAINT}]{_esc(state.status)}[/]", id="footer")


def _command_row(command: Command, selected: bool) -> Horizontal:
    if command.reason is not None:
        name = f"[{FAINT}]{_esc(command.label)}[/] [{FAINT} italic]— {_esc(command.reason)}[/]"
    else:
        name = f"[{TEXT}]{_esc(command.label)}[/]"
    hint = f"[{FAINT}]{command.hint}[/]" if command.hint else ""
    return Horizontal(
        Static(name, classes="a-name"),
        Static(hint, classes="a-hint"),
        classes="act hot" if selected else "act",
    )


def _list_overlay_widget(overlay: ListOverlay) -> Container:
    shown = visible(overlay.commands, overlay.query)
    typed = f"{_esc(overlay.query)}[{ACCENT}]▎[/]"
    search = typed if overlay.query else f"[{FAINT}]Search…[/]"
    rows: list[Static | Horizontal] = [
        Static(f"[{MUTED}]{_esc(overlay.header)}[/]", id="ov-header"),
        Static(search, id="ov-search"),
    ]
    cursor = _clamp(overlay.index, 0, len(shown) - 1) if shown else 0
    for slot, command_index in enumerate(shown):
        rows.append(_command_row(overlay.commands[command_index], selected=slot == cursor))
    if not shown:
        rows.append(Static(f"  [{FAINT}]no matches[/]"))
    return Container(Vertical(*rows, id="menu"), id="overlay")


def _form_overlay_widget(form: FormOverlay) -> Container:
    rows: list[Static] = [Static(f"[b {TEXT}]{_esc(form.label)}[/]", id="ov-header")]
    for i, entry in enumerate(form.entries):
        pointer = f"[{ACCENT}]▌[/]" if i == form.focus else " "
        label = _esc(entry.field.description or entry.field.name)
        mark = f"[{HIGH}]*[/]" if entry.field.required else ""
        value = _esc(control_value(entry.control)) or f"[{FAINT}]·[/]"
        rows.append(
            Static(
                f"{pointer} [{MUTED}]{_esc(entry.field.name)}{mark}[/]  "
                f"[{TEXT}]{value}[/]   [{FAINT}]{label}[/]",
                classes="f-row",
            )
        )
    rows.append(
        Static(f"[{FAINT}]tab move · ←/→ choose · enter submit · esc cancel[/]", id="ov-foot")
    )
    return Container(Vertical(*rows, id="menu"), id="overlay")


def _capture_overlay_widget(capture: CaptureOverlay) -> Container:
    return Container(
        Vertical(
            Static(f"[{MUTED}]New issue in [b {TEXT}]{_esc(capture.slug)}[/][/]", id="ov-header"),
            Static(f"[{TEXT}]{_esc(capture.text)}[/][{ACCENT}]▎[/]", id="ov-search"),
            id="menu",
        ),
        id="overlay",
    )


_CHEATS: list[tuple[str, str]] = [
    ("j / k", "move"),
    ("J / K", "half page"),
    ("g / G", "top / bottom"),
    ("h / l", "columns (board)"),
    ("tab", "cycle layout"),
    ("[ / ]", "prev / next project"),
    ("x / space", "issue actions"),
    ("X", "project actions"),
    (":", "command palette"),
    ("s / p / e / d", "status / priority / title / delete"),
    ("n", "new issue"),
    ("/", "filter"),
    ("P", "switch project"),
    ("R", "refresh"),
    ("q", "quit"),
]


def _cheatsheet_widget() -> Container:
    rows: list[Static] = [Static(f"[b {TEXT}]Keys[/]", id="ov-header")]
    rows.extend(
        Static(f"  [{ACCENT}]{keys:<14}[/] [{MUTED}]{what}[/]", classes="cheat")
        for keys, what in _CHEATS
    )
    rows.append(Static(f"  [{FAINT}]any key to close[/]", classes="cheat"))
    return Container(Vertical(*rows, id="menu"), id="overlay")


def _overlay_widget(state: State) -> Container | None:
    match state.overlay:
        case None:
            return None
        case ListOverlay():
            return _list_overlay_widget(state.overlay)
        case FormOverlay():
            return _form_overlay_widget(state.overlay)
        case CaptureOverlay():
            return _capture_overlay_widget(state.overlay)
        case CheatsheetOverlay():
            return _cheatsheet_widget()
    assert_never(state.overlay)


# --- the terminal ---------------------------------------------------------

_NAMED_KEYS = frozenset(
    {
        "up",
        "down",
        "left",
        "right",
        "enter",
        "escape",
        "tab",
        "backspace",
        "space",
        "shift+tab",
        "ctrl+d",
        "ctrl+u",
    }
)


def _key_string(event: events.Key) -> str | None:
    if event.key in _NAMED_KEYS:
        return event.key
    character = event.character
    if character is not None and character.isprintable():
        return character
    return None


class TrackerApp(App[None]):
    """The terminal the view is rendered to, and nothing else: it holds a ``State``,
    maps each keypress through ``on_key`` then ``apply``, and rebuilds the tree from
    the result. No domain logic lives here."""

    CSS = """
    Screen { background: #0B0C0F; layers: base overlay; }
    #screen { layer: base; width: 100%; height: 100%; }
    #topbar { height: 1; padding: 0 1; margin: 1 0 0 0; }
    #body { height: 1fr; padding: 1 0; }
    #footer { dock: bottom; height: 1; border-top: solid #23262E; padding: 0 1; }

    .row { height: 1; }
    .row.sel { background: #8B8CF0 14%; }
    .c-bar { width: 2; }
    .c-glyph { width: 2; }
    .c-id { width: 8; }
    .c-title { width: 1fr; }
    .c-pri { width: 8; text-align: right; padding-right: 1; }

    .col { width: 1fr; height: auto; padding: 0 1; }
    .col-h { padding-bottom: 1; }
    .card { padding: 0 1; margin-bottom: 1; height: auto; }
    .card.sel { background: #8B8CF0 14%; }

    #overlay {
        layer: overlay;
        width: 100%;
        height: 100%;
        align: center top;
        background: #0B0C0F 55%;
    }
    #menu {
        width: 56;
        height: auto;
        margin-top: 3;
        padding: 1 1;
        background: #1B1E25;
        border: round #8B8CF0;
    }
    #ov-header { padding: 0 1; height: 1; }
    #ov-search { padding: 0 1; height: 1; color: #565B66; }
    #ov-foot { padding: 1 1 0 1; }
    .act { height: 1; padding: 0 1; }
    .act.hot { background: #8B8CF0 14%; }
    .a-name { width: 1fr; }
    .a-hint { width: 6; text-align: right; }
    .f-row { height: 1; padding: 0 1; }
    .cheat { height: 1; padding: 0 1; }
    """

    def __init__(self, engine: Engine, state: State) -> None:
        super().__init__()
        self._engine = engine
        self._state = state

    def compose(self) -> ComposeResult:
        state = self._state
        with Container(id="screen"):
            yield _topbar(state)
            yield _body(state)
            yield _footer(state)
        overlay = _overlay_widget(state)
        if overlay is not None:
            yield overlay

    def on_resize(self, event: events.Resize) -> None:
        self._state.size = (event.size.width, event.size.height)

    async def on_key(self, event: events.Key) -> None:
        key = _key_string(event)
        if key is None:
            return
        event.stop()
        self._state = apply(self._engine, self._state, on_key(self._state, key))
        if self._state.quit:
            self.exit()
            return
        await self.recompose()


def _initial_scope(engine: Engine) -> Scope:
    projects = project_api.project_list(engine)
    slug = match_path([(p.slug, p.path) for p in projects], os.getcwd())
    return ProjectScope(slug) if slug is not None else AllScope()


def run(engine: Engine) -> None:
    TrackerApp(engine, start(engine, _initial_scope(engine))).run()
