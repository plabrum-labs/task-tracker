"""The TUI state machine, driven with no terminal and no pty.

The pure helpers (``columns``, ``fits``, selection resolution, the command a menu
draws from an offer) are asserted directly. The reducer — ``on_key`` then the
``apply`` that may write — is driven against an in-memory database seeded through
the domain ``api``, exactly the two steps ``TrackerApp`` runs per keystroke minus
the terminal. Nothing here names a Textual widget.
"""

import pytest
from sqlalchemy import Engine

from tt.domains.issue import api as issue_api
from tt.domains.issue.schemas import IssueListItem
from tt.domains.project import api as project_api
from tt.frontend import tui
from tt.frontend.tui import (
    AllScope,
    CaptureOverlay,
    Choosing,
    CloseOverlay,
    Editing,
    FormOverlay,
    Ignored,
    ListOverlay,
    MoveRow,
    OpenOverlay,
    OverlayMove,
    OverlayPick,
    OverlayType,
    ProjectScope,
    RunAccelerator,
    SettingsCommit,
    SettingsCycle,
    SettingsMove,
    SettingsOverlay,
    ShiftProject,
    StartFilter,
    State,
)
from tt.platform.config import ThemeName

# --- helpers --------------------------------------------------------------


def _item(issue_id: int, status: str = "todo", priority: str = "normal") -> IssueListItem:
    return IssueListItem(
        id=issue_id, project="tt", title=f"issue {issue_id}", status=status, priority=priority
    )


def _seed(engine: Engine) -> None:
    """One project ``tt`` at ``/repo/tt`` with two issues, high before normal — the
    order a scope loads them in."""
    project_api.project_action(
        engine, "createProject", {"slug": "tt", "title": "task tracker", "path": "/repo/tt"}
    )
    project_api.project_action(
        engine, "addIssue", {"title": "ship the mvp", "priority": "high"}, "tt"
    )
    project_api.project_action(
        engine, "addIssue", {"title": "write readme", "priority": "normal"}, "tt"
    )


@pytest.fixture
def seeded(db: Engine) -> Engine:
    _seed(db)
    return db


def press(engine: Engine, state: State, key: str) -> State:
    """One keystroke: the pure ``on_key`` then the ``apply`` that may write."""
    return tui.apply(engine, state, tui.on_key(state, key))


# --- reducer: loading -----------------------------------------------------


