"""What driving the domain from outside looks like: read what an object offers,
pick one, supply its arguments. A TUI user and an agent do the same thing, so this
is roughly ``tt project show tt`` followed by ``tt project addIssue tt …``.

Against a throwaway in-memory database, so it prints the same thing every time it
is run. The action layer needs the connection now — an ``execute`` writes for
itself and returns a message — so this drives the real ``api`` rather than a set
of literals.
"""

import json
from typing import Any

from tt import schema
from tt.domains.issue import api as issue_api
from tt.domains.project import api as project_api
from tt.platform import db
from tt.platform.actions import Enum, Field, Offer, OptionalText, Refused, Runnable, Text


def _field_json(field: Field) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": field.name,
        "required": field.required,
        "description": field.description,
    }
    match field.kind:
        case Text():
            return {**base, "type": "text"}
        case OptionalText():
            return {**base, "type": "text", "optional": True}
        case Enum(values=values):
            return {**base, "type": "enum", "values": values}


def _show(title: str, offers: list[Offer]) -> None:
    print(f"--- {title}")
    for offer in offers:
        match offer.state:
            case Runnable():
                state = "runnable"
            case Refused(reason):
                state = f"refused: {reason}"
        print(f"{offer.key!r} ({state})")
        print(json.dumps([_field_json(field) for field in offer.fields], indent=2))


def main() -> None:
    engine = db.connect("sqlite://")
    schema.create_all(engine)

    # No projects yet: the top-level creator, the one thing there is to offer
    # before any project exists.
    _show("no projects yet", project_api.top_level_offers())

    # Make one, then add an issue under it — each a real dispatch inside its own
    # transaction, exactly as a frontend would run it.
    with db.transaction(engine) as tx:
        project_api.trigger(tx, "createProject", {"slug": "tt", "title": "task tracker"})

    with db.transaction(engine) as tx:
        project_api.trigger(tx, "addIssue", {"title": "ship the mvp", "priority": "high"}, "tt")

    with db.reading(engine) as session:
        _, project_offers = project_api.show(session, "tt")
        _show("the project, with one issue to do", project_offers)
        for item in issue_api.list_issues(session, "tt"):
            _, issue_offers = issue_api.show(session, item.id)
            _show("the issue", issue_offers)


if __name__ == "__main__":
    main()
