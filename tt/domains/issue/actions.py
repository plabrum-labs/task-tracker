"""Everything an issue can be asked.

One action is one class: its object type, its key, its label, its two hooks and
its ``execute``, which holds the deps and mutates the loaded row — an edit sets
every editable column, a delete stamps ``deleted_at``. The flush is the group's,
once, so an ``execute`` only says what it changed. The payload shapes are in
``schemas``, one model per action.

Nothing an issue offers refuses anything. Many issues may be ``doing`` at once,
there is no WIP rule and no status machine, so every action's hooks are the
default and the only refusal is the blank title ``EditIssue.execute`` states. Each
action registers by decorating itself with ``issue_actions``.
"""

from datetime import UTC, datetime

from sqlalchemy import select

from tt.domains.epic.models import Epic
from tt.domains.issue import queries, schemas
from tt.domains.issue.models import Issue
from tt.domains.milestone.models import Milestone
from tt.domains.tag import queries as tag_queries
from tt.platform.actions import (
    ActionDeps,
    ActionGroup,
    ActionResponse,
    Conflict,
    Empty,
    Invalid,
    ObjectAction,
)

issue_actions: ActionGroup[Issue] = ActionGroup("issue", locate=queries.get_issue)


def _resolve_epic(deps: ActionDeps, epic_id: int | None, project_id: int) -> int | None:
    """The id of the live epic an issue is being filed under, or ``None`` to clear
    the link. An epic in another project — or none at all — is refused: an issue's
    epic must belong to the issue's project."""
    if epic_id is None:
        return None
    epic = deps.tx.scalars(
        select(Epic).where(Epic.id == epic_id, Epic.deleted_at.is_(None))
    ).first()
    if epic is None:
        raise Conflict(f"no epic {epic_id}")
    if epic.project_id != project_id:
        raise Conflict(f"epic {epic_id} is not in this project")
    return epic.id


def _resolve_milestone(
    deps: ActionDeps, milestone_id: int | None, new_epic_id: int | None, old_epic_id: int | None
) -> int | None:
    """The id of the live milestone an issue is being filed under, or ``None``. A
    milestone must belong to the issue's (new) epic. A milestone that instead
    belongs to the epic the issue is *leaving* is dropped rather than blocking the
    epic move — that is the stale link a re-submit carries. Any other mismatch is an
    explicit foreign choice, and refused."""
    if milestone_id is None:
        return None
    milestone = deps.tx.scalars(
        select(Milestone).where(Milestone.id == milestone_id, Milestone.deleted_at.is_(None))
    ).first()
    if milestone is None:
        raise Conflict(f"no milestone {milestone_id}")
    if milestone.epic_id == new_epic_id:
        return milestone.id
    if milestone.epic_id == old_epic_id:
        return None
    raise Conflict(f"milestone {milestone_id} is not in this epic")


@issue_actions
class EditIssue(ObjectAction[Issue, schemas.EditIssuePayload]):
    # One whole-object edit: the payload carries every editable field and each is
    # set. Status rides along here like any other field; the focused move is
    # ``SetStatus``.
    KEY = "edit"
    LABEL = "Edit"
    Payload = schemas.EditIssuePayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.EditIssuePayload, deps: ActionDeps
    ) -> ActionResponse:
        title = payload.title.strip()
        if not title:
            raise Invalid("title is required")
        obj.title = title
        obj.body = payload.body
        obj.status = payload.status
        obj.priority = payload.priority
        obj.due_date = payload.due_date
        old_epic_id = obj.epic_id
        new_epic_id = _resolve_epic(deps, payload.epic, obj.project_id)
        obj.epic_id = new_epic_id
        obj.milestone_id = _resolve_milestone(deps, payload.milestone, new_epic_id, old_epic_id)
        return ActionResponse(message=f"{obj.subject()}: saved")


@issue_actions
class SetStatus(ObjectAction[Issue, schemas.SetStatusPayload]):
    # The direct status move. The whole-object edit still carries status; this is
    # the one-field verb a frontend binds to a quick key. No transition rules —
    # any status may follow any other, exactly as the edit allows.
    KEY = "setStatus"
    LABEL = "Set status"
    Payload = schemas.SetStatusPayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.SetStatusPayload, deps: ActionDeps
    ) -> ActionResponse:
        obj.status = payload.status
        return ActionResponse(message=f"{obj.subject()}: {payload.status}")


@issue_actions
class SetDueDate(ObjectAction[Issue, schemas.SetDueDatePayload]):
    # The direct due-date move, the mirror of ``SetStatus``. The whole-object edit
    # still carries the due date; this is the one-field verb. A null payload clears
    # it — no date is a real state, not a refusal.
    KEY = "setDueDate"
    LABEL = "Set due date"
    Payload = schemas.SetDueDatePayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.SetDueDatePayload, deps: ActionDeps
    ) -> ActionResponse:
        obj.due_date = payload.due_date
        when = payload.due_date.isoformat() if payload.due_date is not None else "cleared"
        return ActionResponse(message=f"{obj.subject()}: due {when}")


@issue_actions
class TagIssue(ObjectAction[Issue, schemas.TagNamePayload]):
    # Attach a tag by name. Idempotent: attaching a tag the issue already wears is a
    # no-op that still succeeds. An unknown name is refused — a tag is created
    # through ``createTag``, not conjured by attaching it.
    KEY = "tagIssue"
    LABEL = "Tag"
    Payload = schemas.TagNamePayload

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.TagNamePayload, deps: ActionDeps
    ) -> ActionResponse:
        name = payload.name.strip()
        tag = tag_queries.get_tag_by_name(deps.tx, name)
        if tag is None:
            raise Conflict(f'no tag "{name}"')
        if tag not in obj.tags:
            obj.tags.append(tag)
        return ActionResponse(message=f'{obj.subject()}: tagged "{tag.name}"')


@issue_actions
class UntagIssue(ObjectAction[Issue, schemas.TagNamePayload]):
    # Detach a tag by name. An unknown name is refused, and so is detaching a tag the
    # issue does not wear — the request names a link that is not there.
    KEY = "untagIssue"
    LABEL = "Untag"
    Payload = schemas.TagNamePayload

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.TagNamePayload, deps: ActionDeps
    ) -> ActionResponse:
        name = payload.name.strip()
        tag = tag_queries.get_tag_by_name(deps.tx, name)
        if tag is None:
            raise Conflict(f'no tag "{name}"')
        if tag not in obj.tags:
            raise Conflict(f'{obj.subject()} is not tagged "{tag.name}"')
        obj.tags.remove(tag)
        return ActionResponse(message=f'{obj.subject()}: untagged "{tag.name}"')


@issue_actions
class Delete(ObjectAction[Issue, Empty]):
    # The soft delete, which is an update like any other — the column it sets is
    # not one the edits touch, and every read filters it out, so the row vanishes.
    KEY = "delete"
    LABEL = "Delete"
    Payload = Empty

    @classmethod
    def execute(cls, obj: Issue, payload: Empty, deps: ActionDeps) -> ActionResponse:
        obj.deleted_at = datetime.now(UTC)
        return ActionResponse(message=f"{obj.subject()}: deleted")
