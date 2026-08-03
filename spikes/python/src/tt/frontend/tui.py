"""The erased path with a person on the end of it.

Same three calls as the CLI would make: ``wire.available`` for the menu,
``form.of_schema`` for the arguments, ``wire.dispatch`` for the write. Nothing
here names an action key — a row appears in a menu because the action is
registered, and its form has the fields its payload model derived. Adding a fifth
issue action changes no line of this file.

Every screen is one movable list of rows, and a row is either somewhere to go or
something to do. That is what lets three screens share one cursor, one Enter and
one renderer: the children of an object and the actions over it are both just
things you can pick.

Nothing is cached across a write. Every write reloads what it drew, so a menu is
never left showing a row that has since changed, and the dispatch that follows a
submission is checked again against the row as it is now.

The state machine — ``Screen``, ``Row``, ``Control``, ``Form``, ``State``,
``Intent``, ``on_key``, ``apply``, ``load`` and ``render_lines`` — is plain
framework-free Python, so it is driven in ``tests/test_tui.py`` with an in-memory
database and no terminal. ``on_key`` and ``render_lines`` are pure and ``apply``
is the only half that touches the database. ``TrackerApp`` owns nothing but the
terminal: it turns a Textual key event into one of our key strings, runs the pure
``on_key`` then ``apply``, and draws ``render_lines``.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, assert_never

from sqlalchemy import Engine
from textual import events
from textual.app import App, ComposeResult
from textual.widgets import Static

from tt.domains import schema
from tt.domains.issue import actions as issue_actions
from tt.domains.issue import services as issue_services
from tt.domains.project import actions as project_actions
from tt.domains.project import services as project_services
from tt.platform import db, form, wire
from tt.platform.action import Offered, Refused, Runnable
from tt.platform.error import Error, Invalid
from tt.platform.form import FormError

BROWSING = "up/down to move, enter to pick, esc to go back, q to quit"
FILLING = "tab to move, left/right to choose, enter to submit, esc to go back"


# --- screen ---------------------------------------------------------------


@dataclass(frozen=True)
class Projects:
    """The root list of live projects."""


@dataclass(frozen=True)
class Issues:
    """The issues of one project, addressed by its slug."""

    slug: str


@dataclass(frozen=True)
class Detail:
    """One issue. It carries the project slug as well as the id because the way
    back out of a deleted issue is the project it was in, and by then the issue is
    not there to be asked."""

    project: str
    id: int


type Screen = Projects | Issues | Detail


def parent(screen: Screen) -> Screen | None:
    match screen:
        case Projects():
            return None
        case Issues():
            return Projects()
        case Detail(project=project):
            return Issues(project)
    assert_never(screen)


# --- rows -----------------------------------------------------------------


@dataclass(frozen=True)
class Go:
    """Somewhere to move the cursor to."""

    label: str
    to: Screen


@dataclass(frozen=True)
class Do:
    """Something to run. A ``Do`` carries the key it would run and nothing else —
    no object and no store call. Each screen is about one object, and that object
    is the whole of what determines its group, so the write loads the row again and
    dispatches against the group the screen already implies."""

    key: str
    offered: Offered
    schema: dict[str, Any]


type Row = Go | Do


def row_label(row: Row) -> str:
    """A refused ``Do`` shows its reason rather than being dropped, which is the
    whole of what availability buys a person over a fixed set of commands."""
    match row:
        case Go(label=label):
            return label
        case Do(key=key, offered=offered):
            match offered:
                case Runnable():
                    return key
                case Refused(reason=reason):
                    return f"{key} ({reason})"
    assert_never(row)


# --- form controls --------------------------------------------------------


@dataclass(frozen=True)
class Editing:
    """A text box for a string."""

    text: str


@dataclass(frozen=True)
class Choosing:
    """A cycling selector over the closed list an enum advertises."""

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


@dataclass
class Entered:
    """One rendered field: the schema-derived field and the control filling it.
    Mutable because typing into it replaces the control in place, the way the Rust
    reducer takes ``&mut`` to the focused entry."""

    field: form.Field
    control: Control


@dataclass
class Form:
    """The arguments being filled in. ``None`` on ``State`` while none is up; the
    screen underneath does not change, so leaving a form is dropping it and nothing
    else."""

    key: str
    entries: list[Entered]
    focus: int


@dataclass
class State:
    screen: Screen
    header: str
    rows: list[Row]
    selected: int
    form: Form | None
    status: str
    quit: bool


def _empty() -> State:
    return State(
        screen=Projects(),
        header="",
        rows=[],
        selected=0,
        form=None,
        status=BROWSING,
        quit=False,
    )


def selected_row(state: State) -> Row | None:
    if 0 <= state.selected < len(state.rows):
        return state.rows[state.selected]
    return None


# --- intents --------------------------------------------------------------


@dataclass(frozen=True)
class Ignored:
    """The keystroke means nothing on this screen."""


@dataclass(frozen=True)
class Move:
    """Move the cursor by ``n`` rows."""

    n: int


@dataclass(frozen=True)
class EnterIntent:
    """Pick the selected row."""


@dataclass(frozen=True)
class Back:
    """Leave the form, or go up a screen."""


@dataclass(frozen=True)
class Quit:
    """Stop the loop."""


@dataclass(frozen=True)
class Insert:
    """Type one character into the focused text box."""

    char: str


@dataclass(frozen=True)
class Rub:
    """Delete the last character of the focused text box."""


@dataclass(frozen=True)
class NextField:
    """Move focus to the next form field."""


@dataclass(frozen=True)
class Cycle:
    """Cycle the focused selector by ``n``."""

    n: int


@dataclass(frozen=True)
class Submit:
    """Build the payload and dispatch it."""


type Intent = (
    Ignored | Move | EnterIntent | Back | Quit | Insert | Rub | NextField | Cycle | Submit
)


def on_key(state: State, key: str) -> Intent:
    """Pure. A form takes the keys a form takes and the list takes the rest, and
    nothing here decides what a key *does* — only what it means. Keys are the plain
    lowercase names ``"up"``, ``"down"``, ``"enter"``, ``"escape"``, ``"tab"``,
    ``"backspace"``, ``"left"``, ``"right"`` or the printable character itself, so
    the reducer needs no Textual import."""
    if state.form is not None:
        match key:
            case "escape":
                return Back()
            case "tab" | "down":
                return NextField()
            case "enter":
                return Submit()
            case "backspace":
                return Rub()
            case "left":
                return Cycle(-1)
            case "right":
                return Cycle(1)
            case _ if len(key) == 1:
                return Insert(key)
            case _:
                return Ignored()
    match key:
        case "up":
            return Move(-1)
        case "down":
            return Move(1)
        case "enter":
            return EnterIntent()
        case "escape":
            return Back()
        case "q":
            return Quit()
        case _:
            return Ignored()


# --- loading --------------------------------------------------------------


def _action_rows[Obj](group: wire.Group[Obj], obj: Obj) -> list[Row]:
    return [
        Do(key=entry.key, offered=offered, schema=entry.schema)
        for entry, offered in wire.available(group, obj)
    ]


def load(engine: Engine, screen: Screen) -> tuple[str, list[Row]]:
    """One screen, read fresh. The header is what the screen is about and the rows
    are what can be done from it, in that order. Raises ``Error`` when the object
    the screen is about has gone, which is what ``_reload`` turns into falling out
    to the parent."""
    with db.reading(engine) as session:
        match screen:
            case Projects():
                projects = project_services.projects(session)
                rows = _action_rows(project_actions.root(), projects)
                rows.extend(
                    Go(
                        label=(
                            f"{p.slug:<12} {p.title:<24} {wire.name_of(p.status):<8} "
                            f"{p.todo}/{p.doing}/{p.done}"
                        ),
                        to=Issues(p.slug),
                    )
                    for p in projects
                )
                return ("projects", rows)
            case Issues(slug=slug):
                project = project_services.project(session, slug)
                if project is None:
                    raise Invalid(f"no project {slug!r}")
                issues = issue_services.issues(session, slug)
                rows = _action_rows(project_actions.group(), project)
                rows.extend(
                    Go(
                        label=(
                            f"{i.id:<4} {wire.name_of(i.status):<8} "
                            f"{wire.name_of(i.priority):<6} {i.title}"
                        ),
                        to=Detail(project=slug, id=i.id),
                    )
                    for i in issues
                )
                return (f"{project.subject()}: {project.title}", rows)
            case Detail(id=issue_id):
                issue = issue_services.issue(session, issue_id)
                if issue is None:
                    raise Invalid(f"no issue {issue_id}")
                rows = _action_rows(issue_actions.group(), issue)
                return (f"{issue.subject()}: {issue.title}", rows)
    assert_never(screen)


def _write(engine: Engine, screen: Screen, key: str, payload: dict[str, Any]) -> str:
    """Load, then dispatch, inside one transaction. Each screen is about one object,
    and that object is the whole of what determines its group — so there is no
    group-by-group pairing left to write here, only which object the screen loads. A
    refusal raised by ``dispatch`` propagates out and the transaction rolls back."""
    with db.transaction(engine) as tx:
        match screen:
            case Projects():
                projects = project_services.projects(tx)
                return wire.dispatch(project_actions.root(), projects, key, payload, tx)
            case Issues(slug=slug):
                project = project_services.project(tx, slug)
                if project is None:
                    raise Invalid(f"no project {slug!r}")
                return wire.dispatch(project_actions.group(), project, key, payload, tx)
            case Detail(id=issue_id):
                issue = issue_services.issue(tx, issue_id)
                if issue is None:
                    raise Invalid(f"no issue {issue_id}")
                return wire.dispatch(issue_actions.group(), issue, key, payload, tx)
    assert_never(screen)


def _reload(engine: Engine, state: State) -> State:
    """Read the current screen back, and fall out to the parent if the object it is
    about has gone. That is how deleting the thing you are looking at navigates, and
    it names no action key to do it."""
    try:
        header, rows = load(engine, state.screen)
    except Error as e:
        up = parent(state.screen)
        if up is None:
            state.status = str(e)
            state.rows = []
            return state
        state.screen = up
        state.selected = 0
        return _reload(engine, state)
    state.selected = min(state.selected, max(len(rows) - 1, 0))
    state.header = header
    state.rows = rows
    return state


def start(engine: Engine) -> State:
    """The first screen, loaded."""
    return _reload(engine, _empty())


# --- applying -------------------------------------------------------------


def _control_of(field_: form.Field) -> Control:
    match field_.kind:
        case form.Text() | form.OptionalText():
            return Editing("")
        case form.Enum(values=values):
            return Choosing(list(values), 0)
    assert_never(field_.kind)


def _focused(state: State) -> Entered | None:
    current = state.form
    if current is None:
        return None
    if 0 <= current.focus < len(current.entries):
        return current.entries[current.focus]
    return None


def _edit_focused(state: State, transform: Callable[[str], str]) -> None:
    entry = _focused(state)
    if entry is None:
        return
    match entry.control:
        case Editing(text=text):
            entry.control = Editing(transform(text))
        case Choosing():
            return


def _cycle_focused(state: State, n: int) -> None:
    entry = _focused(state)
    if entry is None:
        return
    match entry.control:
        case Choosing(values=values, index=index) if values:
            entry.control = Choosing(values, (index + n) % len(values))
        case _:
            return


def _enter(engine: Engine, state: State) -> State:
    match selected_row(state):
        case None:
            return state
        case Go(to=to):
            state.screen = to
            state.selected = 0
            state.status = BROWSING
            return _reload(engine, state)
        case Do(key=key, offered=Refused(reason=reason)):
            state.status = f"{key}: {reason}"
            return state
        case Do(key=key, schema=action_schema):
            # A schema the form cannot render is reported rather than approximated,
            # so an action is never handed a payload missing half of what it asked
            # for.
            try:
                fields = form.of_schema(action_schema)
            except FormError as e:
                state.status = f"{key}: {e}"
                return state
            state.form = Form(
                key=key,
                entries=[Entered(field=f, control=_control_of(f)) for f in fields],
                focus=0,
            )
            state.status = FILLING
            return state


def _submit(engine: Engine, state: State) -> State:
    current = state.form
    if current is None:
        return state
    values = [(entry.field, control_value(entry.control)) for entry in current.entries]
    payload = form.payload(values)
    try:
        message = _write(engine, state.screen, current.key, payload)
    except Error as e:
        # A refusal leaves the form up with its values intact, because that is
        # where the fix usually is.
        state.status = f"{current.key}: {e}"
        return state
    state.form = None
    state.status = message
    return _reload(engine, state)


def apply(engine: Engine, state: State, intent: Intent) -> State:
    """The half with the database in it."""
    match intent:
        case Ignored():
            return state
        case Quit():
            state.quit = True
            return state
        case Move(n=n):
            last = max(len(state.rows) - 1, 0)
            state.selected = max(0, min(state.selected + n, last))
            return state
        case Back():
            if state.form is not None:
                state.form = None
                state.status = BROWSING
                return state
            up = parent(state.screen)
            if up is None:
                return state
            state.screen = up
            state.selected = 0
            return _reload(engine, state)
        case EnterIntent():
            return _enter(engine, state)
        case NextField():
            current = state.form
            if current is not None and current.entries:
                current.focus = (current.focus + 1) % len(current.entries)
            return state
        case Insert(char=char):
            _edit_focused(state, lambda text: text + char)
            return state
        case Rub():
            _edit_focused(state, lambda text: text[:-1])
            return state
        case Cycle(n=n):
            _cycle_focused(state, n)
            return state
        case Submit():
            return _submit(engine, state)
    assert_never(intent)


# --- rendering ------------------------------------------------------------


def render_lines(state: State) -> list[str]:
    """What is on screen, as lines of text. Pure: it reads the state and touches
    nothing else, so the tests assert against what it draws."""
    lines = [state.header]
    current = state.form
    if current is None:
        for i, row in enumerate(state.rows):
            pointer = "> " if i == state.selected else "  "
            lines.append(f"{pointer}{row_label(row)}")
    else:
        lines.append(current.key)
        for i, entry in enumerate(current.entries):
            pointer = "> " if i == current.focus else "  "
            # The label is the field's description, which Pydantic took from
            # ``Field(description=...)``. It falls back to the property name only
            # when a field carries none.
            label = entry.field.description or entry.field.name
            mark = "*" if entry.field.required else ""
            lines.append(
                f"{pointer}{entry.field.name}{mark} [{control_value(entry.control)}]  {label}"
            )
        lines.append("* required")
    lines.append(state.status)
    return lines


# --- the terminal ---------------------------------------------------------

# The Textual key names our pure ``on_key`` understands. Anything else printable
# is passed through as its own character, and anything else is dropped.
_NAMED_KEYS = frozenset({"up", "down", "left", "right", "enter", "escape", "tab", "backspace"})


def _key_string(event: events.Key) -> str | None:
    """Bridge a Textual key event to one of our key strings. A named key passes as
    its name; a printable key passes as its character (so ``"q"`` is a character,
    not a command); everything else is nothing to the reducer."""
    if event.key in _NAMED_KEYS:
        return event.key
    character = event.character
    if character is not None and character.isprintable():
        return character
    return None


class TrackerApp(App[None]):
    """The terminal the view is rendered to, and nothing else: it holds a ``State``,
    maps each keypress through the pure ``on_key`` and ``apply``, and draws
    ``render_lines``. No domain logic lives here."""

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self._engine = engine
        self._state = start(engine)

    def compose(self) -> ComposeResult:
        yield Static(id="view")

    def on_mount(self) -> None:
        self._draw()

    def on_key(self, event: events.Key) -> None:
        key = _key_string(event)
        if key is None:
            return
        event.stop()
        intent = on_key(self._state, key)
        self._state = apply(self._engine, self._state, intent)
        if self._state.quit:
            self.exit()
            return
        self._draw()

    def _draw(self) -> None:
        self.query_one("#view", Static).update("\n".join(render_lines(self._state)))


def main() -> None:
    url = os.environ.get("TT_DB", "sqlite:///tt.db")
    engine = db.connect(url)
    schema.initialise(engine)
    TrackerApp(engine).run()
