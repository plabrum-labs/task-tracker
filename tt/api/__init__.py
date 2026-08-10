"""tt's public client API: a stable, typed, importable surface for programmatic
consumers.

Import ``TtClient`` and the read shapes from here and nothing beneath — this
package is the versioned contract, and the modules under it are its internals.
A consumer pins tt and bumps deliberately; a breaking shape change is a visible,
versioned event, never a silent wire change.
"""

from tt.api.client import TtClient
from tt.api.enums import EpicStatus, IssueStatus, Priority
from tt.api.exceptions import TtError, TtNotFound, TtRefused
from tt.api.types import Comment, Epic, Issue, IssueSummary

__all__ = [
    "Comment",
    "Epic",
    "EpicStatus",
    "Issue",
    "IssueStatus",
    "IssueSummary",
    "Priority",
    "TtClient",
    "TtError",
    "TtNotFound",
    "TtRefused",
]
