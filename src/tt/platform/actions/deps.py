"""What an action is handed besides its object and its payload.

``ActionDeps`` is the seam. Today it carries only the open transaction; an MCP or
an HTTP edge grows the request-scoped things an ``execute`` needs — a user, a
clock, a task queue — into this one dataclass, and no ``execute`` signature
changes to receive them. Keeping it here rather than passing a bare ``Session``
is the whole of what buys that.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ActionDeps:
    """The request-scoped dependencies an action's ``execute`` is given."""

    tx: Session
