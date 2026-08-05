"""The detail of the selected issue, shown beside the list.

The pane tracks the cursor: the browse screen assigns ``detail`` and the subtree
rebuilds from it (``recompose``). It is a focusable ``VerticalScroll`` so a body
taller than the pane can be read — Enter moves focus here and Escape returns to the
list. Under the body sits the comment thread, and it is what ``j``/``k`` walk while
the pane holds focus, so the screen can address a single comment; with no thread to
walk they scroll instead. The status glyph mixes its colour into one markup string,
so it names its theme variable through ``status_var``; everything else is plain text
drawn in a CSS component class.
"""

from __future__ import annotations

from rich.markup import escape as esc
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static

from tt.domains.comment.schemas import CommentView
from tt.domains.issue.schemas import IssueDetail
from tt.frontend.tui.domainview import (
    glyph,
    index_of,
    marker,
    move_comment,
    status_var,
    surviving_id,
)


class CommentRow(Horizontal):
    """One comment of the thread: the gutter bar the issue rows use, the date it was
    written, and its wrapped body. An edit is shown as a dated suffix, and only when
    the comment has actually been edited."""

    def __init__(self, comment: CommentView, selected: bool) -> None:
        super().__init__(classes="sel" if selected else "")
        self._comment = comment
        self._selected = selected

    def compose(self) -> ComposeResult:
        comment = self._comment
        yield Static("▌" if self._selected else " ", classes="bar")
        yield Static(f"{comment.created_at:%m-%d}", classes="d-comment-date")
        edited = (
            f"  [$text-disabled](edited {comment.updated_at:%m-%d})[/]"
            if comment.updated_at != comment.created_at
            else ""
        )
        yield Static(f"{esc(comment.body.strip())}{edited}", classes="d-comment-body")

    def on_mount(self) -> None:
        if self._selected:
            self.scroll_visible()


class DetailPane(VerticalScroll):
    """The selected issue's detail. ``detail`` is a recomposing reactive the screen
    assigns, ``None`` when nothing is selected. Focusable so a long body scrolls;
    the flat browse list is not."""

    detail: reactive[IssueDetail | None] = reactive[IssueDetail | None](None, recompose=True)

    # The comment the thread's cursor is on, ``None`` when none is. The screen reads
    # it to address a comment, and clears it on leaving the pane — the bar would
    # otherwise claim a selection that browse keys no longer act on.
    selected_comment_id: reactive[int | None] = reactive[int | None](None, recompose=True)

    # Only in effect while the pane holds focus (Enter), so these shadow the
    # screen's own ``j``/``k``/``g``/``G`` without stealing them from browse mode.
    BINDINGS = [
        Binding("j", "next_comment", "next comment", show=False),
        Binding("k", "previous_comment", "previous comment", show=False),
        Binding("g", "scroll_home", "top", show=False),
        Binding("G", "scroll_end", "bottom", show=False),
    ]

    def compose(self) -> ComposeResult:
        detail = self.detail
        if detail is None:
            yield Static("[$text-disabled]no issue selected[/]", classes="d-empty")
            return
        yield Static(
            f"[$text-muted]{esc(detail.ref)}[/]  [b]{esc(detail.title)}[/]", classes="d-header"
        )
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
        comments = detail.comments
        yield Static(f"[$text-muted]COMMENTS · {len(comments)}[/]", classes="d-comment-head")
        if not comments:
            yield Static("[$text-disabled]no comments[/]", classes="d-empty")
        for comment in comments:
            yield CommentRow(comment, comment.id == self.selected_comment_id)

    def watch_detail(self, old: IssueDetail | None, new: IssueDetail | None) -> None:
        # A write reloads the whole detail, so the comment cursor is reconciled onto a
        # survivor the way the screen reconciles the issue selection: the same comment
        # if it is still here, else whatever now sits where it was.
        if self.selected_comment_id is None:
            return
        fallback = index_of(old.comments if old is not None else [], self.selected_comment_id)
        self.selected_comment_id = surviving_id(
            new.comments if new is not None else [], self.selected_comment_id, fallback
        )

    def _move_comment(self, delta: int) -> None:
        # A thread is what ``j``/``k`` walk here; with none there is no comment to
        # address, so they fall back to scrolling a body taller than the pane.
        comments = self.detail.comments if self.detail is not None else []
        if not comments:
            self.scroll_relative(y=delta, animate=False)
            return
        self.selected_comment_id = move_comment(comments, self.selected_comment_id, delta)

    def action_next_comment(self) -> None:
        self._move_comment(1)

    def action_previous_comment(self) -> None:
        self._move_comment(-1)
