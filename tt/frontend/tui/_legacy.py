"""The domain with a person on the end of it — one keyboard-driven screen.

Bare ``tt`` opens on the project whose ``path`` is an ancestor of the working
directory, and shows its issues the way lazygit shows a repo: a dense list you
never leave. Status and priority read as glyphs and colour; everything you can do
to what the cursor is on lives in a command menu that is nothing but the object's
offered actions. Nothing here names an action key to build a menu — a row is a
row because the object offered it, and its form has the fields its payload
derived; only the single-key ``d`` (delete) accelerator names a key, and it runs
through the same offers so availability can never diverge. The menu's ``Edit``
opens a form seeded from the object it addresses, so an edit begins from the
current values rather than blank.

The state machine — ``State``, ``on_key``, ``apply`` and the pure helpers around
them — is framework-free Python, driven in ``tests/test_tui.py`` with an in-memory
database and no terminal. ``on_key`` is pure and ``apply`` is the only half that
touches the database. ``TrackerApp`` owns nothing but the terminal: it turns a key
event into one of our key strings, runs ``on_key`` then ``apply``, and rebuilds the
widget tree from the new ``State``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Literal, assert_never

from rich.markup import escape as _esc
from sqlalchemy import Engine
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.theme import Theme
from textual.widgets import Static

from tt.domains.issue import api as issue_api
from tt.domains.issue.schemas import IssueListItem
from tt.domains.project import api as project_api
from tt.domains.project.schemas import ProjectListItem
from tt.frontend.tui.domainview import (
    ACCELERATORS,
    FAR,
    HALF,
    LAYOUTS,
    AllScope,
    Command,
    IssueTarget,
    Layout,
    Navigate,
    ProjectScope,
    ProjectTarget,
    RootTarget,
    RunAction,
    Scope,
    Target,
    _clamp,
    _first_id,
    columns,
    glyph,
    index_of,
    issue_commands,
    issue_ref,
    marker,
    match_path,
    move_selection,
    next_layout,
    project_commands,
    surviving_id,
    switcher_commands,
    visible,
)
from tt.platform import config
from tt.platform.actions import (
    REFUSALS,
    Enum,
    Field,
    OptionalText,
    Refused,
    Text,
)
from tt.platform.actions import (
    payload as build_payload,
)
from tt.platform.config import Prefs, ThemeName

# --- the visual vocabulary ------------------------------------------------

# The two themes, the one source of truth for every colour. Standard tokens carry
# the semantic colours; the ``variables`` block pins the exact hex the app has
# always drawn and adds ``priority-high``, the one token with no standard name.
# These are the only hex literals in the file — the CSS and the markup both read
# their colours back out of the active theme (via ``$`` variables and ``Palette``
# respectively), never repeating them.
#
# ``ansi=True`` is what lets the terminal's own background show through: it disables
# Textual's truecolor filter so an ``ansi_default`` background is emitted as the
# terminal's default rather than resolved to opaque black, while the truecolor hex
# above still paints on top. It also switches on the ``:ansi`` pseudo-class, whose
# stock ``Screen``/``App`` CSS references ``$ansi-background``/``$ansi-foreground`` —
# variables only the built-in ANSI themes define, so we must supply them here or CSS
# parsing fails. They surface only as inline-mode border colours, which this
# fullscreen app never draws; the foreground/background hex is the honest value.
_ANSI_VARS_DARK = {"ansi-background": "#0B0C0F", "ansi-foreground": "#E7E9EE"}
_ANSI_VARS_LIGHT = {"ansi-background": "#FFFFFF", "ansi-foreground": "#1A1D23"}

TT_DARK = Theme(
    name=ThemeName.DARK.value,
    dark=True,
    ansi=True,
    primary="#8B8CF0",
    foreground="#E7E9EE",
    warning="#E3B341",
    success="#3FB950",
    background="#0B0C0F",
    surface="#1B1E25",
    panel="#1B1E25",
    variables={
        "text-muted": "#8A8F99",
        "text-disabled": "#565B66",
        "border": "#23262E",
        "priority-high": "#F0883E",
        **_ANSI_VARS_DARK,
    },
)
TT_LIGHT = Theme(
    name=ThemeName.LIGHT.value,
    dark=False,
    ansi=True,
    primary="#5457D6",
    foreground="#1A1D23",
    warning="#B7791F",
    success="#1F883D",
    background="#FFFFFF",
    surface="#F0F1F4",
    panel="#F0F1F4",
    variables={
        "text-muted": "#5A606B",
        "text-disabled": "#9AA0AB",
        "border": "#D5D8DE",
        "priority-high": "#B5540B",
        **_ANSI_VARS_LIGHT,
    },
)


@dataclass(frozen=True)
class Palette:
    """The concrete hex the markup sites interpolate. ``$theme-variables`` resolve
    in the ``CSS`` block but not inside ``Static`` content markup, so a render helper
    is handed a palette of already-resolved colours rather than variable names. Built
    once per recompose from the active theme, and passed in explicitly so the render
    helpers stay pure functions of their inputs."""

    accent: str
    text: str
    muted: str
    faint: str
    high: str
    status: dict[str, str]

    @classmethod
    def of(cls, app: App[None]) -> Palette:
        v = app.get_css_variables()
        return cls(
            accent=v["primary"],
            text=v["foreground"],
            muted=v["text-muted"],
            faint=v["text-disabled"],
            high=v["priority-high"],
            status={"todo": v["text-muted"], "doing": v["warning"], "done": v["success"]},
        )


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


def _control_from(kind: Text | OptionalText | Enum, value: Any) -> Control:
    """A control pre-filled from the target's current value: a text box holding the
    string (a null becomes blank), a selector positioned on the current member."""
    match kind:
        case Text() | OptionalText():
            return Editing(str(value or ""))
        case Enum(values=values):
            options = list(values)
            return Choosing(options, options.index(value))
    assert_never(kind)


def _entry_of(field: Field, seed: dict[str, Any] | None) -> Entered:
    if seed is None:
        return Entered(field=field, control=_control_of(field.kind))
    return Entered(field=field, control=_control_from(field.kind, seed[field.name]))


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


@dataclass(frozen=True)
class Setting:
    """One row of the settings modal: a stable ``key``, the label it shows, the
    options it cycles, and which one is selected."""

    key: str
    name: str
    options: tuple[str, ...]
    index: int


@dataclass
class SettingsOverlay:
    settings: list[Setting]
    focus: int


type Overlay = (
    ListOverlay | FormOverlay | CaptureOverlay | CheatsheetOverlay | SettingsOverlay | None
)


# The theme options the modal offers, each paired with the name it persists as.
_THEME_OPTIONS: tuple[tuple[str, ThemeName], ...] = (
    ("Dark", ThemeName.DARK),
    ("Light", ThemeName.LIGHT),
)


def _settings_overlay(theme: ThemeName) -> SettingsOverlay:
    """The modal seeded from the current preference — one setting today, structured
    as a list so a second is additive."""
    index = next(i for i, (_, name) in enumerate(_THEME_OPTIONS) if name == theme)
    setting = Setting(
        key="theme",
        name="Theme",
        options=tuple(label for label, _ in _THEME_OPTIONS),
        index=index,
    )
    return SettingsOverlay(settings=[setting], focus=0)


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
    theme: ThemeName


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
        theme=ThemeName.DARK,
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
    kind: Literal["issue", "project", "palette", "switcher", "cheatsheet", "settings"]


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


@dataclass(frozen=True)
class SettingsMove:
    n: int


@dataclass(frozen=True)
class SettingsCycle:
    n: int


@dataclass(frozen=True)
class SettingsCommit:
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
    | SettingsMove
    | SettingsCycle
    | SettingsCommit
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


def _settings_key(key: str) -> Intent:
    match key:
        case "escape":
            return CloseOverlay()
        case "up":
            return SettingsMove(-1)
        case "down":
            return SettingsMove(1)
        case "left":
            return SettingsCycle(-1)
        case "right":
            return SettingsCycle(1)
        case "enter":
            return SettingsCommit()
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
        case "enter" | "x" | " ":
            return OpenOverlay("issue")
        case "X":
            return OpenOverlay("project")
        case ":":
            return OpenOverlay("palette")
        case "P":
            return OpenOverlay("switcher")
        case "?":
            return OpenOverlay("cheatsheet")
        case ",":
            return OpenOverlay("settings")
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
        case SettingsOverlay():
            return _settings_key(key)
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
            entries=[_entry_of(f, run.seed) for f in run.fields],
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
        case "settings":
            state.overlay = _settings_overlay(state.theme)
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
    detail, offers = issue_api.issue_detail(engine, issue.id)
    state.overlay = ListOverlay(
        header=f"{issue_ref(issue.project, issue.id)} · {issue.title}",
        commands=issue_commands(offers, issue.id, detail.model_dump(mode="json")),
        query="",
        index=0,
    )
    return state


def _open_project_menu(engine: Engine, state: State) -> State:
    slug = _slug_of(state)
    if slug is None:
        state.status = "pick a project first (P)"
        return state
    detail, offers = project_api.project_detail(engine, slug)
    state.overlay = ListOverlay(
        header=f"Project · {slug}",
        commands=project_commands(offers, slug, detail.model_dump(mode="json")),
        query="",
        index=0,
    )
    return state


def _open_palette(engine: Engine, state: State) -> State:
    commands: list[Command] = []
    issue = _selected_issue(state)
    if issue is not None:
        detail, offers = issue_api.issue_detail(engine, issue.id)
        commands.extend(issue_commands(offers, issue.id, detail.model_dump(mode="json")))
    slug = _slug_of(state)
    if slug is not None:
        project_detail, offers = project_api.project_detail(engine, slug)
        commands.extend(project_commands(offers, slug, project_detail.model_dump(mode="json")))
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
    # All-projects is a stop on the cycle, so ``]`` off the last project lands
    # back on it rather than skipping straight to the first.
    scopes: list[Scope] = [AllScope(), *(ProjectScope(p.slug) for p in state.projects)]
    base = next((i for i, s in enumerate(scopes) if s == state.scope), 0)
    state.scope = scopes[(base + n) % len(scopes)]
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


def _settings_move(state: State, n: int) -> State:
    if isinstance(state.overlay, SettingsOverlay) and state.overlay.settings:
        state.overlay.focus = (state.overlay.focus + n) % len(state.overlay.settings)
    return state


def _settings_cycle(state: State, n: int) -> State:
    if not isinstance(state.overlay, SettingsOverlay):
        return state
    overlay = state.overlay
    setting = overlay.settings[overlay.focus]
    if setting.options:
        index = (setting.index + n) % len(setting.options)
        overlay.settings[overlay.focus] = replace(setting, index=index)
    return state


def _settings_commit(state: State) -> State:
    overlay = state.overlay
    if not isinstance(overlay, SettingsOverlay):
        return state
    state.theme = next(
        (_THEME_OPTIONS[s.index][1] for s in overlay.settings if s.key == "theme"),
        state.theme,
    )
    state.overlay = None
    state.status = BROWSING
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
        case SettingsMove(n=n):
            return _settings_move(state, n)
        case SettingsCycle(n=n):
            return _settings_cycle(state, n)
        case SettingsCommit():
            return _settings_commit(state)
    assert_never(intent)


def apply(engine: Engine, state: State, intent: Intent) -> State:
    """The half with the database in it."""
    return _apply(engine, state, intent)


# --- rendering ------------------------------------------------------------


def _topbar(state: State, palette: Palette) -> Static:
    slug = _slug_of(state)
    if slug is None:
        name = f"[{palette.accent}]◆[/] [b {palette.text}]all projects[/]"
    else:
        project = _project(state, slug)
        title = f" [{palette.muted}]{_esc(project.title)}[/]" if project and project.title else ""
        name = f"[{palette.accent}]◆[/] [b {palette.text}]{_esc(slug)}[/]{title}"
    count = f"[{palette.muted}]{len(visible_issues(state))} issues[/]"
    tabs = " ".join(
        f"[b {palette.accent}]{name_}[/]"
        if name_ == state.layout
        else f"[{palette.faint}]{name_}[/]"
        for name_ in LAYOUTS
    )
    return Static(f"  {name}   {count}    {tabs}", id="topbar")


def _row(issue: IssueListItem, selected: bool, palette: Palette) -> Horizontal:
    char = glyph(issue.status)
    color = palette.status.get(issue.status, palette.muted)
    bar = f"[{palette.accent}]▌[/]" if selected else " "
    title_color = palette.text if issue.status != "done" else palette.muted
    pri = marker(issue.priority)
    pri_markup = f"[{palette.high}]{pri} high[/]" if pri else ""
    return Horizontal(
        Static(bar, classes="c-bar"),
        Static(f"[{color}]{char}[/]", classes="c-glyph"),
        Static(f"[{palette.muted}]{_esc(issue_ref(issue.project, issue.id))}[/]", classes="c-id"),
        Static(f"[{title_color}]{_esc(issue.title)}[/]", classes="c-title"),
        Static(pri_markup, classes="c-pri"),
        classes="row sel" if selected else "row",
    )


def _card(issue: IssueListItem, selected: bool, palette: Palette) -> Static:
    pri = marker(issue.priority)
    tag = f"[{palette.high}]{pri}[/] " if pri else ""
    ref = f"[{palette.faint}]{_esc(issue_ref(issue.project, issue.id))}[/]"
    body = f"{tag}{ref}\n[{palette.text}]{_esc(issue.title)}[/]"
    return Static(body, classes="card sel" if selected else "card")


def _board(state: State, shown: list[IssueListItem], palette: Palette) -> Horizontal:
    panels: list[Vertical] = []
    for col in columns(shown, "board"):
        char = glyph(col.status or "")
        color = palette.status.get(col.status or "", palette.muted)
        count = f"[{palette.faint}]{len(col.issues)}[/]"
        head = Static(
            f"[{color}]{char}[/] [{palette.muted}]{col.title}[/]  {count}",
            classes="col-h",
        )
        cards = [_card(issue, issue.id == state.selected_id, palette) for issue in col.issues]
        panels.append(Vertical(head, *cards, classes="col"))
    return Horizontal(*panels, id="body")


def _body(state: State, palette: Palette) -> VerticalScroll | Horizontal:
    shown = visible_issues(state)
    if not shown:
        empty = "no issues — n to add one" if _slug_of(state) else "no issues"
        return VerticalScroll(Static(f"  [{palette.faint}]{empty}[/]"), id="body")
    if state.layout == "board":
        return _board(state, shown, palette)
    rows = [_row(issue, issue.id == state.selected_id, palette) for issue in shown]
    return VerticalScroll(*rows, id="body")


def _footer(state: State, palette: Palette) -> Static:
    if state.filtering:
        return Static(
            f"  [{palette.accent}]/[/]{_esc(state.filter)}[{palette.faint}]▎[/]", id="footer"
        )
    return Static(f"  [{palette.faint}]{_esc(state.status)}[/]", id="footer")


def _command_row(command: Command, selected: bool, palette: Palette) -> Horizontal:
    if command.reason is not None:
        name = (
            f"[{palette.faint}]{_esc(command.label)}[/] "
            f"[{palette.faint} italic]— {_esc(command.reason)}[/]"
        )
    else:
        name = f"[{palette.text}]{_esc(command.label)}[/]"
    hint = f"[{palette.faint}]{command.hint}[/]" if command.hint else ""
    return Horizontal(
        Static(name, classes="a-name"),
        Static(hint, classes="a-hint"),
        classes="act hot" if selected else "act",
    )


def _list_overlay_widget(overlay: ListOverlay, palette: Palette) -> Container:
    shown = visible(overlay.commands, overlay.query)
    typed = f"{_esc(overlay.query)}[{palette.accent}]▎[/]"
    search = typed if overlay.query else f"[{palette.faint}]Search…[/]"
    rows: list[Static | Horizontal] = [
        Static(f"[{palette.muted}]{_esc(overlay.header)}[/]", id="ov-header"),
        Static(search, id="ov-search"),
    ]
    cursor = _clamp(overlay.index, 0, len(shown) - 1) if shown else 0
    for slot, command_index in enumerate(shown):
        rows.append(_command_row(overlay.commands[command_index], slot == cursor, palette))
    if not shown:
        rows.append(Static(f"  [{palette.faint}]no matches[/]"))
    return Container(Vertical(*rows, id="menu"), id="overlay")


def _form_overlay_widget(form: FormOverlay, palette: Palette) -> Container:
    rows: list[Static] = [Static(f"[b {palette.text}]{_esc(form.label)}[/]", id="ov-header")]
    for i, entry in enumerate(form.entries):
        focused = i == form.focus
        mark = f" [{palette.high}]*[/]" if entry.field.required else ""
        desc = (
            f"  [{palette.faint}]{_esc(entry.field.description)}[/]"
            if entry.field.description
            else ""
        )
        rows.append(
            Static(f"[{palette.muted}]{_esc(entry.field.name)}[/]{mark}{desc}", classes="f-label")
        )
        rows.append(_form_input_widget(entry.control, focused, palette))
    rows.append(
        Static(
            f"[{palette.faint}]tab move · ←/→ choose · enter submit · esc cancel[/]", id="ov-foot"
        )
    )
    return Container(Vertical(*rows, id="menu"), id="overlay")


def _form_input_widget(control: Control, focused: bool, palette: Palette) -> Static:
    match control:
        case Editing(text=text):
            value = _esc(text)
            if focused:
                return Static(
                    f"[{palette.text}]{value}[/][{palette.accent}]▎[/]", classes="f-box focus"
                )
            return Static(value or f"[{palette.faint}]·[/]", classes="f-val")
        case Choosing(values=values, index=index):
            options = _option_strip(values, index, palette)
            classes = "f-enum focus" if focused else "f-enum"
            return Static(options, classes=classes)
    assert_never(control)


def _option_strip(options: tuple[str, ...] | list[str], index: int, palette: Palette) -> str:
    """A row of options with the selected one reversed — the look a form enum and a
    settings row both draw."""
    return " ".join(
        f"[reverse {palette.accent}] {_esc(option)} [/]"
        if slot == index
        else f"[{palette.faint}]{_esc(option)}[/]"
        for slot, option in enumerate(options)
    )


def _capture_overlay_widget(capture: CaptureOverlay, palette: Palette) -> Container:
    return Container(
        Vertical(
            Static(
                f"[{palette.muted}]New issue in [b {palette.text}]{_esc(capture.slug)}[/][/]",
                id="ov-header",
            ),
            Static(
                f"[{palette.text}]{_esc(capture.text)}[/][{palette.accent}]▎[/]", id="ov-search"
            ),
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
    ("d", "delete issue"),
    ("n", "new issue"),
    ("/", "filter"),
    (",", "settings"),
    ("P", "switch project"),
    ("R", "refresh"),
    ("q", "quit"),
]


def _cheatsheet_widget(palette: Palette) -> Container:
    rows: list[Static] = [Static(f"[b {palette.text}]Keys[/]", id="ov-header")]
    rows.extend(
        Static(f"  [{palette.accent}]{keys:<14}[/] [{palette.muted}]{what}[/]", classes="cheat")
        for keys, what in _CHEATS
    )
    rows.append(Static(f"  [{palette.faint}]any key to close[/]", classes="cheat"))
    return Container(Vertical(*rows, id="menu"), id="overlay")


def _settings_overlay_widget(overlay: SettingsOverlay, palette: Palette) -> Container:
    rows: list[Static] = [Static(f"[b {palette.text}]Settings[/]", id="ov-header")]
    for i, setting in enumerate(overlay.settings):
        focused = i == overlay.focus
        label_color = palette.text if focused else palette.muted
        rows.append(Static(f"[{label_color}]{_esc(setting.name)}[/]", classes="f-label"))
        classes = "f-enum focus" if focused else "f-enum"
        rows.append(Static(_option_strip(setting.options, setting.index, palette), classes=classes))
    rows.append(
        Static(
            f"[{palette.faint}]↑/↓ setting · ←/→ choose · enter save · esc cancel[/]", id="ov-foot"
        )
    )
    return Container(Vertical(*rows, id="menu"), id="overlay")


def _overlay_widget(state: State, palette: Palette) -> Container | None:
    match state.overlay:
        case None:
            return None
        case ListOverlay():
            return _list_overlay_widget(state.overlay, palette)
        case FormOverlay():
            return _form_overlay_widget(state.overlay, palette)
        case CaptureOverlay():
            return _capture_overlay_widget(state.overlay, palette)
        case CheatsheetOverlay():
            return _cheatsheet_widget(palette)
        case SettingsOverlay():
            return _settings_overlay_widget(state.overlay, palette)
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

    # No hex here: every colour is a ``$`` variable the active theme resolves, so a
    # theme switch re-paints on the next recompose. ``ansi_default`` lets the
    # terminal's own background show through rather than painting one over it.
    CSS = """
    Screen { background: ansi_default; layers: base overlay; }
    #screen { layer: base; width: 100%; height: 100%; }
    #topbar { height: 1; padding: 0 1; margin: 1 0 0 0; }
    #body { height: 1fr; padding: 1 0; }
    #footer { dock: bottom; height: 1; border-top: solid $border; padding: 0 1; }

    .row { height: 1; }
    .row.sel { background: $primary 14%; }
    .c-bar { width: 2; }
    .c-glyph { width: 2; }
    .c-id { width: 8; }
    .c-title { width: 1fr; }
    .c-pri { width: 8; text-align: right; padding-right: 1; }

    .col { width: 1fr; height: auto; padding: 0 1; }
    .col-h { padding-bottom: 1; }
    .card { padding: 0 1; margin-bottom: 1; height: auto; }
    .card.sel { background: $primary 14%; }

    #overlay {
        layer: overlay;
        width: 100%;
        height: 100%;
        align: center top;
        background: $background 55%;
    }
    #menu {
        width: 56;
        height: auto;
        margin-top: 3;
        padding: 1 1;
        background: $panel;
        border: round $primary;
    }
    #ov-header { padding: 0 1; height: 1; }
    #ov-search { padding: 0 1; height: 1; color: $text-disabled; }
    #ov-foot { padding: 1 1 0 1; }
    .act { height: 1; padding: 0 1; }
    .act.hot { background: $primary 14%; }
    .a-name { width: 1fr; }
    .a-hint { width: 6; text-align: right; }
    .f-label { height: 1; padding: 0 1; }
    .f-val { height: 1; padding: 0 2; }
    .f-box { width: 1fr; height: 3; padding: 0 1; margin: 0 1; border: round $border; }
    .f-box.focus { border: round $primary; }
    .f-enum { height: 1; padding: 0 2; }
    .f-enum.focus { padding: 0 1; }
    .cheat { height: 1; padding: 0 1; }
    """

    def __init__(self, engine: Engine, state: State) -> None:
        super().__init__()
        self._engine = engine
        self._state = state
        # Register before the first ``compose`` — ``on_mount`` fires too late — so the
        # persisted theme is already active when the opening palette is derived.
        self.register_theme(TT_DARK)
        self.register_theme(TT_LIGHT)
        self.theme = state.theme.value

    def compose(self) -> ComposeResult:
        state = self._state
        palette = Palette.of(self)
        with Container(id="screen"):
            yield _topbar(state, palette)
            yield _body(state, palette)
            yield _footer(state, palette)
        overlay = _overlay_widget(state, palette)
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
        # A committed settings change is the only thing that moves the theme; persist
        # it and re-point Textual before the recompose re-derives the palette.
        if self.theme != self._state.theme.value:
            self.theme = self._state.theme.value
            config.save(Prefs(theme=self._state.theme))
        await self.recompose()


def _initial_scope(engine: Engine) -> Scope:
    projects = project_api.project_list(engine)
    slug = match_path([(p.slug, p.path) for p in projects], os.getcwd())
    return ProjectScope(slug) if slug is not None else AllScope()


def _startup_theme(prefs: Prefs, env_theme: str | None) -> ThemeName:
    """The theme the app opens on. ``TEXTUAL_THEME`` wins when it names one of ours —
    a per-session override that is not written back — otherwise the persisted
    preference. Anything else (unset, or a built-in theme this app does not paint for)
    falls through to the preference."""
    try:
        return ThemeName(env_theme)
    except ValueError:
        return prefs.theme


def run(engine: Engine) -> None:
    state = start(engine, _initial_scope(engine))
    state.theme = _startup_theme(config.load(), os.environ.get("TEXTUAL_THEME"))
    TrackerApp(engine, state).run()
