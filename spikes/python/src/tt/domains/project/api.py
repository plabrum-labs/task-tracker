"""The project domain's one public surface.

A frontend imports this and nothing else of the domain, and hands it an engine —
never a session. Every call that touches the database is wrapped in
``@with_transaction``, so one public call is one transaction and the frontend
draws no ``with`` block of its own. The names are the flat surface an MCP or an
HTTP edge exposes: ``project_list``/``project_get``/``project_detail`` read, and
``project_action`` writes. ``top_level_offers`` and ``action_schemas`` ask nothing
of the database, so they take no engine.
"""

from typing import Any

from sqlalchemy.orm import Session

from tt.domains.project import queries, schemas
from tt.domains.project.actions import project_actions
from tt.domains.project.models import Project
from tt.platform.actions import ActionDeps, ActionResponse, Field, Invalid, Offer, name_of
from tt.platform.db import with_transaction

# --- reads ----------------------------------------------------------------


def _list_item(project: Project) -> schemas.ProjectListItem:
    return schemas.ProjectListItem(
        slug=project.slug,
        title=project.title,
        status=name_of(project.status),
        todo=project.todo,
        doing=project.doing,
        done=project.done,
    )


def _detail(project: Project) -> schemas.ProjectDetail:
    return schemas.ProjectDetail(
        slug=project.slug,
        title=project.title,
        body=project.body,
        status=name_of(project.status),
        todo=project.todo,
        doing=project.doing,
        done=project.done,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@with_transaction
def project_list(tx: Session) -> list[schemas.ProjectListItem]:
    return [_list_item(project) for project in queries.list_projects(tx)]


@with_transaction
def project_get(tx: Session, slug: str) -> schemas.ProjectDetail | None:
    project = queries.get_project(tx, slug)
    return _detail(project) if project is not None else None


@with_transaction
def project_detail(tx: Session, slug: str) -> tuple[schemas.ProjectDetail, list[Offer]]:
    """A project and what it offers, for a detail view."""
    project = queries.get_project(tx, slug)
    if project is None:
        raise Invalid(f"no project {slug!r}")
    return _detail(project), project_actions.offers(project)


def top_level_offers() -> list[Offer]:
    """What the group offers with no project to address — ``createProject``."""
    return project_actions.top_level_offers()


# --- write ----------------------------------------------------------------


@with_transaction
def project_action(tx: Session, key: str, payload: Any, slug: str | None = None) -> ActionResponse:
    """Run an action by key. An object action passes the project's slug as its
    address; the top-level ``createProject`` passes ``None`` and queries for
    itself."""
    return project_actions.trigger(ActionDeps(tx), key, payload, slug)


# --- codegen --------------------------------------------------------------


def action_schemas() -> list[tuple[str, list[Field]]]:
    """Every object-action key a project answers to, with the fields its payload
    asks for."""
    return project_actions.action_schemas()
