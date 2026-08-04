"""The two themes the TUI paints in, and how the opening one is chosen.

The themes are the one source of truth for every colour, and each colour is one of
two kinds. Text follows the terminal: ``foreground`` and the muted/disabled tiers
are ANSI names (``ansi_default``, ``ansi_bright_black``), so the body text adopts
whatever scheme the terminal is themed in and inherits its contrast — the lazygit
approach. Identity is pinned: the accents (``primary``, the status glyphs,
``priority-high``) stay truecolor hex so the ``◆`` and the priority marker read the
same everywhere. ``style.tcss`` and the content markup read every colour back
through ``$`` variables, so nothing downstream carries a literal.

Keeping ``primary``/``background`` as hex is also what lets the selection tint
(``$primary 14%``) and the modal dim (``$background 55%``) composite — an alpha
blend needs a concrete base colour, which an ANSI name does not give.

``ansi=True`` is what lets the terminal's own background show through: it disables
Textual's truecolor filter so an ``ansi_default`` background is emitted as the
terminal's default rather than resolved to opaque black, while the pinned hex still
paints on top and the ANSI text names resolve against the terminal palette. It also
switches on the ``:ansi`` pseudo-class, whose stock ``Screen``/``App`` CSS
references ``$ansi-background``/``$ansi-foreground`` — variables only the built-in
ANSI themes define, so we must supply them here or CSS parsing fails. They surface
only as inline-mode border colours, which this fullscreen app never draws.
"""

from __future__ import annotations

from textual.theme import Theme

from tt.platform import config
from tt.platform.config import Prefs, ThemeName

_ANSI_VARS_DARK = {"ansi-background": "#0B0C0F", "ansi-foreground": "#E7E9EE"}
_ANSI_VARS_LIGHT = {"ansi-background": "#FFFFFF", "ansi-foreground": "#1A1D23"}

TT_DARK = Theme(
    name=ThemeName.DARK.value,
    dark=True,
    ansi=True,
    primary="#8B8CF0",
    foreground="ansi_default",
    warning="#E3B341",
    success="#3FB950",
    background="#0B0C0F",
    surface="#1B1E25",
    panel="#1B1E25",
    variables={
        "text-muted": "ansi_bright_black",
        "text-disabled": "ansi_bright_black",
        "border": "#23262E",
        "priority-high": "#F0883E",
        **_ANSI_VARS_DARK,
    },
)
TT_LIGHT = Theme(
    name=ThemeName.LIGHT.value,
    dark=False,
    ansi=True,
    primary="#5457D6",
    foreground="ansi_default",
    warning="#B7791F",
    success="#1F883D",
    background="#FFFFFF",
    surface="#F0F1F4",
    panel="#F0F1F4",
    variables={
        "text-muted": "ansi_bright_black",
        "text-disabled": "ansi_bright_black",
        "border": "#D5D8DE",
        "priority-high": "#B5540B",
        **_ANSI_VARS_LIGHT,
    },
)

THEMES = (TT_DARK, TT_LIGHT)


def startup_theme(prefs: Prefs, env_theme: str | None) -> ThemeName:
    """The theme the app opens on. ``TEXTUAL_THEME`` wins when it names one of ours —
    a per-session override that is not written back — otherwise the persisted
    preference. Anything else (unset, or a built-in theme this app does not paint for)
    falls through to the preference."""
    try:
        return ThemeName(env_theme)
    except ValueError:
        return prefs.theme


def save_theme(theme: ThemeName) -> None:
    """Persist a committed theme choice as the new preference."""
    config.save(Prefs(theme=theme))
