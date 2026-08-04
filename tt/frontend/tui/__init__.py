"""The terminal UI.

``run(engine)`` opens it — the one call ``cli.py`` makes and ``test_cli.py``
monkeypatches. The package is idiomatic Textual: an ``App`` with a browse ``Screen``
and modal overlays (``screens/``), custom widgets (``widgets/``), a native theme
system (``theme.py``), and a framework-free pure core (``domainview.py``) driven
directly in tests.
"""

from tt.frontend.tui.app import run

__all__ = ["run"]
