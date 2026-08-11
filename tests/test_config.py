"""The preferences file: a round-trip of every setting it holds — the theme, the
layout, and the list of saved views — and the ways a bad file falls back.

``TT_CONFIG`` points every case at a tmp path, so nothing here touches the real
``~/.config``. ``load`` is total — no input should raise — so the malformed cases
assert a default rather than an error.
"""

import tomllib
from pathlib import Path

import pytest

from tt.platform.config import (
    Prefs,
    SavedFilter,
    SavedView,
    ThemeName,
    load,
    save,
    save_layout,
    save_theme,
    save_views,
)


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.toml"
    monkeypatch.setenv("TT_CONFIG", str(path))
    return path


def test_save_then_load_round_trips_every_field(config_file: Path) -> None:
    save(Prefs(theme=ThemeName.LIGHT, layout="board"))
    assert load() == Prefs(theme=ThemeName.LIGHT, layout="board")


def test_saving_one_field_leaves_the_others_intact(config_file: Path) -> None:
    # save_theme and save_layout each read-modify-write, so persisting one
    # preference must not reset the other to its default.
    save_theme(ThemeName.LIGHT)
    save_layout("board")
    assert load() == Prefs(theme=ThemeName.LIGHT, layout="board")


def test_an_unknown_layout_keeps_the_valid_theme(config_file: Path) -> None:
    config_file.write_text('[ui]\ntheme = "tt-light"\nlayout = "spreadsheet"\n')
    assert load() == Prefs(theme=ThemeName.LIGHT, layout="list")


def test_save_creates_the_config_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "nested" / "dir" / "config.toml"
    monkeypatch.setenv("TT_CONFIG", str(path))
    save(Prefs(theme=ThemeName.DARK))
    assert path.exists()


def test_a_missing_file_loads_the_default(config_file: Path) -> None:
    assert not config_file.exists()
    assert load() == Prefs()


def test_malformed_toml_loads_the_default(config_file: Path) -> None:
    config_file.write_text("this is not = valid = toml")
    assert load() == Prefs()


def test_an_unknown_theme_loads_the_default(config_file: Path) -> None:
    config_file.write_text('[ui]\ntheme = "solarized"\n')
    assert load() == Prefs()


def test_an_empty_table_loads_the_default(config_file: Path) -> None:
    config_file.write_text("[ui]\n")
    assert load() == Prefs()


# --- saved views ----------------------------------------------------------

_VIEWS = (
    SavedView(
        name="bugs everywhere",
        # No project: the view spans them all, which is what cuts a tag across projects.
        project=None,
        group=("tag", "status"),
        filter=SavedFilter(tag="bug", text="parser"),
    ),
    SavedView(
        name="tt board",
        project="tt",
        group=("status",),
        filter=SavedFilter(
            status="doing",
            priority="high",
            epic="tt-e1",
            milestone="tt-m1",
            due_before="2026-08-15",
        ),
    ),
)


def test_save_then_load_round_trips_a_list_of_views(config_file: Path) -> None:
    save(Prefs(theme=ThemeName.LIGHT, layout="board", views=_VIEWS))
    assert load() == Prefs(theme=ThemeName.LIGHT, layout="board", views=_VIEWS)


def test_a_view_with_no_scope_group_or_filter_round_trips(config_file: Path) -> None:
    # Every field but the name is empty, so the written table has almost nothing in it —
    # what comes back must still be the same view rather than a defaulted one.
    bare = (SavedView(name="everything"),)
    save(Prefs(views=bare))
    assert load().views == bare


def test_saving_views_leaves_the_other_preferences_intact(config_file: Path) -> None:
    save_theme(ThemeName.LIGHT)
    save_layout("board")
    save_views(_VIEWS)
    assert load() == Prefs(theme=ThemeName.LIGHT, layout="board", views=_VIEWS)


def test_saving_the_theme_leaves_the_views_intact(config_file: Path) -> None:
    save_views(_VIEWS)
    save_theme(ThemeName.LIGHT)
    assert load().views == _VIEWS


def test_a_file_with_no_views_loads_none(config_file: Path) -> None:
    config_file.write_text('[ui]\ntheme = "tt-light"\n')
    assert load().views == ()


def test_a_nameless_view_is_dropped_and_the_rest_load(config_file: Path) -> None:
    # A view is recalled and deleted by name, so an entry carrying none is unusable; it
    # goes the way a bad theme does, without taking the readable entries with it.
    config_file.write_text(
        '[ui]\ntheme = "tt-dark"\n\n'
        '[[views]]\ngroup = ["status"]\n\n'
        '[[views]]\nname = "mine"\nproject = "tt"\n'
    )
    assert [view.name for view in load().views] == ["mine"]


def test_views_that_are_not_tables_load_as_none(config_file: Path) -> None:
    config_file.write_text('views = ["mine", 3]\n\n[ui]\ntheme = "tt-dark"\n')
    assert load() == Prefs()


# --- foreign tables -------------------------------------------------------


def test_save_preserves_a_table_it_does_not_own(config_file: Path) -> None:
    # [sync] is hand-edited and not this module's to write, so persisting a
    # preference must leave it — and its nested array of tables — untouched.
    config_file.write_text('[sync]\ninterval = "15m"\n\n[[sync.mirror]]\nhost = "walter"\n')
    save_theme(ThemeName.LIGHT)
    with config_file.open("rb") as handle:
        raw = tomllib.load(handle)
    assert raw["sync"] == {"interval": "15m", "mirror": [{"host": "walter"}]}
    assert load().theme == ThemeName.LIGHT
