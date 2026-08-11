"""The per-user preferences file, and the settings it holds today.

A TOML table under the XDG config home (``~/.config/tt/config.toml``), mirroring
``db.py``'s path logic — ``TT_CONFIG`` overrides it, which is how a test points at
a tmp file. This module knows nothing of textual: the theme, the layout and the
saved views are plain values here, and the frontend maps them onto its widgets.
That is why a stored view holds strings rather than a filter or a grouping — the
dimensions and the facets are the frontend's vocabulary, not the file's.

``load`` is total. A missing file, a parse error, or an unrecognised value for
any field all fall back to that field's default rather than crashing the TUI on
a hand-edited file — preferences are a convenience, never a precondition for the
app starting.
"""

import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
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
class SavedFilter:
    """The facets a stored view narrows on, as the file holds them: plain strings, with
    the due cutoff an ISO date. A facet the view does not use holds nothing."""

    status: str | None = None
    priority: str | None = None
    epic: str | None = None
    milestone: str | None = None
    tag: str | None = None
    due_before: str | None = None
    text: str = ""


@dataclass(frozen=True)
class SavedView:
    """A named slice of the tracker: the project it pins (``None`` spans every project,
    which is how one view cuts across them), the dimensions it groups by outermost
    first, and the facets it narrows on."""

    name: str
    project: str | None = None
    group: tuple[str, ...] = ()
    filter: SavedFilter = field(default_factory=SavedFilter)


@dataclass(frozen=True)
class Prefs:
    theme: ThemeName = ThemeName.DARK
    layout: Layout = "list"
    views: tuple[SavedView, ...] = ()


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


def _text(table: Mapping[str, object], key: str) -> str | None:
    value = table.get(key)
    return value if isinstance(value, str) else None


def _saved_filter(value: object) -> SavedFilter:
    table: Mapping[str, object] = value if isinstance(value, dict) else {}
    return SavedFilter(
        status=_text(table, "status"),
        priority=_text(table, "priority"),
        epic=_text(table, "epic"),
        milestone=_text(table, "milestone"),
        tag=_text(table, "tag"),
        due_before=_text(table, "due_before"),
        text=_text(table, "text") or "",
    )


def _saved_view(entry: object) -> SavedView | None:
    """One stored view, or ``None`` when the entry cannot be one: a view is recalled and
    deleted by name, so an entry carrying none is dropped rather than loaded nameless."""
    if not isinstance(entry, dict):
        return None
    table: Mapping[str, object] = entry
    name = _text(table, "name")
    if not name:
        return None
    group = table.get("group")
    levels = group if isinstance(group, list) else []
    return SavedView(
        name=name,
        project=_text(table, "project"),
        group=tuple(level for level in levels if isinstance(level, str)),
        filter=_saved_filter(table.get("filter")),
    )


def _views(value: object) -> tuple[SavedView, ...]:
    if not isinstance(value, list):
        return _DEFAULTS.views
    return tuple(view for view in (_saved_view(entry) for entry in value) if view is not None)


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
    return Prefs(
        theme=_theme(ui.get("theme")),
        layout=_layout(ui.get("layout")),
        views=_views(data.get("views")),
    )


def _filter_table(narrowing: SavedFilter) -> dict[str, str]:
    # TOML has no null, so a facet the view does not use is left out of its table
    # altogether; a blank text facet is no facet at all and goes the same way.
    held: list[tuple[str, str | None]] = [
        ("status", narrowing.status),
        ("priority", narrowing.priority),
        ("epic", narrowing.epic),
        ("milestone", narrowing.milestone),
        ("tag", narrowing.tag),
        ("due_before", narrowing.due_before),
        ("text", narrowing.text or None),
    ]
    return {name: value for name, value in held if value is not None}


def _view_table(view: SavedView) -> dict[str, object]:
    table: dict[str, object] = {"name": view.name, "group": list(view.group)}
    if view.project is not None:
        table["project"] = view.project
    narrowing = _filter_table(view.filter)
    if narrowing:
        table["filter"] = narrowing
    return table


