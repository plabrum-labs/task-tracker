"""The TUI state machine, driven with no terminal and no pty.

``on_key`` is pure and ``render_lines`` draws into a list of strings, so the whole
reducer is assertable against an in-memory database seeded through the domain
services — the same three steps ``TrackerApp`` runs per keystroke, minus the
terminal. That is what splitting the key handler from the write is for.
"""

import pytest
from sqlalchemy import Engine

from tt.domains.issue import Draft as IssueDraft
from tt.domains.issue import Priority, Status
from tt.domains.issue import services as issue_services
from tt.domains.project import Draft as ProjectDraft
from tt.domains.project import Status as ProjectStatus
from tt.domains.project import services as project_services
from tt.frontend import tui
from tt.frontend.tui import (
    Back,
    Choosing,
    Cycle,
    Detail,
    Do,
    EnterIntent,
    Ignored,
    Insert,
    Issues,
    Move,
    Projects,
    Quit,
    State,
    Submit,
)
from tt.platform import db as platform_db


def _seed_tt(engine: Engine) -> None:
    """One project ``tt`` titled "task tracker" with one high-priority issue "ship
    the mvp", seeded through the create services — the same path a frontend takes."""
    with platform_db.transaction(engine) as tx:
        project = project_services.create_project(
            tx, ProjectDraft(slug="tt", title="task tracker", body="")
        )
        draft = IssueDraft(title="ship the mvp", body="", priority=Priority.HIGH)
        issue_services.create_issue(tx, project, draft)


@pytest.fixture
def seeded(db: Engine) -> Engine:
    _seed_tt(db)
    return db


def press(engine: Engine, state: State, key: str) -> State:
    """One keystroke: the pure ``on_key`` then the ``apply`` that may write, exactly
    as ``TrackerApp`` runs them."""
    return tui.apply(engine, state, tui.on_key(state, key))


def labels(state: State) -> list[str]:
    return [tui.row_label(row) for row in state.rows]


def test_the_first_screen_offers_the_root_creator_and_the_projects(seeded: Engine) -> None:
    state = tui.start(seeded)
    assert state.screen == Projects()
    names = labels(state)
    assert names[0] == "createProject"
    assert names[1].startswith("tt")
    assert any("> createProject" in line for line in tui.render_lines(state))


def test_a_key_means_the_same_thing_whether_or_not_anything_runs(seeded: Engine) -> None:
    # ``on_key`` is pure, so this is the whole of what it decides.
    state = tui.start(seeded)
    assert tui.on_key(state, "down") == Move(1)
    assert tui.on_key(state, "up") == Move(-1)
    assert tui.on_key(state, "enter") == EnterIntent()
    assert tui.on_key(state, "q") == Quit()
    assert tui.on_key(state, "z") == Ignored()

    # The same keys mean form-things once a form is up.
    filling = press(seeded, state, "enter")
    assert filling.form is not None
    assert tui.on_key(filling, "q") == Insert("q")
    assert tui.on_key(filling, "right") == Cycle(1)
    assert tui.on_key(filling, "enter") == Submit()
    assert tui.on_key(filling, "escape") == Back()


def test_the_three_screens_are_reached_by_moving_and_pressing_enter(seeded: Engine) -> None:
    state = tui.start(seeded)

    # Down past ``createProject`` onto the project, then in.
    state = press(seeded, state, "down")
    state = press(seeded, state, "enter")
    assert state.screen == Issues("tt")
    assert "project tt" in state.header
    rows = labels(state)
    assert rows[:4] == [
        "editTitle",
        "editBody",
        "editStatus",
        "delete (archive it first)",
    ]
    # A refusal is rendered rather than obeyed, and it says why.
    assert any("delete (archive it first)" in line for line in tui.render_lines(state))

    # Past the four actions and the creator onto the issue, then in.
    for _ in range(5):
        state = press(seeded, state, "down")
    state = press(seeded, state, "enter")
    assert state.screen == Detail(project="tt", id=1)
    assert "ship the mvp" in state.header
    assert labels(state) == ["editTitle", "editBody", "editStatus", "editPriority", "delete"]

    # Escape walks back out the way it came in, and stops at the root.
    state = press(seeded, state, "escape")
    assert state.screen == Issues("tt")
    state = press(seeded, state, "escape")
    assert state.screen == Projects()
    state = press(seeded, state, "escape")
    assert state.screen == Projects()


