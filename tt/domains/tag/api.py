"""The tag domain's one public surface.

As ``../project/api.py``: a frontend hands it an engine, never a session, and
every database call is wrapped in ``@with_transaction``. ``tag_list``/``tag_detail``
read, ``tag_action`` writes — an object action passes the tag's id as its address, the
top-level ``createTag`` passes ``None`` and queries for itself. ``top_level_offers``
and ``action_schemas`` ask nothing of the database, so they take no engine.
"""

from typing import Any

from sqlalchemy.orm import Session

from tt.domains.tag import queries, schemas
from tt.domains.tag.actions import tag_actions
from tt.domains.tag.models import Tag
from tt.platform.actions import ActionDeps, ActionResponse, Field, Invalid, Offer
from tt.platform.db import with_transaction

# --- reads ----------------------------------------------------------------


def _list_item(tag: Tag) -> schemas.TagListItem:
    return schemas.TagListItem(id=tag.id, name=tag.name)


@with_transaction
def tag_list(tx: Session) -> list[schemas.TagListItem]:
    return [_list_item(tag) for tag in queries.list_tags(tx)]


@with_transaction
def tag_detail(tx: Session, tag_id: int) -> tuple[schemas.TagListItem, list[Offer]]:
    """A tag and what it offers, for a menu opened against it. A tag has no detail
    shape of its own — it is a label — so the list item is the whole of it."""
    tag = queries.get_tag(tx, tag_id)
    if tag is None:
        raise Invalid(f"no tag {tag_id}")
    return _list_item(tag), tag_actions.offers(tag)


def top_level_offers() -> list[Offer]:
    """What the group offers with no tag to address — ``createTag``."""
    return tag_actions.top_level_offers()


# --- write ----------------------------------------------------------------


@with_transaction
def tag_action(tx: Session, key: str, payload: Any, tag_id: int | None = None) -> ActionResponse:
    """Run an action by key. An object action passes the tag's id as its address;
    the top-level ``createTag`` passes ``None`` and queries for itself."""
    return tag_actions.trigger(ActionDeps(tx), key, payload, tag_id)


# --- codegen --------------------------------------------------------------


def action_schemas() -> list[tuple[str, list[Field]]]:
    """Every object-action key a tag answers to, with the fields its payload asks
    for."""
    return tag_actions.action_schemas()
