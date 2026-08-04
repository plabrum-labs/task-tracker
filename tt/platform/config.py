"""The per-user preferences file, and the settings it holds today.

A flat TOML table under the XDG config home (``~/.config/tt/config.toml``),
mirroring ``db.py``'s path logic — ``TT_CONFIG`` overrides it, which is how a
test points at a tmp file. This module knows nothing of textual: the theme and
layout are plain values here, and the frontend maps them onto its widgets.

``load`` is total. A missing file, a parse error, or an unrecognised value for
any field all fall back to that field's default rather than crashing the TUI on
a hand-edited file — preferences are a convenience, never a precondition for the
app starting.
"""

import os
import tomllib
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Literal

import tomli_w


class ThemeName(StrEnum):
    """A registered theme's name, which is also the value stored in the file."""

    DARK = "tt-dark"
    LIGHT = "tt-light"


# The body's view mode, persisted so it reopens on the one you left. The frontend
# owns the ordering and fit rules; this is only the vocabulary the file stores.
type Layout = Literal["list", "board"]
LAYOUTS: tuple[Layout, ...] = ("list", "board")


@dataclass(frozen=True)
class Prefs:
    theme: ThemeName = ThemeName.DARK
    layout: Layout = "list"


def config_path() -> Path:
    """The preferences file. ``TT_CONFIG`` overrides it — the tests go through
    that. Otherwise ``$XDG_CONFIG_HOME/tt/config.toml``, with ``~/.config`` the
    default config home."""
    override = os.environ.get("TT_CONFIG")
    if override is not None:
        return Path(override)
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home) / "tt" / "config.toml"


_DEFAULTS = Prefs()


def _theme(value: object) -> ThemeName:
    try:
        return ThemeName(value)
    except ValueError:
        return _DEFAULTS.theme


def _layout(value: object) -> Layout:
    for layout in LAYOUTS:
        if layout == value:
            return layout
    return _DEFAULTS.layout


def load() -> Prefs:
    """Read the preferences, defaulting each field past anything the file cannot
    supply — a bad value for one setting never discards the others."""
    path = config_path()
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return Prefs()
    ui = data.get("ui")
    ui = ui if isinstance(ui, dict) else {}
    return Prefs(theme=_theme(ui.get("theme")), layout=_layout(ui.get("layout")))


def save(prefs: Prefs) -> None:
    """Write the preferences, creating the config directory on demand."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        tomli_w.dump({"ui": {"theme": prefs.theme.value, "layout": prefs.layout}}, handle)


def save_theme(theme: ThemeName) -> None:
    """Persist a committed theme choice, leaving the other preferences intact."""
    save(replace(load(), theme=theme))


def save_layout(layout: Layout) -> None:
    """Persist the layout you switched to, leaving the other preferences intact."""
    save(replace(load(), layout=layout))