def _raw_document() -> dict[str, object]:
    """The config file as its unparsed tables, or empty when it cannot be read.

    Fail-soft the same way ``load`` is: a save must not lose a table it does not
    own just because some other table in the file is malformed."""
    try:
        with config_path().open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def save(prefs: Prefs) -> None:
    """Write the preferences, creating the config directory on demand.

    Only the ``ui`` and ``views`` tables are this module's to write, so the rest
    of the existing document is loaded and preserved — a hand-edited ``[sync]``
    table survives the TUI persisting a theme, which would otherwise erase every
    table this rebuild did not list."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    document = _raw_document()
    document["ui"] = {"theme": prefs.theme.value, "layout": prefs.layout}
    if prefs.views:
        document["views"] = [_view_table(view) for view in prefs.views]
    else:
        # No views is the absence of the table, not an empty one — so clearing them
        # drops a stale ``[[views]]`` rather than leaving it for ``load`` to reread.
        document.pop("views", None)
    with path.open("wb") as handle:
        tomli_w.dump(document, handle)


def save_theme(theme: ThemeName) -> None:
    """Persist a committed theme choice, leaving the other preferences intact."""
    save(replace(load(), theme=theme))


def save_layout(layout: Layout) -> None:
    """Persist the layout you switched to, leaving the other preferences intact."""
    save(replace(load(), layout=layout))


def save_views(views: Sequence[SavedView]) -> None:
    """Persist the saved views, leaving the other preferences intact."""
    save(replace(load(), views=tuple(views)))


# --- the [sync] table -----------------------------------------------------
#
# The mirrors a device keeps itself fresh against, and the cadence its installed
# schedule pulls on. tt reads this table and never writes it — a mirror is
# hand-edited — so there is no save counterpart here, and the ``save`` hardening
# above is what keeps a TUI theme write from erasing it.


@dataclass(frozen=True)
class Mirror:
    """One ssh host tt mirrors its database to, and the remote data directory the
    SQLite fileset lives under. ``path`` defaults to the same XDG-relative location
    tt uses locally, so a mirror is a host and nothing else in the common case."""

    host: str
    path: str = ".local/share/tt"


@dataclass(frozen=True)
class SyncConfig:
    """The [sync] table: the mirrors tt keeps a device fresh against, and the cadence
    (in seconds) the installed schedule pulls on."""

    mirrors: tuple[Mirror, ...] = ()
    interval: int = 900


_SYNC_DEFAULTS = SyncConfig()
_INTERVAL_UNITS: Mapping[str, int] = {"s": 1, "m": 60, "h": 3600}


def _interval(value: object) -> int:
    """A cadence as whole seconds. Accepts a bare positive integer of seconds, or a
    string with an ``s``/``m``/``h`` suffix (``"15m"`` → 900); anything unparseable
    or non-positive falls back to the default rather than raising on a hand-edited
    file. ``bool`` is not an integer count here — TOML has no bool cadence."""
    if isinstance(value, bool):
        return _SYNC_DEFAULTS.interval
    if isinstance(value, int):
        return value if value > 0 else _SYNC_DEFAULTS.interval
    if not isinstance(value, str):
        return _SYNC_DEFAULTS.interval
    text = value.strip()
    unit = _INTERVAL_UNITS.get(text[-1:])
    number = text[:-1] if unit is not None else text
    try:
        seconds = int(number) * (unit if unit is not None else 1)
    except ValueError:
        return _SYNC_DEFAULTS.interval
    return seconds if seconds > 0 else _SYNC_DEFAULTS.interval


def _mirror(entry: object) -> Mirror | None:
    """One configured mirror, or ``None`` when it names no host — a mirror with
    nothing to rsync to is dropped rather than loaded hostless, the way a nameless
    view is."""
    if not isinstance(entry, dict):
        return None
    table: Mapping[str, object] = entry
    host = _text(table, "host")
    if not host:
        return None
    path = _text(table, "path")
    if path:
        return Mirror(host=host, path=path)
    return Mirror(host=host)


def load_sync() -> SyncConfig:
    """Read the [sync] table, defaulting each field past anything the file cannot
    supply. Total like ``load``: a missing or malformed table yields no mirrors and
    the default interval, a mirror with no host is dropped, and a bad interval falls
    back — sync is a convenience, never a precondition for the app starting."""
    data = _raw_document()
    sync = data.get("sync")
    sync = sync if isinstance(sync, dict) else {}
    mirrors = sync.get("mirror")
    entries = mirrors if isinstance(mirrors, list) else []
    return SyncConfig(
        mirrors=tuple(m for m in (_mirror(entry) for entry in entries) if m is not None),
        interval=_interval(sync.get("interval")),
    )
