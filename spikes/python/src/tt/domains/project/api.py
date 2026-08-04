"""The project domain's one public surface.

As ``../issue/api.py``, plus ``top_level_offers``: the project group has one
action with no object to address — ``createProject`` — so a caller that has no
project yet still has something to offer. ``trigger`` routes both, an address for
an object action and ``None`` for the top-level one.
"""

from typing import Any

from sqlalchemy.orm import Session

from tt.domains.project import queries, schemas
from tt.domains.project.actions import project_actions
from tt.domains.project.models import Project
from tt.platform.actions import ActionDeps, ActionResponse, Field, Invalid, Offer, name_of

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


def list_projects(db: Session) -> list[schemas.ProjectListItem]:
    return [_list_item(project) for project in queries.live(db)]


def get_project(db: Session, slug: str) -> schemas.ProjectDetail | None:
    project = queries.by_slug(db, slug)
    return _detail(project) if project is not None else None


def show(db: Session, slug: str) -> tuple[schemas.ProjectDetail, list[Offer]]:
    """A project and what it offers, for a detail view."""
    project = queries.by_slug(db, slug)
    if project is None:
        raise Invalid(f"no project {slug!r}")
    return _detail(project), project_actions.offers(project)


def top_level_offers() -> list[Offer]:
    """What the group offers with no project to address — ``createProject``."""
    return project_actions.top_level_offers()


# --- write ----------------------------------------------------------------


def trigger(tx: Session, key: str, payload: Any, slug: str | None = None) -> ActionResponse:
    """Run an action by key. An object action passes the project's slug as its
    address; the top-level ``createProject`` passes ``None`` and queries for
    itself."""
    return project_actions.trigger(ActionDeps(tx), key, payload, slug)


# --- codegen --------------------------------------------------------------


def action_schemas() -> list[tuple[str, list[Field]]]:
    """Every object-action key a project answers to, with the fields its payload
    asks for."""
    return project_actions.action_schemas()
