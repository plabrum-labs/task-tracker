"""The detail of the selected issue, shown beside the list.

The pane tracks the cursor: the browse screen assigns ``detail`` and the subtree
rebuilds from it (``recompose``). It is a focusable ``VerticalScroll`` so a body
taller than the pane can be read — Enter moves focus here, ``j``/``k`` scroll, and
Escape returns to the list. The status glyph mixes its colour into one markup
string, so it names its theme variable through ``status_var``; everything else is
plain text drawn in a CSS component class.
"""

from __future__ import annotations

from rich.markup import escape as esc
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static

from tt.domains.issue.schemas import IssueDetail
from tt.frontend.tui.domainview import glyph, issue_ref, marker, status_var


class DetailPane(VerticalScroll):
    """The selected issue's detail. ``detail`` is a recomposing reactive the screen
    assigns, ``None`` when nothing is selected. Focusable so a long body scrolls;
    the flat browse list is not."""

    detail: reactive[IssueDetail | None] = reactive[IssueDetail | None](None, recompose=True)

    # Only in effect while the pane holds focus (Enter), so these shadow the
    # screen's own ``j``/``k``/``g``/``G`` without stealing them from browse mode.
    BINDINGS = [
        Binding("j", "scroll_down", "down", show=False),
        Binding("k", "scroll_up", "up", show=False),
        Binding("g", "scroll_home", "top", show=False),
        Binding("G", "scroll_end", "bottom", show=False),
    ]

    def compose(self) -> ComposeResult:
        detail = self.detail
        if detail is None:
            yield Static("[$text-disabled]no issue selected[/]", classes="d-empty")
            return
        ref = issue_ref(detail.project, detail.id)
        yield Static(f"[$text-muted]{esc(ref)}[/]  [b]{esc(detail.title)}[/]", classes="d-header")
        var = status_var(detail.status)
        pri = "   [$priority-high]▲ high[/]" if marker(detail.priority) else ""
        status = f"[{var}]{glyph(detail.status)} {esc(detail.status)}[/]{pri}"
        yield Static(status, classes="d-meta")
        yield Static(
            f"[$text-disabled]created {detail.created_at:%Y-%m-%d}"
            f"   updated {detail.updated_at:%Y-%m-%d}[/]",
            classes="d-meta",
        )
        body = detail.body.strip()
        yield Static(esc(body) if body else "[$text-disabled]no description[/]", classes="d-body")
