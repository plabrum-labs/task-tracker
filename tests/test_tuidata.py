"""The TUI's write side: one addressed target routed to the domain api that owns it.

``dispatch`` is the whole of it, so each case seeds a row through the create actions
and asserts the write landed on that row — no widget and no event loop, just the
routing a menu row's target picks out.
"""

import pytest
from sqlalchemy import Engine

from tt.domains.epic import api as epic_api
from tt.domains.epic.schemas import EpicListItem
from tt.domains.issue import api as issue_api
from tt.domains.milestone import api as milestone_api
from tt.domains.milestone.schemas import MilestoneListItem
from tt.domains.project import api as project_api
from tt.domains.tag import api as tag_api
from tt.frontend.tui import data
from tt.frontend.tui.domainview import (
    EpicTarget,
    IssueTarget,
    MilestoneTarget,
    ProjectTarget,
    TagTarget,
)
from tt.platform.actions import Conflict


@pytest.fixture
def seeded(db: Engine) -> Engine:
    """One project holding an epic, a milestone filed under it, an issue, and one tag."""
    project_api.project_action(db, "createProject", {"slug": "tt", "title": "task tracker"}, None)
    project_api.project_action(db, "addEpic", {"title": "the parser"}, "tt")
    project_api.project_action(db, "addIssue", {"title": "ship the mvp"}, "tt")
    project_api.project_action(db, "addMilestone", {"title": "beta", "epic": "the parser"}, "tt")
    tag_api.tag_action(db, "createTag", {"name": "api"}, None)
    return db


def _epic(engine: Engine) -> EpicListItem:
    return epic_api.epic_list(engine, "tt")[0]


def _milestone(engine: Engine) -> MilestoneListItem:
    return milestone_api.milestone_list(engine, "tt")[0]


def _tag_id(engine: Engine) -> int:
    return next(t.id for t in tag_api.tag_list(engine) if t.name == "api")


def test_dispatch_routes_an_epic_target_to_the_epic_api(seeded: Engine) -> None:
    title = _epic(seeded).title
    message = data.dispatch(seeded, EpicTarget("tt", title), "setStatus", {"status": "done"})
    assert "done" in message
    written = epic_api.epic_get(seeded, "tt", title)
    assert written is not None and written.status == "done"


def test_dispatch_routes_a_milestone_target_to_the_milestone_api(seeded: Engine) -> None:
    title = _milestone(seeded).title
    data.dispatch(
        seeded,
        MilestoneTarget("tt", title),
        "edit",
        {"title": "beta cut", "due_date": None, "epic": None},
    )
    written = milestone_api.milestone_get(seeded, "tt", "beta cut")
    assert written is not None and written.title == "beta cut"


def test_dispatch_routes_a_tag_target_to_the_tag_api(seeded: Engine) -> None:
    tag_id = _tag_id(seeded)
    data.dispatch(seeded, TagTarget(tag_id), "rename", {"name": "parser"})
    assert [t.name for t in tag_api.tag_list(seeded)] == ["parser"]
    # A tag addressing none is the top-level create, the way ``createProject`` runs
    # against no project.
    data.dispatch(seeded, TagTarget(None), "createTag", {"name": "api"})
    assert sorted(t.name for t in tag_api.tag_list(seeded)) == ["api", "parser"]


def test_dispatch_carries_a_refusal_back_from_the_object_it_addressed(seeded: Engine) -> None:
    # The epic still holds a live issue, so its delete refuses — the object's answer,
    # raised for the screen to put in the status line rather than written.
    title = _epic(seeded).title
    issue = issue_api.issue_list(seeded, "tt")[0]
    data.dispatch(
        seeded,
        IssueTarget(issue.ref),
        "edit",
        {
            "title": issue.title,
            "body": "",
            "status": issue.status,
            "priority": issue.priority,
            "due_date": None,
            "epic": title,
            "milestone": None,
        },
    )
    with pytest.raises(Exception, match="reassign or close 1 issue first"):
        data.dispatch(seeded, EpicTarget("tt", title), "delete", {})
    assert epic_api.epic_get(seeded, "tt", title) is not None


def test_dispatch_still_routes_the_targets_that_were_here_before(seeded: Engine) -> None:
    # The new cases sit alongside the old ones rather than replacing them.
    message = data.dispatch(seeded, ProjectTarget("tt"), "addIssue", {"title": "triage inbox"})
    assert "created" in message
    assert "triage inbox" in [i.title for i in issue_api.issue_list(seeded, "tt")]
    with pytest.raises(Conflict):
        data.dispatch(seeded, TagTarget(None), "createTag", {"name": "api"})
