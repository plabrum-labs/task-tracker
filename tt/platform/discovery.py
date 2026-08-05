"""Auto-discovery utility for importing Python modules by filename pattern.

Used to trigger decorator-based registration and __init_subclass__ registration
(e.g. models, tasks) at startup without requiring explicit imports.

Vendored from ``snacks.utils.discovery``; once snacks is a package this module is
deleted and the import swapped to it, so it is kept faithful to that source.

Example:
    from pathlib import Path
    import myapp
    from tt.platform.discovery import discover_and_import

    discover_and_import(["models.py", "models/**/*.py"], search_root=Path(myapp.__file__).parent)
    discover_and_import(["tasks.py"], search_root=Path(myapp.__file__).parent)

`search_root` is the app's package directory (e.g. `.../src/myapp`). snacks cannot
assume the app's directory layout, so the root is passed in explicitly rather than
derived from `__file__` (the forced decouple from the source app's hardcoded
`Path(__file__).parent.parent.parent / base_path`). Module names are computed
relative to `search_root.parent`, so that directory must be importable (on sys.path).
"""

import logging
from importlib import import_module
from pathlib import Path

logger = logging.getLogger(__name__)


def discover_and_import(
    patterns: list[str],
    search_root: Path,
    exclude_paths: list[str] | None = None,
) -> list[str]:
    """Discover and import all modules matching the given filename patterns.

    Args:
        patterns: Filename patterns to match (e.g. ["models.py", "models/**/*.py"]).
        search_root: Package directory to search under (e.g. the app's package root).
        exclude_paths: Path segments to skip (default: __pycache__, test, tests, alembic).

    Returns:
        List of imported module names.
    """
    if exclude_paths is None:
        exclude_paths = ["__pycache__", "test", "tests", "alembic"]

    search_dir = search_root
    import_root = search_root.parent

    if not search_dir.exists():
        logger.warning("Search directory does not exist: %s", search_dir)
        return []

    imported: list[str] = []
    seen: set[str] = set()

    for pattern in patterns:
        for file_path in search_dir.rglob(pattern):
            if any(excluded in file_path.parts for excluded in exclude_paths):
                continue
            if file_path.name == "__init__.py":
                continue

            try:
                relative = file_path.relative_to(import_root)
                module_name = ".".join((*relative.parts[:-1], relative.stem))

                if module_name in seen:
                    continue

                import_module(module_name)
                imported.append(module_name)
                seen.add(module_name)
                logger.debug("Discovered: %s", module_name)

            except Exception:
                logger.exception("Failed to import discovered module: %s", file_path)

    logger.info("Auto-discovery imported %d modules for patterns %s", len(imported), patterns)
    return imported