def test_start_loads_the_scope_and_selects_the_first_issue(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    assert [i.title for i in state.issues] == ["ship the mvp", "write readme"]
    assert state.selected_id == state.issues[0].id


# --- reducer: keys mean different things in different contexts -------------


def test_a_key_means_a_move_while_browsing_and_a_letter_in_a_menu(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    assert tui.on_key(state, "j") == MoveRow(1)
    assert tui.on_key(state, "x") == OpenOverlay("issue")
    assert tui.on_key(state, "d") == RunAccelerator("delete")
    assert tui.on_key(state, "s") == Ignored()  # the per-field accelerators are gone
    assert tui.on_key(state, "/") == StartFilter()
    assert tui.on_key(state, "]") == ShiftProject(1)
    assert tui.on_key(state, "z") == Ignored()

    menu = press(seeded, state, "x")
    assert isinstance(menu.overlay, ListOverlay)
    assert tui.on_key(menu, "j") == OverlayType("j")  # letters filter the menu
    assert tui.on_key(menu, "down") == OverlayMove(1)
    assert tui.on_key(menu, "enter") == OverlayPick()
    assert tui.on_key(menu, "escape") == CloseOverlay()


def test_the_spacebar_reaches_a_form_as_a_character(seeded: Engine) -> None:
    """The terminal maps the spacebar to the character itself, not a named ``space``
    key that every text handler drops. It stays the issue-menu accelerator while
    browsing, and types a literal space into a form field."""
    from textual import events

    space = events.Key("space", " ")
    assert tui._key_string(space) == " "  # not the named "space"

    state = tui.start(seeded, ProjectScope("tt"))
    assert tui.on_key(state, " ") == OpenOverlay("issue")

    state = press(seeded, state, "x")  # issue menu
    form = press(seeded, state, "enter")  # pick Edit; the title box is focused first
    key = tui._key_string(space)
    assert key is not None
    form = tui.apply(seeded, form, tui.on_key(form, key))
    assert isinstance(form.overlay, FormOverlay)
    # The title box opens pre-filled with the issue's title; the space appends to it.
    assert form.overlay.entries[0].control == Editing("ship the mvp ")


def test_the_issue_menu_lists_exactly_what_the_issue_offers(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state = press(seeded, state, "x")
    assert isinstance(state.overlay, ListOverlay)
    labels = [c.label for c in state.overlay.commands]
    assert labels == ["Edit", "Delete"]


# --- reducer: writes ------------------------------------------------------


def test_the_edit_form_opens_pre_filled_from_the_issue(seeded: Engine) -> None:
    # The first issue is the high-priority "ship the mvp", still todo. The form seeds
    # each control from its current value rather than a blank/index-0 default.
    state = tui.start(seeded, ProjectScope("tt"))
    state = press(seeded, state, "x")  # issue menu
    state = press(seeded, state, "enter")  # pick Edit
    assert isinstance(state.overlay, FormOverlay)
    entries = state.overlay.entries  # title, body, status, status_note, priority
    assert entries[0].control == Editing("ship the mvp")
    assert entries[2].control == Choosing(["todo", "doing", "done"], 0)  # todo
    assert entries[4].control == Choosing(["normal", "high"], 1)  # high, not index 0


def test_editing_an_issue_status_through_the_menu_writes(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    issue_id = state.selected_id
    assert issue_id is not None

    state = press(seeded, state, "x")  # issue menu
    state = press(seeded, state, "enter")  # pick Edit — the form is pre-filled
    assert isinstance(state.overlay, FormOverlay)
    assert state.overlay.key == "edit"
    # The whole object is one form: title, body, status, note, priority in order.
    state = press(seeded, state, "down")  # title -> body
    state = press(seeded, state, "down")  # body -> status
    assert isinstance(state.overlay, FormOverlay)
    assert state.overlay.entries[2].control == Choosing(values=["todo", "doing", "done"], index=0)
    state = press(seeded, state, "right")  # todo -> doing
    state = press(seeded, state, "enter")  # submit; the title was pre-filled, so no refusal
    assert state.overlay is None

    detail = issue_api.issue_get(seeded, issue_id)
    assert detail is not None
    assert detail.status == "doing"
    assert detail.title == "ship the mvp"  # the pre-filled title round-tripped untouched


def test_capture_adds_an_issue_to_the_scoped_project(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state = press(seeded, state, "n")
    assert isinstance(state.overlay, CaptureOverlay)
    for char in "triage inbox":
        state = press(seeded, state, char)
    state = press(seeded, state, "enter")
    assert state.overlay is None
    assert any(i.title == "triage inbox" for i in issue_api.issue_list(seeded, "tt"))


def test_a_blank_edit_is_refused_and_leaves_the_form_up(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state = press(seeded, state, "x")  # issue menu
    state = press(seeded, state, "enter")  # pick Edit — title box focused and pre-filled
    assert isinstance(state.overlay, FormOverlay)
    for _ in "ship the mvp":  # clear the seeded title
        state = press(seeded, state, "backspace")
    state = press(seeded, state, "enter")  # submit with the title now blank
    assert isinstance(state.overlay, FormOverlay), "the form stays up on a refusal"
    assert "title is required" in state.status


def test_the_project_menu_greys_a_refused_action_and_picking_it_does_not_write(
    seeded: Engine,
) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state = press(seeded, state, "X")  # the project menu
    assert isinstance(state.overlay, ListOverlay)
    delete = next(c for c in state.overlay.commands if c.label == "Delete")
    assert delete.reason == "archive it first"  # active projects cannot be deleted

    # Move onto it and pick it: the reason lands in the status, and the project stays.
    index = [c.label for c in state.overlay.commands].index("Delete")
    for _ in range(index):
        state = press(seeded, state, "down")
    state = press(seeded, state, "enter")
    assert "archive it first" in state.status
    assert project_api.project_get(seeded, "tt") is not None


def test_deleting_the_selected_issue_moves_the_cursor_to_a_survivor(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    first = state.selected_id
    assert first is not None
    state = press(seeded, state, "d")  # delete has no fields, so it runs at once
    assert state.overlay is None
    remaining = [i.id for i in state.issues]
    assert first not in remaining
    assert state.selected_id in remaining


# --- reducer: browsing ----------------------------------------------------


def test_filter_narrows_the_visible_issues(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state = press(seeded, state, "/")
    assert state.filtering
    for char in "readme":
        state = press(seeded, state, char)
    assert [i.title for i in tui.visible_issues(state)] == ["write readme"]


def test_tab_cycles_to_the_board_only_when_it_fits(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state.size = (100, 30)
    state = press(seeded, state, "tab")
    assert state.layout == "board"

    state.layout = "list"
    state.size = (80, 30)  # too narrow for three columns
    state = press(seeded, state, "tab")
    assert state.layout == "list"


def test_shift_project_moves_the_scope_to_the_next_project(db: Engine) -> None:
    _seed(db)
    project_api.project_action(db, "createProject", {"slug": "web", "title": "website"})
    state = tui.start(db, ProjectScope("tt"))
    state = press(db, state, "]")
    assert state.scope == ProjectScope("web")


def test_shift_project_cycles_back_through_all_projects(db: Engine) -> None:
    _seed(db)
    project_api.project_action(db, "createProject", {"slug": "web", "title": "website"})
    state = tui.start(db, ProjectScope("web"))
    # ``]`` off the last project lands on all-projects, not back on the first.
    state = press(db, state, "]")
    assert state.scope == AllScope()
    # And ``[`` off all-projects wraps round to the last project.
    state = press(db, state, "[")
    assert state.scope == ProjectScope("web")


def test_switching_to_a_missing_project_falls_out_to_all_projects(db: Engine) -> None:
    _seed(db)
    state = tui.start(db, ProjectScope("tt"))
    # Archive then delete the project out from under the scope, then refresh.
    project_api.project_action(db, "edit", {"title": "tt", "body": "", "status": "archived"}, "tt")
    project_api.project_action(db, "delete", {}, "tt")
    state = press(db, state, "R")
    assert state.scope == AllScope()


# --- reducer: settings ----------------------------------------------------


def test_comma_opens_the_settings_modal_on_the_current_theme(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    assert tui.on_key(state, ",") == OpenOverlay("settings")
    state = press(seeded, state, ",")
    assert isinstance(state.overlay, SettingsOverlay)
    theme = next(s for s in state.overlay.settings if s.key == "theme")
    assert theme.options == ("Dark", "Light")
    assert theme.index == 0  # the default theme is dark, so the modal opens on Dark


def test_settings_keys_move_cycle_and_commit(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state = press(seeded, state, ",")
    assert isinstance(state.overlay, SettingsOverlay)
    assert tui.on_key(state, "up") == SettingsMove(-1)
    assert tui.on_key(state, "left") == SettingsCycle(-1)
    assert tui.on_key(state, "right") == SettingsCycle(1)
    assert tui.on_key(state, "enter") == SettingsCommit()


def test_cycling_and_committing_flips_the_theme_and_closes(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    assert state.theme == ThemeName.DARK
    state = press(seeded, state, ",")
    state = press(seeded, state, "right")  # Dark -> Light
    state = press(seeded, state, "enter")  # commit
    assert state.overlay is None
    assert state.theme == ThemeName.LIGHT


def test_cycling_wraps_and_is_pure_until_committed(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state = press(seeded, state, ",")
    state = press(seeded, state, "left")  # index 0 wraps back to the last option
    assert isinstance(state.overlay, SettingsOverlay)
    assert state.overlay.settings[0].index == 1  # showing Light
    assert state.theme == ThemeName.DARK  # but nothing is committed yet


def test_escape_leaves_the_theme_unchanged(seeded: Engine) -> None:
    state = tui.start(seeded, ProjectScope("tt"))
    state = press(seeded, state, ",")
    state = press(seeded, state, "right")  # move the selection to Light
    state = press(seeded, state, "escape")  # cancel without committing
    assert state.overlay is None
    assert state.theme == ThemeName.DARK


def test_startup_theme_prefers_the_env_override_over_the_persisted_preference() -> None:
    from tt.platform.config import Prefs

    light_pref = Prefs(theme=ThemeName.LIGHT)
    # TEXTUAL_THEME wins when it names one of ours.
    assert tui._startup_theme(light_pref, "tt-dark") == ThemeName.DARK
    # Unset, or a theme this app does not paint for, falls through to the preference.
    assert tui._startup_theme(light_pref, None) == ThemeName.LIGHT
    assert tui._startup_theme(light_pref, "textual-dark") == ThemeName.LIGHT
