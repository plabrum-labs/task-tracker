"""The one-line header: the scope's name, its issue count, and the layout tabs.

A single ``Static`` whose content is theme-variable markup (``[$primary]`` and the
rest), so it repaints in the active theme with no resolved hex threaded through it.
"""

from __future__ import annotations

from rich.markup import escape as esc
from textual.widgets import Static

from tt.domains.project.schemas import ProjectListItem
from tt.frontend.tui.domainview import LAYOUTS, Layout, ProjectScope, Scope


class TopBar(Static):
    """The header line. ``show`` is called by the screen whenever the scope, the
    project list, the layout, or the visible count changes."""

    def show(
        self, scope: Scope, projects: list[ProjectListItem], layout: Layout, count: int
    ) -> None:
        if isinstance(scope, ProjectScope):
            project = next((p for p in projects if p.slug == scope.slug), None)
            title = f" [$text-muted]{esc(project.title)}[/]" if project and project.title else ""
            name = f"[$primary]◆[/] [b]{esc(scope.slug)}[/]{title}"
        else:
            name = "[$primary]◆[/] [b]all projects[/]"
        tabs = " ".join(
            f"[b $primary]{tab}[/]" if tab == layout else f"[$text-disabled]{tab}[/]"
            for tab in LAYOUTS
        )
        self.update(f"  {name}   [$text-muted]{count} issues[/]    {tabs}")
