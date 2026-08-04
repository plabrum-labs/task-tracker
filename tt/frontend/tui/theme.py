"""The two themes the TUI paints in, and how the opening one is chosen.

The themes are the one source of truth for every colour. Standard tokens carry the
semantic colours; the ``variables`` block pins the exact hex the app draws and adds
``priority-high``, the one token with no standard name. These are the only hex
literals in the frontend — the ``style.tcss`` and the content markup both read their
colours back through ``$`` variables, so a theme switch repaints with no hardcoded
hex anywhere else.

``ansi=True`` is what lets the terminal's own background show through: it disables
Textual's truecolor filter so an ``ansi_default`` background is emitted as the
terminal's default rather than resolved to opaque black, while the truecolor hex
above still paints on top. It also switches on the ``:ansi`` pseudo-class, whose
stock ``Screen``/``App`` CSS references ``$ansi-background``/``$ansi-foreground`` —
variables only the built-in ANSI themes define, so we must supply them here or CSS
parsing fails. They surface only as inline-mode border colours, which this
fullscreen app never draws; the foreground/background hex is the honest value.
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
    foreground="#E7E9EE",
    warning="#E3B341",
    success="#3FB950",
    background="#0B0C0F",
    surface="#1B1E25",
    panel="#1B1E25",
    variables={
        "text-muted": "#8A8F99",
        "text-disabled": "#565B66",
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
    foreground="#1A1D23",
    warning="#B7791F",
    success="#1F883D",
    background="#FFFFFF",
    surface="#F0F1F4",
    panel="#F0F1F4",
    variables={
        "text-muted": "#5A606B",
        "text-disabled": "#9AA0AB",
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
