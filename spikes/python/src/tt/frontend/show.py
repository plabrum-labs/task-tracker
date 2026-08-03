"""What the erased path looks like from outside: read the object, see what it
offers, pick one, supply arguments. A TUI user and an agent do the same thing, so
this is roughly ``tt project show tt`` followed by ``tt project addIssue tt …``.

Against a throwaway in-memory database, so it prints the same thing every time it
is run. The action layer needs the connection now — an ``execute`` writes for
itself and returns a message — so this drives the real path rather than a set of
literals.
"""

import json
from typing import Any

from tt import schema
from tt.domains.issue import actions as issue_actions
from tt.domains.issue import services as issue_services
from tt.domains.project import actions as project_actions
from tt.domains.project import services as project_services
from tt.platform import db, wire
from tt.platform.action import Refused, Runnable


def _show(title: str, group: wire.Group[Any], obj: Any) -> None:
    print(f"--- {title}")
    for entry, offered in wire.available(group, obj):
        match offered:
            case Runnable():
                state = "runnable"
            case Refused(reason):
                state = f"refused: {reason}"
        print(f"{entry.key!r} ({state})")
        print(json.dumps(entry.schema, indent=2))


def main() -> None:
    engine = db.connect("sqlite://")
    schema.create_all(engine)

    # No projects yet: the root creator, offered against the empty list it is
    # checked against.
    with db.reading(engine) as session:
        _show("no projects yet", project_actions.root(), project_services.list_projects(session))

    # Make one, then add an issue under it — each a real dispatch inside its own
    # transaction, exactly as a frontend would run it.
    with db.transaction(engine) as tx:
        projects = project_services.list_projects(tx)
        wire.dispatch(
            project_actions.root(),
            projects,
            "createProject",
            {"slug": "tt", "title": "task tracker"},
            tx,
        )

    with db.transaction(engine) as tx:
        project = project_services.get_project(tx, "tt")
        assert project is not None
        wire.dispatch(
            project_actions.group(),
            project,
            "addIssue",
            {"title": "ship the mvp", "priority": "high"},
            tx,
        )

    with db.reading(engine) as session:
        project = project_services.get_project(session, "tt")
        assert project is not None
        _show("the project, with one issue to do", project_actions.group(), project)
        for issue in issue_services.list_issues(session, "tt"):
            _show("the issue", issue_actions.group(), issue)


if __name__ == "__main__":
    main()
