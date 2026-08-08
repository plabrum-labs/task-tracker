"""Everything an issue can be asked.

One action is one class: its object type, its key, its label, its two hooks and
its ``execute``, which holds the deps and mutates the loaded row — an edit sets
every editable column, a delete stamps ``deleted_at``. The flush is the group's,
once, so an ``execute`` only says what it changed. The payload shapes are in
``schemas``, one model per action.

Every action's availability hooks are the default: an issue offers all of them at
every status, since there is no WIP rule and no status machine. A refusal, when one
comes, is raised inside ``execute`` against the data — the blank title
``EditIssue`` states, and the same-project, no-self and no-cycle rules the
dependency verbs enforce. Each action registers by decorating itself with
``issue_actions``.
"""

from datetime import UTC, datetime

from tt.domains.comment.models import Comment
from tt.domains.epic import queries as epic_queries
from tt.domains.issue import queries, schemas
from tt.domains.issue.models import Issue
from tt.domains.milestone import queries as milestone_queries
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

issue_actions: ActionGroup[Issue] = ActionGroup("issue", locate=queries.resolve_ref)


def _resolve_epic(deps: ActionDeps, epic_ref: str | None, project_id: int) -> int | None:
    """The id of the live epic an issue is being filed under, addressed by its ref,
    or ``None`` to clear the link. An epic in another project — or none at all — is
    refused: an issue's epic must belong to the issue's project."""
    if epic_ref is None:
        return None
    epic = epic_queries.resolve_ref(deps.tx, epic_ref)
    if epic is None:
        raise Conflict(f"no epic {epic_ref}")
    if epic.project_id != project_id:
        raise Conflict(f"epic {epic_ref} is not in this project")
    return epic.id


def _resolve_milestone(
    deps: ActionDeps, milestone_ref: str | None, new_epic_id: int | None, old_epic_id: int | None
) -> int | None:
    """The id of the live milestone an issue is being filed under, addressed by its
    ref, or ``None``. A milestone must belong to the issue's (new) epic. A milestone
    that instead belongs to the epic the issue is *leaving* is dropped rather than
    blocking the epic move — that is the stale link a re-submit carries. Any other
    mismatch is an explicit foreign choice, and refused."""
    if milestone_ref is None:
        return None
    milestone = milestone_queries.resolve_ref(deps.tx, milestone_ref)
    if milestone is None:
        raise Conflict(f"no milestone {milestone_ref}")
    if milestone.epic_id == new_epic_id:
        return milestone.id
    if milestone.epic_id == old_epic_id:
        return None
    raise Conflict(f"milestone {milestone_ref} is not in this epic")


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
class AddDependency(ObjectAction[Issue, schemas.DependencyRefPayload]):
    # Record that this issue depends on another, the dependency named by ref. The
    # rules a plain link like a tag does not carry: same-project only, no self-edge,
    # and no cycle — making ``obj`` depend on ``dependency`` is refused when
    # ``dependency`` already depends on ``obj`` transitively. Idempotent otherwise: an
    # edge the issue already carries is a no-op that still succeeds, like ``tagIssue``.
    KEY = "addDependency"
    LABEL = "Add dependency"
    Payload = schemas.DependencyRefPayload

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.DependencyRefPayload, deps: ActionDeps
    ) -> ActionResponse:
        dependency = queries.resolve_ref(deps.tx, payload.dependency)
        if dependency is None:
            raise Conflict(f"no issue {payload.dependency}")
        if dependency.project_id != obj.project_id:
            raise Conflict(f"issue {dependency.ref} is not in this project")
        if dependency.id == obj.id:
            raise Conflict("an issue cannot depend on itself")
        if dependency in obj.depends_on:
            return ActionResponse(message=f"{obj.subject()}: depends on {dependency.ref}")
        # The new edge makes ``obj`` depend on ``dependency``. It closes a loop exactly
        # when ``dependency`` already depends on ``obj`` — ``dependency`` sits among the
        # issues that transitively depend on ``obj`` — so a walk forward from ``obj``
        # would reach ``dependency`` and back again.
        if dependency.id in queries.dependents_closure(deps.tx, obj.id):
            raise Conflict("would create a dependency cycle")
        obj.depends_on.append(dependency)
        return ActionResponse(message=f"{obj.subject()}: depends on {dependency.ref}")


@issue_actions
class RemoveDependency(ObjectAction[Issue, schemas.DependencyRefPayload]):
    # Drop a dependency edge, the dependency named by ref. An unknown ref is refused,
    # and so is removing an edge the issue does not carry — the request names a link
    # that is not there, mirroring ``untagIssue``.
    KEY = "removeDependency"
    LABEL = "Remove dependency"
    Payload = schemas.DependencyRefPayload

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.DependencyRefPayload, deps: ActionDeps
    ) -> ActionResponse:
        dependency = queries.resolve_ref(deps.tx, payload.dependency)
        if dependency is None:
            raise Conflict(f"no issue {payload.dependency}")
        if dependency not in obj.depends_on:
            raise Conflict(f"{obj.subject()} does not depend on {dependency.ref}")
        obj.depends_on.remove(dependency)
        return ActionResponse(message=f"{obj.subject()}: no longer depends on {dependency.ref}")


@issue_actions
class AddComment(ObjectAction[Issue, schemas.AddCommentPayload]):
    # An action on an issue writing to the comments table, the mirror of an epic's
    # ``AddMilestone``. The store assigns the id, so the message reads the row it
    # wrote.
    KEY = "addComment"
    LABEL = "Add comment"
    Payload = schemas.AddCommentPayload

    @classmethod
    def execute(
        cls, obj: Issue, payload: schemas.AddCommentPayload, deps: ActionDeps
    ) -> ActionResponse:
        body = payload.body.strip()
        if not body:
            raise Invalid("body is required")
        comment = Comment(issue=obj, body=body)
        deps.tx.add(comment)
        deps.tx.flush()
        deps.tx.refresh(comment, ["created_at", "updated_at"])
        return ActionResponse(message=f"{comment.subject()}: created", created_id=comment.id)


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
