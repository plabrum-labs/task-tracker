"""The TUI's read and write side onto the domain apis.

``resolve`` reads a scope back — every live project, and the issues of the scoped
one or of all of them — falling out to all-projects when a scoped project has gone,
which is how deleting it navigates. ``dispatch`` runs one write against an addressed
target. Selection reconciliation stays in ``domainview``'s pure ``surviving_id`` /
``index_of``; this module only loads the rows they reconcile over.
"""

from __future__ import annotations

import os
from typing import Any, assert_never

from sqlalchemy import Engine

from tt.domains.comment import api as comment_api
from tt.domains.epic import api as epic_api
from tt.domains.issue import api as issue_api
from tt.domains.issue.schemas import IssueListItem
from tt.domains.milestone import api as milestone_api
from tt.domains.project import api as project_api
from tt.domains.project.schemas import ProjectListItem
from tt.domains.tag import api as tag_api
from tt.frontend.tui.domainview import (
    AllScope,
    CommentTarget,
    EpicTarget,
    IssueTarget,
    MilestoneTarget,
    ProjectScope,
    ProjectTarget,
    RootTarget,
    Scope,
    TagTarget,
    Target,
    match_path,
)


def initial_scope(engine: Engine) -> Scope:
    """The scope the app opens on: the project whose path is an ancestor of the
    working directory, or all projects when the shell is outside every one."""
    projects = project_api.project_list(engine)
    slug = match_path([(p.slug, p.path) for p in projects], os.getcwd())
    return ProjectScope(slug) if slug is not None else AllScope()


def resolve(
    engine: Engine, scope: Scope
) -> tuple[Scope, list[ProjectListItem], list[IssueListItem]]:
    """The effective scope and its rows. A ``ProjectScope`` whose project has gone
    falls out to ``AllScope``, so deleting the scoped project navigates rather than
    reading an empty view."""
    projects = project_api.project_list(engine)
    if isinstance(scope, ProjectScope) and all(p.slug != scope.slug for p in projects):
        scope = AllScope()
    if isinstance(scope, ProjectScope):
        issues = issue_api.issue_list(engine, scope.slug)
    else:
        issues = [issue for p in projects for issue in issue_api.issue_list(engine, p.slug)]
    return scope, projects, issues


def dispatch(engine: Engine, target: Target, key: str, payload: dict[str, Any]) -> str:
    """Run one action by key against an addressed target, returning its message. A
    refusal raises through ``REFUSALS`` for the caller to surface."""
    match target:
        case IssueTarget(ref=ref):
            return issue_api.issue_action(engine, key, payload, ref).message
        case ProjectTarget(slug=slug):
            return project_api.project_action(engine, key, payload, slug).message
        case CommentTarget(comment_id=comment_id):
            return comment_api.comment_action(engine, key, payload, comment_id).message
        case EpicTarget(project_slug=project_slug, title=title):
            return epic_api.epic_action(engine, key, payload, project_slug, title).message
        case MilestoneTarget(project_slug=project_slug, title=title):
            return milestone_api.milestone_action(engine, key, payload, project_slug, title).message
        case TagTarget(tag_id=tag_id):
            return tag_api.tag_action(engine, key, payload, tag_id).message
        case RootTarget():
            return project_api.project_action(engine, key, payload, None).message
    assert_never(target)
