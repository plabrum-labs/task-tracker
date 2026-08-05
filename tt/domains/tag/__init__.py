"""The tag domain: its type and association table in ``models``, its reads in
``queries``, its actions in ``actions`` and its public surface in ``api``. A tag is
the one non-exclusive, cross-cutting axis — a global vocabulary, many-to-many to
issues only, for slicing across projects."""

from tt.domains.tag.models import Tag, issue_tags

__all__ = ["Tag", "issue_tags"]
