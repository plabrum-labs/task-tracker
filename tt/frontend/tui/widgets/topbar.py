"""The one-line header: the scope's name, its issue count, and how the body is
grouped.

A single ``Static`` whose content is theme-variable markup (``[$primary]`` and the
rest), so it repaints in the active theme with no resolved hex threaded through it.
"""

from __future__ import annotations

from rich.markup import escape as esc
from textual.widgets import Static

from tt.domains.project.schemas import ProjectListItem
from tt.frontend.tui.domainview import GroupBy, GroupRender, ProjectScope, Scope


class TopBar(Static):
    """The header line. ``show`` is called by the screen whenever the scope, the
    project list, the grouping, the render, or the visible count changes."""

    def show(
        self,
        scope: Scope,
        projects: list[ProjectListItem],
        by: GroupBy,
        render: GroupRender,
        count: int,
    ) -> None:
        if isinstance(scope, ProjectScope):
            project = next((p for p in projects if p.slug == scope.slug), None)
            title = f" [$text-muted]{esc(project.title)}[/]" if project and project.title else ""
            name = f"[$primary]◆[/] [b]{esc(scope.slug)}[/]{title}"
        else:
            name = "[$primary]◆[/] [b]all projects[/]"
        # The flat list is grouped by nothing, so it says so as a shape rather than as
        # a dimension; the render is named only when it is the one you chose.
        grouping = "flat" if by == "none" else f"by {by}"
        fan = "  [$text-disabled]·[/] [$text-muted]columns[/]" if render == "columns" else ""
        self.update(f"  {name}   [$text-muted]{count} issues[/]    [b $primary]{grouping}[/]{fan}")
