"""The application shell: registers the themes, holds the engine, opens the screen.

``TrackerApp`` owns nothing of the domain. It registers the two themes before the
first paint so the opening screen is drawn in the persisted (or overridden) theme,
holds the engine the screens read and write through, and installs ``MainScreen``.
The CSS lives in ``style.tcss`` beside this module.
"""

from __future__ import annotations

import os

from sqlalchemy import Engine
from textual.app import App

from tt.frontend.tui.screens.main import MainScreen
from tt.frontend.tui.theme import TT_DARK, TT_LIGHT, startup_theme
from tt.platform import config
from tt.platform.config import ThemeName


class TrackerApp(App[None]):
    """The terminal the tracker is drawn to. One screen, the themes, the engine."""

    CSS_PATH = "style.tcss"

    # The tracker has its own ``:`` command palette; Textual's built-in one would
    # otherwise claim ``ctrl+p``, which the browse screen binds to move up.
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, engine: Engine, *, theme: ThemeName | None = None) -> None:
        super().__init__()
        self._engine = engine
        self.register_theme(TT_DARK)
        self.register_theme(TT_LIGHT)
        # The theme must be active before the stylesheet is parsed — ``style.tcss``
        # references custom variables (``$priority-high``) that only our themes define,
        # and that parse happens on registration, before ``on_mount``. The opening
        # theme is a test override, else the persisted preference with
        # ``TEXTUAL_THEME`` as a per-session override.
        opening = theme or startup_theme(config.load(), os.environ.get("TEXTUAL_THEME"))
        self.theme = opening.value

    @property
    def engine(self) -> Engine:
        return self._engine

    def on_mount(self) -> None:
        self.push_screen(MainScreen(self._engine))


def run(engine: Engine) -> None:
    """Open the terminal UI on ``engine``. The one call ``cli.py`` makes."""
    TrackerApp(engine).run()
