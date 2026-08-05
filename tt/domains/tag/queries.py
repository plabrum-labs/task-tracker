"""Reads against the tags table: the live list, one tag by id, and one by name.

Liveness is the tag's own ``deleted_at``. The by-name read is what ``createTag``
and an issue's ``tagIssue``/``untagIssue`` resolve through — at most one live tag
holds a name, since the partial unique index covers live rows.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from tt.domains.tag.models import Tag

_loaded = select(Tag).order_by(Tag.name.asc())


def list_tags(db: Session) -> list[Tag]:
    """Live tags in name order."""
    return list(db.scalars(_loaded.where(Tag.deleted_at.is_(None))))


def get_tag(db: Session, tag_id: int) -> Tag | None:
    """The one live tag by id, or ``None``."""
    return db.scalars(_loaded.where(Tag.deleted_at.is_(None), Tag.id == tag_id)).first()


def get_tag_by_name(db: Session, name: str) -> Tag | None:
    """The one live tag of a name, or ``None`` — at most one, since the partial
    unique index covers live rows."""
    return db.scalars(_loaded.where(Tag.deleted_at.is_(None), Tag.name == name)).first()
