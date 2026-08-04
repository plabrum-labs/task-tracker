"""Throwaway fidelity spike: how close can Textual get to the design study?

Fake data, no database. Renders the main issue list with the Linear-style
selection bar and glyph statuses, and a command-menu overlay powered by a static
action list. Run headless to dump SVG screenshots for comparison.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

ACCENT = "#8B8CF0"
TEXT = "#E7E9EE"
MUTED = "#8A8F99"
FAINT = "#565B66"
TODO = "#8A8F99"
DOING = "#E3B341"
DONE = "#3FB950"
HIGH = "#F0883E"

GLYPH = {"todo": ("○", TODO), "doing": ("◐", DOING), "done": ("●", DONE)}

# status, priority, id, title, age, selected
ISSUES = [
    ("doing", "high", "TT-4", "Ship the MVP", "2h", True),
    ("todo", "high", "TT-7", "Wire the board layout", "4h", False),
    ("doing", "", "TT-2", "Frontmatter $EDITOR form", "1d", False),
    ("todo", "", "TT-9", "Persist layout to XDG state", "1d", False),
    ("done", "", "TT-5", "columns() over [][]Issue", "2d", False),
    ("done", "", "TT-1", "Bare tt opens the TUI", "3d", False),
]

# ico, name, hint, reason (None = runnable)
ISSUE_ACTIONS = [
    (("◐", DOING), "Change status", "s", None),
    (("▲", HIGH), "Change priority", "p", None),
    (("✎", MUTED), "Edit title", "t", None),
    (("▤", MUTED), "Edit in $EDITOR", "e", None),
    (("⌫", MUTED), "Delete issue", "⌦", None),
]
PROJECT_ACTIONS = [
    (("+", DONE), "New issue", "n", None),
    (("▢", FAINT), "Archive project", "·", "finish or drop 2 issues first"),
]


class Row(Horizontal):
    def __init__(
        self, status: str, priority: str, id: str, title: str, age: str, sel: bool
    ) -> None:
        super().__init__(classes="row" + (" sel" if sel else ""))
        self._status = status
        self._priority = priority
        self._id = id
        self._title = title
        self._age = age
        self._sel = sel

    def compose(self) -> ComposeResult:
        glyph, gcolor = GLYPH[self._status]
        bar = f"[{ACCENT}]▌[/]" if self._sel else " "
        tcolor = TEXT if self._status != "done" else MUTED
        pri = f"[{HIGH}]▲ high[/]" if self._priority == "high" else ""
        yield Static(bar, classes="c-bar")
        yield Static(f"[{gcolor}]{glyph}[/]", classes="c-glyph")
        yield Static(f"[{MUTED}]{self._id}[/]", classes="c-id")
        yield Static(f"[{tcolor}]{self._title}[/]", classes="c-title")
        yield Static(pri, classes="c-pri")
        yield Static(f"[{FAINT}]{self._age}[/]", classes="c-age")


class ActionRow(Horizontal):
    def __init__(
        self, ico: tuple[str, str], name: str, hint: str, reason: str | None, hot: bool
    ) -> None:
        cls = "act" + (" hot" if hot else "") + (" off" if reason else "")
        super().__init__(classes=cls)
        self._ico = ico
        self._name = name
        self._hint = hint
        self._reason = reason

    def compose(self) -> ComposeResult:
        glyph, color = self._ico
        color = FAINT if self._reason else color
        name_color = FAINT if self._reason else TEXT
        name = f"[{name_color}]{self._name}[/]"
        if self._reason:
            name += f" [{FAINT} italic]— {self._reason}[/]"
        yield Static(f"[{color}]{glyph}[/]", classes="a-ico")
        yield Static(name, classes="a-name")
        yield Static(f"[{FAINT}]{self._hint}[/]", classes="a-hint")


class Menu(ModalScreen[None]):
    CSS = """
    Menu { align: center top; background: #0B0C0F 55%; }
    #menu {
        width: 48; height: auto; margin-top: 3;
        background: #1B1E25; border: round #8B8CF0;
    }
    #ctx { padding: 0 2; height: 1; }
    #search { padding: 0 2; height: 1; margin-top: 1; border-bottom: solid #23262E; color: #565B66; }
    .grp { padding: 0 2; height: 1; margin-top: 1; color: #565B66; text-style: none; }
    .act { height: 1; padding: 0 2; }
    .act.hot { background: #8B8CF0 14%; }
    .a-ico { width: 3; }
    .a-name { width: 1fr; }
    .a-hint { width: 4; text-align: right; }
    #mfoot { padding: 1 2; border-top: solid #23262E; color: #565B66; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="menu"):
            yield Static(f"[b {TEXT}]TT-4[/] [{MUTED}]· Ship the MVP[/]", id="ctx")
            yield Static("Search actions…", id="search")
            yield Static("ISSUE", classes="grp")
            for i, (ico, name, hint, reason) in enumerate(ISSUE_ACTIONS):
                yield ActionRow(ico, name, hint, reason, hot=(i == 0))
            yield Static("PROJECT · task-tracker", classes="grp")
            for ico, name, hint, reason in PROJECT_ACTIONS:
                yield ActionRow(ico, name, hint, reason, hot=False)
            yield Static(f"[{FAINT}]↑↓ move   enter run   esc close[/]", id="mfoot")

    def on_key(self, event) -> None:  # noqa: ANN001
        if event.key == "escape":
            self.dismiss(None)


class Spike(App[None]):
    CSS = """
    Screen { background: #0B0C0F; }
    #topbar { dock: top; height: 1; padding: 0 2; margin-top: 1; }
    #list { padding: 1 0; }
    #keybar { dock: bottom; }
    .row { height: 1; }
    .c-bar { width: 2; }
    .c-glyph { width: 2; }
    .c-id { width: 6; }
    .c-title { width: 1fr; }
    .c-pri { width: 8; text-align: right; }
    .c-age { width: 5; text-align: right; padding-right: 2; }
    #keybar { height: 1; padding: 0 2; border-top: solid #23262E; }
    """

    def compose(self) -> ComposeResult:
        yield Static(
            f"  [{ACCENT}]◆[/] [b {TEXT}]task-tracker[/]   [{MUTED}]6 issues[/]"
            f"                              [b {ACCENT}]list[/] [{FAINT}]· split · board[/]",
            id="topbar",
        )
        with VerticalScroll(id="list"):
            for status, priority, id, title, age, sel in ISSUES:
                yield Row(status, priority, id, title, age, sel)
        yield Static(
            f"  [{ACCENT}]j/k[/][{FAINT}] move   [/][{ACCENT}]s[/][{FAINT}] status   [/]"
            f"[{ACCENT}]p[/][{FAINT}] priority   [/][{ACCENT}]n[/][{FAINT}] new   [/]"
            f"[{ACCENT}]space[/][{FAINT}] actions   [/][{ACCENT}]/[/][{FAINT}] filter   [/]"
            f"[{ACCENT}]tab[/][{FAINT}] layout   [/][{ACCENT}]P[/][{FAINT}] project   [/]"
            f"[{ACCENT}]?[/][{FAINT}] keys   [/][{ACCENT}]q[/][{FAINT}] quit[/]",
            id="keybar",
        )

    def on_key(self, event) -> None:  # noqa: ANN001
        if event.key == "space":
            self.push_screen(Menu())
        elif event.key == "q":
            self.exit()


if __name__ == "__main__":
    Spike().run()
