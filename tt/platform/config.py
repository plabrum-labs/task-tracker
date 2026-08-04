"""The per-user preferences file, and the one setting it holds today.

A flat TOML table under the XDG config home (``~/.config/tt/config.toml``),
mirroring ``db.py``'s path logic — ``TT_CONFIG`` overrides it, which is how a
test points at a tmp file. This module knows nothing of textual: the theme is a
plain string here, and the frontend maps it onto a ``Theme``.

``load`` is total. A missing file, a parse error, or an unknown theme string all
fall back to the defaults rather than crashing the TUI on a hand-edited file —
preferences are a convenience, never a precondition for the app starting.
"""

import os
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import tomli_w


class ThemeName(StrEnum):
    """A registered theme's name, which is also the value stored in the file."""

    DARK = "tt-dark"
    LIGHT = "tt-light"


@dataclass(frozen=True)
class Prefs:
    theme: ThemeName = ThemeName.DARK


def config_path() -> Path:
    """The preferences file. ``TT_CONFIG`` overrides it — the tests go through
    that. Otherwise ``$XDG_CONFIG_HOME/tt/config.toml``, with ``~/.config`` the
    default config home."""
    override = os.environ.get("TT_CONFIG")
    if override is not None:
        return Path(override)
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home) / "tt" / "config.toml"


def load() -> Prefs:
    """Read the preferences, defaulting past anything the file cannot supply."""
    path = config_path()
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return Prefs()
    ui = data.get("ui")
    theme = ui.get("theme") if isinstance(ui, dict) else None
    try:
        return Prefs(theme=ThemeName(theme))
    except ValueError:
        return Prefs()


def save(prefs: Prefs) -> None:
    """Write the preferences, creating the config directory on demand."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        tomli_w.dump({"ui": {"theme": prefs.theme.value}}, handle)
