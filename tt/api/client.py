"""``TtClient``: the stable, typed way to drive tt from another program.

A client holds one ``Engine`` and delegates to the domain ``api`` modules — the
same engine-in, one-transaction-per-call surface the CLI uses — then maps their
internal schemas to the public dataclasses in ``types``. A refusal from a write
(the domain's ``Invalid``/``Conflict``) becomes ``TtRefused``; a machine failure
propagates untouched. A missing row on a read becomes ``TtNotFound``.

The surface is intentionally small — the five operations a programmatic consumer
needs today — and additive: more can be added without breaking a pinned caller.
"""

from sqlalchemy import Engine

from tt import schema
from tt.api import _adapt
from tt.api.enums import IssueStatus
from tt.api.exceptions import TtNotFound, TtRefused
from tt.api.types import Comment, Epic, Issue, IssueSummary
from tt.domains.comment import api as comment_api
from tt.domains.epic import api as epic_api
from tt.domains.issue import api as issue_api
from tt.domains.project import api as project_api
from tt.platform.actions import REFUSALS


class TtClient:
    """A connection to a tt database. Construct with an explicit database
    location, or ``None`` for the shared per-user file the CLI opens."""

    def __init__(self, db: str | None = None) -> None:
        self._engine = schema.open_db(db)

    @classmethod
    def from_engine(cls, engine: Engine) -> "TtClient":
        """Wrap an already-open engine instead of resolving one. The seam a test
        or an embedding host uses to hand the client its own database."""
        client = cls.__new__(cls)
        client._engine = engine
        return client

    # --- reads ------------------------------------------------------------

    def list_issues(self, *, project: str | None = None) -> list[IssueSummary]:
        """The issues of one project, or of every project when ``project`` is
        ``None``, as list-view rows."""
        if project is not None:
            slugs = [project]
        else:
            slugs = [row.slug for row in project_api.project_list(self._engine)]
        return [
            _adapt.summary_from(item)
            for slug in slugs
            for item in issue_api.issue_list(self._engine, slug)
        ]

    def show_issue(self, ref: str) -> Issue:
        """The full issue a ref names. Raises ``TtNotFound`` if no such issue."""
        detail = issue_api.issue_get(self._engine, ref)
        if detail is None:
            raise TtNotFound(f"no issue {ref}")
        return _adapt.issue_from(detail)

    def show_epic(self, project: str, title: str) -> Epic:
        """The epic titled ``title`` in project ``project``. An epic is addressed
        by title within its project, not by a ref. Raises ``TtNotFound`` if no
        such epic."""
        detail = epic_api.epic_get(self._engine, project, title)
        if detail is None:
            raise TtNotFound(f"no epic {title!r} in {project}")
        return _adapt.epic_from(detail)

    # --- writes -----------------------------------------------------------

    def set_status(self, ref: str, status: IssueStatus) -> Issue:
        """Move an issue to ``status`` and return the updated issue. Raises
        ``TtRefused`` if the tracker declines."""
        try:
            issue_api.issue_action(self._engine, "setStatus", {"status": status.value}, ref)
        except REFUSALS as error:
            raise TtRefused(str(error)) from error
        return self.show_issue(ref)

    def add_comment(self, ref: str, body: str) -> Comment:
        """Add a comment to an issue and return the created comment. Raises
        ``TtRefused`` if the tracker declines."""
        try:
            response = issue_api.issue_action(self._engine, "addComment", {"body": body}, ref)
        except REFUSALS as error:
            raise TtRefused(str(error)) from error
        # ``addComment`` always reports the row it created; a missing id would be
        # a contract break in the domain, not a state a consumer can reach.
        assert response.created_id is not None
        created = comment_api.comment_get(self._engine, response.created_id)
        assert created is not None
        return _adapt.comment_from(created)
