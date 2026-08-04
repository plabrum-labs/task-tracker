"""The issue domain: its vocabularies in ``enums``, its type in ``models``, its
reads in ``queries``, its actions in ``actions`` and its public surface in
``api``."""

from tt.domains.issue.enums import Priority, Status
from tt.domains.issue.models import Issue

__all__ = ["Issue", "Priority", "Status"]
