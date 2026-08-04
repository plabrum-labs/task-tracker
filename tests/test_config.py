"""The preferences file: a round-trip, and the ways a bad file falls back.

``TT_CONFIG`` points every case at a tmp path, so nothing here touches the real
``~/.config``. ``load`` is total — no input should raise — so the malformed cases
assert a default rather than an error.
"""

from pathlib import Path

import pytest

from tt.platform.config import Prefs, ThemeName, load, save


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.toml"
    monkeypatch.setenv("TT_CONFIG", str(path))
    return path


def test_save_then_load_round_trips_the_theme(config_file: Path) -> None:
    save(Prefs(theme=ThemeName.LIGHT))
    assert load() == Prefs(theme=ThemeName.LIGHT)


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
