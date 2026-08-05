"""The comment domain: its type in ``models``, its reads in ``queries``, its
actions in ``actions`` and its public surface in ``api``. A comment is the
thinnest owned child of an issue — a body and the timestamps every row carries,
with no status of its own, so it has no ``enums`` module. It is created through
the issue it hangs off (``issue.addComment``) and edited or deleted on its own."""

from tt.domains.comment.models import Comment

__all__ = ["Comment"]