def test_an_enum_field_is_a_cycling_selector_and_a_text_field_is_typed_into(
    seeded: Engine,
) -> None:
    state = tui.start(seeded)
    for _ in range(2):
        state = press(seeded, state, "down")
    state = press(seeded, state, "enter")  # into the project
    for _ in range(5):
        state = press(seeded, state, "down")
    state = press(seeded, state, "enter")  # into the issue
    for _ in range(2):
        state = press(seeded, state, "down")
    state = press(seeded, state, "enter")  # editStatus

    form = state.form
    assert form is not None
    assert form.key == "editStatus"
    assert form.entries[0].control == Choosing(values=["todo", "doing", "done"], index=0)
    # The label is the doc comment, not the property name.
    assert any("Where the issue is up to." in line for line in tui.render_lines(state)), (
        tui.render_lines(state)
    )

    state = press(seeded, state, "right")
    assert _value(state, 0) == "doing"
    # It wraps, so a selector never has a blank state to mean "absent".
    for _ in range(2):
        state = press(seeded, state, "right")
    assert _value(state, 0) == "todo"
    state = press(seeded, state, "right")

    # Tab to the note, type into it, rub a character out.
    state = press(seeded, state, "tab")
    for char in "started!":
        state = press(seeded, state, char)
    state = press(seeded, state, "backspace")
    assert _value(state, 1) == "started"

    state = press(seeded, state, "enter")
    assert state.form is None, "a successful write closes the form"
    with platform_db.reading(seeded) as session:
        issue = issue_services.get_issue(session, 1)
    assert issue is not None
    assert issue.status == Status.DOING
    assert issue.status_note == "started"


def test_a_refusal_leaves_the_form_up_with_its_values_intact(seeded: Engine) -> None:
    state = tui.start(seeded)
    state = press(seeded, state, "enter")  # createProject

    for char in "tt":
        state = press(seeded, state, char)
    state = press(seeded, state, "enter")

    assert state.form is not None, "the form should still be up"
    assert _value(state, 0) == "tt"
    assert "already exists" in state.status


def test_deleting_what_the_screen_is_about_falls_out_to_the_parent(db: Engine) -> None:
    # Nothing here names ``delete``. The screen is reloaded after every write and
    # the fall-out is what happens when the object it was about has gone.
    _seed_tt(db)
    # Empty the project of live issues and archive it, so ``delete`` is runnable.
    with platform_db.transaction(db) as tx:
        issue = issue_services.list_issues(tx, "tt")[0]
        issue_services.delete_issue(tx, issue)
        archived = project_services.get_project(tx, "tt")
        assert archived is not None
        archived.status = ProjectStatus.ARCHIVED

    state = tui.start(db)
    state = press(db, state, "down")
    state = press(db, state, "enter")
    assert state.screen == Issues("tt")

    # Down to ``delete``, which is runnable now the project is archived.
    for _ in range(3):
        state = press(db, state, "down")
    row = tui.selected_row(state)
    assert isinstance(row, Do)
    assert row.key == "delete"
    state = press(db, state, "enter")  # the form has no fields
    state = press(db, state, "enter")  # submit it

    assert state.screen == Projects()
    with platform_db.reading(db) as session:
        assert project_services.get_project(session, "tt") is None


def _value(state: State, index: int) -> str:
    assert state.form is not None
    return tui.control_value(state.form.entries[index].control)
