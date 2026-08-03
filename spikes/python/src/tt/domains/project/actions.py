"""Everything a project can be asked, in three registrations.

This is the half of the domain that has preconditions. Every issue action is
always ``Runnable``; a project refuses three things, and all three refusals read
something that is on the object only because the store put it there.

A creator is an ordinary ``Action`` whose ``execute`` writes a different table —
``addIssue`` inserts an issue, ``createProject`` a project — and nothing about the
declaration marks either as special.

There is one group per object, save for the two objects that are not a live
project: ``deleted_group`` is offered against a ``Restorable``, and ``root``
against the list of live projects a ``createProject`` is checked against.
"""

from dataclasses import replace

from sqlalchemy.orm import Session

from tt.domains.issue import Draft as IssueDraft
from tt.domains.issue import Priority
from tt.domains.issue import services as issue_services
from tt.domains.project import Draft, Project, Restorable, Status, schemas, services
from tt.platform.action import Action, Checked
from tt.platform.error import Conflict, Invalid
from tt.platform.wire import Empty, Group, entry_of


def _saved(project: Project, tx: Session) -> str:
    """The editable columns, written and reported as one. ``updated_at`` is the
    store's to stamp."""
    services.update_project(tx, project)
    return f"{project.subject()}: saved"


def _issues(n: int) -> str:
    return "1 issue" if n == 1 else f"{n} issues"


class EditTitle(Action[Project, schemas.EditTitlePayload]):
    KEY = "editTitle"
    Payload = schemas.EditTitlePayload

    @classmethod
    def execute(
        cls, obj: Project, payload: schemas.EditTitlePayload, tx: Session, checked: Checked
    ) -> str:
        title = payload.title.strip()
        if not title:
            raise Invalid("title is required")
        return _saved(replace(obj, title=title), tx)


class EditBody(Action[Project, schemas.EditBodyPayload]):
    KEY = "editBody"
    Payload = schemas.EditBodyPayload

    @classmethod
    def execute(
        cls, obj: Project, payload: schemas.EditBodyPayload, tx: Session, checked: Checked
    ) -> str:
        return _saved(replace(obj, body=payload.body), tx)


class EditStatus(Action[Project, schemas.EditStatusPayload]):
    # The refusal is stated against the object rather than against the payload, so
    # it holds whichever status was asked for — archiving is the only move that
    # could break it, and asking to stay active is refused too.
    KEY = "editStatus"
    Payload = schemas.EditStatusPayload

    @classmethod
    def is_disabled(cls, obj: Project) -> str | None:
        if obj.doing > 0:
            return f"finish or drop {_issues(obj.doing)} first"
        return None

    @classmethod
    def execute(
        cls, obj: Project, payload: schemas.EditStatusPayload, tx: Session, checked: Checked
    ) -> str:
        return _saved(replace(obj, status=payload.status), tx)


class Delete(Action[Project, Empty]):
    KEY = "delete"
    Payload = Empty

    @classmethod
    def is_disabled(cls, obj: Project) -> str | None:
        match obj.status:
            case Status.ACTIVE:
                return "archive it first"
            case Status.ARCHIVED:
                return None

    @classmethod
    def execute(cls, obj: Project, payload: Empty, tx: Session, checked: Checked) -> str:
        subject = obj.subject()
        services.delete_project(tx, obj)
        return f"{subject}: deleted"


class Restore(Action[Restorable, Empty]):
    # The one refusal a hook can state only because its object was widened to
    # carry the answer. The partial unique index is still what guarantees it;
    # this is what turns a constraint violation into a sentence.
    KEY = "restore"
    Payload = Empty

    @classmethod
    def is_disabled(cls, obj: Restorable) -> str | None:
        slug = obj.deleted.inner.slug
        if any(project.slug == slug for project in obj.live):
            return f'project "{slug}" exists again'
        return None

    @classmethod
    def execute(cls, obj: Restorable, payload: Empty, tx: Session, checked: Checked) -> str:
        services.restore_project(tx, obj.deleted)
        return f"{obj.deleted.inner.subject()}: restored"


class AddIssue(Action[Project, schemas.AddIssuePayload]):
    # An action on a project writing to the issues table. The store assigns the
    # id, so the message reads the row it wrote rather than the draft it built.
    KEY = "addIssue"
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
        cls, obj: Project, payload: schemas.AddIssuePayload, tx: Session, checked: Checked
    ) -> str:
        title = payload.title.strip()
        if not title:
            raise Invalid("title is required")
        draft = IssueDraft(
            project_id=obj.id,
            title=title,
            body=payload.body or "",
            priority=payload.priority or Priority.NORMAL,
        )
        created = issue_services.create_issue(tx, draft)
        return f"{created.subject()}: created"


class CreateProject(Action[list[Project], schemas.CreateProjectPayload]):
    # The duplicate check cannot be an availability hook, because a hook is given
    # the parent and not the payload — it can be told there are projects and not
    # which slug is being asked for. So the loaded list is the object, and the
    # refusal comes from ``execute``.
    KEY = "createProject"
    Payload = schemas.CreateProjectPayload

    @classmethod
    def execute(
        cls,
        obj: list[Project],
        payload: schemas.CreateProjectPayload,
        tx: Session,
        checked: Checked,
    ) -> str:
        slug = payload.slug.strip()
        if not slug:
            raise Invalid("slug is required")
        if any(project.slug == slug for project in obj):
            raise Conflict(f'project "{slug}" already exists')
        created = services.create_project(
            tx, Draft(slug=slug, title=payload.title or "", body=payload.body or "")
        )
        return f"{created.subject()}: created"


def group() -> Group[Project]:
    """Everything a live project offers, in the order it is offered: the edits,
    then leaving, then the one thing it makes."""
    return [
        entry_of(EditTitle),
        entry_of(EditBody),
        entry_of(EditStatus),
        entry_of(Delete),
        entry_of(AddIssue),
    ]


def deleted_group() -> Group[Restorable]:
    """What a row in the trash offers, which is coming back and nothing else."""
    return [entry_of(Restore)]


def root() -> Group[list[Project]]:
    """The one action with no object to address. Its object is the list of live
    projects, which is what a uniqueness refusal has to read."""
    return [entry_of(CreateProject)]
