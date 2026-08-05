"""Everything a tag can be asked.

``createTag`` is the top-level create, the mirror of ``createProject``: no object
to address, so the duplicate check queries the live tags through the transaction
itself. ``rename`` and ``delete`` are the object actions. A rename guards the same
live-only uniqueness the create does; a delete clears the tag's issue links before
it stamps ``deleted_at``, so no issue is left wearing a deleted tag. The flush is
the group's, once.
"""

from datetime import UTC, datetime

from sqlalchemy import delete

from tt.domains.tag import queries, schemas
from tt.domains.tag.models import Tag, issue_tags
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

tag_actions: ActionGroup[Tag] = ActionGroup("tag", locate=queries.get_tag)


@tag_actions
class CreateTag(TopLevelAction[schemas.CreateTagPayload]):
    # No object to address: the duplicate check queries the live tags through the
    # transaction itself. The partial unique index guarantees it; this is what turns
    # a would-be constraint violation into a sentence.
    KEY = "createTag"
    LABEL = "Create tag"
    Payload = schemas.CreateTagPayload

    @classmethod
    def execute(cls, payload: schemas.CreateTagPayload, deps: ActionDeps) -> ActionResponse:
        name = payload.name.strip()
        if not name:
            raise Invalid("name is required")
        if queries.get_tag_by_name(deps.tx, name) is not None:
            raise Conflict(f'tag "{name}" already exists')
        tag = Tag(name=name)
        deps.tx.add(tag)
        deps.tx.flush()
        deps.tx.refresh(tag, ["created_at", "updated_at"])
        return ActionResponse(message=f"{tag.subject()}: created", created_id=tag.id)


@tag_actions
class Rename(ObjectAction[Tag, schemas.RenameTagPayload]):
    # The one edit a tag has. It guards the same live-only uniqueness the create
    # does, read here as a sentence rather than a constraint violation.
    KEY = "rename"
    LABEL = "Rename"
    Payload = schemas.RenameTagPayload
    SEED_FROM_TARGET = True

    @classmethod
    def execute(
        cls, obj: Tag, payload: schemas.RenameTagPayload, deps: ActionDeps
    ) -> ActionResponse:
        name = payload.name.strip()
        if not name:
            raise Invalid("name is required")
        existing = queries.get_tag_by_name(deps.tx, name)
        if existing is not None and existing.id != obj.id:
            raise Conflict(f'tag "{name}" already exists')
        obj.name = name
        return ActionResponse(message=f"{obj.subject()}: renamed")


@tag_actions
class Delete(ObjectAction[Tag, Empty]):
    # The soft delete. The tag's issue links have no lifecycle of their own, so they
    # are removed outright first — the tag then stamps ``deleted_at`` and leaves no
    # issue wearing it, and its name is free for a new tag to take.
    KEY = "delete"
    LABEL = "Delete"
    Payload = Empty

    @classmethod
    def execute(cls, obj: Tag, payload: Empty, deps: ActionDeps) -> ActionResponse:
        deps.tx.execute(delete(issue_tags).where(issue_tags.c.tag_id == obj.id))
        obj.deleted_at = datetime.now(UTC)
        return ActionResponse(message=f"{obj.subject()}: deleted")
