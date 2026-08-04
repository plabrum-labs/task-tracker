"""Everything a project can be asked.

This is the half of the domain that has preconditions. Every issue action is
always ``Runnable``; a project refuses on its counts and its status. ``delete``
and ``addIssue`` refuse at the menu (an ``is_disabled`` reading the loaded row);
``edit`` refuses only in ``execute`` — archiving a project with issues in progress
— so a title or body edit that keeps it active is offered even while it is busy.

A creator writes a table its ``execute`` chooses — ``addIssue`` inserts an issue
against the project it was loaded for, ``createProject`` a project it queries the
duplicates of itself. ``addIssue`` is an ``ObjectAction`` because it hangs off a
project; ``createProject`` is a ``TopLevelAction`` because it has no object to
address, so its uniqueness is read in ``execute`` rather than off a parent. Each
action registers by decorating itself with ``project_actions``.
"""

from datetime import UTC, datetime

from sqlalchemy import select

from tt.domains.issue.enums import Priority
from tt.domains.issue.enums import Status as IssueStatus
from tt.domains.issue.models import Issue
from tt.domains.project import queries, schemas
from tt.domains.project.enums import Status
from tt.domains.project.models import Project
from tt.platform.actions import (
    ActionDeps,
    ActionGroup,
    ActionResponse,
    Conflict,
    Empty,
    Invalid,
    ObjectAction,
    TopLevelAction,
)

project_actions: ActionGroup[Project] = ActionGroup("project", locate=queries.get_project)


def _issues(n: int) -> str:
    return "1 issue" if n == 1 else f"{n} issues"


def _path_owner(deps: ActionDeps, path: str, exclude_id: int | None) -> Project | None:
    """The live project that already owns ``path``, if any but ``exclude_id``. The
    partial unique index guarantees at most one; this is what turns its constraint
    violation into a sentence a refusal can carry."""
    stmt = select(Project).where(Project.path == path, Project.deleted_at.is_(None))
    if exclude_id is not None:
        stmt = stmt.where(Project.id != exclude_id)
    return deps.tx.scalars(stmt).first()


@project_actions
class EditProject(ObjectAction[Project, schemas.EditProjectPayload]):
    # One whole-object edit. The one precondition is stated in ``execute`` against
    # the object rather than as an ``is_disabled``: only *archiving* a project with
    # issues in progress is refused, so a title or body edit that keeps it active is
    # offered — and runs — even while it is busy.
    KEY = "edit"
    LABEL = "Edit"
    Payload = schemas.EditProjectPayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Project, payload: schemas.EditProjectPayload, deps: ActionDeps
    ) -> ActionResponse:
        if payload.status is Status.ARCHIVED and obj.doing > 0:
            raise Conflict(f"finish or drop {_issues(obj.doing)} first")
        title = payload.title.strip()
        if not title:
            raise Invalid("title is required")
        obj.title = title
        obj.body = payload.body
        obj.status = payload.status
        return ActionResponse(message=f"{obj.subject()}: saved")


@project_actions
class Delete(ObjectAction[Project, Empty]):
    KEY = "delete"
    LABEL = "Delete"
    Payload = Empty

    @classmethod
    def is_disabled(cls, obj: Project) -> str | None:
        match obj.status:
            case Status.ACTIVE:
                return "archive it first"
            case Status.ARCHIVED:
                return None

    @classmethod
    def execute(cls, obj: Project, payload: Empty, deps: ActionDeps) -> ActionResponse:
        obj.deleted_at = datetime.now(UTC)
        return ActionResponse(message=f"{obj.subject()}: deleted")


@project_actions
class SetPath(ObjectAction[Project, schemas.SetPathPayload]):
    # The directory bare tt resolves a project from. A live-only unique index covers
    # ``path``, so binding a directory another live project already owns is the one
    # thing this refuses — read here so it is a sentence, not a constraint violation.
    KEY = "setPath"
    LABEL = "Set path"
    Payload = schemas.SetPathPayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Project, payload: schemas.SetPathPayload, deps: ActionDeps
    ) -> ActionResponse:
        path = payload.path.strip()
        if not path:
            raise Invalid("path is required")
        owner = _path_owner(deps, path, exclude_id=obj.id)
        if owner is not None:
            raise Conflict(f'path already belongs to "{owner.slug}"')
        obj.path = path
        return ActionResponse(message=f"{obj.subject()}: path set")


@project_actions
class AddIssue(ObjectAction[Project, schemas.AddIssuePayload]):
    # An action on a project writing to the issues table. The store assigns the
    # id, so the message reads the row it wrote rather than the payload it built.
    KEY = "addIssue"
    LABEL = "Add issue"
    Payload = schemas.AddIssuePayload

    @classmethod
    def is_disabled(cls, obj: Project) -> str | None:
        match obj.status:
            case Status.ARCHIVED:
                return "project is archived"
            case Status.ACTIVE:
                return None

    @classmethod
    def execute(
        cls, obj: Project, payload: schemas.AddIssuePayload, deps: ActionDeps
    ) -> ActionResponse:
        title = payload.title.strip()
        if not title:
            raise Invalid("title is required")
        issue = Issue(
            project=obj,
            title=title,
            body=payload.body or "",
            status=IssueStatus.TODO,
            priority=payload.priority or Priority.NORMAL,
            status_note=None,
        )
        deps.tx.add(issue)
        deps.tx.flush()
        deps.tx.refresh(issue, ["created_at", "updated_at"])
        return ActionResponse(message=f"{issue.subject()}: created", created_id=issue.id)


@project_actions
class CreateProject(TopLevelAction[schemas.CreateProjectPayload]):
    # No object to address and no parent to read: the duplicate check queries the
    # live projects through the transaction itself. The partial unique index is
    # what guarantees it; this is what turns a would-be constraint violation into a
    # sentence.
    KEY = "createProject"
    LABEL = "Create project"
    Payload = schemas.CreateProjectPayload

    @classmethod
    def execute(cls, payload: schemas.CreateProjectPayload, deps: ActionDeps) -> ActionResponse:
        slug = payload.slug.strip()
        if not slug:
            raise Invalid("slug is required")
        existing = deps.tx.scalars(
            select(Project).where(Project.slug == slug, Project.deleted_at.is_(None))
        ).first()
        if existing is not None:
            raise Conflict(f'project "{slug}" already exists')
        path = payload.path.strip() if payload.path else None
        if path is not None:
            owner = _path_owner(deps, path, exclude_id=None)
            if owner is not None:
                raise Conflict(f'path already belongs to "{owner.slug}"')
        project = Project(
            slug=slug,
            title=payload.title or "",
            body=payload.body or "",
            status=Status.ACTIVE,
            path=path,
            issues=[],
        )
        deps.tx.add(project)
        deps.tx.flush()
        deps.tx.refresh(project, ["created_at", "updated_at"])
        return ActionResponse(message=f"{project.subject()}: created", created_id=project.id)
