"""Everything a milestone can be asked.

A milestone is thin, so its actions are: a whole-object ``edit`` over its title,
due date and optional epic, the focused ``setDueDate`` verb it also carries, and a
``delete``. Like an epic, ``delete`` refuses while the milestone still holds live
issues, so no live issue is left pointing at a deleted milestone. The flush is the
group's, once. Creating a milestone is a project's ``addMilestone``, in
``../project/actions.py``, since a milestone is project-level. Each action
registers by decorating itself with ``milestone_actions``.
"""

from datetime import UTC, datetime

from tt.domains.epic import queries as epic_queries
from tt.domains.milestone import queries, schemas
from tt.domains.milestone.models import Milestone
from tt.platform.actions import (
    ActionDeps,
    ActionGroup,
    ActionResponse,
    Conflict,
    Empty,
    Invalid,
    ObjectAction,
)
from tt.platform.refs import NamedRef

milestone_actions: ActionGroup[Milestone] = ActionGroup(
    "milestone", locate=queries.resolve_by_title
)


def _issues(n: int) -> str:
    return "1 issue" if n == 1 else f"{n} issues"


def resolve_epic(deps: ActionDeps, epic_title: str | None, project_slug: str) -> int | None:
    """The id of the live epic a milestone is filed under, addressed by its title
    within the milestone's project, or ``None`` to leave it unfiled. An epic the
    project does not hold is refused."""
    if epic_title is None:
        return None
    epic = epic_queries.resolve_by_title(deps.tx, NamedRef(project_slug, epic_title))
    if epic is None:
        raise Conflict(f'no epic "{epic_title}"')
    return epic.id


@milestone_actions
class EditMilestone(ObjectAction[Milestone, schemas.EditMilestonePayload]):
    # One whole-object edit: the payload carries every editable field and each is
    # set. The due date rides along here; the focused move is ``SetDueDate``.
    KEY = "edit"
    LABEL = "Edit"
    Payload = schemas.EditMilestonePayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Milestone, payload: schemas.EditMilestonePayload, deps: ActionDeps
    ) -> ActionResponse:
        title = payload.title.strip()
        if not title:
            raise Invalid("title is required")
        # A milestone is addressed by title within its project, so a rename onto one a
        # live sibling already holds is refused — the partial unique index guarantees
        # it; this is what turns that into a sentence.
        if queries.title_owner(deps.tx, obj.project_id, title, exclude_id=obj.id) is not None:
            raise Conflict(f'a milestone "{title}" already exists')
        obj.title = title
        obj.due_date = payload.due_date
        obj.epic_id = resolve_epic(deps, payload.epic, obj.project.slug)
        return ActionResponse(message=f"{obj.subject()}: saved")


@milestone_actions
class SetDueDate(ObjectAction[Milestone, schemas.SetDueDatePayload]):
    # The direct due-date move. A null payload clears it — no date is a real state,
    # not a refusal.
    KEY = "setDueDate"
    LABEL = "Set due date"
    Payload = schemas.SetDueDatePayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Milestone, payload: schemas.SetDueDatePayload, deps: ActionDeps
    ) -> ActionResponse:
        obj.due_date = payload.due_date
        when = payload.due_date.isoformat() if payload.due_date is not None else "cleared"
        return ActionResponse(message=f"{obj.subject()}: due {when}")


@milestone_actions
class Delete(ObjectAction[Milestone, Empty]):
    # The soft delete, refused while the milestone still holds live issues so no
    # live issue is left pointing at a deleted milestone.
    KEY = "delete"
    LABEL = "Delete"
    Payload = Empty

    @classmethod
    def is_disabled(cls, obj: Milestone) -> str | None:
        open_issues = obj.issue_count()
        if open_issues > 0:
            return f"reassign or close {_issues(open_issues)} first"
        return None

    @classmethod
    def execute(cls, obj: Milestone, payload: Empty, deps: ActionDeps) -> ActionResponse:
        obj.deleted_at = datetime.now(UTC)
        return ActionResponse(message=f"{obj.subject()}: deleted")
